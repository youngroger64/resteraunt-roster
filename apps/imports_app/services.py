from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
import re
from openpyxl import load_workbook
from django.db import transaction
from django.db.models.functions import Lower
from apps.employees.models import Department, Employee, EmployeeNameAlias
from apps.roster.models import (
    AnnualLeave,
    RosterPurpose,
    RosterStatus,
    RosterWeek,
    Shift,
)

OFF_WORDS = {"", "off", "0ff", "-", "none", "holiday", "hol", "leave"}
HOUSEKEEPING_NOTE = "Housekeeping"

# Spreadsheet spellings that already mean one clocking ID.
# A missing target is left for the manager; it is not created here.
HARDCODED_ALIASES = {
    "danny parker": "201",
    "daniel parker": "201",
    "roland smatavics": "202",
    "rolands smatavics": "202",
    "michelle spenser": "218",
    "michelle spencer": "218",
    "eleonor matrosova": "231",
    "eleonora matrosova": "231",
    "natalia moldovan": "234",
    "nataliia moldovan": "234",
    "blaithnaid deegan": "223",
    "blathnaid deegan": "223",
}


class RosterImportError(Exception):
    """The import cannot be applied. Nothing from this attempt is saved."""


class UnresolvedRosterNames(Exception):
    """Names still need a manager decision, so the week has not been changed."""

    def __init__(self, plan):
        self.plan = plan
        super().__init__("Choose an employee for every unresolved name.")

# A trailing area token must be the whole marker, not part of a word.
# hk is checked before k. am/pm/mn stay time suffixes and are not areas.
_AREA_MARKER = re.compile(
    r"^(?P<body>.*\d)\s*(?P<area>hk|w|k)\s*$",
    re.IGNORECASE,
)

def _text(value):
    return str(value).strip() if value is not None else ""

def _normalise_name(value):
    return " ".join(_text(value).split()).strip()

def _find_sheet(workbook, week_start=None):
    # If a week was explicitly supplied, prefer the sheet
    # containing that exact W/C date.
    if week_start:
        expected = {
            f"w/c{week_start.day}/{week_start.month}/{str(week_start.year)[2:]}",
            f"w/c{week_start.day}/{week_start.month}/{week_start.year}",
        }

        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(
                min_row=1,
                max_row=min(sheet.max_row, 15),
                values_only=True,
            ):
                for value in row:
                    cleaned = _text(value).lower().replace(" ", "")
                    if cleaned in expected:
                        return sheet

    # No week supplied:
    # first look across ALL worksheets for an explicit
    # W/C, Week Commencing, W/E or Week Ending heading.
    date_heading = re.compile(
        r"(?:w\s*/?\s*[ce]|week\s+(?:commencing|ending))\s*"
        r"\d{1,2}[\/.-]\d{1,2}[\/.-]\d{2,4}",
        re.I,
    )

    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(
            min_row=1,
            max_row=min(sheet.max_row, 15),
            values_only=True,
        ):
            for value in row:
                if date_heading.search(_text(value)):
                    return sheet

    # Older spreadsheets without an explicit date:
    # choose a sheet that looks like a Monday-Sunday roster.
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(
            min_row=1,
            max_row=min(sheet.max_row, 15),
            values_only=True,
        ):
            values = [_text(v).lower().strip() for v in row]

            if (
                any(v.startswith("mon") for v in values)
                and any(v.startswith("sun") for v in values)
            ):
                return sheet

    return workbook.active

def _parse_week_start(sheet):
    """
    Read the roster week from common manager spreadsheet headings.

    W/C or Week Commencing = the Monday/week-start date.
    W/E or Week Ending = the week-ending date, converted back to Monday.
    """
    for row in sheet.iter_rows(
        min_row=1,
        max_row=min(sheet.max_row, 10),
        values_only=True,
    ):
        for value in row:
            text = _text(value)

            # Week commencing: W/C 7/9/26
            match = re.search(
                r"(?:w\s*/?\s*c|week\s+commencing)\s*"
                r"(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{2,4})",
                text,
                re.I,
            )
            if match:
                day, month, year = map(int, match.groups())
                if year < 100:
                    year += 2000
                return date(year, month, day)

            # Week ending: W/E 13/9/26
            match = re.search(
                r"(?:w\s*/?\s*e|week\s+ending)\s*"
                r"(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{2,4})",
                text,
                re.I,
            )
            if match:
                day, month, year = map(int, match.groups())
                if year < 100:
                    year += 2000

                week_end = date(year, month, day)
                return week_end - timedelta(days=week_end.weekday())

    return None

