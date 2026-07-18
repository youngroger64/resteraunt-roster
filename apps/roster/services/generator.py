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
    hour = parsed[0][1].hour
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


def target_hours(pattern):
    return max(0.0, float(pattern.average_weekly_hours))


def automatic_hour_ceiling(pattern):
    average = target_hours(pattern)
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
    if not compatible(employee, department):
        return -999
    if current_days >= target_days(pattern):
        return -999

    proposed_hours = signature_duration(signature)
    if current_hours + proposed_hours > automatic_hour_ceiling(pattern):
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

    if not availability["possible_split"]:
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
            current_hours=current_hours.get(pattern.employee_id, 0.0),
            current_days=current_days.get(pattern.employee_id, 0),
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
                    current_hours=current_hours.get(pattern.employee_id, 0.0),
                    current_days=current_days.get(pattern.employee_id, 0),
                    availability=availability,
                ),
                "possible_split": availability["possible_split"],
            }
        )

    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


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
        pattern.employee_id: 0.0 for pattern in patterns
    }
    current_days = {
        pattern.employee_id: 0 for pattern in patterns
    }

    created = 0
    open_count = 0
    suggestions = []

    staffing_patterns = StaffingPattern.objects.filter(
        confidence__gte=25,
        average_required__gte=0.5,
    ).order_by("weekday", "department", "shift_signature")

    for staffing in staffing_patterns:
        required = max(1, round(float(staffing.average_required)))
        shift_date = target.week_start + timedelta(days=staffing.weekday)

        for _slot_number in range(required):
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

                current_days[best_pattern.employee_id] = (
                    current_days.get(best_pattern.employee_id, 0) + 1
                )
                current_hours[best_pattern.employee_id] = (
                    current_hours.get(best_pattern.employee_id, 0.0)
                    + worked_hours
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

    return {
        "created": created,
        "open": open_count,
        "suggestions": suggestions,
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
