from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.roster.models import RosterWeek, Shift
from apps.employees.models import Employee


class RosterManagementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="manager",
            password="test-password",
        )
        self.client.login(
            username="manager",
            password="test-password",
        )

    def test_delete_draft_removes_roster(self):
        roster = RosterWeek.objects.create(
            week_start=date(2026, 8, 3)
        )

        response = self.client.post(
            reverse("roster:delete", args=[roster.pk])
        )

        self.assertRedirects(response, reverse("roster:list"))
        self.assertFalse(
            RosterWeek.objects.filter(pk=roster.pk).exists()
        )

    def test_published_roster_is_not_deleted(self):
        roster = RosterWeek.objects.create(
            week_start=date(2026, 8, 10),
            status="published",
        )
        employee = Employee.objects.create(
            first_name="Sent",
            last_name="Week",
            department="restaurant",
        )
        shift = Shift.objects.create(
            roster_week=roster,
            employee=employee,
            department="restaurant",
            date=date(2026, 8, 10),
            start_time="08:00",
            end_time="16:00",
        )

        response = self.client.post(
            reverse("roster:delete", args=[roster.pk])
        )

        self.assertRedirects(response, reverse("roster:list"))
        self.assertTrue(
            RosterWeek.objects.filter(pk=roster.pk).exists()
        )
        self.assertTrue(Shift.objects.filter(pk=shift.pk).exists())

        page = self.client.get(reverse("roster:delete", args=[roster.pk]))
        self.assertRedirects(page, reverse("roster:list"))
        self.assertTrue(
            RosterWeek.objects.filter(pk=roster.pk).exists()
        )

    def test_published_week_has_no_delete_link(self):
        published = RosterWeek.objects.create(
            week_start=date(2026, 8, 17),
            status="published",
        )
        draft = RosterWeek.objects.create(
            week_start=date(2026, 8, 24),
        )

        response = self.client.get(reverse("roster:list"))
        html = response.content.decode()

        self.assertNotIn(
            reverse("roster:delete", args=[published.pk]),
            html,
        )
        self.assertIn(
            reverse("roster:delete", args=[draft.pk]),
            html,
        )