def _parse_clock(raw, suffix=""):
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
    elif suffix in {"mn", "midnight"}:
        hour, minute = 0, 0
    if hour > 23 or minute > 59:
        raise ValueError("Invalid time")
    return datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M").time()

def _normalise_chunk(value):
    text = value.lower().strip()

    # Manager shorthand:
    #   10.4.30 -> 10-4.30
    # meaning 10:00 to 16:30 after normal AM/PM inference.
    #
    # Keep four-part forms such as 8.30.4.30 untouched; the
    # existing rule below already handles those correctly.
    if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{2}", text):
        first, second, minutes = text.split(".")
        text = f"{first}-{second}.{minutes}"
    text = text.replace("—", "-").replace("–", "-").replace(" to ", "-")
    text = text.replace("midnight", "mn")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)

    # Common spreadsheet mistakes.
    text = re.sub(r"^(\d{1,2}[.:]\d{1,2})\.-", r"\1-", text)
    text = re.sub(
        r"^(\d{1,2}(?:[.:]\d{1,2})?)\-(\d{1,2})\-(\d{2})(am|pm|mn)?$",
        r"\1-\2.\3\4",
        text,
    )
    text = re.sub(
        r"^(\d{1,2}[.:]\d{1,2})[.:](\d{1,2}[.:]\d{1,2})(am|pm|mn)?$",
        r"\1-\2\3",
        text,
    )
    text = re.sub(
        r"^(\d{1,2})[.:](\d{1,2})(am|pm|mn)$",
        r"\1-\2\3",
        text,
    )
    text = re.sub(r"(\d)\.(am|pm|mn)$", r"\1\2", text)
    return text

def split_area_marker(value):
    """
    Split a trailing K, W or HK area token from a shift cell.

    Returns (time_text, area) where area is "k", "w", "hk", or None.
    The token may be upper or lower case, with or without a space.
    """
    text = _text(value).lower().strip()
    match = _AREA_MARKER.match(text)
    if not match:
        return text, None
    return match.group("body").strip(), match.group("area").lower()


def assignment_for_area(area, section_department):
    """
    K and W select kitchen or wash up.
    HK keeps the spreadsheet section and is recorded as housekeeping.
    """
    if area == "k":
        return Department.KITCHEN, ""
    if area == "w":
        return Department.WASHUP, ""
    if area == "hk":
        return section_department, HOUSEKEEPING_NOTE
    return section_department, ""


def is_annual_leave(value):
    """True only when the whole cell is AL, in any case."""
    return _text(value).lower() == "al"


