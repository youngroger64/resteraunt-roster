
import csv
import io
import re
from datetime import datetime

from django.db import transaction

from apps.employees.models import Employee
from apps.roster.models import (
    ClockingPatternImport,
    EmployeeClockingPattern,
)


DAY_KEYS = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}


def _normalise_name(value):
    value = (value or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


def _parse_float(value, default=0.0):
    try:
        if value in (None, "", "nan"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_int(value, default=0):
    try:
        if value in (None, "", "nan"):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_time(value):
    value = (value or "").strip()
    if not value or value.lower() == "nan":
        return None

    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            pass

    return None


def _employee_name(employee):
    if hasattr(employee, "full_name"):
        value = employee.full_name
        if callable(value):
            value = value()
        if value:
            return str(value)

    first = getattr(employee, "first_name", "") or ""
    last = getattr(employee, "last_name", "") or ""
    return f"{first} {last}".strip()


def _employee_number(employee):
    # The roster app currently uses external_id as the bridge to the
    # clocking application's employee_number.
    for field in ("external_id", "employee_number"):
        if hasattr(employee, field):
            value = getattr(employee, field)
            if value not in (None, ""):
                return str(value).strip()
    return ""


def _build_employee_indexes():
    by_number = {}
    by_name = {}

    for employee in Employee.objects.all():
        number = _employee_number(employee)
        if number:
            by_number[number] = employee

        name = _normalise_name(_employee_name(employee))
        if name:
            by_name[name] = employee

    return by_number, by_name


def _match_employee(row, by_number, by_name):
    number = str(row.get("employee_number", "") or "").strip()

    if number and number in by_number:
        return by_number[number], "employee_number"

    name = _normalise_name(row.get("employee", ""))
    if name and name in by_name:
        return by_name[name], "name"

    return None, None


def classify_worker(weeks_observed, active_weeks, avg_shifts_active, avg_hours_active):
    """
    Classification describes regularity, not importance.

    A one-day-per-week employee can be a perfectly valid regular part-time
    worker. Low hours must never mean "discard this employee".
    """
    if weeks_observed <= 0 or active_weeks <= 0:
        return EmployeeClockingPattern.WorkerType.INSUFFICIENT

    activity_ratio = active_weeks / weeks_observed

    # Core employees are present almost every week and normally work
    # roughly four or more shifts.
    if activity_ratio >= 0.80 and (
        avg_shifts_active >= 3.75 or avg_hours_active >= 30
    ):
        return EmployeeClockingPattern.WorkerType.CORE

    # Regular part-time includes people who reliably work even one or two
    # shifts per week. This is the important Dara/Grainne/Jack behaviour.
    if activity_ratio >= 0.50:
        return EmployeeClockingPattern.WorkerType.REGULAR_PART_TIME

    # Two or more observed active weeks is enough to keep a worker in the
    # useful occasional pool rather than throwing the evidence away.
    if active_weeks >= 2:
        return EmployeeClockingPattern.WorkerType.OCCASIONAL

    return EmployeeClockingPattern.WorkerType.INSUFFICIENT


def confidence_for(sample_confidence, weeks_observed, active_weeks):
    supplied = (sample_confidence or "").strip().lower()

    supplied_map = {
        "high": 90,
        "medium": 70,
        "low": 45,
    }

    base = supplied_map.get(supplied, 0)

    if not base:
        if weeks_observed >= 6 and active_weeks >= 5:
            base = 85
        elif active_weeks >= 3:
            base = 65
        elif active_weeks >= 2:
            base = 45
        else:
            base = 25

    return max(0, min(100, base))


def _read_csv(uploaded_file):
    if hasattr(uploaded_file, "read"):
        raw = uploaded_file.read()
    else:
        raw = uploaded_file

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig")

    return list(csv.DictReader(io.StringIO(raw)))


@transaction.atomic
def import_clocking_patterns(roster_patterns_file, day_patterns_file, source_name=""):
    roster_rows = _read_csv(roster_patterns_file)
    day_rows = _read_csv(day_patterns_file)

    batch = ClockingPatternImport.objects.create(
        source_name=source_name or "Clocking pattern CSV import",
        imported_rows=len(roster_rows) + len(day_rows),
    )

    by_number, by_name = _build_employee_indexes()

    day_data = {}

    for row in day_rows:
        employee, matched_by = _match_employee(row, by_number, by_name)
        if employee is None:
            continue

        day_name = (row.get("day") or "").strip().lower()
        day_key = DAY_KEYS.get(day_name)
        if not day_key:
            continue

        entry = day_data.setdefault(employee.pk, {})
        entry[day_key] = {
            "shifts_seen": _parse_int(row.get("shifts_seen")),
            "weeks_worked": _parse_int(row.get("weeks_worked_this_day")),
            "dataset_weeks": _parse_int(row.get("dataset_weeks")),
            "probability": _parse_float(row.get("probability_working_pct")),
            "typical_start": row.get("typical_start") or "",
            "typical_finish": row.get("typical_finish") or "",
            "average_shift_length": row.get("average_shift_length") or "",
        }

    imported = 0
    unmatched = []

    for row in roster_rows:
        employee, matched_by = _match_employee(row, by_number, by_name)

        if employee is None:
            unmatched.append({
                "employee_number": row.get("employee_number", ""),
                "employee": row.get("employee", ""),
            })
            continue

        weeks_observed = _parse_int(row.get("dataset_weeks"))
        active_weeks = _parse_int(row.get("weeks_seen"))

        avg_shifts_active = _parse_float(
            row.get("avg_shifts_per_active_week")
        )

        avg_hours_active = _parse_float(
            row.get("avg_rostered_hours_per_active_week")
        )

        worker_type = classify_worker(
            weeks_observed=weeks_observed,
            active_weeks=active_weeks,
            avg_shifts_active=avg_shifts_active,
            avg_hours_active=avg_hours_active,
        )

        EmployeeClockingPattern.objects.update_or_create(
            employee=employee,
            defaults={
                "import_batch": batch,
                "weeks_observed": weeks_observed,
                "active_weeks": active_weeks,
                "average_weekly_hours": avg_hours_active,
                "average_shifts_per_active_week": avg_shifts_active,
                "typical_start_time": _parse_time(row.get("typical_start")),
                "typical_end_time": _parse_time(row.get("typical_finish")),
                "weekday_counts": day_data.get(employee.pk, {}),
                "worker_type": worker_type,
                "confidence": confidence_for(
                    row.get("sample_confidence"),
                    weeks_observed,
                    active_weeks,
                ),
            },
        )

        imported += 1

    batch.notes = (
        f"Matched {imported} employees. "
        f"Unmatched {len(unmatched)} employees."
    )
    batch.save(update_fields=["notes"])

    return {
        "batch": batch,
        "imported": imported,
        "unmatched": unmatched,
        "roster_rows": len(roster_rows),
        "day_rows": len(day_rows),
    }
