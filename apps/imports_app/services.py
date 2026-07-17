from datetime import datetime, timedelta
import re
from openpyxl import load_workbook
from django.db import transaction
from apps.employees.models import Department, Employee
from apps.roster.models import RosterWeek, Shift

def _text(value):
    return str(value).strip() if value is not None else ""

@transaction.atomic
def import_employees(file_obj):
    workbook = load_workbook(file_obj, data_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return 0, ["Workbook is empty."]

    headers = [_text(v).lower().replace(" ", "_") for v in rows[0]]
    aliases = {
        "id": ["id", "employee_id", "staff_id"],
        "first_name": ["first_name", "firstname", "first"],
        "last_name": ["last_name", "lastname", "surname"],
        "name": ["name", "employee", "employee_name"],
        "department": ["department", "area", "section"],
    }
    def index_for(key):
        for alias in aliases[key]:
            if alias in headers:
                return headers.index(alias)
        return None

    indexes = {key: index_for(key) for key in aliases}
    count = 0
    warnings = []
    for row_number, row in enumerate(rows[1:], start=2):
        full_name = _text(row[indexes["name"]]) if indexes["name"] is not None and indexes["name"] < len(row) else ""
        first = _text(row[indexes["first_name"]]) if indexes["first_name"] is not None and indexes["first_name"] < len(row) else ""
        last = _text(row[indexes["last_name"]]) if indexes["last_name"] is not None and indexes["last_name"] < len(row) else ""
        if full_name and not first:
            pieces = full_name.split(maxsplit=1)
            first = pieces[0]
            last = pieces[1] if len(pieces) > 1 else ""
        if not first:
            continue
        external_id = _text(row[indexes["id"]]) if indexes["id"] is not None and indexes["id"] < len(row) else ""
        dept_text = _text(row[indexes["department"]]).lower() if indexes["department"] is not None and indexes["department"] < len(row) else ""
        department = Department.BAR if "bar" in dept_text else Department.RESTAURANT
        defaults = {
            "first_name": first,
            "last_name": last,
            "department": department,
            "can_work_bar": department == Department.BAR,
            "can_work_restaurant": department == Department.RESTAURANT,
            "is_active": True,
        }
        if external_id:
            Employee.objects.update_or_create(external_id=external_id, defaults=defaults)
        else:
            Employee.objects.update_or_create(first_name=first, last_name=last, defaults=defaults)
        count += 1
    return count, warnings

def _parse_shift(value):
    text = _text(value).lower()
    if not text or text in {"off", "-", "none", "holiday", "hol"}:
        return []
    text = text.replace(" to ", "-").replace("–", "-").replace("&", ",")
    chunks = [x.strip() for x in text.split(",") if x.strip()]
    result = []
    for chunk in chunks:
        match = re.search(r"(\d{1,2}(?:[:.]\d{1,2})?)\s*(am|pm)?\s*-\s*(\d{1,2}(?:[:.]\d{1,2})?)\s*(am|pm)?", chunk)
        if not match:
            continue
        start_raw, start_suffix, end_raw, end_suffix = match.groups()
        def parse(raw, suffix):
            raw = raw.replace(".", ":")
            if ":" not in raw:
                raw += ":00"
            hour, minute = [int(x) for x in raw.split(":", 1)]
            if suffix == "pm" and hour < 12:
                hour += 12
            if suffix == "am" and hour == 12:
                hour = 0
            return datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M").time()
        start = parse(start_raw, start_suffix)
        end = parse(end_raw, end_suffix)
        # Restaurant shorthand commonly uses 5 to mean 17:00 when start is morning.
        if not end_suffix and end.hour < 8 and start.hour >= 8:
            end = end.replace(hour=end.hour + 12)
        result.append((start, end))
    return result

@transaction.atomic
def import_roster(file_obj, week_start):
    workbook = load_workbook(file_obj, data_only=True)
    sheet = workbook.active
    roster, _ = RosterWeek.objects.get_or_create(week_start=week_start)
    rows = list(sheet.iter_rows(values_only=True))
    department = Department.RESTAURANT
    count = 0
    warnings = []

    # Find a row containing Monday..Sunday.
    header_index = None
    day_columns = {}
    for idx, row in enumerate(rows):
        lowered = [_text(v).lower() for v in row]
        if "monday" in lowered and "sunday" in lowered:
            header_index = idx
            for day_no, name in enumerate(["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]):
                day_columns[day_no] = lowered.index(name)
            break
    if header_index is None:
        return roster, 0, ["Could not find a Monday-to-Sunday header row."]

    for row_number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        first_cell = _text(row[0] if row else "")
        low = first_cell.lower()
        if low in {"restaurant", "bar"}:
            department = Department.BAR if low == "bar" else Department.RESTAURANT
            continue
        if not first_cell:
            continue
        employee, _ = Employee.objects.get_or_create(
            first_name=first_cell,
            last_name="",
            defaults={
                "department": department,
                "can_work_bar": department == Department.BAR,
                "can_work_restaurant": department == Department.RESTAURANT,
            },
        )
        for day_no, col in day_columns.items():
            if col >= len(row):
                continue
            parsed = _parse_shift(row[col])
            date = week_start + timedelta(days=day_no)
            for segment, (start, end) in enumerate(parsed, start=1):
                Shift.objects.update_or_create(
                    roster_week=roster,
                    employee=employee,
                    date=date,
                    segment=segment,
                    defaults={
                        "department": department,
                        "start_time": start,
                        "end_time": end,
                        "source": "imported",
                        "confidence": 95,
                    },
                )
                count += 1
    return roster, count, warnings
