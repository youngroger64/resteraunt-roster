from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.employees.models import Employee
from apps.roster.models import (
    AvailabilityException,
    EmployeeScheduleProfile,
    OpenShift,
    OpenShiftRequest,
    RosterStatus,
    RosterWeek,
    Shift,
    ShiftResponse,
)
from apps.roster.services.generator import (
    automatic_department_eligible,
    candidate_availability,
    target_days,
    target_hours,
)
from apps.roster.services.publisher import publish_roster
from apps.roster.models import EmployeePattern


class StaffWorkflowTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(
            external_id="701",
            first_name="Fiona",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.other = Employee.objects.create(
            external_id="702",
            first_name="Catherine",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.week = RosterWeek.objects.create(
            week_start=date.today() - timedelta(days=date.today().weekday()),
            status=RosterStatus.PUBLISHED,
        )
        self.shift = Shift.objects.create(
            roster_week=self.week,
            employee=self.employee,
            department="restaurant",
            date=max(date.today(), self.week.week_start),
            start_time=time(10),
            end_time=time(17),
        )

    def identify(self):
        return self.client.post(reverse("roster:staff"), {"employee_number": "701"})

    def test_staff_can_identify_and_see_published_shift(self):
        self.identify()
        response = self.client.get(reverse("roster:staff"))
        self.assertContains(response, "Fiona")
        self.assertContains(response, "10:00")

    def test_cannot_work_keeps_shift_assigned_and_creates_request(self):
        self.identify()
        self.client.post(
            reverse("roster:staff_shift_response", args=[self.shift.pk]),
            {"action": "cannot_work", "reason": "College"},
        )
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.employee, self.employee)
        response = ShiftResponse.objects.get(shift=self.shift)
        self.assertEqual(response.status, "cannot_work")
        self.assertEqual(response.reason, "College")

    def test_explicit_unavailability_blocks_generator_candidate(self):
        AvailabilityException.objects.create(
            employee=self.other,
            date=self.shift.date,
            unavailable=True,
        )
        result = candidate_availability(
            self.week, self.other, self.shift.date, "10:00-17:00"
        )
        self.assertFalse(result["available"])
        self.assertIn("unavailable", result["reason"].lower())

    def test_staff_can_request_open_shift(self):
        open_shift = OpenShift.objects.create(
            roster_week=self.week,
            department="restaurant",
            date=self.shift.date + timedelta(days=1),
            start_time=time(18),
            end_time=time(22),
            source_signature="18:00-22:00",
        )
        self.identify()
        self.client.post(reverse("roster:staff_open_shift_request", args=[open_shift.pk]))
        self.assertTrue(
            OpenShiftRequest.objects.filter(open_shift=open_shift, employee=self.employee).exists()
        )


class ApprovedProfileTests(TestCase):
    def test_manager_profile_overrides_learned_targets(self):
        employee = Employee.objects.create(first_name="Cori")
        pattern = EmployeePattern.objects.create(
            employee=employee,
            average_weekly_hours=32,
            average_days_worked=4,
            normal_department="restaurant",
        )
        EmployeeScheduleProfile.objects.create(
            employee=employee,
            target_hours=40,
            target_days=5,
            preferred_department="restaurant",
        )
        self.assertEqual(target_hours(pattern, "restaurant"), 40)
        self.assertEqual(target_days(pattern), 5)

    def test_manager_primary_area_is_authoritative_for_automatic_generation(self):
        employee = Employee.objects.create(
            first_name="Donal",
            department="restaurant",
            can_work_restaurant=True,
            can_work_bar=True,
        )
        pattern = EmployeePattern.objects.create(
            employee=employee,
            average_weekly_hours=16,
            average_days_worked=2,
            normal_department="restaurant",
        )
        EmployeeScheduleProfile.objects.create(
            employee=employee,
            target_hours=16,
            target_days=2,
            preferred_department="bar",
        )
        self.assertTrue(automatic_department_eligible(pattern, "bar"))
        self.assertFalse(automatic_department_eligible(pattern, "restaurant"))


class PublishMultipleWeeksTests(TestCase):
    def test_publishing_next_week_does_not_supersede_previous_week(self):
        User = get_user_model()
        user = User.objects.create_user(username="manager", password="x")
        monday = date.today() - timedelta(days=date.today().weekday())
        first = RosterWeek.objects.create(week_start=monday)
        second = RosterWeek.objects.create(week_start=monday + timedelta(days=7))
        publish_roster(first, user)
        publish_roster(second, user)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, RosterStatus.PUBLISHED)
        self.assertEqual(second.status, RosterStatus.PUBLISHED)
