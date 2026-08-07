from datetime import datetime, timedelta
import math

from django.db import transaction

from apps.employees.models import Department
from apps.roster.models import (
    EmployeePattern,
    OpenShift,
    RosterPurpose,
    RosterWeek,
    Shift,
    StaffingPattern,
    CoveragePattern,
    DailyStaffingPattern,
    ShiftTemplatePattern,
)

DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MINIMUM_SPLIT_GAP_MINUTES = 90


def _normalise_generated_time(start, end):
    if start.hour >= 12 and 7 <= end.hour <= 12 and end.hour <= start.hour:
        end = end.replace(hour=(end.hour + 12) % 24)

    if start.hour >= 18 and end.hour == 12:
        end = end.replace(hour=0)

    if start.hour <= 8 and end.hour <= 2:
        start = start.replace(hour=start.hour + 12)

    return start, end


def parse_signature(text):
    if not text or text == "OFF":
        return []

    parsed = []
    for segment, part in enumerate(text.split(","), start=1):
        start_text, end_text = [
            value.strip() for value in part.split("-", 1)
        ]
        start = datetime.strptime(start_text, "%H:%M").time()
        end = datetime.strptime(end_text, "%H:%M").time()
        start, end = _normalise_generated_time(start, end)
        parsed.append((segment, start, end))
    return parsed


def compatible(employee, department):
    if department == Department.BAR:
        return employee.can_work_bar
    return employee.can_work_restaurant


def _interval(shift_date, start, end):
    start_dt = datetime.combine(shift_date, start)
    end_dt = datetime.combine(shift_date, end)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return start_dt, end_dt


def _signature_intervals(shift_date, signature):
    return [
        _interval(shift_date, start, end)
        for _segment, start, end in parse_signature(signature)
    ]


def _existing_intervals(roster, employee, shift_date):
    return [
        _interval(shift.date, shift.start_time, shift.end_time)
        for shift in Shift.objects.filter(
            roster_week=roster,
            employee=employee,
            date=shift_date,
        )
    ]


def candidate_availability(roster, employee, shift_date, signature):
    proposed = _signature_intervals(shift_date, signature)
    existing = _existing_intervals(roster, employee, shift_date)

    if not existing:
        return {
            "available": True,
            "possible_split": False,
            "reason": "Available for this shift",
        }

    minimum_gap = timedelta(minutes=MINIMUM_SPLIT_GAP_MINUTES)
    possible_split = True

    for proposed_start, proposed_end in proposed:
        for existing_start, existing_end in existing:
            overlaps = (
                proposed_start < existing_end
                and proposed_end > existing_start
            )
            if overlaps:
                return {
                    "available": False,
                    "possible_split": False,
                    "reason": (
                        "Already working "
                        f"{existing_start.strftime('%H:%M')}–"
                        f"{existing_end.strftime('%H:%M')}"
                    ),
                }

            gap_before = existing_start - proposed_end
            gap_after = proposed_start - existing_end
            valid_gap = (
                gap_before >= minimum_gap
                or gap_after >= minimum_gap
            )
            if not valid_gap:
                return {
                    "available": False,
                    "possible_split": False,
                    "reason": "Less than 90 minutes between shifts",
                }

    return {
        "available": True,
        "possible_split": possible_split,
        "reason": "Possible split shift",
    }


def duration_hours(start, end):
    start_dt, end_dt = _interval(datetime(2026, 1, 1).date(), start, end)
    return (end_dt - start_dt).total_seconds() / 3600


def signature_duration(signature):
    return sum(
        duration_hours(start, end)
        for _segment, start, end in parse_signature(signature)
    )



def shift_band(signature):
    parsed = parse_signature(signature)
    if not parsed:
        return "unknown"

    total_hours = signature_duration(signature)
    hour = parsed[0][1].hour

    if total_hours <= 4.5 and 11 <= hour < 16:
        return "short"
    if hour < 11:
        return "morning"
    if hour < 16:
        return "day"
    if hour < 20:
        return "evening"
    return "late"


def employee_typical_band(pattern, weekday):
    typical = pattern.typical_shifts.get(DAY_KEYS[weekday], {})
    signature = typical.get("shift", "OFF")
    return "unknown" if signature == "OFF" else shift_band(signature)