def parse_shift_cell(value, department=None):
    original = _text(value)
    text = original.lower().strip()

    if text in OFF_WORDS:
        return [], None

    text, _area = split_area_marker(text)

    text = text.replace("&", ";")
    chunks = [
        part.strip()
        for part in re.split(r";|,\s+", text)
        if part.strip()
    ]
    parsed = []

    for raw_chunk in chunks:
        chunk = _normalise_chunk(raw_chunk)

        # Common manager typing variations.
        chunk = chunk.replace("--", "-")
        chunk = re.sub(r"(\d)\.am", r"\1am", chunk)
        chunk = re.sub(r"(\d)\.pm", r"\1pm", chunk)

        # 1030pm -> 10.30pm, 0830 -> 08.30
        chunk = re.sub(
            r"(?<!\d)(\d{1,2})(\d{2})(am|pm|mn)(?!\d)",
            r"\1.\2\3",
            chunk,
        )
        chunk = re.sub(
            r"(?<!\d)(\d{1,2})(\d{2})(?=-|$)",
            r"\1.\2",
            chunk,
        )

        close_match = re.match(
            r"^(\d{1,2}(?:[.:]\d{1,2})?)(am|pm)?-close$",
            chunk,
        )

        if close_match:
            start_time = _parse_clock(
                close_match.group(1),
                close_match.group(2) or "",
            )

            if (
                not close_match.group(2)
                and department == Department.BAR
                and start_time.hour < 12
            ):
                start_time = start_time.replace(
                    hour=start_time.hour + 12
                )

            parsed.append(
                (start_time, _parse_clock("1", "am"))
            )
            continue

        match = re.match(
            r"^(\d{1,2}(?:[.:]\d{1,2})?)(am|pm)?-"
            r"(\d{1,2}(?:[.:]\d{1,2})?)(am|pm|mn)?$",
            chunk,
        )

        if not match:
            return [], f"Could not understand '{original}'"

        start_raw, start_suffix, end_raw, end_suffix = match.groups()

        try:
            start_time = _parse_clock(
                start_raw,
                start_suffix or "",
            )
            end_time = _parse_clock(
                end_raw,
                end_suffix or "",
            )
        except ValueError:
            return [], f"Could not understand '{original}'"

        # ----------------------------------------------------
        # BAR
        # Manager writes evening bar shifts as:
        #   8-1pm      -> 20:00-01:00
        #   8-11.30pm  -> 20:00-23:30
        #   9-12.30    -> 21:00-00:30
        # ----------------------------------------------------
        if department == Department.BAR:

            if not start_suffix and start_time.hour < 12:
                start_time = start_time.replace(
                    hour=start_time.hour + 12
                )

            end_hour_raw = int(
                re.split(r"[.:]", end_raw)[0]
            )

            # In an evening BAR shift, 12 / 12.30 at the end
            # means midnight even if the manager accidentally writes "pm".
            # Examples:
            #   9-12.30   -> 21:00-00:30
            #   9-12.30pm -> 21:00-00:30
            #   8pm-12pm  -> 20:00-00:00
            if (
                end_hour_raw == 12
                and start_time.hour >= 18
                and end_suffix != "am"
            ):
                end_time = end_time.replace(hour=0)

            # 1 / 1.30 at end of evening bar shift means after midnight.
            elif end_hour_raw <= 3 and start_time.hour >= 18:
                end_time = end_time.replace(hour=end_hour_raw)

            elif not end_suffix and end_time.hour < 12:
                end_time = end_time.replace(
                    hour=end_time.hour + 12
                )

            # Manager sometimes writes 8-1pm even though 1 is
            # actually 1am after the evening shift.
            if (
                end_suffix == "pm"
                and end_hour_raw <= 3
                and start_time.hour >= 18
            ):
                end_time = end_time.replace(hour=end_hour_raw)

        # ----------------------------------------------------
        # RESTAURANT / KITCHEN / WASHUP
        # Missing suffix normally means daytime start.
        # If finish appears earlier than start, it is normally PM:
        #   10-9.30 -> 10:00-21:30
        #   8-6.30  -> 08:00-18:30
        # ----------------------------------------------------
        else:
            if not start_suffix and start_time.hour <= 7:
                start_time = start_time.replace(
                    hour=start_time.hour + 12
                )

            if not end_suffix:
                # In restaurant/kitchen/washup rosters, an unsuffixed
                # finish earlier than midday is normally an evening
                # finish when the shift starts in the morning.
                # Examples: 10-9.30 -> 21:30, 8am-9.30 -> 21:30.
                if (
                    end_time.hour < 12
                    and (
                        end_time <= start_time
                        or start_suffix == "am"
                    )
                ):
                    end_time = end_time.replace(
                        hour=end_time.hour + 12
                    )

        parsed.append((start_time, end_time))

    return parsed, None


def _alias_key(name):
    return _normalise_name(name).lower()


def _split_name(name):
    pieces = _normalise_name(name).split(maxsplit=1)
    first = pieces[0] if pieces else ""
    last = pieces[1] if len(pieces) > 1 else ""
    return first, last


def _exact_employees(first, last):
    return list(
        Employee.objects.annotate(
            first_lower=Lower("first_name"),
            last_lower=Lower("last_name"),
        ).filter(
            first_lower=first.lower(),
            last_lower=last.lower(),
        )
    )


def _candidate_row(name, employee, score, same_first):
    return {
        "id": employee.pk,
        "name": employee.full_name,
        "external_id": employee.external_id,
        "score": int(round(score * 100)),
        "same_first": same_first,
    }


def _rows_for(name, employees):
    rows = []
    for employee in employees:
        score, same_first = _name_score(name, employee)
        rows.append(_candidate_row(name, employee, score or 0, same_first))
    rows.sort(key=lambda row: (-row["score"], row["name"], row["id"]))
    return rows


