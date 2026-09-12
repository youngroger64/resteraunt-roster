from datetime import date, datetime, timedelta
import re
from openpyxl import load_workbook
from django.db import transaction
from django.db.models.functions import Lower
from apps.employees.models import Department, Employee
from apps.roster.models import RosterPurpose, RosterWeek, Shift

OFF_WORDS = {"", "off", "0ff", "-", "none", "holiday", "hol", "leave"}

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

def parse_shift_cell(value, department=None):
    original = _text(value)
    text = original.lower().strip()

    if text in OFF_WORDS:
        return [], None

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


def _employee_for_name(name, department):
    normalised = _normalise_name(name)

    # Manager spreadsheet aliases / spelling variations.
    # Prefer the official clocking-system employee record.
    aliases = {
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
    }

    external_id = aliases.get(normalised.lower())

    if external_id:
        employee = Employee.objects.filter(
            external_id=external_id
        ).first()

        if employee:
            changed = []

            if (
                department == Department.RESTAURANT
                and not employee.can_work_restaurant
            ):
                employee.can_work_restaurant = True
                changed.append("can_work_restaurant")

            if (
                department == Department.BAR
                and not employee.can_work_bar
            ):
                employee.can_work_bar = True
                changed.append("can_work_bar")

            if (
                department == Department.KITCHEN
                and not employee.can_work_kitchen
            ):
                employee.can_work_kitchen = True
                changed.append("can_work_kitchen")

            if (
                department == Department.WASHUP
                and not employee.can_work_washup
            ):
                employee.can_work_washup = True
                changed.append("can_work_washup")

            if changed:
                changed.append("updated_at")
                employee.save(update_fields=changed)

            return employee

    pieces = normalised.split(maxsplit=1)
    first = pieces[0]
    last = pieces[1] if len(pieces) > 1 else ""

    employee = Employee.objects.annotate(
        first_lower=Lower("first_name"),
        last_lower=Lower("last_name"),
    ).filter(
        first_lower=first.lower(),
        last_lower=last.lower(),
    ).first()

    if not employee and not last:
        employee = Employee.objects.filter(
            first_name__iexact=first
        ).first()

    if not employee:
        employee = Employee.objects.create(
            first_name=first,
            last_name=last,
            department=department,
            can_work_bar=department == Department.BAR,
            can_work_restaurant=department == Department.RESTAURANT,
            can_work_kitchen=department == Department.KITCHEN,
            can_work_washup=department == Department.WASHUP,
        )

    else:
        changed = []

        capability_fields = {
            Department.RESTAURANT: "can_work_restaurant",
            Department.BAR: "can_work_bar",
            Department.KITCHEN: "can_work_kitchen",
            Department.WASHUP: "can_work_washup",
        }

        field = capability_fields.get(department)

        if field and not getattr(employee, field):
            setattr(employee, field, True)
            changed.append(field)

        if changed:
            changed.append("updated_at")
            employee.save(update_fields=changed)

    return employee


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

@transaction.atomic
def import_roster(file_obj, week_start=None, purpose=RosterPurpose.HISTORIC):
    workbook = load_workbook(file_obj, data_only=True)
    sheet = _find_sheet(workbook, week_start=week_start)
    week_start = week_start or _parse_week_start(sheet)
    if week_start is None:
        raise ValueError("No readable week date.")

    roster, _ = RosterWeek.objects.get_or_create(
        week_start=week_start,
        defaults={"purpose": purpose},
    )
    roster.purpose = purpose
    roster.save(update_fields=["purpose", "updated_at"])
    roster.shifts.all().delete()
    roster.unresolved_shifts.all().delete()

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
        return roster, 0, [{
            "message": "Could not find the employee and weekday columns.",
            "choices": ["Choose another file", "Enter shifts manually"],
        }]

    department = Department.RESTAURANT
    count = 0
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

        employee = _employee_for_name(name, department)

        for day_number, column in day_columns.items():
            raw_value = row[column] if column < len(row) else None
            parsed_shifts, error = parse_shift_cell(raw_value, department)

            if error:
                issues.append({
                    "row": row_number,
                    "employee": employee.full_name,
                    "date": (week_start + timedelta(days=day_number)).isoformat(),
                    "value": _text(raw_value),
                    "message": error,
                    "choices": ["Treat as OFF", "Fix manually"],
                })
                continue

            shift_date = week_start + timedelta(days=day_number)

            # Segment numbers belong to the employee/day, not to each
            # spreadsheet row or department.
            last_segment = (
                Shift.objects
                .filter(
                    roster_week=roster,
                    employee=employee,
                    date=shift_date,
                )
                .order_by("-segment")
                .values_list("segment", flat=True)
                .first()
                or 0
            )

            for offset, (start, end) in enumerate(parsed_shifts, start=1):
                Shift.objects.create(
                    roster_week=roster,
                    employee=employee,
                    department=department,
                    date=shift_date,
                    segment=last_segment + offset,
                    start_time=start,
                    end_time=end,
                    source="imported",
                    confidence=95,
                )
                count += 1

                # Safety check: do not silently accept an unusually long shift.
                start_minutes = start.hour * 60 + start.minute
                end_minutes = end.hour * 60 + end.minute

                if end_minutes <= start_minutes:
                    duration_minutes = (24 * 60 - start_minutes) + end_minutes
                else:
                    duration_minutes = end_minutes - start_minutes

                if duration_minutes > 12 * 60:
                    hours = duration_minutes / 60

                    issues.append({
                        "row": row_number,
                        "employee": employee.full_name,
                        "date": shift_date.isoformat(),
                        "value": _text(raw_value),
                        "message": (
                            f"Unusually long shift: "
                            f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')} "
                            f"({hours:g} hours). Please check it."
                        ),
                        "choices": ["Fix manually", "Keep as entered"],
                    })

    return roster, count, issues