def has_historic_split_pattern(pattern, weekday, proposed_signature):
    typical = pattern.typical_shifts.get(DAY_KEYS[weekday], {})
    signature = typical.get("shift", "OFF")
    confidence = int(typical.get("confidence", 0))
    return (
        "," in signature
        and signature == proposed_signature
        and confidence >= 50
    )


def target_days(pattern):
    return min(7, max(0, math.ceil(float(pattern.average_days_worked))))




def target_hours(pattern, department=None):
    total_target = max(
        0.0,
        float(pattern.average_weekly_hours or 0),
    )
    restaurant_target = max(
        0.0,
        float(
            getattr(
                pattern,
                "restaurant_target_hours",
                0,
            ) or 0
        ),
    )
    bar_target = max(
        0.0,
        float(
            getattr(
                pattern,
                "bar_target_hours",
                0,
            ) or 0
        ),
    )

    if restaurant_target <= 0 and bar_target <= 0:
        normal_department = (
            pattern.normal_department
            or pattern.employee.department
        )
        if department is None:
            return total_target
        return (
            total_target
            if department == normal_department
            else 0.0
        )

    if department == Department.BAR:
        return bar_target
    if department == Department.RESTAURANT:
        return restaurant_target
    return total_target


def automatic_department_eligible(pattern, department):
    employee = pattern.employee

    if not compatible(employee, department):
        return False

    restaurant_target = float(
        getattr(
            pattern,
            "restaurant_target_hours",
            0,
        ) or 0
    )
    bar_target = float(
        getattr(
            pattern,
            "bar_target_hours",
            0,
        ) or 0
    )

    if restaurant_target <= 0 and bar_target <= 0:
        normal_department = (
            pattern.normal_department
            or employee.department
        )
        return normal_department == department

    if department == Department.BAR:
        return (
            employee.department == Department.BAR
            or pattern.normal_department == Department.BAR
            or bar_target > 0
        )

    return (
        employee.department == Department.RESTAURANT
        or pattern.normal_department
        == Department.RESTAURANT
        or restaurant_target > 0
    )


def minimum_target_hours(pattern, department=None):
    average = target_hours(pattern, department)
    if average < 8:
        return 0.0
    return round(average * 0.80, 2)


def hours_target_score(pattern, current_hours, proposed_hours, department=None):
    average = target_hours(pattern, department)
    if average <= 0:
        return 0

    projected = current_hours + proposed_hours
    if projected > automatic_hour_ceiling(pattern, department):
        return -999

    completion = current_hours / average if average else 1
    projected_completion = projected / average if average else 1

    # Regular/high-hour employees sit at the top of the food chain until
    # they reach a reasonable share of their proven payroll average.
    seniority = min(45, round(average * 1.25))
    if completion < 0.50:
        return 90 + seniority
    if completion < 0.75:
        return 60 + seniority
    if completion < 0.90:
        return 30 + round(seniority / 2)
    if projected_completion <= 1.0:
        return 10
    return -round((projected - average) * 6)


def effective_priority_ratio(pattern, current_hours, department=None):
    average = target_hours(pattern, department)
    if average < 8:
        return 9.0
    return current_hours / average


def automatic_hour_ceiling(pattern, department):

    average = target_hours(pattern, department)
    if average < 10:
        return average + 2
    return average + 2.5


def score_candidate(
    pattern,
    weekday,
    department,
    signature,
    current_hours,
    current_days,
    availability=None,
):
    employee = pattern.employee
    if not automatic_department_eligible(pattern, department):
        return -999
    average_hours = target_hours(
        pattern,
        department,
    )
    completion = (
        current_hours / average_hours
        if average_hours >= 8
        else 1.0
    )

    if current_days >= 6:
        return -999

    if (
        current_days >= target_days(pattern)
        and completion >= 0.75
    ):
        return -999

    proposed_hours = signature_duration(signature)
    if current_hours + proposed_hours > automatic_hour_ceiling(pattern, department):
        return -999

    key = DAY_KEYS[weekday]
    probability = int(pattern.day_probabilities.get(key, 0))
    typical = pattern.typical_shifts.get(key, {})
    typical_signature = typical.get("shift", "OFF")
    typical_confidence = int(typical.get("confidence", 0))
    proposed_band = shift_band(signature)
    typical_band = employee_typical_band(pattern, weekday)

    incompatible = {
        ("morning", "evening"),
        ("morning", "late"),
        ("evening", "morning"),
        ("late", "morning"),
    }
    if (
        typical_band != "unknown"
        and (typical_band, proposed_band) in incompatible
        and typical_confidence >= 50
    ):
        return -999

    if availability and availability.get("possible_split"):
        if not has_historic_split_pattern(pattern, weekday, signature):
            return -999

    score = probability
    score += 30 if pattern.normal_department == department else -20

    if typical_signature == signature:
        score += 50
    elif typical_signature != "OFF":
        score += 15 if typical_band == proposed_band else -25

    hour_score = hours_target_score(
        pattern,
        current_hours,
        proposed_hours,
        department,
    )
    if hour_score <= -999:
        return -999
    score += hour_score

    if target_days(pattern) - current_days == 1:
        score += 8
    if float(pattern.average_days_worked) < 1:
        score -= 35

    score += round(typical_confidence * 0.15)
    return score

