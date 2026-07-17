from datetime import timedelta
from django.db import transaction
from apps.roster.models import RosterWeek, Shift

@transaction.atomic
def copy_roster(source: RosterWeek, target: RosterWeek) -> int:
    day_delta = target.week_start - source.week_start
    shifts = []
    for old in source.shifts.select_related("employee"):
        shifts.append(Shift(
            roster_week=target,
            employee=old.employee,
            department=old.department,
            date=old.date + day_delta,
            start_time=old.start_time,
            end_time=old.end_time,
            segment=old.segment,
            source="copied",
            confidence=90,
            notes=old.notes,
        ))
    Shift.objects.bulk_create(shifts)
    return len(shifts)
