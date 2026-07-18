from datetime import datetime, timedelta
from django.db import transaction
from apps.roster.models import EmployeePattern, RosterPurpose, RosterWeek, Shift

@transaction.atomic
def copy_roster(source: RosterWeek, target: RosterWeek) -> int:
    delta = target.week_start - source.week_start
    copied = []
    for old in source.shifts.all():
        copied.append(Shift(
            roster_week=target, employee=old.employee, department=old.department,
            date=old.date + delta, start_time=old.start_time, end_time=old.end_time,
            segment=old.segment, source="copied", confidence=90, notes=old.notes,
        ))
    Shift.objects.bulk_create(copied)
    return len(copied)

def parse_signature(text):
    if not text or text == "OFF":
        return []
    result = []
    for segment, part in enumerate(text.split(","), start=1):
        start, end = [v.strip() for v in part.split("-", 1)]
        result.append((segment, datetime.strptime(start, "%H:%M").time(), datetime.strptime(end, "%H:%M").time()))
    return result

@transaction.atomic
def generate_from_patterns(target, minimum_confidence=50):
    target.purpose = RosterPurpose.WEEKLY
    target.save(update_fields=["purpose","updated_at"])
    target.shifts.all().delete()
    unresolved, created = [], 0
    for pattern in EmployeePattern.objects.select_related("employee"):
        for day_no, key in enumerate(["mon","tue","wed","thu","fri","sat","sun"]):
            detail = pattern.typical_shifts.get(key, {})
            suggestion = detail.get("shift", "OFF")
            confidence = int(detail.get("confidence", 0))
            date = target.week_start + timedelta(days=day_no)
            if confidence < minimum_confidence:
                unresolved.append({
                    "employee_id": pattern.employee_id,
                    "employee": pattern.employee.full_name,
                    "date": date.isoformat(),
                    "suggestion": suggestion,
                    "confidence": confidence,
                })
                continue
            for segment, start, end in parse_signature(suggestion):
                Shift.objects.create(
                    roster_week=target, employee=pattern.employee,
                    department=pattern.normal_department or pattern.employee.department,
                    date=date, start_time=start, end_time=end, segment=segment,
                    source="learned", confidence=confidence,
                )
                created += 1
    return created, unresolved
