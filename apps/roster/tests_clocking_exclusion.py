import json
from datetime import date, time
from io import BytesIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from apps.employees.models import Employee
from apps.imports_app.services import import_roster
from apps.roster.models import AnnualLeave, RosterStatus, RosterWeek, Shift
from apps.roster.services.clocking_publish import (
    ClockingPublishError,
    build_clocking_roster_payload,
    publish_employee_roster_to_clocking,
    publish_week_to_clocking,
)


WEEK = date(2026, 10, 12)


def _accepted():
    response = mock.MagicMock()
    response.read.return_value = json.dumps({
        "ok": True,
        "published": True,
        "fully_applied": True,
        "warnings": [],
    }).encode()
    response.status = 200
    context = mock.MagicMock()
    context.__enter__.return_value = response
    return context


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


@override_settings(
    CLOCKING_BASE_URL="http://clocking.test",
    ROSTER_INTEGRATION_TOKEN="test-token",
)
class ClockingExclusionTests(TestCase):
    def setUp(self):
        self.roster = RosterWeek.objects.create(week_start=WEEK)
        self.dan = Employee.objects.create(
            external_id="",
            first_name="Dan",
            last_name="Deegan",
            exclude_from_clocking=True,
        )
        self.fiona = Employee.objects.create(
            external_id="207",
            first_name="Fiona",
            last_name="Conway",
        )
        self.sophia = Employee.objects.create(
            external_id="",
            first_name="Sophia",
            last_name="",
        )

    def add_shift(self, employee, day=WEEK, start=time(20, 0), end=time(0, 30)):
        return Shift.objects.create(
            roster_week=self.roster,
            employee=employee,
            department="bar",
            date=day,
            start_time=start,
            end_time=end,
        )

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_excluded_employee_without_an_id_does_not_block_publish(self, urlopen):
        self.add_shift(self.dan)
        captured = {}

        def capture(request, timeout=15):
            captured["body"] = json.loads(request.data.decode())
            return _accepted()

        urlopen.side_effect = capture
        publish_week_to_clocking(self.roster)
        self.assertEqual(captured["body"]["shifts"], [])
        self.assertEqual(captured["body"]["annual_leave"], [])
        self.assertNotIn("Dan Deegan", json.dumps(captured["body"]))

    def test_ordinary_employee_without_an_id_still_blocks_publish(self):
        self.add_shift(self.sophia)
        self.assertFalse(self.sophia.exclude_from_clocking)
        with self.assertRaises(ClockingPublishError) as raised:
            publish_week_to_clocking(self.roster)
        self.assertIn("Sophia", str(raised.exception))
        self.assertNotIn("Dan Deegan", str(raised.exception))

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_mixed_roster_sends_only_included_employees(self, urlopen):
        self.add_shift(self.dan)
        fiona_shift = self.add_shift(
            self.fiona,
            start=time(8, 30),
            end=time(16, 30),
        )
        captured = {}

        def capture(request, timeout=15):
            captured["body"] = json.loads(request.data.decode())
            return _accepted()

        urlopen.side_effect = capture
        publish_week_to_clocking(self.roster)
        self.assertEqual(
            [row["employee_number"] for row in captured["body"]["shifts"]],
            ["207"],
        )
        self.assertEqual(
            captured["body"]["shifts"][0]["external_shift_id"],
            str(fiona_shift.pk),
        )

    def test_reimport_keeps_the_exclusion_and_does_not_exclude_blank_ids(self):
        Employee.objects.create(
            external_id="",
            first_name="Chloe",
            last_name="Martin",
        )
        import_roster(_workbook([
            ["Bar"],
            ["Dan Deegan", "20.00-00.30", "off", "off", "off", "off", "off", "off"],
            ["Sophia", "11.00-19.30", "off", "off", "off", "off", "off", "off"],
            ["Chloe Martin", "11.30-16.00", "off", "off", "off", "off", "off", "off"],
        ]))
        dan = Employee.objects.get(first_name="Dan", last_name="Deegan")
        sophia = Employee.objects.get(first_name="Sophia")
        chloe = Employee.objects.get(first_name="Chloe", last_name="Martin")
        self.assertTrue(dan.exclude_from_clocking)
        self.assertEqual(dan.external_id, "")
        self.assertFalse(sophia.exclude_from_clocking)
        self.assertFalse(chloe.exclude_from_clocking)
        self.assertEqual(Employee.objects.filter(first_name="Dan", last_name="Deegan").count(), 1)

        import_roster(_workbook([
            ["Bar"],
            ["Dan Deegan", "off", "20.00-00.30", "off", "off", "off", "off", "off"],
        ]))
        dan.refresh_from_db()
        self.assertTrue(dan.exclude_from_clocking)
        self.assertEqual(
            list(Shift.objects.filter(employee=dan).values_list("date", flat=True)),
            [date(2026, 10, 13)],
        )

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_republish_of_a_published_week_still_omits_the_excluded_employee(self, urlopen):
        self.add_shift(self.dan)
        self.add_shift(self.fiona, start=time(8, 30), end=time(16, 30))
        self.roster.status = RosterStatus.PUBLISHED
        self.roster.save(update_fields=["status"])
        captured = {}

        def capture(request, timeout=15):
            captured["body"] = json.loads(request.data.decode())
            return _accepted()

        urlopen.side_effect = capture
        publish_week_to_clocking(self.roster)
        self.assertEqual(
            [row["employee_number"] for row in captured["body"]["shifts"]],
            ["207"],
        )

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_excluded_annual_leave_is_left_out_of_the_payload(self, urlopen):
        AnnualLeave.objects.create(
            roster_week=self.roster,
            employee=self.dan,
            date=WEEK,
        )
        fiona_leave = AnnualLeave.objects.create(
            roster_week=self.roster,
            employee=self.fiona,
            date=WEEK,
        )
        captured = {}

        def capture(request, timeout=15):
            captured["body"] = json.loads(request.data.decode())
            return _accepted()

        urlopen.side_effect = capture
        publish_week_to_clocking(self.roster)
        self.assertEqual(captured["body"]["annual_leave"], [{
            "employee_number": "207",
            "external_leave_id": str(fiona_leave.pk),
            "date": "2026-10-12",
        }])
        self.assertEqual(captured["body"]["shifts"], [])

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_switching_exclusion_off_includes_the_employee_again(self, urlopen):
        shift = self.add_shift(self.dan)
        self.dan.exclude_from_clocking = False
        self.dan.save(update_fields=["exclude_from_clocking"])
        with self.assertRaises(ClockingPublishError) as raised:
            publish_week_to_clocking(self.roster)
        self.assertIn("Dan Deegan", str(raised.exception))

        self.dan.external_id = "240"
        self.dan.save(update_fields=["external_id"])
        captured = {}

        def capture(request, timeout=15):
            captured["body"] = json.loads(request.data.decode())
            return _accepted()

        urlopen.side_effect = capture
        publish_week_to_clocking(self.roster)
        self.assertEqual(captured["body"]["shifts"][0]["employee_number"], "240")
        self.assertEqual(
            captured["body"]["shifts"][0]["external_shift_id"],
            str(shift.pk),
        )

    def test_individual_republish_of_an_excluded_employee_without_an_id_is_local(self):
        self.add_shift(self.dan)
        with mock.patch(
            "apps.roster.services.clocking_publish.urllib.request.urlopen"
        ) as urlopen:
            result = publish_employee_roster_to_clocking(self.roster, self.dan)
        urlopen.assert_not_called()
        self.assertTrue(result["fully_applied"])
        payload = build_clocking_roster_payload(self.roster)
        self.assertEqual(payload["roster_only"], [])

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_individual_republish_clears_an_excluded_employee_who_has_an_id(self, urlopen):
        self.dan.external_id = "240"
        self.dan.save(update_fields=["external_id"])
        shift = self.add_shift(self.dan)
        AnnualLeave.objects.create(
            roster_week=self.roster,
            employee=self.dan,
            date=WEEK,
        )
        captured = {}

        def capture(request, timeout=15):
            captured["body"] = json.loads(request.data.decode())
            return _accepted()

        urlopen.side_effect = capture
        publish_employee_roster_to_clocking(self.roster, self.dan)
        self.assertEqual(captured["body"]["shifts"], [])
        self.assertEqual(captured["body"]["annual_leave"], [])
        self.assertEqual(
            captured["body"]["removed_external_shift_ids"],
            [str(shift.pk)],
        )
        self.assertEqual(captured["body"]["employee_number"], "240")


class ExclusionCheckboxTests(TestCase):
    def test_edit_screen_explains_the_exclusion(self):
        user = get_user_model().objects.create_user("manager", password="secret")
        self.client.force_login(user)
        employee = Employee.objects.create(first_name="Dan", last_name="Deegan")
        response = self.client.get(reverse("employees:edit", args=[employee.pk]))
        self.assertContains(response, "Exclude from clocking integration")
        self.assertContains(
            response,
            "Keep this employee on the roster, but do not send their "
            "shifts to the clocking/payroll application.",
        )
        self.assertContains(response, 'name="exclude_from_clocking"')

        response = self.client.post(
            reverse("employees:edit", args=[employee.pk]),
            {
                "external_id": "",
                "first_name": "Dan",
                "last_name": "Deegan",
                "department": "bar",
                "can_work_restaurant": "on",
                "is_active": "on",
                "exclude_from_clocking": "on",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        employee.refresh_from_db()
        self.assertTrue(employee.exclude_from_clocking)
