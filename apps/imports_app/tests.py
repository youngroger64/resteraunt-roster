from datetime import date, time, timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import Workbook

from apps.employees.models import Department, Employee
from apps.roster.models import AnnualLeave, RosterStatus, RosterWeek, Shift

from .services import (
    HOUSEKEEPING_NOTE,
    assignment_for_area,
    import_roster,
    is_annual_leave,
    parse_shift_cell,
    split_area_marker,
)

class ShiftParserTests(SimpleTestCase):
    def assert_shift(self, text, start_hour, end_hour):
        parsed, error = parse_shift_cell(text)
        self.assertIsNone(error)
        self.assertEqual(parsed[0][0].hour, start_hour)
        self.assertEqual(parsed[0][1].hour, end_hour)

    def test_standard(self):
        self.assert_shift("8.30-5pm", 8, 17)

    def test_split(self):
        parsed, error = parse_shift_cell("12.30-3.30 & 6-10.30")
        self.assertIsNone(error)
        self.assertEqual(len(parsed), 2)

    def test_dot_separator_typo(self):
        self.assert_shift("8.30.4.30", 8, 16)

    def test_hyphen_minutes_typo(self):
        self.assert_shift("6-10-30", 18, 22)

    def test_close(self):
        self.assert_shift("8pm-close", 20, 1)

    def test_midnight(self):
        self.assert_shift("4-12mn", 16, 0)

    def test_single_time_needs_choice(self):
        parsed, error = parse_shift_cell("12.30")
        self.assertEqual(parsed, [])
        self.assertIsNotNone(error)


class ManagerShorthandRegressionTests(SimpleTestCase):
    def test_three_part_manager_shorthand(self):
        parsed, error = parse_shift_cell("10.4.30")

        self.assertIsNone(error)
        self.assertEqual(len(parsed), 1)

        start, end = parsed[0]

        self.assertEqual((start.hour, start.minute), (10, 0))
        self.assertEqual((end.hour, end.minute), (16, 30))


class AreaSuffixParserTests(SimpleTestCase):
    def assert_times(self, text, start, end):
        parsed, error = parse_shift_cell(text)
        self.assertIsNone(error, error)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(
            (parsed[0][0].hour, parsed[0][0].minute),
            start,
        )
        self.assertEqual(
            (parsed[0][1].hour, parsed[0][1].minute),
            end,
        )

    def test_housekeeping_suffix_with_space_and_case(self):
        for text in ("10.00-14.00 hk", "10.00-14.00 HK", "10.00-14.00hk"):
            self.assert_times(text, (10, 0), (14, 0))
            _body, area = split_area_marker(text)
            self.assertEqual(area, "hk")

    def test_washup_and_kitchen_suffixes(self):
        cases = [
            ("17.00-22.30 w", "w", (17, 0), (22, 30)),
            ("17.00-22.30W", "w", (17, 0), (22, 30)),
            ("16.00-22.00 K", "k", (16, 0), (22, 0)),
            ("10.00-16.00 k", "k", (10, 0), (16, 0)),
            ("11.00-19.30k", "k", (11, 0), (19, 30)),
            ("11.00-19.30K", "k", (11, 0), (19, 30)),
        ]
        for text, area, start, end in cases:
            self.assert_times(text, start, end)
            self.assertEqual(split_area_marker(text)[1], area)

    def test_area_words_are_not_fuzzy_matched(self):
        parsed, error = parse_shift_cell("10.00-14.00 kitchen")
        self.assertEqual(parsed, [])
        self.assertIsNotNone(error)
        self.assertIsNone(split_area_marker("10.00-14.00 kitchen")[1])

    def test_ordinary_entries_keep_existing_meaning(self):
        self.assert_times("8.30-5pm", (8, 30), (17, 0))
        parsed, error = parse_shift_cell("12.30-3.30 & 6-10.30")
        self.assertIsNone(error)
        self.assertEqual(len(parsed), 2)
        parsed, error = parse_shift_cell("4-12mn")
        self.assertIsNone(error)
        self.assertEqual(parsed[0][0].hour, 16)
        self.assertEqual(parsed[0][1].hour, 0)
        parsed, error = parse_shift_cell("off")
        self.assertEqual(parsed, [])
        self.assertIsNone(error)
        parsed, error = parse_shift_cell("8pm-close")
        self.assertIsNone(error)
        self.assertEqual(parsed[0][0].hour, 20)
        self.assertEqual(parsed[0][1].hour, 1)

    def test_assignment_uses_existing_departments(self):
        self.assertEqual(
            assignment_for_area("k", Department.WASHUP),
            (Department.KITCHEN, ""),
        )
        self.assertEqual(
            assignment_for_area("w", Department.RESTAURANT),
            (Department.WASHUP, ""),
        )
        self.assertEqual(
            assignment_for_area("hk", Department.WASHUP),
            (Department.WASHUP, HOUSEKEEPING_NOTE),
        )
        self.assertEqual(
            assignment_for_area(None, Department.BAR),
            (Department.BAR, ""),
        )


