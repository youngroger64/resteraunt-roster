from collections import Counter, defaultdict
from decimal import Decimal
from django.db import transaction
from apps.employees.models import Department, Employee
from apps.roster.models import (
    EmployeePattern,
    RosterPurpose,
    RosterWeek,
    Shift,
    StaffingPattern,
    CoveragePattern,
    DailyStaffingPattern,
    ShiftTemplatePattern,
    PayrollRecord,
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



def _signature_band(shifts):
    if not shifts:
        return "unknown"
    first_start = min(shift.start_time for shift in shifts)
    total_hours = sum(shift.duration_hours for shift in shifts)

    # Short midday shifts need their own band because they represent
    # lunch cover rather than a full day shift.
    if total_hours <= 4.5 and 11 <= first_start.hour < 16:
        return "short"

    if first_start.hour < 11:
        return "morning"
    if first_start.hour < 16:
        return "day"
    if first_start.hour < 20:
        return "evening"
    return "late"



def _plausible_shift_group(shifts):
    if not shifts:
        return False

    # Bad imports such as 09:00-01:00 create a 16-hour single shift.
    # Do not let one malformed historic cell become a weekly template.
    if any(shift.duration_hours > 12 for shift in shifts):
        return False

    total_hours = sum(
        shift.duration_hours for shift in shifts
    )
    return total_hours <= 13


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
    DailyStaffingPattern.objects.all().delete()
    ShiftTemplatePattern.objects.all().delete()

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
        weekly_department_hours = defaultdict(Counter)

        for shift in shifts:
            grouped[(shift.roster_week_id, shift.date.weekday())].append(shift)
            departments[shift.department] += 1
            weekly_hours[shift.roster_week_id] += shift.duration_hours
            weekly_department_hours[shift.department][shift.roster_week_id] += shift.duration_hours

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
        payroll_hours = list(
            PayrollRecord.objects.filter(employee=employee)
            .order_by("-payroll_week__week_end")
            .values_list("total_hours", flat=True)[:10]
        )
        roster_average_hours = (
            sum(weekly_hours.get(week_id, 0) for week_id in historic_weeks)
            / week_count
            if week_count else 0
        )
        payroll_average = (
            sum(float(value) for value in payroll_hours) / len(payroll_hours)
            if payroll_hours else 0
        )

        restaurant_target = (
            sum(
                weekly_department_hours[Department.RESTAURANT].get(week_id, 0)
                for week_id in historic_weeks
            ) / week_count
            if week_count else 0
        )
        bar_target = (
            sum(
                weekly_department_hours[Department.BAR].get(week_id, 0)
                for week_id in historic_weeks
            ) / week_count
            if week_count else 0
        )

        if payroll_average:
            average_hours = payroll_average
            tracked_total = restaurant_target + bar_target
            if tracked_total > payroll_average and tracked_total > 0:
                scale = payroll_average / tracked_total
                restaurant_target *= scale
                bar_target *= scale
        else:
            average_hours = roster_average_hours

        average_days = sum(days_per_week) / week_count if week_count else 0

        pattern = EmployeePattern.objects.create(
            employee=employee,
            weeks_seen=week_count,
            normal_department=departments.most_common(1)[0][0] if departments else "",
            average_weekly_hours=Decimal(str(round(average_hours, 2))),
            payroll_average_hours=Decimal(str(round(payroll_average, 2))),
            restaurant_target_hours=Decimal(str(round(restaurant_target, 2))),
            bar_target_hours=Decimal(str(round(bar_target, 2))),
            average_days_worked=Decimal(str(round(average_days, 2))),
            consistency=round(sum(consistency_parts) / len(consistency_parts)) if consistency_parts else 0,
            day_probabilities=probabilities,
            typical_shifts=typical,
        )
        employee_results.append(pattern)


    # Business shift templates.
    #
    # The exact shifts a manager repeatedly writes are more useful than
    # minimum slot coverage alone. We therefore learn both:
    #   1. StaffingPattern for backwards compatibility / coverage fallback.
    #   2. ShiftTemplatePattern for the normal daily roster blueprint.
    slot_counts = defaultdict(Counter)

    for week_id in historic_weeks:
        grouped = defaultdict(list)

        for shift in Shift.objects.filter(
            roster_week_id=week_id
        ):
            grouped[
                (
                    shift.date.weekday(),
                    shift.department,
                    shift.employee_id,
                )
            ].append(shift)

        per_week_slots = Counter()

        for (
            weekday,
            department,
            _employee_id,
        ), day_shifts in grouped.items():
            if not _plausible_shift_group(day_shifts):
                continue

            signature = shift_signature(day_shifts)
            per_week_slots[
                (
                    weekday,
                    department,
                    signature,
                )
            ] += 1

        for key, count in per_week_slots.items():
            slot_counts[key][week_id] = count

    for (
        weekday,
        department,
        signature,
    ), counts_by_week in slot_counts.items():
        counts = [
            counts_by_week.get(week_id, 0)
            for week_id in historic_weeks
        ]
        positive_counts = [
            value for value in counts if value > 0
        ]
        if not positive_counts:
            continue

        weeks_present = len(positive_counts)
        confidence = (
            round(
                (weeks_present / week_count) * 100
            )
            if week_count else 0
        )

        # Existing pattern remains useful for fallback coverage.
        average_required = (
            sum(counts) / week_count
            if week_count else 0
        )
        StaffingPattern.objects.create(
            weekday=weekday,
            department=department,
            shift_signature=signature,
            average_required=Decimal(
                str(round(average_required, 2))
            ),
            weeks_seen=week_count,
            confidence=confidence,
        )

        # Exact day blueprint: when the shift exists, how many copies
        # are normally on the roster?
        typical_count = max(
            1,
            round(_median(positive_counts)),
        )
        ShiftTemplatePattern.objects.create(
            weekday=weekday,
            department=department,
            shift_signature=signature,
            typical_count=typical_count,
            weeks_seen=week_count,
            confidence=confidence,
        )


    # Daily staffing demand: distinct workers and shift-band mix.
    daily_headcounts = defaultdict(Counter)
    daily_band_counts = defaultdict(lambda: defaultdict(Counter))

    for week_id in historic_weeks:
        grouped = defaultdict(list)
        for shift in Shift.objects.filter(
            roster_week_id=week_id
        ):
            grouped[
                (
                    shift.date.weekday(),
                    shift.department,
                    shift.employee_id,
                )
            ].append(shift)

        per_day_employees = Counter()
        per_day_bands = Counter()

        for (
            weekday,
            department,
            _employee_id,
        ), employee_shifts in grouped.items():
            per_day_employees[(weekday, department)] += 1
            band = _signature_band(employee_shifts)
            per_day_bands[(weekday, department, band)] += 1

        for key, count in per_day_employees.items():
            daily_headcounts[key][week_id] = count

        for (
            weekday,
            department,
            band,
        ), count in per_day_bands.items():
            daily_band_counts[
                (weekday, department)
            ][band][week_id] = count

    for (weekday, department), counts_by_week in daily_headcounts.items():
        counts = [
            counts_by_week.get(week_id, 0)
            for week_id in historic_weeks
        ]
        typical = round(_median(counts))
        positive_counts = [
            count for count in counts if count > 0
        ]
        minimum = (
            max(1, round(_median(positive_counts)))
            if positive_counts else 0
        )
        weeks_present = sum(count > 0 for count in counts)

        band_counts = {}
        for band in (
            "morning",
            "day",
            "short",
            "evening",
            "late",
        ):
            band_week_counts = daily_band_counts[
                (weekday, department)
            ][band]
            values = [
                band_week_counts.get(week_id, 0)
                for week_id in historic_weeks
            ]
            learned = round(_median(values))
            if learned > 0:
                band_counts[band] = learned

        DailyStaffingPattern.objects.create(
            weekday=weekday,
            department=department,
            typical_headcount=max(0, typical),
            minimum_headcount=max(0, minimum),
            band_counts=band_counts,
            weeks_seen=week_count,
            confidence=(
                round((weeks_present / week_count) * 100)
                if week_count else 0
            ),
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
        "daily_staffing_patterns": DailyStaffingPattern.objects.count(),
        "shift_templates": ShiftTemplatePattern.objects.count(),
        "historic_weeks": week_count,
    }
