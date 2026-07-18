from datetime import datetime, timedelta
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

DAY_KEYS = ["mon","tue","wed","thu","fri","sat","sun"]

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

        # Old imported shorthand may contain 18:00-10:00 or 17:00-10:00.
        # Interpret an unsuffixed end before the evening start as PM.
        if (
            start.hour >= 12
            and end.hour <= 12
            and end.hour != 0
            and end.hour <= start.hour
        ):
            end = end.replace(hour=(end.hour + 12) % 24)

        result.append((segment, start, end))

    return result

def compatible(employee, department):
    return employee.can_work_bar if department == Department.BAR else employee.can_work_restaurant

def score_candidate(pattern, weekday, department, signature, current_hours):
    employee = pattern.employee
    if not compatible(employee, department):
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
    if current_hours >= float(pattern.average_weekly_hours) + 4:
        score -= 30
    if pattern.average_days_worked <= 1 and probability < 50:
        score -= 25
    score += round(typical_confidence * 0.15)
    return score

@transaction.atomic
def generate_business_roster(target: RosterWeek, uncertain_threshold=75):
    target.purpose = RosterPurpose.WEEKLY
    target.save(update_fields=["purpose","updated_at"])
    target.shifts.all().delete()
    target.open_shifts.all().delete()

    patterns = list(EmployeePattern.objects.select_related("employee"))
    assigned_days = set()
    current_hours = {pattern.employee_id: 0.0 for pattern in patterns}
    created = 0
    open_count = 0
    suggestions = []

    staffing_patterns = StaffingPattern.objects.filter(
        confidence__gte=25,
        average_required__gte=0.5,
    )

    for staffing in staffing_patterns:
        required = max(1, round(float(staffing.average_required)))
        date = target.week_start + timedelta(days=staffing.weekday)

        for slot_number in range(required):
            ranked = []
            for pattern in patterns:
                if (pattern.employee_id, date) in assigned_days:
                    continue
                score = score_candidate(
                    pattern,
                    staffing.weekday,
                    staffing.department,
                    staffing.shift_signature,
                    current_hours.get(pattern.employee_id, 0.0),
                )
                ranked.append((score, pattern))

            ranked.sort(key=lambda item: item[0], reverse=True)
            best_score, best_pattern = ranked[0] if ranked else (-999, None)

            if best_pattern and best_score >= uncertain_threshold:
                duration = 0.0
                for segment, start, end in parse_signature(staffing.shift_signature):
                    shift = Shift.objects.create(
                        roster_week=target,
                        employee=best_pattern.employee,
                        department=staffing.department,
                        date=date,
                        start_time=start,
                        end_time=end,
                        segment=segment,
                        source="generated",
                        confidence=min(best_score, 100),
                    )
                    duration += shift.duration_hours
                    created += 1
                assigned_days.add((best_pattern.employee_id, date))
                current_hours[best_pattern.employee_id] = (
                    current_hours.get(best_pattern.employee_id, 0.0) + duration
                )
            else:
                first_segment = parse_signature(staffing.shift_signature)[0]
                _segment, start, end = first_segment
                OpenShift.objects.create(
                    roster_week=target,
                    department=staffing.department,
                    date=date,
                    start_time=start,
                    end_time=end,
                    source_signature=staffing.shift_signature,
                    confidence=max(best_score, 0),
                    notes="Needs manager choice",
                )
                open_count += 1
                suggestions.append({
                    "date": date.isoformat(),
                    "department": staffing.department,
                    "shift": staffing.shift_signature,
                    "choices": [
                        {
                            "employee_id": pattern.employee_id,
                            "name": pattern.employee.full_name,
                            "score": score,
                        }
                        for score, pattern in ranked[:3] if score > 0
                    ],
                })

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
