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

        self.client.post(
            reverse("roster:delete", args=[roster.pk])
        )

        self.assertTrue(
            RosterWeek.objects.filter(pk=roster.pk).exists()
        )
