import io
import json
import urllib.error
from datetime import date, time
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.employees.models import Employee
from apps.roster.models import RosterStatus, RosterWeek, Shift
from apps.roster.services.clocking_publish import (
    ClockingPublishError,
    publish_employee_roster_to_clocking,
    publish_week_to_clocking,
)


WEEK = date(2026, 7, 6)


def _accepted_body(warnings=None, published=True, fully_applied=True):
    return json.dumps({
        "ok": True,
        "published": published,
        "fully_applied": fully_applied,
        "warnings": warnings or [],
    }).encode()


def _response(body, status=200):
    response = mock.MagicMock()
    response.read.return_value = body
    response.status = status
    context = mock.MagicMock()
    context.__enter__.return_value = response
    return context


@override_settings(
    CLOCKING_BASE_URL="http://clocking.test",
    ROSTER_INTEGRATION_TOKEN="test-token",
)
class ClockingWeekPublishTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="manager",
            password="secret",
        )
        self.client.force_login(self.user)
        self.roster = RosterWeek.objects.create(week_start=WEEK)
        self.fiona = Employee.objects.create(
            external_id="701",
            first_name="Fiona",
            department="restaurant",
        )
        self.catherine = Employee.objects.create(
            external_id="702",
            first_name="Catherine",
            department="restaurant",
        )
        self.fiona_shift = Shift.objects.create(
            roster_week=self.roster,
            employee=self.fiona,
            department="restaurant",
            date=WEEK,
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        self.catherine_shift = Shift.objects.create(
            roster_week=self.roster,
            employee=self.catherine,
            department="restaurant",
            date=WEEK,
            start_time=time(18, 0),
            end_time=time(23, 0),
        )

    def publish(self):
        return self.client.post(
            reverse("roster:publish", args=[self.roster.pk])
        )

    def assert_unpublished(self):
        self.roster.refresh_from_db()
        self.assertEqual(self.roster.status, RosterStatus.DRAFT)
        self.assertIsNone(self.roster.published_at)
        self.assertIsNone(self.roster.published_by_id)
        self.fiona_shift.refresh_from_db()
        self.assertEqual(self.fiona_shift.start_time, time(9, 0))

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_successful_publish_sends_the_week_then_marks_it_published(self, urlopen):
        seen_status = []

        def capture(request, timeout=15):
            self.roster.refresh_from_db()
            seen_status.append(self.roster.status)
            self.assertEqual(
                request.full_url,
                "http://clocking.test/api/v1/roster/publish/",
            )
            self.assertEqual(request.get_header("X-integration-token"), "test-token")
            self.sent = json.loads(request.data.decode())
            return _response(_accepted_body())

        urlopen.side_effect = capture

        response = self.publish()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(seen_status, [RosterStatus.DRAFT])
        self.roster.refresh_from_db()
        self.assertEqual(self.roster.status, RosterStatus.PUBLISHED)
        self.assertEqual(self.roster.published_by, self.user)
        self.assertIsNotNone(self.roster.published_at)

        self.assertEqual(self.sent["week_start"], "2026-07-06")
        self.assertEqual(len(self.sent["shifts"]), 2)
        self.assertNotIn("roster_only", self.sent)
        sent_by_employee = {
            row["employee_number"]: row for row in self.sent["shifts"]
        }
        self.assertEqual(
            sent_by_employee["701"]["external_shift_id"],
            str(self.fiona_shift.pk),
        )
        self.assertEqual(sent_by_employee["701"]["date"], "2026-07-06")
        self.assertEqual(sent_by_employee["701"]["start_time"], "09:00")
        self.assertEqual(sent_by_employee["701"]["end_time"], "17:00")
        self.assertNotIn("break_minutes", sent_by_employee["701"])
        self.assertEqual(
            sent_by_employee["702"]["external_shift_id"],
            str(self.catherine_shift.pk),
        )

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_clocking_rejection_leaves_the_roster_unpublished(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            url="http://clocking.test/api/v1/roster/publish/",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(json.dumps({
                "published": False,
                "errors": [{
                    "code": "EMPLOYEE_NOT_FOUND",
                    "employee_number": "701",
                }],
            }).encode()),
        )

        response = self.publish()
        self.assertEqual(response.status_code, 302)
        self.assert_unpublished()
        follow = self.client.get(response.url)
        messages = [str(message) for message in get_messages(follow.wsgi_request)]
        self.assertTrue(any("EMPLOYEE_NOT_FOUND" in message for message in messages))

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_fully_applied_false_leaves_the_roster_unpublished(self, urlopen):
        urlopen.return_value = _response(_accepted_body(
            published=True,
            fully_applied=False,
            warnings=[{
                "code": "MANAGER_SHIFT_PRESERVED",
                "message": "Manager-owned shift was left unchanged.",
                "employee_number": "701",
                "date": "2026-07-06",
            }],
        ))

        response = self.publish()
        self.assert_unpublished()
        follow = self.client.get(response.url)
        messages = [str(message) for message in get_messages(follow.wsgi_request)]
        self.assertTrue(any(
            "Manager-owned shift was left unchanged." in message
            for message in messages
        ))
        self.assertFalse(any(message == "Roster published." for message in messages))

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_network_failure_leaves_the_roster_unpublished(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("connection refused")

        self.publish()
        self.assert_unpublished()

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_invalid_response_leaves_the_roster_unpublished(self, urlopen):
        urlopen.return_value = _response(b"not-json")

        with self.assertRaises(ClockingPublishError):
            publish_week_to_clocking(self.roster)
        self.assert_unpublished()

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_missing_external_id_does_not_call_clocking(self, urlopen):
        Employee.objects.create(
            external_id="",
            first_name="Roster",
            last_name="Only",
            department="kitchen",
        )
        kitchen = Employee.objects.get(first_name="Roster")
        Shift.objects.create(
            roster_week=self.roster,
            employee=kitchen,
            department="kitchen",
            date=WEEK,
            start_time=time(12, 0),
            end_time=time(20, 0),
        )

        with self.assertRaises(ClockingPublishError) as raised:
            publish_week_to_clocking(self.roster)
        self.assertIn("Roster Only", str(raised.exception))
        urlopen.assert_not_called()

        self.publish()
        self.assert_unpublished()
        urlopen.assert_not_called()

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_warnings_are_shown_when_clocking_accepts_the_week(self, urlopen):
        urlopen.return_value = _response(_accepted_body(warnings=[{
            "code": "REFERENCED_SHIFT_PRESERVED",
            "message": "Published shift was kept because a manager attendance decision references it.",
            "employee_number": "701",
            "date": "2026-07-06",
        }]))

        response = self.publish()
        self.roster.refresh_from_db()
        self.assertEqual(self.roster.status, RosterStatus.PUBLISHED)
        follow = self.client.get(response.url)
        messages = [str(message) for message in get_messages(follow.wsgi_request)]
        self.assertTrue(any("manager attendance decision" in message for message in messages))
        self.assertTrue(any(message == "Roster published." for message in messages))

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_employee_republish_sends_external_shift_id(self, urlopen):
        captured = {}

        def capture(request, timeout=15):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode())
            return _response(_accepted_body())

        urlopen.side_effect = capture
        publish_employee_roster_to_clocking(self.roster, self.fiona)

        self.assertEqual(
            captured["url"],
            "http://clocking.test/api/v1/roster/publish-employee/",
        )
        self.assertEqual(captured["body"]["shifts"], [{
            "employee_number": "701",
            "external_shift_id": str(self.fiona_shift.pk),
            "date": "2026-07-06",
            "start_time": "09:00",
            "end_time": "17:00",
        }])
