from collections import Counter, defaultdict
from decimal import Decimal
from django.db import transaction
from apps.employees.models import Employee
from apps.roster.models import EmployeePattern, RosterPurpose, Shift

DAY_KEYS = ["mon","tue","wed","thu","fri","sat","sun"]

def signature(shifts):
    return ", ".join(
        f"{s.start_time.strftime('%H:%M')}-{s.end_time.strftime('%H:%M')}"
        for s in sorted(shifts, key=lambda x: x.segment)
    )

@transaction.atomic
def learn_patterns():
    week_ids = list(
        Shift.objects.filter(roster_week__purpose=RosterPurpose.HISTORIC)
        .values_list("roster_week_id", flat=True).distinct()
    )
    week_count = len(week_ids)
    results = []
    for employee in Employee.objects.filter(is_active=True):
        shifts = list(Shift.objects.filter(
            employee=employee,
            roster_week__purpose=RosterPurpose.HISTORIC,
        ).select_related("roster_week"))
        if not shifts:
            continue
        grouped = defaultdict(list)
        departments = Counter()
        hours = Counter()
        for shift in shifts:
            grouped[(shift.roster_week_id, shift.date.weekday())].append(shift)
            departments[shift.department] += 1
            hours[shift.roster_week_id] += shift.duration_hours

        probabilities, typical, consistency_parts = {}, {}, []
        for weekday, key in enumerate(DAY_KEYS):
            signatures = Counter()
            worked = 0
            for week_id in week_ids:
                day_shifts = grouped.get((week_id, weekday), [])
                if day_shifts:
                    worked += 1
                    signatures[signature(day_shifts)] += 1
            probabilities[key] = round((worked / week_count) * 100) if week_count else 0
            if signatures:
                best, occurrences = signatures.most_common(1)[0]
                confidence = round((occurrences / week_count) * 100)
                typical[key] = {"shift": best, "confidence": confidence}
                consistency_parts.append(confidence)
            else:
                typical[key] = {"shift": "OFF", "confidence": 100}

        days_per_week = []
        for week_id in week_ids:
            days_per_week.append(sum(1 for d in range(7) if grouped.get((week_id, d))))
        pattern, _ = EmployeePattern.objects.update_or_create(
            employee=employee,
            defaults={
                "weeks_seen": week_count,
                "normal_department": departments.most_common(1)[0][0] if departments else "",
                "average_weekly_hours": Decimal(str(round(sum(hours.values()) / week_count, 2))),
                "average_days_worked": Decimal(str(round(sum(days_per_week) / week_count, 2))),
                "consistency": round(sum(consistency_parts) / len(consistency_parts)) if consistency_parts else 0,
                "day_probabilities": probabilities,
                "typical_shifts": typical,
            },
        )
        results.append(pattern)
    return results
