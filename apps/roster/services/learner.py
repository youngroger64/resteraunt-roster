from collections import Counter, defaultdict
from decimal import Decimal
from django.db import transaction
from apps.employees.models import Employee
from apps.roster.models import (
    EmployeePattern,
    RosterPurpose,
    RosterWeek,
    Shift,
    StaffingPattern,
    CoveragePattern,
)

DAY_KEYS = ["mon","tue","wed","thu","fri","sat","sun"]

def shift_signature(shifts):
    return ", ".join(
        f"{shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')}"
        for shift in sorted(shifts, key=lambda item: item.segment)
    )


def _covered_slots(shift):
    start = shift.start_time.hour * 60 + shift.start_time.minute
    end = shift.end_time.hour * 60 + shift.end_time.minute
    if end <= start:
        end += 1440
    return range((start // 30) * 30, ((end + 29) // 30) * 30, 30)


def _median(values):
    values = sorted(values)
    if not values:
        return 0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


@transaction.atomic
def learn_patterns():

    historic_weeks = list(
        RosterWeek.objects.filter(
            purpose=RosterPurpose.HISTORIC,
            shifts__isnull=False,
        ).distinct().values_list("id", flat=True)
    )
    week_count = len(historic_weeks)
    employee_results = []

    EmployeePattern.objects.all().delete()
    StaffingPattern.objects.all().delete()
    CoveragePattern.objects.all().delete()

    # Employee patterns
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
                    signatures[shift_signature(day_shifts)] += 1

            probabilities[key] = round((worked_weeks / week_count) * 100) if week_count else 0

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
        average_days = sum(days_per_week) / week_count if week_count else 0

        pattern = EmployeePattern.objects.create(
            employee=employee,
            weeks_seen=week_count,
            normal_department=departments.most_common(1)[0][0] if departments else "",
            average_weekly_hours=Decimal(str(round(average_hours, 2))),
            average_days_worked=Decimal(str(round(average_days, 2))),
            consistency=round(sum(consistency_parts) / len(consistency_parts)) if consistency_parts else 0,
            day_probabilities=probabilities,
            typical_shifts=typical,
        )
        employee_results.append(pattern)

    # Business staffing patterns
    slot_counts = defaultdict(Counter)
    for week_id in historic_weeks:
        grouped = defaultdict(list)
        for shift in Shift.objects.filter(roster_week_id=week_id):
            grouped[(shift.date.weekday(), shift.department, shift.employee_id)].append(shift)

        per_week_slots = Counter()
        for (weekday, department, _employee_id), day_shifts in grouped.items():
            signature = shift_signature(day_shifts)
            per_week_slots[(weekday, department, signature)] += 1

        for key, count in per_week_slots.items():
            slot_counts[key][week_id] = count

    for (weekday, department, signature), counts_by_week in slot_counts.items():
        counts = [counts_by_week.get(week_id, 0) for week_id in historic_weeks]
        average_required = sum(counts) / week_count if week_count else 0
        weeks_present = sum(1 for count in counts if count > 0)
        confidence = round((weeks_present / week_count) * 100) if week_count else 0
        StaffingPattern.objects.create(
            weekday=weekday,
            department=department,
            shift_signature=signature,
            average_required=Decimal(str(round(average_required, 2))),
            weeks_seen=week_count,
            confidence=confidence,
        )


    coverage_counts = defaultdict(Counter)
    
    for week_id in historic_weeks:
        per_week = Counter()
        for shift in Shift.objects.filter(roster_week_id=week_id):
            for slot_minute in _covered_slots(shift):
                per_week[
                    (shift.date.weekday(), shift.department, slot_minute)
                ] += 1
        for key, count in per_week.items():
            coverage_counts[key][week_id] = count
    
    for (weekday, department, slot_minute), counts_by_week in coverage_counts.items():
        counts = [counts_by_week.get(week_id, 0) for week_id in historic_weeks]
        required = _median(counts)
        if required < 0.5:
            continue
        weeks_present = sum(1 for value in counts if value > 0)
        CoveragePattern.objects.create(
            weekday=weekday,
            department=department,
            slot_minute=slot_minute,
            average_required=Decimal(str(round(required, 2))),
            weeks_seen=week_count,
            confidence=round((weeks_present / week_count) * 100) if week_count else 0,
        )
    
    return {
        "employees": employee_results,
        "staffing_patterns": StaffingPattern.objects.count(),
        "coverage_patterns": CoveragePattern.objects.count(),
        "historic_weeks": week_count,
    }