def _workbook(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet["B2"] = "W/C 12/10/26"
    sheet["C2"] = "Monday"
    sheet["D2"] = "Tue"
    sheet["E2"] = "Wed"
    sheet["F2"] = "Thurs"
    sheet["G2"] = "Friday"
    sheet["H2"] = "Sat"
    sheet["I2"] = "Sun"
    for offset, row in enumerate(rows, start=3):
        for column, value in enumerate(row, start=2):
            sheet.cell(offset, column, value)
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    stream.name = "roster.xlsx"
    return stream


class RosterAreaImportTests(TestCase):
    def test_nataliia_receives_five_area_shifts(self):
        Employee.objects.create(
            external_id="234",
            first_name="Nataliia",
            last_name="Moldovan",
            department=Department.WASHUP,
        )
        roster, count, issues = import_roster(_workbook([
            ["Wash up"],
            [
                "Nataliia Moldovan",
                "10.00-14.00 hk",
                "off",
                "off",
                "17.00-22.30 w",
                "16.00-22.00 K",
                "10.00-16.00 k",
                "11.00-19.30k",
            ],
        ]))

        self.assertEqual(count, 5)
        self.assertEqual(issues, [])
        self.assertEqual(
            Employee.objects.filter(last_name="Moldovan").count(),
            1,
        )
        employee = Employee.objects.get(external_id="234")
        self.assertTrue(employee.can_work_kitchen)
        self.assertTrue(employee.can_work_washup)
        shifts = list(
            Shift.objects.filter(employee=employee).order_by("date")
        )
        self.assertEqual(
            [
                (
                    shift.date.isoformat(),
                    shift.start_time.strftime("%H:%M"),
                    shift.end_time.strftime("%H:%M"),
                    shift.department,
                    shift.notes,
                )
                for shift in shifts
            ],
            [
                ("2026-10-12", "10:00", "14:00", Department.WASHUP, HOUSEKEEPING_NOTE),
                ("2026-10-15", "17:00", "22:30", Department.WASHUP, ""),
                ("2026-10-16", "16:00", "22:00", Department.KITCHEN, ""),
                ("2026-10-17", "10:00", "16:00", Department.KITCHEN, ""),
                ("2026-10-18", "11:00", "19:30", Department.KITCHEN, ""),
            ],
        )
        self.assertEqual(roster.week_start, date(2026, 10, 12))

    def test_reimport_replaces_the_same_week_without_duplicates(self):
        Employee.objects.create(
            external_id="234",
            first_name="Nataliia",
            last_name="Moldovan",
        )
        workbook_rows = [
            ["Wash up"],
            [
                "Nataliia Moldovan",
                "10.00-14.00 hk",
                "off",
                "off",
                "17.00-22.30 w",
                "16.00-22.00 K",
                "10.00-16.00 k",
                "11.00-19.30k",
            ],
        ]
        import_roster(_workbook(workbook_rows))
        import_roster(_workbook(workbook_rows))
        self.assertEqual(Shift.objects.count(), 5)
        self.assertEqual(RosterWeek.objects.count(), 1)

    def test_employee_in_two_sections_keeps_both_valid_shifts(self):
        employee = Employee.objects.create(
            external_id="229",
            first_name="Clea",
            last_name="Whelan",
            department=Department.RESTAURANT,
        )
        _roster, count, issues = import_roster(_workbook([
            ["Restaurant"],
            ["Clea Whelan", "odd", "off", "off", "off", "off", "off", "off"],
            ["Wash up"],
            ["Clea Whelan", "off", "off", "off", "off", "18.00-23.30", "16.00-23.30", "off"],
        ]))

        self.assertEqual(count, 2)
        self.assertEqual(Employee.objects.filter(last_name="Whelan").count(), 1)
        shifts = list(employee.roster_shifts.order_by("date"))
        self.assertEqual(
            [(shift.date.isoformat(), shift.department) for shift in shifts],
            [
                ("2026-10-16", Department.WASHUP),
                ("2026-10-17", Department.WASHUP),
            ],
        )
        self.assertEqual(shifts[0].start_time, time(18, 0))
        self.assertEqual(shifts[1].start_time, time(16, 0))
        self.assertEqual(len(issues), 1)
        self.assertTrue(issues[0]["skipped"])
        self.assertEqual(issues[0]["employee"], "Clea Whelan")
        self.assertEqual(issues[0]["value"], "odd")
        self.assertEqual(issues[0]["date"], "2026-10-12")

    def test_import_does_not_remove_another_weeks_manager_shift(self):
        employee = Employee.objects.create(
            external_id="229",
            first_name="Clea",
            last_name="Whelan",
        )
        other_week = RosterWeek.objects.create(
            week_start=date(2026, 10, 5),
            status=RosterStatus.PUBLISHED,
        )
        kept = Shift.objects.create(
            roster_week=other_week,
            employee=employee,
            department=Department.RESTAURANT,
            date=date(2026, 10, 5),
            start_time=time(9, 0),
            end_time=time(17, 0),
            source="manual",
            notes="Added by manager",
        )
        import_roster(_workbook([
            ["Wash up"],
            ["Clea Whelan", "off", "off", "off", "off", "18.00-23.30", "off", "off"],
        ]))
        kept.refresh_from_db()
        self.assertEqual(kept.notes, "Added by manager")
        self.assertEqual(kept.roster_week.status, RosterStatus.PUBLISHED)
        self.assertEqual(
            Shift.objects.filter(roster_week=other_week).count(),
            1,
        )


class RosterImportWarningTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            "manager",
            password="secret",
        )
        self.client.force_login(user)
        Employee.objects.create(
            external_id="229",
            first_name="Clea",
            last_name="Whelan",
        )

    def _upload(self, rows):
        stream = _workbook(rows)
        upload = SimpleUploadedFile(
            "roster.xlsx",
            stream.getvalue(),
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
        return self.client.post(
            reverse("imports:roster"),
            {"purpose": "historic", "file": upload},
            follow=True,
        )

    def test_skipped_cell_is_visible_and_not_reported_as_success(self):
        response = self._upload([
            ["Restaurant"],
            ["Clea Whelan", "odd", "off", "off", "off", "18.00-22.00", "off", "off"],
        ])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 cell(s) were skipped.")
        self.assertContains(response, "Clea Whelan")
        self.assertContains(response, "2026-10-12")
        self.assertContains(response, "odd")
        self.assertContains(response, "Could not understand")
        self.assertNotContains(response, 'class="alert success"')
        self.assertEqual(Shift.objects.count(), 1)

    def test_clean_import_reports_success(self):
        response = self._upload([
            ["Wash up"],
            ["Clea Whelan", "off", "off", "off", "off", "18.00-23.30", "off", "off"],
        ])
        self.assertContains(response, "Imported 1 shift segments.")
        self.assertNotContains(response, "were skipped")


class HousekeepingStaffDisplayTests(TestCase):
    def test_staff_portal_shows_housekeeping_without_changing_department(self):
        employee = Employee.objects.create(
            external_id="234",
            first_name="Nataliia",
            last_name="Moldovan",
            department=Department.WASHUP,
            can_work_washup=True,
        )
        today = date.today()
        week = RosterWeek.objects.create(
            week_start=today - timedelta(days=today.weekday()),
            status=RosterStatus.PUBLISHED,
        )
        Shift.objects.create(
            roster_week=week,
            employee=employee,
            department=Department.WASHUP,
            date=today,
            start_time=time(10, 0),
            end_time=time(14, 0),
            notes=HOUSEKEEPING_NOTE,
        )
        self.client.post(
            reverse("roster:staff"),
            {"employee_number": "234"},
        )
        response = self.client.get(reverse("roster:staff"))
        self.assertContains(response, "Wash up")
        self.assertContains(response, "Housekeeping")
        self.assertContains(response, "10:00")


class AnnualLeaveImportTests(TestCase):
    def test_al_is_leave_not_a_shift(self):
        self.assertTrue(is_annual_leave("AL"))
        self.assertTrue(is_annual_leave(" al "))
        self.assertFalse(is_annual_leave("A/L"))
        self.assertFalse(is_annual_leave("annual leave"))
        parsed, error = parse_shift_cell("off")
        self.assertEqual(parsed, [])
        self.assertIsNone(error)

    def test_import_stores_leave_without_a_shift_and_reimport_replaces_it(self):
        Employee.objects.create(
            external_id="207",
            first_name="Fiona",
            last_name="Conway",
        )
        first, count, issues = import_roster(_workbook([
            ["Restaurant"],
            ["Fiona Conway", "AL", "al", "  Al ", "8.30-16.30", "off", "off", "off"],
        ]))
        self.assertEqual(count, 1)
        self.assertEqual(issues, [])
        self.assertEqual(
            list(
                AnnualLeave.objects.order_by("date").values_list("date", flat=True)
            ),
            [date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)],
        )
        self.assertEqual(Shift.objects.count(), 1)
        self.assertEqual(AnnualLeave.objects.count(), 3)

        import_roster(_workbook([
            ["Restaurant"],
            ["Fiona Conway", "AL", "off", "off", "8.30-16.30", "off", "off", "off"],
        ]))
        self.assertEqual(AnnualLeave.objects.count(), 1)
        self.assertEqual(
            AnnualLeave.objects.get().date,
            date(2026, 10, 12),
        )
        self.assertEqual(AnnualLeave.objects.filter(roster_week=first).count(), 1)
        self.assertEqual(Shift.objects.count(), 1)