def _name_score(name, employee):
    key = _alias_key(name)
    full = _alias_key(employee.full_name)
    ratio = SequenceMatcher(None, key, full).ratio() if key and full else 0
    sheet_first, _sheet_last = _split_name(name)
    employee_first = employee.first_name.lower()
    same_first = bool(sheet_first) and sheet_first.lower() == employee_first
    first_ratio = (
        SequenceMatcher(None, sheet_first.lower(), employee_first).ratio()
        if sheet_first else 0
    )
    if same_first:
        return max(ratio, first_ratio, 1.0), True
    if ratio >= 0.78 or first_ratio >= 0.78:
        return max(ratio, first_ratio), False
    return None, False


def _fuzzy_candidates(name):
    scored = []
    for employee in Employee.objects.all():
        score, same_first = _name_score(name, employee)
        if score is None:
            continue
        scored.append(_candidate_row(name, employee, score, same_first))
    scored.sort(key=lambda row: (-row["score"], row["name"], row["id"]))
    return scored[:12]


def _matched(employee):
    return {
        "status": "matched",
        "employee": employee,
        "employee_id": employee.pk,
        "reserved_external_id": "",
        "candidates": [],
        "ambiguous": False,
    }


def _unresolved(candidates, ambiguous=False, reserved=""):
    return {
        "status": "unresolved",
        "employee": None,
        "employee_id": None,
        "reserved_external_id": reserved,
        "candidates": candidates,
        "ambiguous": ambiguous,
    }


def classify_roster_name(name):
    """Match a spreadsheet name without creating or changing an employee."""
    key = _alias_key(name)
    first, last = _split_name(name)
    reserved = HARDCODED_ALIASES.get(key, "")

    if reserved:
        employee = Employee.objects.filter(external_id=reserved).first()
        if employee:
            return _matched(employee)
        return _unresolved(_fuzzy_candidates(name), reserved=reserved)

    alias = (
        EmployeeNameAlias.objects
        .select_related("employee")
        .filter(normalised_name=key)
        .first()
    )
    if alias:
        return _matched(alias.employee)

    exact = _exact_employees(first, last)
    if len(exact) == 1:
        return _matched(exact[0])
    if len(exact) > 1:
        return _unresolved(_rows_for(name, exact), ambiguous=True)

    if not last:
        same_first = list(Employee.objects.filter(first_name__iexact=first))
        if len(same_first) > 1:
            return _unresolved(_rows_for(name, same_first), ambiguous=True)

    return _unresolved(_fuzzy_candidates(name))


def _public_person(name, classified):
    return {
        "key": _alias_key(name),
        "name": _normalise_name(name),
        "status": classified["status"],
        "employee_id": classified["employee_id"],
        "reserved_external_id": classified["reserved_external_id"],
        "ambiguous": classified["ambiguous"],
        "candidates": classified["candidates"],
    }


def _employee_for_name(name, department):
    """Payroll matching. Roster imports use the resolution flow instead."""
    classified = classify_roster_name(name)
    if classified["status"] == "matched":
        _ensure_area_capability(classified["employee"], department)
        return classified["employee"]
    if classified["ambiguous"] or classified["reserved_external_id"]:
        raise ValueError(f"Employee name needs a decision: {name}")
    if len(classified["candidates"]) == 1:
        employee = Employee.objects.get(pk=classified["candidates"][0]["id"])
        _ensure_area_capability(employee, department)
        return employee
    if len(classified["candidates"]) > 1:
        raise ValueError(f"Employee name matches more than one person: {name}")

    first, last = _split_name(name)
    return Employee.objects.create(
        first_name=first,
        last_name=last,
        department=department,
        can_work_bar=department == Department.BAR,
        can_work_restaurant=department == Department.RESTAURANT,
        can_work_kitchen=department == Department.KITCHEN,
        can_work_washup=department == Department.WASHUP,
    )


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
        "first_name": ["first_name", "firstname", "forename", "first"],
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

    for row in rows[1:]:
        full_name = _text(row[indexes["name"]]) if indexes["name"] is not None else ""
        first = _text(row[indexes["first_name"]]) if indexes["first_name"] is not None else ""
        last = _text(row[indexes["last_name"]]) if indexes["last_name"] is not None else ""

        if full_name and not first:
            parts = full_name.split(maxsplit=1)
            first = parts[0]
            last = parts[1] if len(parts) > 1 else ""
        if not first:
            continue

        external_id = _text(row[indexes["id"]]) if indexes["id"] is not None else ""
        department_text = (
            _text(row[indexes["department"]]).lower()
            if indexes["department"] is not None
            else ""
        )
        department = Department.BAR if "bar" in department_text else Department.RESTAURANT
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
            Employee.objects.update_or_create(
                first_name=first, last_name=last, defaults=defaults
            )
        count += 1

    return count, []

