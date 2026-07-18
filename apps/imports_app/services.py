from datetime import date, datetime, timedelta
import re
from openpyxl import load_workbook
from django.db import transaction
from apps.employees.models import Department, Employee
from apps.roster.models import RosterPurpose, RosterWeek, Shift

OFF_WORDS = {"", "off", "0ff", "-", "none", "holiday", "hol", "leave"}

def _text(value):
    return str(value).strip() if value is not None else ""

def _find_sheet(workbook):
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 15), values_only=True):
            values = [_text(v).lower() for v in row]
            if any(v.startswith("mon") for v in values) and any(v.startswith("sun") for v in values):
                return sheet
    return workbook.active

def _parse_week_start(sheet):
    for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 10), values_only=True):
        for value in row:
            match = re.search(r"(?:w\s*/?\s*e|week\s+ending)\s*(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{2,4})", _text(value), re.I)
            if match:
                day, month, year = map(int, match.groups())
                if year < 100:
                    year += 2000
                return date(year, month, day) - timedelta(days=6)
    return None

def _parse_time(raw, suffix=""):
    raw = raw.lower().strip().replace(".", ":")
    raw = re.sub(r"[^0-9:]", "", raw)
    if ":" not in raw:
        raw += ":00"
    hour, minute = map(int, raw.split(":", 1))
    suffix = suffix.lower()
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    return datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M").time()

def parse_shift_cell(value):
    text = _text(value).lower().strip()
    if text in OFF_WORDS:
        return [], None
    text = text.replace("—", "-").replace("–", "-").replace(" to ", "-")
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"(\d{1,2}[.:]\d{1,2})[.:](\d{1,2}[.:]\d{1,2})", r"\1-\2", text)
    text = text.replace("&", ",")
    parsed = []
    for chunk in [part.strip() for part in text.split(",") if part.strip()]:
        close_match = re.search(r"(\d{1,2}(?:[.:]\d{1,2})?)\s*(am|pm)?\s*-\s*close", chunk)
        if close_match:
            start = _parse_time(close_match.group(1), close_match.group(2) or "")
            if start.hour <= 7:
                start = start.replace(hour=start.hour + 12)
            parsed.append((start, datetime.strptime("01:00", "%H:%M").time()))
            continue
        match = re.search(
            r"(\d{1,2}(?:[.:]\d{1,2})?)\s*(am|pm)?\s*-\s*(\d{1,2}(?:[.:]\d{1,2})?)\s*(am|pm)?",
            chunk,
        )
        if not match:
            return [], f"Could not understand '{value}'"
        start_raw, start_suffix, end_raw, end_suffix = match.groups()
        start = _parse_time(start_raw, start_suffix or "")
        end = _parse_time(end_raw, end_suffix or "")
        if not start_suffix and start.hour <= 7:
            start = start.replace(hour=start.hour + 12)
        if not end_suffix and end.hour <= 7:
            end = end.replace(hour=end.hour + 12)
        parsed.append((start, end))
    return parsed, None

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
    def idx(key):
        for alias in aliases[key]:
            if alias in headers:
                return headers.index(alias)
        return None
    indexes = {key: idx(key) for key in aliases}
    count = 0
    for row in rows[1:]:
        full = _text(row[indexes["name"]]) if indexes["name"] is not None else ""
        first = _text(row[indexes["first_name"]]) if indexes["first_name"] is not None else ""
        last = _text(row[indexes["last_name"]]) if indexes["last_name"] is not None else ""
        if full and not first:
            parts = full.split(maxsplit=1)
            first = parts[0]
            last = parts[1] if len(parts) > 1 else ""
        if not first:
            continue
        external_id = _text(row[indexes["id"]]) if indexes["id"] is not None else ""
        dept = _text(row[indexes["department"]]).lower() if indexes["department"] is not None else ""
        department = Department.BAR if "bar" in dept else Department.RESTAURANT
        defaults = {
            "first_name": first, "last_name": last, "department": department,
            "can_work_bar": department == Department.BAR,
            "can_work_restaurant": department == Department.RESTAURANT,
            "is_active": True,
        }
        if external_id:
            Employee.objects.update_or_create(external_id=external_id, defaults=defaults)
        else:
            Employee.objects.update_or_create(first_name=first, last_name=last, defaults=defaults)
        count += 1
    return count, []

@transaction.atomic
def import_roster(file_obj, week_start=None, purpose=RosterPurpose.HISTORIC):
    workbook = load_workbook(file_obj, data_only=True)
    sheet = _find_sheet(workbook)
    week_start = week_start or _parse_week_start(sheet)
    if week_start is None:
        raise ValueError("No readable week date.")
    roster, _ = RosterWeek.objects.get_or_create(week_start=week_start, defaults={"purpose": purpose})
    roster.purpose = purpose
    roster.save(update_fields=["purpose", "updated_at"])
    roster.shifts.all().delete()

    rows = list(sheet.iter_rows(values_only=True))
    header_index = None
    name_column = None
    day_columns = {}
    for idx, row in enumerate(rows):
        values = [_text(v).lower().strip() for v in row]
        monday = next((i for i, v in enumerate(values) if v.startswith("mon")), None)
        sunday = next((i for i, v in enumerate(values) if v.startswith("sun")), None)
        if monday is not None and sunday is not None:
            header_index = idx
            name_column = monday - 1
            for prefix, day_no in [("mon",0),("tue",1),("wed",2),("thu",3),("fri",4),("sat",5),("sun",6)]:
                day_columns[day_no] = next(i for i, v in enumerate(values) if v.startswith(prefix))
            break
    if header_index is None:
        return roster, 0, [{"message":"Could not find Monday-to-Sunday columns.","choices":["Choose another file","Enter shifts manually"]}]

    department = Department.RESTAURANT
    count = 0
    issues = []
    for row_number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        name = " ".join(_text(row[name_column]).split()) if name_column < len(row) else ""
        if name.lower() in {"restaurant", "bar"}:
            department = Department.BAR if name.lower() == "bar" else Department.RESTAURANT
            continue
        if not name:
            continue
        employee, created = Employee.objects.get_or_create(
            first_name=name, last_name="",
            defaults={
                "department": department,
                "can_work_bar": department == Department.BAR,
                "can_work_restaurant": department == Department.RESTAURANT,
            },
        )
        for day_no, col in day_columns.items():
            raw = row[col] if col < len(row) else None
            shifts, error = parse_shift_cell(raw)
            if error:
                issues.append({
                    "employee": employee.full_name,
                    "day": day_no,
                    "value": _text(raw),
                    "message": error,
                    "choices": ["Treat as OFF", "Fix manually"],
                })
                continue
            for segment, (start, end) in enumerate(shifts, start=1):
                Shift.objects.create(
                    roster_week=roster, employee=employee, department=department,
                    date=week_start + timedelta(days=day_no), segment=segment,
                    start_time=start, end_time=end, source="imported", confidence=95,
                )
                count += 1
    return roster, count, issues
