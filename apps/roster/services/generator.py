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


def _normalise_generated_time(start, end):
    # Evening shorthand inherited from older imports:
    # 18:00-10:00 means 18:00-22:00, not a 16-hour shift.
    if start.hour >= 12 and 7 <= end.hour <= 12 and end.hour <= start.hour:
        end = end.replace(hour=(end.hour + 12) % 24)

    # 19:00-12:30 is almost certainly 19:00-00:30.
    if start.hour >= 18 and end.hour == 12:
        end = end.replace(hour=0)

    # 08:00-01:00 in an evening staffing pattern is likely 20:00-01:00.
    if start.hour <= 8 and end.hour <= 2:
        start = start.replace(hour=start.hour + 12)

    return start, end


def parse_signature(text):
    if not text or text == "OFF":
        return []

    result = []
    for segment, part in enumerate(text.split(","), start=1):
        start_text, end_text = [
            value.strip() for value in part.split("-", 1)
        ]
        start = datetime.strptime(start_text, "%H:%M").time()
        end = datetime.strptime(end_text, "%H:%M").time()
        start, end = _normalise_generated_time(start, end)
        result.append((segment, start, end))

    return result


def compatible(employee, department):
    if department == Department.BAR:
        return employee.can_work_bar
    return employee.can_work_restaurant


def duration_hours(start, end):
    anchor = datetime(2026, 1, 1)
    start_dt = datetime.combine(anchor.date(), start)
    end_dt = datetime.combine(anchor.date(), end)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return (end_dt - start_dt).total_seconds() / 3600


def signature_duration(signature):
    return sum(
        duration_hours(start, end)
        for _segment, start, end in parse_signature(signature)
    )


def target_days(pattern):
    # 4.5 days should become 5, but never more than 7.
    return min(7, max(0, math.ceil(float(pattern.average_days_worked))))


def target_hours(pattern):
    return max(0.0, float(pattern.average_weekly_hours))


def automatic_hour_ceiling(pattern):
    average = target_hours(pattern)

    # Give a small realistic tolerance, not a whole extra shift.
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
):
    employee = pattern.employee

    if not compatible(employee, department):
        return -999

    employee_target_days = target_days(pattern)
    employee_hour_ceiling = automatic_hour_ceiling(pattern)
    proposed_hours = signature_duration(signature)

    # Hard limits: never auto-assign beyond the learned weekly shape.
    if current_days >= employee_target_days:
        return -999

    if current_hours + proposed_hours > employee_hour_ceiling:
        return -999

    key = DAY_KEYS[weekday]
    probability = int(pattern.day_probabilities.get(key, 0))
    typical = pattern.typical_shifts.get(key, {})
    typical_signature = typical.get("shift", "OFF")
    typical_confidence = int(typical.get("confidence", 0))

    score = probability

    if pattern.normal_department == department:
        score += 25

    if typical_signature == signature:
        score += 40
    elif typical_signature != "OFF":
        score += 10

    # Prefer completing a normal week, but not exceeding it.
    remaining_days = employee_target_days - current_days
    if remaining_days == 1:
        score += 8

    remaining_hours = employee_hour_ceiling - current_hours
    if proposed_hours <= remaining_hours:
        score += 6

    # Rare workers should remain reserve choices.
    if float(pattern.average_days_worked) < 1:
        score -= 35

    score += round(typical_confidence * 0.15)
    return score


@transaction.atomic
def generate_business_roster(target: RosterWeek, uncertain_threshold=75):
    target.purpose = RosterPurpose.WEEKLY
    target.save(update_fields=["purpose", "updated_at"])
    target.shifts.all().delete()
    target.open_shifts.all().delete()

    patterns = list(
        EmployeePattern.objects.select_related("employee")
    )

    assigned_days = set()
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
            ranked = []

            for pattern in patterns:
                if (pattern.employee_id, shift_date) in assigned_days:
                    continue

                score = score_candidate(
                    pattern=pattern,
                    weekday=staffing.weekday,
                    department=staffing.department,
                    signature=staffing.shift_signature,
                    current_hours=current_hours.get(pattern.employee_id, 0.0),
                    current_days=current_days.get(pattern.employee_id, 0),
                )
                ranked.append((score, pattern))

            ranked.sort(key=lambda item: item[0], reverse=True)
            best_score, best_pattern = (
                ranked[0] if ranked else (-999, None)
            )

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

                assigned_days.add(
                    (best_pattern.employee_id, shift_date)
                )
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
                OpenShift.objects.create(
                    roster_week=target,
                    department=staffing.department,
                    date=shift_date,
                    start_time=start,
                    end_time=end,
                    source_signature=staffing.shift_signature,
                    confidence=max(best_score, 0),
                    notes=(
                        "No suitable employee within normal "
                        "days and hours"
                    ),
                )
                open_count += 1
                suggestions.append(
                    {
                        "date": shift_date.isoformat(),
                        "department": staffing.department,
                        "shift": staffing.shift_signature,
                        "reason": (
                            "Assigning the next person would exceed "
                            "their normal days or hours."
                        ),
                        "choices": [
                            {
                                "employee_id": pattern.employee_id,
                                "name": pattern.employee.full_name,
                                "score": score,
                                "current_hours": round(
                                    current_hours.get(
                                        pattern.employee_id, 0.0
                                    ),
                                    1,
                                ),
                                "target_hours": round(
                                    target_hours(pattern),
                                    1,
                                ),
                                "current_days": current_days.get(
                                    pattern.employee_id, 0
                                ),
                                "target_days": target_days(pattern),
                            }
                            for score, pattern in ranked[:5]
                            if score > -999
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