def _clock(value):
    return datetime.strptime(value, "%H:%M").time()


def _optional_employee_id(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RosterImportError("Choose an employee from the list.") from exc


def _save_alias(name, employee):
    key = _alias_key(name)
    reserved = HARDCODED_ALIASES.get(key)
    if reserved and (employee.external_id or "") != reserved:
        raise RosterImportError(
            f"{name} is already reserved for clocking ID {reserved}."
        )
    current = (
        EmployeeNameAlias.objects
        .select_related("employee")
        .filter(normalised_name=key)
        .first()
    )
    if current:
        if current.employee_id != employee.pk:
            raise RosterImportError(
                f"{name} is already an alias for {current.employee.full_name}."
            )
        return current
    return EmployeeNameAlias.objects.create(
        normalised_name=key,
        employee=employee,
    )


def _new_employee(name, department, exclude):
    first, last = _split_name(name)
    if _exact_employees(first, last):
        raise RosterImportError(
            f"{_normalise_name(name)} already matches an employee. "
            "Choose that employee instead of creating another."
        )
    return Employee.objects.create(
        first_name=first,
        last_name=last,
        department=department or Department.RESTAURANT,
        can_work_bar=department == Department.BAR,
        can_work_restaurant=department == Department.RESTAURANT,
        can_work_kitchen=department == Department.KITCHEN,
        can_work_washup=department == Department.WASHUP,
        external_id="",
        exclude_from_clocking=exclude,
    )


def _section_for(plan, key):
    for entry in plan["entries"]:
        if entry["person"] == key:
            return entry.get("section") or Department.RESTAURANT
    for person in plan["people"]:
        if person["key"] == key and person.get("sections"):
            return person["sections"][0]
    return Department.RESTAURANT


def prepare_roster_import(file_obj, week_start=None, purpose=RosterPurpose.HISTORIC):
    """Read a workbook without creating employees or changing a week."""
    workbook = load_workbook(file_obj, data_only=True)
    sheet = _find_sheet(workbook, week_start=week_start)
    week_start = week_start or _parse_week_start(sheet)
    if week_start is None:
        raise ValueError("No readable week date.")

    existing = RosterWeek.objects.filter(week_start=week_start).only("status").first()
    _require_draft_week(existing)

    rows = list(sheet.iter_rows(values_only=True))
    header_index = None
    name_column = None
    day_columns = {}

    for row_index, row in enumerate(rows):
        values = [_text(value).lower().strip() for value in row]
        monday = next((i for i, value in enumerate(values) if value.startswith("mon")), None)
        sunday = next((i for i, value in enumerate(values) if value.startswith("sun")), None)
        if monday is not None and sunday is not None:
            header_index = row_index
            name_column = monday - 1
            for prefix, day_number in [
                ("mon", 0), ("tue", 1), ("wed", 2), ("thu", 3),
                ("fri", 4), ("sat", 5), ("sun", 6),
            ]:
                day_columns[day_number] = next(
                    i for i, value in enumerate(values) if value.startswith(prefix)
                )
            break

    if header_index is None:
        raise ValueError("Could not find the employee and weekday columns.")

    department = Department.RESTAURANT
    people = {}
    entries = []
    issues = []

    for row_number, row in enumerate(rows[header_index + 1:], start=header_index + 2):

        # Department headings in manager workbooks are not always in
        # the employee-name column. Detect them anywhere in the row.
        row_values = [_text(value).lower().strip() for value in row]

        heading = None
        for value in row_values:
            cleaned = value.replace("-", " ").replace("_", " ")
            cleaned = " ".join(cleaned.split())

            if cleaned == "restaurant":
                heading = Department.RESTAURANT
                break
            elif cleaned == "bar":
                heading = Department.BAR
                break
            elif cleaned == "kitchen":
                heading = Department.KITCHEN
                break
            elif cleaned in {"wash up", "washup"}:
                heading = Department.WASHUP
                break

        if heading is not None:
            department = heading
            continue

        name = _normalise_name(
            row[name_column] if name_column < len(row) else ""
        )

        if not name:
            continue

        key = _alias_key(name)
        if key not in people:
            people[key] = _public_person(name, classify_roster_name(name))
            people[key]["sections"] = []
        if department not in people[key]["sections"]:
            people[key]["sections"].append(department)

        for day_number, column in day_columns.items():
            raw_value = row[column] if column < len(row) else None
            shift_date = (week_start + timedelta(days=day_number)).isoformat()

            if is_annual_leave(raw_value):
                entries.append({
                    "kind": "leave",
                    "person": key,
                    "date": shift_date,
                    "section": department,
                    "row": row_number,
                    "value": _text(raw_value),
                })
                continue

            parsed_shifts, error = parse_shift_cell(raw_value, department)
            if error:
                issues.append({
                    "person": key,
                    "row": row_number,
                    "employee": _normalise_name(name),
                    "date": shift_date,
                    "value": _text(raw_value),
                    "message": error,
                    "skipped": True,
                    "choices": ["Treat as OFF", "Fix manually"],
                })
                continue

            _time_text, area = split_area_marker(raw_value)
            shift_department, shift_notes = assignment_for_area(area, department)
            for start, end in parsed_shifts:
                entries.append({
                    "kind": "shift",
                    "person": key,
                    "date": shift_date,
                    "start": start.strftime("%H:%M"),
                    "end": end.strftime("%H:%M"),
                    "department": shift_department,
                    "section": department,
                    "notes": shift_notes,
                    "row": row_number,
                    "value": _text(raw_value),
                })
                start_minutes = start.hour * 60 + start.minute
                end_minutes = end.hour * 60 + end.minute
                if end_minutes <= start_minutes:
                    duration_minutes = (24 * 60 - start_minutes) + end_minutes
                else:
                    duration_minutes = end_minutes - start_minutes
                if duration_minutes > 12 * 60:
                    hours = duration_minutes / 60
                    issues.append({
                        "person": key,
                        "row": row_number,
                        "employee": _normalise_name(name),
                        "date": shift_date,
                        "value": _text(raw_value),
                        "message": (
                            f"Unusually long shift: "
                            f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')} "
                            f"({hours:g} hours). Please check it."
                        ),
                        "choices": ["Fix manually", "Keep as entered"],
                        "skipped": False,
                    })

    return {
        "week_start": week_start.isoformat(),
        "purpose": str(purpose),
        "people": list(people.values()),
        "entries": entries,
        "issues": issues,
    }


def _resolved_employees(plan, resolutions):
    choices = {}
    missing = []
    for person in plan["people"]:
        if person["status"] != "unresolved":
            continue
        choice = resolutions.get(person["key"]) or {}
        action = str(choice.get("action") or "").strip()
        if action not in {"use_existing", "create", "roster_only"}:
            missing.append(person["name"])
            continue
        employee_id = _optional_employee_id(choice.get("employee_id"))
        if action == "use_existing" and employee_id is None:
            missing.append(person["name"])
        choices[person["key"]] = {
            "action": action,
            "employee_id": employee_id,
            "person": person,
        }
    if missing:
        raise RosterImportError(
            "Choose an option for every name: " + ", ".join(missing) + "."
        )

    bound = {}
    for person in plan["people"]:
        if person["status"] == "matched":
            employee = Employee.objects.filter(pk=person["employee_id"]).first()
            if employee is None:
                raise RosterImportError(
                    f"{person['name']} no longer matches an employee. "
                    "Upload the roster again."
                )
            bound[person["key"]] = employee
            continue

        choice = choices[person["key"]]
        action = choice["action"]
        reserved = person.get("reserved_external_id") or ""
        if reserved and action != "use_existing":
            raise RosterImportError(
                f"{person['name']} is reserved for clocking ID {reserved}, "
                "which is not on the roster."
            )

        if action == "use_existing":
            employee = Employee.objects.filter(pk=choice["employee_id"]).first()
            if employee is None:
                raise RosterImportError(
                    f"Choose an employee for {person['name']}."
                )
            if reserved and (employee.external_id or "") != reserved:
                raise RosterImportError(
                    f"{person['name']} is reserved for clocking ID {reserved}."
                )
            _save_alias(person["name"], employee)
        elif action == "create":
            employee = _new_employee(
                person["name"],
                _section_for(plan, person["key"]),
                exclude=False,
            )
            _save_alias(person["name"], employee)
        else:
            employee_id = choice["employee_id"]
            if employee_id:
                employee = Employee.objects.filter(pk=employee_id).first()
                if employee is None:
                    raise RosterImportError(
                        f"Choose an employee for {person['name']}."
                    )
            else:
                first, last = _split_name(person["name"])
                exact = _exact_employees(first, last)
                if len(exact) > 1:
                    raise RosterImportError(
                        f"Choose which {person['name']} to keep on the roster only."
                    )
                employee = exact[0] if exact else _new_employee(
                    person["name"],
                    _section_for(plan, person["key"]),
                    exclude=True,
                )
            if not employee.exclude_from_clocking:
                employee.exclude_from_clocking = True
                employee.save(update_fields=["exclude_from_clocking", "updated_at"])
            _save_alias(person["name"], employee)
        bound[person["key"]] = employee

    for person in plan["people"]:
        employee = bound[person["key"]]
        for section in person.get("sections") or []:
            _ensure_area_capability(employee, section)
    return bound


@transaction.atomic
def apply_roster_plan(plan, resolutions):
    """Apply one fully resolved plan. A failure restores the previous rows."""
    bound = _resolved_employees(plan, resolutions or {})
    week_start = date.fromisoformat(plan["week_start"])
    purpose = plan["purpose"]
    roster = (
        RosterWeek.objects.select_for_update()
        .filter(week_start=week_start)
        .first()
    )
    _require_draft_week(roster)
    if roster is None:
        roster = RosterWeek.objects.create(
            week_start=week_start,
            purpose=purpose,
        )
    else:
        roster.purpose = purpose
        roster.save(update_fields=["purpose", "updated_at"])

    roster.shifts.all().delete()
    roster.unresolved_shifts.all().delete()
    roster.annual_leave.all().delete()

    count = 0
    issues = []
    for issue in plan.get("issues") or []:
        employee = bound.get(issue.get("person"))
        row = {key: value for key, value in issue.items() if key != "person"}
        if employee is not None:
            row["employee"] = employee.full_name
        issues.append(row)

    for entry in plan["entries"]:
        employee = bound[entry["person"]]
        shift_date = date.fromisoformat(entry["date"])

        if entry["kind"] == "leave":
            AnnualLeave.objects.get_or_create(
                roster_week=roster,
                employee=employee,
                date=shift_date,
            )
            _ensure_area_capability(employee, entry.get("section"))
            continue

        _ensure_area_capability(employee, entry.get("department"))
        last_segment = (
            Shift.objects.filter(
                roster_week=roster,
                employee=employee,
                date=shift_date,
            )
            .order_by("-segment")
            .values_list("segment", flat=True)
            .first()
            or 0
        )
        Shift.objects.create(
            roster_week=roster,
            employee=employee,
            department=entry["department"],
            date=shift_date,
            segment=last_segment + 1,
            start_time=_clock(entry["start"]),
            end_time=_clock(entry["end"]),
            source="imported",
            confidence=95,
            notes=entry.get("notes") or "",
        )
        count += 1

    return roster, count, issues


def _require_draft_week(roster):
    """Excel may create a week or replace a draft. A sent week stays as it is."""
    if roster is None:
        return
    if roster.status == RosterStatus.PUBLISHED:
        raise RosterImportError(
            "Published rosters cannot be replaced from Excel."
        )
    if roster.status != RosterStatus.DRAFT:
        raise RosterImportError(
            "Only a draft roster can be replaced from Excel."
        )


def import_roster(file_obj, week_start=None, purpose=RosterPurpose.HISTORIC, resolutions=None):
    plan = prepare_roster_import(
        file_obj,
        week_start=week_start,
        purpose=purpose,
    )
    unresolved = [
        person for person in plan["people"] if person["status"] == "unresolved"
    ]
    if unresolved and not resolutions:
        raise UnresolvedRosterNames(plan)
    return apply_roster_plan(plan, resolutions or {})


def _ensure_area_capability(employee, department):
    field = {
        Department.RESTAURANT: "can_work_restaurant",
        Department.BAR: "can_work_bar",
        Department.KITCHEN: "can_work_kitchen",
        Department.WASHUP: "can_work_washup",
    }.get(department)
    if field and not getattr(employee, field):
        setattr(employee, field, True)
        employee.save(update_fields=[field, "updated_at"])
