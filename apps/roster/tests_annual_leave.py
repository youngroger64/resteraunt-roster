import json
from datetime import date, time, timedelta
from unittest import mock

from django.test import TestCase, override_settings

from apps.employees.models import Employee
from apps.roster.models import AnnualLeave, RosterWeek, Shift
from apps.roster.services.clocking_publish import (
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


@override_settings(
    CLOCKING_BASE_URL="http://clocking.test",
    ROSTER_INTEGRATION_TOKEN="test-token",
)
class AnnualLeavePublishPayloadTests(TestCase):
    def setUp(self):
        self.roster = RosterWeek.objects.create(week_start=WEEK)
        self.fiona = Employee.objects.create(
            external_id="207",
            first_name="Fiona",
            last_name="Conway",
        )
        self.leave = AnnualLeave.objects.create(
            roster_week=self.roster,
            employee=self.fiona,
            date=WEEK,
        )
        Shift.objects.create(
            roster_week=self.roster,
            employee=self.fiona,
            department="restaurant",
            date=WEEK + timedelta(days=3),
            start_time=time(8, 30),
            end_time=time(16, 30),
        )

    def test_week_payload_sends_leave_separately_from_shifts(self):
        payload = build_clocking_roster_payload(self.roster)
        self.assertEqual(payload["annual_leave"], [{
            "employee_number": "207",
            "external_leave_id": str(self.leave.pk),
            "date": "2026-10-12",
        }])
        self.assertEqual(len(payload["shifts"]), 1)
        self.assertNotIn("start_time", payload["annual_leave"][0])

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_publish_posts_annual_leave(self, urlopen):
        captured = {}

        def capture(request, timeout=15):
            captured["body"] = json.loads(request.data.decode())
            return _accepted()

        urlopen.side_effect = capture
        publish_week_to_clocking(self.roster)
        self.assertEqual(
            captured["body"]["annual_leave"][0]["external_leave_id"],
            str(self.leave.pk),
        )
        self.assertEqual(len(captured["body"]["shifts"]), 1)

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_employee_republish_sends_only_that_employees_leave(self, urlopen):
        other = Employee.objects.create(
            external_id="229",
            first_name="Clea",
            last_name="Whelan",
        )
        AnnualLeave.objects.create(
            roster_week=self.roster,
            employee=other,
            date=WEEK,
        )
        captured = {}

        def capture(request, timeout=15):
            captured["body"] = json.loads(request.data.decode())
            return _accepted()

        urlopen.side_effect = capture
        publish_employee_roster_to_clocking(self.roster, self.fiona)
        self.assertEqual(
            [row["employee_number"] for row in captured["body"]["annual_leave"]],
            ["207"],
        )

    def test_leave_without_a_clocking_id_blocks_publish(self):
        Employee.objects.create(
            external_id="",
            first_name="Cori",
            last_name="Hamm",
        )
        person = Employee.objects.get(first_name="Cori")
        AnnualLeave.objects.create(
            roster_week=self.roster,
            employee=person,
            date=WEEK,
        )
        with self.assertRaises(Exception) as raised:
            publish_week_to_clocking(self.roster)
        self.assertIn("Cori Hamm", str(raised.exception))