def candidate_reasons(
    pattern,
    weekday,
    department,
    signature,
    current_hours,
    current_days,
    availability,
):
    key = DAY_KEYS[weekday]
    probability = int(pattern.day_probabilities.get(key, 0))
    typical = pattern.typical_shifts.get(key, {})
    typical_signature = typical.get("shift", "OFF")

    reasons = []
    if pattern.normal_department == department:
        reasons.append("Usually works this area")
    if probability >= 75:
        reasons.append(f"Usually works {key.title()}")
    elif probability >= 50:
        reasons.append(f"Often works {key.title()}")

    if typical_signature == signature:
        reasons.append("Usually works this shift time")
    elif employee_typical_band(pattern, weekday) == shift_band(signature):
        reasons.append("Usually works this time of day")

    if (
        target_hours(pattern) >= 8
        and current_hours < minimum_target_hours(pattern)
    ):
        reasons.append("Below normal weekly hours")
    elif not availability["possible_split"]:
        reasons.append("Available for this shift")
    elif has_historic_split_pattern(pattern, weekday, signature):
        reasons.append("Historically works this split pattern")

    if float(pattern.average_days_worked) < 1:
        reasons.append("Rare worker — reserve option")

    return reasons[:3]

def rank_candidates(
    roster,
    patterns,
    weekday,
    department,
    signature,
    current_hours,
    current_days,
    shift_date,
):
    ranked = []

    for pattern in patterns:
        availability = candidate_availability(
            roster=roster,
            employee=pattern.employee,
            shift_date=shift_date,
            signature=signature,
        )

        if not availability["available"]:
            continue

        score = score_candidate(
            pattern=pattern,
            weekday=weekday,
            department=department,
            signature=signature,
            current_hours=current_hours.get((pattern.employee_id, department), 0.0),
            current_days=current_days.get((pattern.employee_id, department), 0),
            availability=availability,
        )

        if score <= -999:
            continue

        ranked.append(
            {
                "score": score,
                "pattern": pattern,
                "reasons": candidate_reasons(
                    pattern=pattern,
                    weekday=weekday,
                    department=department,
                    signature=signature,
                    current_hours=current_hours.get((pattern.employee_id, department), 0.0),
                    current_days=current_days.get((pattern.employee_id, department), 0),
                    availability=availability,
                ),
                "possible_split": availability["possible_split"],
            }
        )


    ranked.sort(
        key=lambda item: (
            -effective_priority_ratio(
                item["pattern"],
                current_hours.get(
                    (
                        item["pattern"].employee_id,
                        department,
                    ),
                    0.0,
                ),
                department,
            ),
            target_hours(
                item["pattern"],
                department,
            ),
            item["score"],
        ),
        reverse=True,
    )
    return ranked



