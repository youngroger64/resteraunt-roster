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
from apps.roster.models import (
    RosterStatus,
    RosterWeek,
    Shift,
    ShiftResponse,
    ShiftResponseStatus,
    StaffRosterNotice,
)


WEEK = date(2026, 10, 5)  # Monday


def _accepted_body(fully_applied=True):
    return json.dumps({
        "ok": True,
        "published": True,
        "fully_applied": fully_applied,
        "warnings": [],
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
class ShiftChangeTests(TestCase):
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
            last_name="Walsh",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.catherine = Employee.objects.create(
            external_id="702",
            first_name="Catherine",
            last_name="Kirby",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.shift = Shift.objects.create(
            roster_week=self.roster,
            employee=self.fiona,
            department="restaurant",
            date=WEEK,
            start_time=time(8, 0),
            end_time=time(22, 0),
            segment=1,
        )

    def change_url(self):
        return reverse("roster:shift_change", args=[self.roster.pk])

    def cancel_url(self):
        return reverse("roster:shift_change_cancel", args=[self.roster.pk])

    def test_add_shift_starts_from_one_button(self):
        response = self.client.get(
            reverse("roster:detail", args=[self.roster.pk])
        )
        html = response.content.decode()
        self.assertIn('id="shift-change-add"', html)
        self.assertIn("+ Add shift", html)
        self.assertRegex(
            html,
            r'id="shift-change-save" hidden>\s*Save changes\s*</button>',
        )
        self.assertNotRegex(
            html,
            r'id="shift-change-save"[^>]*>\s*Add shift\s*</button>',
        )

    def post_change(self, **overrides):
        data = {
            "shift_id": self.shift.pk,
            "employee_id": self.fiona.pk,
            "date": WEEK.isoformat(),
            "start_time": "12:00",
            "end_time": "22:00",
        }
        data.update(overrides)
        return self.client.post(self.change_url(), data)

    def messages(self, response):
        return [message.message for message in get_messages(response.wsgi_request)]

    def publish_roster(self):
        self.roster.status = RosterStatus.PUBLISHED
        self.roster.save(update_fields=["status"])

    def test_draft_edit_keeps_the_shift_and_does_not_call_clocking(self):
        with mock.patch(
            "apps.roster.services.clocking_publish.urllib.request.urlopen"
        ) as urlopen:
            response = self.post_change()

        self.assertEqual(response.status_code, 302)
        urlopen.assert_not_called()
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.start_time, time(12, 0))
        self.assertEqual(self.shift.end_time, time(22, 0))
        self.assertEqual(self.shift.employee, self.fiona)
        self.assertEqual(StaffRosterNotice.objects.count(), 0)

    def test_draft_add_does_not_call_clocking(self):
        with mock.patch(
            "apps.roster.services.clocking_publish.urllib.request.urlopen"
        ) as urlopen:
            response = self.post_change(
                shift_id="",
                date=(WEEK.replace(day=6)).isoformat(),
                start_time="12:00",
                end_time="22:00",
            )

        self.assertEqual(response.status_code, 302)
        urlopen.assert_not_called()
        added = Shift.objects.exclude(pk=self.shift.pk).get()
        self.assertEqual(added.employee, self.fiona)
        self.assertEqual(added.date, date(2026, 10, 6))
        self.assertEqual(StaffRosterNotice.objects.count(), 0)

    def test_draft_cancel_does_not_call_clocking(self):
        with mock.patch(
            "apps.roster.services.clocking_publish.urllib.request.urlopen"
        ) as urlopen:
            response = self.client.post(
                self.cancel_url(),
                {"shift_id": self.shift.pk},
            )

        self.assertEqual(response.status_code, 302)
        urlopen.assert_not_called()
        self.assertFalse(Shift.objects.filter(pk=self.shift.pk).exists())
        self.assertEqual(StaffRosterNotice.objects.count(), 0)

    def test_save_cell_refuses_a_published_week(self):
        self.publish_roster()
        original_pk = self.shift.pk

        response = self.client.post(
            reverse("roster:save_cell", args=[self.roster.pk]),
            {
                "employee_id": self.fiona.pk,
                "date": WEEK.isoformat(),
                "department": "restaurant",
                "shift_text": "10:00-16:00",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.pk, original_pk)
        self.assertEqual(self.shift.start_time, time(8, 0))
        self.assertEqual(Shift.objects.filter(employee=self.fiona).count(), 1)

    def test_editing_one_shift_leaves_the_other_shift_that_day(self):
        second = Shift.objects.create(
            roster_week=self.roster,
            employee=self.fiona,
            department="restaurant",
            date=WEEK,
            start_time=time(9, 0),
            end_time=time(12, 0),
            segment=2,
        )
        self.post_change(start_time="13:00", end_time="17:00")
        self.shift.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.shift.start_time, time(13, 0))
        self.assertEqual(second.start_time, time(9, 0))
        self.assertEqual(second.end_time, time(12, 0))

    def _capture(self, urlopen, expected_path):
        def capture(request, timeout=15):
            self.assertTrue(request.full_url.endswith(expected_path))
            self.assertNotIn(b"break_minutes", request.data)
            self.sent = json.loads(request.data.decode())
            self.shift_still_present = Shift.objects.filter(pk=self.shift.pk).exists()
            return _response(_accepted_body())

        urlopen.side_effect = capture

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_published_time_edit_keeps_the_shift_and_updates_clocking(self, urlopen):
        self.publish_roster()
        self._capture(urlopen, "/api/v1/roster/publish-employee/")

        response = self.post_change()
        self.assertEqual(response.status_code, 302)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.start_time, time(12, 0))
        self.assertEqual(self.shift.end_time, time(22, 0))
        self.assertEqual(self.sent["shifts"][0]["external_shift_id"], str(self.shift.pk))
        self.assertEqual(self.sent["shifts"][0]["start_time"], "12:00")
        self.assertEqual(self.sent["shifts"][0]["end_time"], "22:00")
        notice = StaffRosterNotice.objects.get()
        self.assertEqual(notice.employee, self.fiona)
        self.assertIn("is now 12:00–22:00 instead of 08:00–22:00", notice.message)

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_published_edit_rolls_back_on_http_failure(self, urlopen):
        self.publish_roster()
        urlopen.side_effect = urllib.error.HTTPError(
            url="http://clocking.test/api/v1/roster/publish-employee/",
            code=400,
            msg="bad",
            hdrs=None,
            fp=io.BytesIO(b'{"error": "Clocking could not save this change."}'),
        )

        response = self.post_change()
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.start_time, time(8, 0))
        self.assertEqual(StaffRosterNotice.objects.count(), 0)
        self.assertTrue(any("not saved" in text for text in self.messages(response)))

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_published_edit_rolls_back_on_network_failure(self, urlopen):
        self.publish_roster()
        urlopen.side_effect = urllib.error.URLError("connection refused")

        response = self.post_change()
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.start_time, time(8, 0))
        self.assertEqual(StaffRosterNotice.objects.count(), 0)
        self.assertTrue(any("could not be reached" in text for text in self.messages(response)))

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_published_edit_rolls_back_when_not_fully_applied(self, urlopen):
        self.publish_roster()
        urlopen.return_value = _response(_accepted_body(fully_applied=False))

        self.post_change()
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.start_time, time(8, 0))
        self.assertEqual(StaffRosterNotice.objects.count(), 0)

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_published_add_sends_the_new_shift(self, urlopen):
        self.publish_roster()
        self._capture(urlopen, "/api/v1/roster/publish-employee/")

        response = self.post_change(
            shift_id="",
            date="2026-10-06",
            start_time="12:00",
            end_time="22:00",
        )
        self.assertEqual(response.status_code, 302)
        added = Shift.objects.exclude(pk=self.shift.pk).get()
        sent_ids = {row["external_shift_id"] for row in self.sent["shifts"]}
        self.assertIn(str(added.pk), sent_ids)
        notice = StaffRosterNotice.objects.get()
        self.assertEqual(
            notice.message,
            f"New shift: {added.date.strftime('%a %d %b')} · 12:00–22:00.",
        )

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_published_add_rolls_back_when_clocking_fails(self, urlopen):
        self.publish_roster()
        urlopen.side_effect = urllib.error.URLError("connection refused")

        self.post_change(shift_id="", date="2026-10-06")
        self.assertEqual(Shift.objects.count(), 1)
        self.assertEqual(StaffRosterNotice.objects.count(), 0)

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_published_cancel_removes_the_shift_only_after_clocking_accepts(self, urlopen):
        self.publish_roster()
        self._capture(urlopen, "/api/v1/roster/publish-employee/")

        response = self.client.post(self.cancel_url(), {"shift_id": self.shift.pk})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.shift_still_present)
        self.assertEqual(
            self.sent["removed_external_shift_ids"],
            [str(self.shift.pk)],
        )
        self.assertEqual(self.sent["shifts"], [])
        self.assertFalse(Shift.objects.filter(pk=self.shift.pk).exists())
        notice = StaffRosterNotice.objects.get()
        self.assertEqual(
            notice.message,
            f"Shift cancelled: {WEEK.strftime('%a %d %b')} · 08:00–22:00.",
        )

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_protected_cancel_keeps_the_local_shift(self, urlopen):
        self.publish_roster()
        urlopen.return_value = _response(_accepted_body(fully_applied=False))

        self.client.post(self.cancel_url(), {"shift_id": self.shift.pk})
        self.assertTrue(Shift.objects.filter(pk=self.shift.pk).exists())
        self.assertEqual(StaffRosterNotice.objects.count(), 0)

    def test_blank_external_id_blocks_published_changes(self):
        self.publish_roster()
        self.fiona.external_id = ""
        self.fiona.save(update_fields=["external_id"])

        with mock.patch(
            "apps.roster.services.clocking_publish.urllib.request.urlopen"
        ) as urlopen:
            edit = self.post_change()
            add = self.post_change(shift_id="", date="2026-10-06")
            cancel = self.client.post(
                self.cancel_url(),
                {"shift_id": self.shift.pk},
            )

        urlopen.assert_not_called()
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.start_time, time(8, 0))
        self.assertEqual(Shift.objects.count(), 1)
        self.assertEqual(StaffRosterNotice.objects.count(), 0)
        for response in (edit, add, cancel):
            self.assertTrue(
                any("Fiona Walsh" in text and "clocking ID" in text for text in self.messages(response))
            )

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_published_reassignment_uses_the_full_week(self, urlopen):
        self.publish_roster()
        self._capture(urlopen, "/api/v1/roster/publish/")

        response = self.post_change(employee_id=self.catherine.pk)
        self.assertEqual(response.status_code, 302)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.pk, self.shift.pk)
        self.assertEqual(self.shift.employee, self.catherine)
        owners = {
            row["external_shift_id"]: row["employee_number"]
            for row in self.sent["shifts"]
        }
        self.assertEqual(owners[str(self.shift.pk)], "702")
        self.assertEqual(StaffRosterNotice.objects.count(), 2)
        self.assertTrue(
            StaffRosterNotice.objects.filter(
                employee=self.fiona,
                message__contains="shift was changed",
            ).exists()
        )
        self.assertTrue(
            StaffRosterNotice.objects.filter(
                employee=self.catherine,
                message__startswith="New shift:",
            ).exists()
        )

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_failed_reassignment_rolls_back(self, urlopen):
        self.publish_roster()
        urlopen.return_value = _response(_accepted_body(fully_applied=False))

        self.post_change(employee_id=self.catherine.pk)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.employee, self.fiona)
        self.assertEqual(StaffRosterNotice.objects.count(), 0)


