import json
from datetime import date, time
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.employees.models import Department, Employee
from apps.roster.models import (
    RosterStatus,
    RosterWeek,
    Shift,
    ShiftResponse,
    ShiftResponseStatus,
    StaffRosterNotice,
)


class PublishedShiftSwapTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(
            username="manager",
            password="testpass123",
        )

        self.roster = RosterWeek.objects.create(
            week_start=date(2026, 9, 14),
            status=RosterStatus.PUBLISHED,
        )

        self.cori = Employee.objects.create(
            first_name="Cori",
            last_name="Hamm",
            department=Department.RESTAURANT,
            is_active=True,
            can_work_restaurant=True,
            external_id="801",
        )

        self.catherine = Employee.objects.create(
            first_name="Catherine",
            last_name="Kirby",
            department=Department.RESTAURANT,
            is_active=True,
            can_work_restaurant=True,
            external_id="802",
        )

        self.cori_shift = Shift.objects.create(
            roster_week=self.roster,
            employee=self.cori,
            department=Department.RESTAURANT,
            date=date(2026, 9, 15),
            start_time=time(13, 0),
            end_time=time(21, 0),
            segment=1,
        )

        self.catherine_shift = Shift.objects.create(
            roster_week=self.roster,
            employee=self.catherine,
            department=Department.RESTAURANT,
            date=date(2026, 9, 16),
            start_time=time(17, 0),
            end_time=time(21, 0),
            segment=1,
        )

        self.response = ShiftResponse.objects.create(
            shift=self.cori_shift,
            employee=self.cori,
            status=ShiftResponseStatus.CANNOT_WORK,
        )

        self.client.login(
            username="manager",
            password="testpass123",
        )

    @override_settings(
        CLOCKING_BASE_URL="http://clocking.test",
        ROSTER_INTEGRATION_TOKEN="test-token",
    )
    @mock.patch("apps.roster.services.clocking_publish.urllib.request.urlopen")
    def test_published_swap_exchanges_employees_and_notifies_both(self, urlopen):
        def capture(request, timeout=15):
            self.assertEqual(
                request.full_url,
                "http://clocking.test/api/v1/roster/publish/",
            )
            self.sent = json.loads(request.data.decode())
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

        urlopen.side_effect = capture

        url = reverse(
            "roster:swap_requested_shift",
            args=[self.roster.pk, self.response.pk],
        )

        response = self.client.post(
            url,
            {"other_shift_id": self.catherine_shift.pk},
        )

        self.assertEqual(response.status_code, 302)

        self.cori_shift.refresh_from_db()
        self.catherine_shift.refresh_from_db()
        self.response.refresh_from_db()

        self.assertEqual(
            self.cori_shift.employee_id,
            self.catherine.id,
        )
        self.assertEqual(
            self.catherine_shift.employee_id,
            self.cori.id,
        )

        self.assertEqual(
            self.response.status,
            ShiftResponseStatus.RESOLVED,
        )

        notices = StaffRosterNotice.objects.filter(
            roster_week=self.roster,
        )

        self.assertEqual(notices.count(), 2)
        self.assertTrue(
            notices.filter(employee=self.cori).exists()
        )
        self.assertTrue(
            notices.filter(employee=self.catherine).exists()
        )
        by_id = {
            row["external_shift_id"]: row["employee_number"]
            for row in self.sent["shifts"]
        }
        self.assertEqual(by_id[str(self.cori_shift.pk)], "802")
        self.assertEqual(by_id[str(self.catherine_shift.pk)], "801")

    def test_swap_rejects_same_shift(self):
        url = reverse(
            "roster:swap_requested_shift",
            args=[self.roster.pk, self.response.pk],
        )

        response = self.client.post(
            url,
            {"other_shift_id": self.cori_shift.pk},
        )

        self.assertEqual(response.status_code, 302)

        self.cori_shift.refresh_from_db()
        self.catherine_shift.refresh_from_db()
        self.response.refresh_from_db()

        self.assertEqual(
            self.cori_shift.employee_id,
            self.cori.id,
        )
        self.assertEqual(
            self.catherine_shift.employee_id,
            self.catherine.id,
        )
        self.assertEqual(
            self.response.status,
            ShiftResponseStatus.CANNOT_WORK,
        )

    def test_swap_rejects_incompatible_department(self):
        bar_person = Employee.objects.create(
            first_name="Bar",
            last_name="Only",
            department=Department.BAR,
            is_active=True,
            can_work_bar=True,
            can_work_restaurant=False,
        )

        bar_shift = Shift.objects.create(
            roster_week=self.roster,
            employee=bar_person,
            department=Department.BAR,
            date=date(2026, 9, 17),
            start_time=time(20, 0),
            end_time=time(23, 30),
            segment=1,
        )

        url = reverse(
            "roster:swap_requested_shift",
            args=[self.roster.pk, self.response.pk],
        )

        response = self.client.post(
            url,
            {"other_shift_id": bar_shift.pk},
        )

        self.assertEqual(response.status_code, 302)

        self.cori_shift.refresh_from_db()
        bar_shift.refresh_from_db()

        self.assertEqual(
            self.cori_shift.employee_id,
            self.cori.id,
        )
        self.assertEqual(
            bar_shift.employee_id,
            bar_person.id,
        )