def signature_slots(signature):
    slots = set()
    for _segment, start, end in parse_signature(signature):
        start_minute = start.hour * 60 + start.minute
        end_minute = end.hour * 60 + end.minute
        if end_minute <= start_minute:
            end_minute += 1440
        slots.update(
            range(
                (start_minute // 30) * 30,
                ((end_minute + 29) // 30) * 30,
                30,
            )
        )
    return slots



def plausible_generated_signature(signature):
    parsed = parse_signature(signature)
    if not parsed:
        return False

    durations = []
    for _segment, start, end in parsed:
        start_dt = datetime.combine(
            datetime.today(),
            start,
        )
        end_dt = datetime.combine(
            datetime.today(),
            end,
        )
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

        duration = (
            end_dt - start_dt
        ).total_seconds() / 3600
        durations.append(duration)

    if any(duration > 12 for duration in durations):
        return False

    return sum(durations) <= 13


def planned_people_count(
    roster,
    shift_date,
    department,
):
    assigned = (
        roster.shifts.filter(
            date=shift_date,
            department=department,
        )
        .values("employee_id")
        .distinct()
        .count()
    )
    open_count = roster.open_shifts.filter(
        date=shift_date,
        department=department,
    ).count()
    return assigned + open_count


def open_shift_already_exists(
    roster,
    shift_date,
    department,
    signature,
):
    return roster.open_shifts.filter(
        date=shift_date,
        department=department,
        source_signature=signature,
    ).exists()


def template_blueprint(weekday, department):
    daily = DailyStaffingPattern.objects.filter(
        weekday=weekday,
        department=department,
        confidence__gte=25,
    ).first()

    target_count = (
        daily.typical_headcount
        if daily else 0
    )

    templates = list(
        ShiftTemplatePattern.objects.filter(
            weekday=weekday,
            department=department,
            confidence__gte=40,
            typical_count__gt=0,
        ).order_by(
            "-confidence",
            "-typical_count",
            "shift_signature",
        )
    )

    slots = []
    for template in templates:
        if not plausible_generated_signature(
            template.shift_signature
        ):
            continue

        for _copy in range(template.typical_count):
            slots.append(
                (
                    template.shift_signature,
                    template.confidence,
                )
            )

    # If exact historical signatures add up to more than the learned
    # normal headcount, retain the strongest recurring shifts only.
    if target_count and len(slots) > target_count:
        slots = slots[:target_count]

    # If history has headcount but exact signatures were too varied,
    # fill remaining template places using the strongest staffing patterns.
    if target_count and len(slots) < target_count:
        fallback = [
            pattern
            for pattern in StaffingPattern.objects.filter(
                weekday=weekday,
                department=department,
                confidence__gte=25,
            ).order_by(
                "-confidence",
                "-average_required",
            )
            if plausible_generated_signature(
                pattern.shift_signature
            )
        ]

        index = 0
        while fallback and len(slots) < target_count:
            pattern = fallback[index % len(fallback)]
            slots.append(
                (
                    pattern.shift_signature,
                    pattern.confidence,
                )
            )
            index += 1

    return target_count, slots


def rebuild_planned_coverage(roster):
    planned = {}

    for shift in roster.shifts.all():
        weekday = shift.date.weekday()
        signature = (
            f"{shift.start_time.strftime('%H:%M')}-"
            f"{shift.end_time.strftime('%H:%M')}"
        )
        add_planned_coverage(
            weekday,
            shift.department,
            signature,
            planned,
        )

    for open_shift in roster.open_shifts.all():
        signature = (
            open_shift.source_signature
            or open_shift.display_time.replace("–", "-")
        )
        add_planned_coverage(
            open_shift.date.weekday(),
            open_shift.department,
            signature,
            planned,
        )

    return planned


def generate_historic_templates(
    roster,
    patterns,
    current_hours,
    current_days,
    uncertain_threshold,
):
    created = 0
    open_count = 0
    suggestions = []

    for weekday in range(7):
        for department in (
            Department.RESTAURANT,
            Department.BAR,
        ):
            target_count, slots = template_blueprint(
                weekday,
                department,
            )
            if not slots:
                continue

            shift_date = (
                roster.week_start
                + timedelta(days=weekday)
            )

            for signature, _confidence in slots:
                if (
                    target_count
                    and planned_people_count(
                        roster,
                        shift_date,
                        department,
                    ) >= target_count
                ):
                    break

                if not plausible_generated_signature(
                    signature
                ):
                    continue

                result = create_assignment_or_open(
                    roster=roster,
                    patterns=patterns,
                    weekday=weekday,
                    department=department,
                    signature=signature,
                    shift_date=shift_date,
                    current_hours=current_hours,
                    current_days=current_days,
                    uncertain_threshold=uncertain_threshold,
                )

                new_created, new_open, new_suggestions = result
                created += new_created
                open_count += new_open
                suggestions.extend(new_suggestions)

    return created, open_count, suggestions


def learned_coverage_requirements():
    return {
        (pattern.weekday, pattern.department, pattern.slot_minute):
            max(1, round(float(pattern.average_required)))
        for pattern in CoveragePattern.objects.filter(
            confidence__gte=25,
            average_required__gte=0.5,
        )
    }


def has_coverage_deficit(
    weekday,
    department,
    signature,
    requirements,
    planned_coverage,
):
    # Backward-compatible fallback before Learning has created
    # coverage patterns.
    if not requirements:
        return True

    relevant_slots = [
        slot
        for slot in signature_slots(signature)
        if (weekday, department, slot) in requirements
    ]

    if not relevant_slots:
        return True

    return any(
        planned_coverage.get((weekday, department, slot), 0)
        < requirements[(weekday, department, slot)]
        for slot in relevant_slots
    )


def add_planned_coverage(
    weekday,
    department,
    signature,
    planned_coverage,
):
    for slot in signature_slots(signature):
        key = (weekday, department, slot)
        planned_coverage[key] = planned_coverage.get(key, 0) + 1



def daily_headcount(roster, shift_date, department):
    return (
        roster.shifts.filter(
            date=shift_date,
            department=department,
        )
        .values("employee_id")
        .distinct()
        .count()
    )


def daily_band_counts(roster, shift_date, department):
    grouped = {}
    for shift in roster.shifts.filter(
        date=shift_date,
        department=department,
    ):
        grouped.setdefault(shift.employee_id, []).append(shift)

    counts = {
        "morning": 0,
        "day": 0,
        "short": 0,
        "evening": 0,
        "late": 0,
    }
    for shifts in grouped.values():
        signature = _block_signature(shifts)
        band = shift_band(signature)
        if band in counts:
            counts[band] += 1
    return counts


def staffing_signatures_for_band(
    weekday,
    department,
    band,
):
    patterns = StaffingPattern.objects.filter(
        weekday=weekday,
        department=department,
        confidence__gte=25,
    ).order_by(
        "-confidence",
        "-average_required",
    )

    matches = [
        pattern.shift_signature
        for pattern in patterns
        if shift_band(pattern.shift_signature) == band
    ]
    if matches:
        return matches

    return [
        pattern.shift_signature
        for pattern in patterns
    ]


def create_assignment_or_open(
    roster,
    patterns,
    weekday,
    department,
    signature,
    shift_date,
    current_hours,
    current_days,
    uncertain_threshold,
):
    if not plausible_generated_signature(signature):
        return 0, 0, []

    ranked = rank_candidates(
        roster=roster,
        patterns=patterns,
        weekday=weekday,
        department=department,
        signature=signature,
        current_hours=current_hours,
        current_days=current_days,
        shift_date=shift_date,
    )

    if ranked:
        best_score = ranked[0]["score"]
        best_pattern = ranked[0]["pattern"]
    else:
        best_score, best_pattern = -999, None

    created = 0
    suggestions = []

    if best_pattern and best_score >= uncertain_threshold:
        worked_hours = 0.0
        for segment, start, end in parse_signature(signature):
            shift = Shift.objects.create(
                roster_week=roster,
                employee=best_pattern.employee,
                department=department,
                date=shift_date,
                start_time=start,
                end_time=end,
                segment=segment,
                source="generated",
                confidence=min(best_score, 100),
            )
            worked_hours += shift.duration_hours
            created += 1

        key = (best_pattern.employee_id, department)
        current_hours[key] = (
            current_hours.get(key, 0.0)
            + worked_hours
        )
        current_days[key] = current_days.get(key, 0) + 1
        return created, 0, suggestions

    parsed = parse_signature(signature)
    if not parsed:
        return 0, 0, suggestions

    _segment, start, end = parsed[0]
    open_shift = OpenShift.objects.create(
        roster_week=roster,
        department=department,
        date=shift_date,
        start_time=start,
        end_time=end,
        source_signature=signature,
        confidence=max(best_score, 0),
        notes="Daily staffing target needs a manager choice",
    )

    suggestions.append(
        {
            "open_shift_id": open_shift.pk,
            "date": shift_date.isoformat(),
            "department": department,
            "shift": signature,
            "choices": [
                {
                    "employee_id": item["pattern"].employee_id,
                    "name": item["pattern"].employee.full_name,
                    "score": item["score"],
                    "reasons": item["reasons"],
                    "possible_split": item["possible_split"],
                }
                for item in ranked[:5]
            ],
            "available_employee_ids": [
                item["pattern"].employee_id
                for item in ranked
            ],
        }
    )
    return 0, 1, suggestions


def fill_daily_staffing_gaps(
    roster,
    patterns,
    current_hours,
    current_days,
    uncertain_threshold,
):
    created = 0
    open_count = 0
    suggestions = []

    daily_patterns = DailyStaffingPattern.objects.filter(
        confidence__gte=25,
        typical_headcount__gt=0,
    ).order_by("weekday", "department")

    for daily in daily_patterns:
        shift_date = roster.week_start + timedelta(
            days=daily.weekday
        )
        target_count = max(
            daily.minimum_headcount,
            daily.typical_headcount,
        )

        current_count = planned_people_count(
            roster,
            shift_date,
            daily.department,
        )
        band_counts = daily_band_counts(
            roster,
            shift_date,
            daily.department,
        )

        required_bands = dict(daily.band_counts or {})

        # First fill missing shift types, then any remaining headcount.
        desired_bands = []
        for band in (
            "morning",
            "day",
            "short",
            "evening",
            "late",
        ):
            missing = max(
                0,
                int(required_bands.get(band, 0))
                - int(band_counts.get(band, 0)),
            )
            desired_bands.extend([band] * missing)

        while len(desired_bands) < max(
            0,
            target_count - current_count,
        ):
            desired_bands.append("any")

        for band in desired_bands:
            if planned_people_count(
                roster,
                shift_date,
                daily.department,
            ) >= target_count:
                break

            signatures = (
                staffing_signatures_for_band(
                    daily.weekday,
                    daily.department,
                    band,
                )
                if band != "any"
                else staffing_signatures_for_band(
                    daily.weekday,
                    daily.department,
                    "unknown",
                )
            )
            if not signatures:
                continue

            added = False
            for signature in signatures:
                result = create_assignment_or_open(
                    roster=roster,
                    patterns=patterns,
                    weekday=daily.weekday,
                    department=daily.department,
                    signature=signature,
                    shift_date=shift_date,
                    current_hours=current_hours,
                    current_days=current_days,
                    uncertain_threshold=uncertain_threshold,
                )
                new_created, new_open, new_suggestions = result
                if new_created or new_open:
                    created += new_created
                    open_count += new_open
                    suggestions.extend(new_suggestions)
                    added = True
                    break

            if not added:
                break

    return created, open_count, suggestions


def _group_generated_shift_blocks(roster):
    blocks = {}
    for shift in roster.shifts.filter(
        source="generated"
    ).select_related("employee"):
        key = (
            shift.employee_id,
            shift.date,
            shift.department,
        )
        blocks.setdefault(key, []).append(shift)
    return list(blocks.values())


def _block_signature(shifts):
    return ", ".join(
        f"{shift.start_time.strftime('%H:%M')}-"
        f"{shift.end_time.strftime('%H:%M')}"
        for shift in sorted(
            shifts,
            key=lambda item: item.segment,
        )
    )


def _hours_and_days(roster):
    hours = {}
    days = {}
    employee_days = set()

    for shift in roster.shifts.all():
        hours[shift.employee_id] = (
            hours.get(shift.employee_id, 0.0)
            + shift.duration_hours
        )
        employee_days.add(
            (shift.employee_id, shift.date)
        )

    for employee_id, shift_date in employee_days:
        days[employee_id] = (
            days.get(employee_id, 0) + 1
        )

    return hours, days


def rebalance_generated_shifts(roster, patterns):
    patterns_by_employee = {
        pattern.employee_id: pattern
        for pattern in patterns
    }
    changes = []

    for _iteration in range(30):
        current_hours, current_days = _hours_and_days(
            roster
        )

        under_patterns = [
            pattern
            for pattern in patterns
            if target_hours(pattern) >= 8
            and current_hours.get(
                pattern.employee_id,
                0.0,
            ) < minimum_target_hours(pattern)
        ]
        under_patterns.sort(
            key=lambda pattern: (
                effective_priority_ratio(
                    pattern,
                    current_hours.get(pattern.employee_id, 0.0),
                ),
                -target_hours(pattern),
            )
        )

        if not under_patterns:
            break

        swap_made = False

        for receiver in under_patterns:
            receiver_hours = current_hours.get(
                receiver.employee_id,
                0.0,
            )
            receiver_days = current_days.get(
                receiver.employee_id,
                0,
            )
            choices = []

            for block in _group_generated_shift_blocks(
                roster
            ):
                donor_id = block[0].employee_id
                if donor_id == receiver.employee_id:
                    continue

                donor = patterns_by_employee.get(
                    donor_id
                )
                if donor is None:
                    continue

                block_hours = sum(
                    shift.duration_hours
                    for shift in block
                )
                donor_hours = current_hours.get(
                    donor_id,
                    0.0,
                )
                donor_after = (
                    donor_hours - block_hours
                )

                if (
                    target_hours(donor) >= 8
                    and donor_after
                    < minimum_target_hours(donor)
                ):
                    continue

                signature = _block_signature(block)
                shift_date = block[0].date
                department = block[0].department

                availability = candidate_availability(
                    roster=roster,
                    employee=receiver.employee,
                    shift_date=shift_date,
                    signature=signature,
                )
                if not availability["available"]:
                    continue

                receiver_score = score_candidate(
                    pattern=receiver,
                    weekday=shift_date.weekday(),
                    department=department,
                    signature=signature,
                    current_hours=receiver_hours,
                    current_days=receiver_days,
                    availability=availability,
                )
                if receiver_score <= -999:
                    continue

                before = (
                    abs(
                        donor_hours
                        - target_hours(donor)
                    )
                    + abs(
                        receiver_hours
                        - target_hours(receiver)
                    )
                )
                after = (
                    abs(
                        donor_after
                        - target_hours(donor)
                    )
                    + abs(
                        receiver_hours
                        + block_hours
                        - target_hours(receiver)
                    )
                )
                improvement = before - after

                if improvement > 0:
                    choices.append(
                        (
                            improvement,
                            receiver_score,
                            block,
                            donor,
                        )
                    )

            if not choices:
                continue

            choices.sort(
                key=lambda item: (
                    item[0],
                    item[1],
                ),
                reverse=True,
            )
            _improvement, score, block, donor = (
                choices[0]
            )
            signature = _block_signature(block)

            for shift in block:
                shift.employee = receiver.employee
                shift.confidence = min(
                    max(score, 0),
                    100,
                )
                shift.notes = (
                    "Rebalanced toward learned "
                    "weekly hours"
                )
                shift.save(
                    update_fields=[
                        "employee",
                        "confidence",
                        "notes",
                        "updated_at",
                    ]
                )

            changes.append(
                {
                    "from": donor.employee.full_name,
                    "to": receiver.employee.full_name,
                    "date": block[0].date.isoformat(),
                    "shift": signature,
                }
            )
            swap_made = True
            break

        if not swap_made:
            break

    return changes


@transaction.atomic
def generate_business_roster(target: RosterWeek, uncertain_threshold=75):
    target.purpose = RosterPurpose.WEEKLY
    target.save(update_fields=["purpose", "updated_at"])
    target.shifts.all().delete()
    target.open_shifts.all().delete()

    patterns = list(
        EmployeePattern.objects.select_related("employee")
    )

    current_hours = {
        (pattern.employee_id, department): 0.0
        for pattern in patterns
        for department in (Department.RESTAURANT, Department.BAR)
    }
    current_days = {
        (pattern.employee_id, department): 0
        for pattern in patterns
        for department in (Department.RESTAURANT, Department.BAR)
    }

    created = 0
    open_count = 0
    suggestions = []

    # First reproduce what a normal historic day actually looks like.
    (
        template_created,
        template_open,
        template_suggestions,
    ) = generate_historic_templates(
        roster=target,
        patterns=patterns,
        current_hours=current_hours,
        current_days=current_days,
        uncertain_threshold=uncertain_threshold,
    )
    created += template_created
    open_count += template_open
    suggestions.extend(template_suggestions)

    # Then use 30-minute demand only as a safety net for genuine coverage gaps.
    coverage_requirements = learned_coverage_requirements()
    planned_coverage = rebuild_planned_coverage(target)

    staffing_patterns = StaffingPattern.objects.filter(
        confidence__gte=25,
        average_required__gte=0.5,
    ).order_by("weekday", "department", "shift_signature")

    for staffing in staffing_patterns:
        if not plausible_generated_signature(
            staffing.shift_signature
        ):
            continue

        required = max(1, round(float(staffing.average_required)))
        shift_date = target.week_start + timedelta(days=staffing.weekday)

        daily = DailyStaffingPattern.objects.filter(
            weekday=staffing.weekday,
            department=staffing.department,
        ).first()
        normal_headcount = (
            daily.typical_headcount
            if daily else 0
        )

        for _slot_number in range(required):
            # Coverage can add one safety shift above the historic normal,
            # but should not turn a normal 8-person Saturday into 11 people.
            if (
                normal_headcount
                and planned_people_count(
                    target,
                    shift_date,
                    staffing.department,
                ) >= normal_headcount + 1
            ):
                continue
            if not has_coverage_deficit(
                weekday=staffing.weekday,
                department=staffing.department,
                signature=staffing.shift_signature,
                requirements=coverage_requirements,
                planned_coverage=planned_coverage,
            ):
                continue

            ranked = rank_candidates(
                roster=target,
                patterns=patterns,
                weekday=staffing.weekday,
                department=staffing.department,
                signature=staffing.shift_signature,
                current_hours=current_hours,
                current_days=current_days,
                shift_date=shift_date,
            )

            if ranked:
                best_score = ranked[0]["score"]
                best_pattern = ranked[0]["pattern"]
            else:
                best_score, best_pattern = -999, None

            if best_pattern and best_score >= uncertain_threshold:
                worked_hours = 0.0

                for segment, start, end in parse_signature(
                    staffing.shift_signature
                ):
                    shift = Shift.objects.create(
                        roster_week=target,
                        employee=best_pattern.employee,
                        department=staffing.department,
                        date=shift_date,
                        start_time=start,
                        end_time=end,
                        segment=segment,
                        source="generated",
                        confidence=min(best_score, 100),
                    )
                    worked_hours += shift.duration_hours
                    created += 1

                allocation_key = (best_pattern.employee_id, staffing.department)
                current_days[allocation_key] = current_days.get(allocation_key, 0) + 1
                current_hours[allocation_key] = current_hours.get(allocation_key, 0.0) + worked_hours
                add_planned_coverage(
                    staffing.weekday,
                    staffing.department,
                    staffing.shift_signature,
                    planned_coverage,
                )
            else:
                parsed = parse_signature(staffing.shift_signature)
                if not parsed:
                    continue

                _segment, start, end = parsed[0]
                open_shift = OpenShift.objects.create(
                    roster_week=target,
                    department=staffing.department,
                    date=shift_date,
                    start_time=start,
                    end_time=end,
                    source_signature=staffing.shift_signature,
                    confidence=max(best_score, 0),
                    notes="Needs manager choice",
                )
                open_count += 1
                add_planned_coverage(
                    staffing.weekday,
                    staffing.department,
                    staffing.shift_signature,
                    planned_coverage,
                )

                suggestions.append(
                    {
                        "open_shift_id": open_shift.pk,
                        "date": shift_date.isoformat(),
                        "department": staffing.department,
                        "shift": staffing.shift_signature,
                        "choices": [
                            {
                                "employee_id": item["pattern"].employee_id,
                                "name": item["pattern"].employee.full_name,
                                "score": item["score"],
                                "reasons": item["reasons"],
                                "possible_split": item["possible_split"],
                            }
                            for item in ranked[:5]
                        ],
                        "available_employee_ids": [
                            item["pattern"].employee_id
                            for item in ranked
                        ],
                    }
                )


    (
        daily_created,
        daily_open,
        daily_suggestions,
    ) = fill_daily_staffing_gaps(
        roster=target,
        patterns=patterns,
        current_hours=current_hours,
        current_days=current_days,
        uncertain_threshold=uncertain_threshold,
    )
    created += daily_created
    open_count += daily_open
    suggestions.extend(daily_suggestions)

    balance_changes = rebalance_generated_shifts(
        target,
        patterns,
    )
    
    return {
        "created": created,
        "open": open_count,
        "suggestions": suggestions,
        "balance_changes": balance_changes,
    }


@transaction.atomic
def copy_roster(source: RosterWeek, target: RosterWeek) -> int:
    day_delta = target.week_start - source.week_start
    copied_shifts = []

    for old_shift in source.shifts.select_related("employee"):
        copied_shifts.append(
            Shift(
                roster_week=target,
                employee=old_shift.employee,
                department=old_shift.department,
                date=old_shift.date + day_delta,
                start_time=old_shift.start_time,
                end_time=old_shift.end_time,
                segment=old_shift.segment,
                source="copied",
                confidence=90,
                notes=old_shift.notes,
            )
        )

    Shift.objects.bulk_create(copied_shifts)
    return len(copied_shifts)