@override_settings(
    CLOCKING_BASE_URL="http://clocking.test",
    ROSTER_INTEGRATION_TOKEN="test-token",
)
class CannotWorkClockingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="manager",
            password="secret",
        )
        self.client.force_login(self.user)
        self.roster = RosterWeek.objects.create(
            week_start=WEEK,
            status=RosterStatus.PUBLISHED,
        )
        self.fiona = Employee.objects.create(
            external_id="701",
            first_name="Fiona",
            last_name="Walsh",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.catherine = Employee.objects.create(
            external_id="702",
            first_name="Catherine",
            last_name="Kirby",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.shift = Shift.objects.create(
            roster_week=self.roster,
            employee=self.fiona,
            department="restaurant",
            date=WEEK,
            start_time=time(8, 0),
            end_time=time(22, 0),
        )
        self.other_shift = Shift.objects.create(
            roster_week=self.roster,
            employee=self.catherine,
            department="restaurant",
            date=date(2026, 10, 6),
            start_time=time(17, 0),
            end_time=time(21, 0),
        )
        self.response_row = ShiftResponse.objects.create(
            shift=self.shift,
            employee=self.fiona,
            status=ShiftResponseStatus.CANNOT_WORK,
        )

    def _accept(self, urlopen, expected_path):
        def capture(request, timeout=15):
            self.assertTrue(request.full_url.endswith(expected_path))
            self.sent = json.loads(request.data.decode())
            return _response(_accepted_body())

        urlopen.side_effect = capture

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_adjust_still_updates_clocking_for_the_same_employee(self, urlopen):
        self._accept(urlopen, "/api/v1/roster/publish-employee/")
        original_pk = self.shift.pk

        response = self.client.post(
            reverse(
                "roster:adjust_requested_shift",
                args=[self.roster.pk, self.response_row.pk],
            ),
            {"start_time": "12:00", "end_time": "22:00"},
        )
        self.assertEqual(response.status_code, 302)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.pk, original_pk)
        self.assertEqual(self.shift.start_time, time(12, 0))
        self.assertEqual(self.sent["shifts"][0]["external_shift_id"], str(original_pk))
        self.response_row.refresh_from_db()
        self.assertEqual(self.response_row.status, ShiftResponseStatus.RESOLVED)
        self.assertEqual(StaffRosterNotice.objects.count(), 1)

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_reassign_updates_clocking(self, urlopen):
        self._accept(urlopen, "/api/v1/roster/publish/")
        original_pk = self.shift.pk

        response = self.client.post(
            reverse(
                "roster:replace_requested_shift",
                args=[self.roster.pk, self.response_row.pk, self.catherine.pk],
            )
        )
        self.assertEqual(response.status_code, 302)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.pk, original_pk)
        self.assertEqual(self.shift.employee, self.catherine)
        owners = {
            row["external_shift_id"]: row["employee_number"]
            for row in self.sent["shifts"]
        }
        self.assertEqual(owners[str(original_pk)], "702")
        self.response_row.refresh_from_db()
        self.assertEqual(self.response_row.status, ShiftResponseStatus.RESOLVED)
        self.assertEqual(StaffRosterNotice.objects.count(), 2)

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_failed_reassign_rolls_back(self, urlopen):
        urlopen.return_value = _response(_accepted_body(fully_applied=False))

        self.client.post(
            reverse(
                "roster:replace_requested_shift",
                args=[self.roster.pk, self.response_row.pk, self.catherine.pk],
            )
        )
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.employee, self.fiona)
        self.response_row.refresh_from_db()
        self.assertEqual(self.response_row.status, ShiftResponseStatus.CANNOT_WORK)
        self.assertEqual(StaffRosterNotice.objects.count(), 0)

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_swap_updates_clocking(self, urlopen):
        self._accept(urlopen, "/api/v1/roster/publish/")

        response = self.client.post(
            reverse(
                "roster:swap_requested_shift",
                args=[self.roster.pk, self.response_row.pk],
            ),
            {"other_shift_id": self.other_shift.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.shift.refresh_from_db()
        self.other_shift.refresh_from_db()
        self.assertEqual(self.shift.employee, self.catherine)
        self.assertEqual(self.other_shift.employee, self.fiona)
        self.response_row.refresh_from_db()
        self.assertEqual(self.response_row.status, ShiftResponseStatus.RESOLVED)
        self.assertEqual(StaffRosterNotice.objects.count(), 2)

    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_failed_swap_rolls_back(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("connection refused")

        self.client.post(
            reverse(
                "roster:swap_requested_shift",
                args=[self.roster.pk, self.response_row.pk],
            ),
            {"other_shift_id": self.other_shift.pk},
        )
        self.shift.refresh_from_db()
        self.other_shift.refresh_from_db()
        self.assertEqual(self.shift.employee, self.fiona)
        self.assertEqual(self.other_shift.employee, self.catherine)
        self.response_row.refresh_from_db()
        self.assertEqual(self.response_row.status, ShiftResponseStatus.CANNOT_WORK)
        self.assertEqual(StaffRosterNotice.objects.count(), 0)
