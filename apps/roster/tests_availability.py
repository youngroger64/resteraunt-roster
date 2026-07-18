from datetime import date, time

from django.test import TestCase

from apps.employees.models import Employee
from apps.roster.models import RosterWeek, Shift
from apps.roster.services.generator import candidate_availability


class CandidateAvailabilityTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(
            first_name="Cora",
            last_name="Doherty",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.roster = RosterWeek.objects.create(
            week_start=date(2026, 8, 3)
        )

    def test_overlap_is_unavailable(self):
        Shift.objects.create(
            roster_week=self.roster,
            employee=self.employee,
            department="restaurant",
            date=date(2026, 8, 9),
            start_time=time(11, 0),
            end_time=time(19, 30),
        )

        result = candidate_availability(
            roster=self.roster,
            employee=self.employee,
            shift_date=date(2026, 8, 9),
            signature="09:30-19:00",
        )

        self.assertFalse(result["available"])
        self.assertIn("Already working", result["reason"])

    def test_short_gap_is_unavailable(self):
        Shift.objects.create(
            roster_week=self.roster,
            employee=self.employee,
            department="restaurant",
            date=date(2026, 8, 9),
            start_time=time(9, 0),
            end_time=time(14, 0),
        )

        result = candidate_availability(
            roster=self.roster,
            employee=self.employee,
            shift_date=date(2026, 8, 9),
            signature="15:00-19:00",
        )

        self.assertFalse(result["available"])
        self.assertIn("90 minutes", result["reason"])

    def test_ninety_minute_gap_is_possible_split(self):
        Shift.objects.create(
            roster_week=self.roster,
            employee=self.employee,
            department="restaurant",
            date=date(2026, 8, 9),
            start_time=time(9, 0),
            end_time=time(14, 0),
        )

        result = candidate_availability(
            roster=self.roster,
            employee=self.employee,
            shift_date=date(2026, 8, 9),
            signature="15:30-19:00",
        )

        self.assertTrue(result["available"])
        self.assertTrue(result["possible_split"])
