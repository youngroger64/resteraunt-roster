from collections import Counter, defaultdict
from decimal import Decimal
from django.db import transaction
from apps.employees.models import Employee
from apps.roster.models import EmployeePattern, RosterPurpose, RosterWeek, Shift

DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

def _signature(shifts):
    return ", ".join(
        f"{shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')}"
        for shift in sorted(shifts, key=lambda item: item.segment)
    )

@transaction.atomic
def learn_patterns():
    historic_weeks = list(
        RosterWeek.objects.filter(
            purpose=RosterPurpose.HISTORIC,
            shifts__isnull=False,
        ).distinct().values_list("id", flat=True)
    )
    week_count = len(historic_weeks)
    results = []

    for employee in Employee.objects.filter(is_active=True):
        shifts = list(
            Shift.objects.filter(
                employee=employee,
                roster_week_id__in=historic_weeks,
            ).select_related("roster_week")
        )
        if not shifts:
            continue

        grouped = defaultdict(list)
        departments = Counter()
        weekly_hours = Counter()

        for shift in shifts:
            grouped[(shift.roster_week_id, shift.date.weekday())].append(shift)
            departments[shift.department] += 1
            weekly_hours[shift.roster_week_id] += shift.duration_hours

        probabilities = {}
        typical = {}
        consistency_parts = []

        for weekday, key in enumerate(DAY_KEYS):
            signatures = Counter()
            worked_weeks = 0
            for week_id in historic_weeks:
                day_shifts = grouped.get((week_id, weekday), [])
                if day_shifts:
                    worked_weeks += 1
                    signatures[_signature(day_shifts)] += 1

            probabilities[key] = round(
                (worked_weeks / week_count) * 100
            ) if week_count else 0

            if signatures:
                best, occurrences = signatures.most_common(1)[0]
                confidence = round((occurrences / week_count) * 100)
                typical[key] = {"shift": best, "confidence": confidence}
                consistency_parts.append(confidence)
            else:
                typical[key] = {"shift": "OFF", "confidence": 100}

        days_per_week = [
            sum(1 for weekday in range(7) if grouped.get((week_id, weekday)))
            for week_id in historic_weeks
        ]

        average_hours = (
            sum(weekly_hours.get(week_id, 0) for week_id in historic_weeks) / week_count
            if week_count else 0
        )
        average_days = (
            sum(days_per_week) / week_count if week_count else 0
        )

        pattern, _ = EmployeePattern.objects.update_or_create(
            employee=employee,
            defaults={
                "weeks_seen": week_count,
                "normal_department": departments.most_common(1)[0][0] if departments else "",
                "average_weekly_hours": Decimal(str(round(average_hours, 2))),
                "average_days_worked": Decimal(str(round(average_days, 2))),
                "consistency": (
                    round(sum(consistency_parts) / len(consistency_parts))
                    if consistency_parts else 0
                ),
                "day_probabilities": probabilities,
                "typical_shifts": typical,
            },
        )
        results.append(pattern)

    return results
