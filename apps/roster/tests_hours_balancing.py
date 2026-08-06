from datetime import date

from django.test import TestCase

from apps.employees.models import Employee
from apps.roster.models import (
    EmployeePattern,
    RosterWeek,
    StaffingPattern,
)
from apps.roster.services.generator import (
    generate_business_roster,
    hours_target_score,
)


class HoursBalancingTests(TestCase):
    def make_pattern(self, name, hours):
        employee = Employee.objects.create(
            first_name=name,
            department="restaurant",
            can_work_restaurant=True,
        )
        pattern = EmployeePattern.objects.create(
            employee=employee,
            weeks_seen=4,
            normal_department="restaurant",
            average_weekly_hours=hours,
            average_days_worked=4,
            consistency=80,
            day_probabilities={
                "mon": 100,
                "thu": 100,
                "fri": 100,
                "sat": 100,
            },
            typical_shifts={
                "mon": {
                    "shift": "10:00-16:30",
                    "confidence": 100,
                },
                "thu": {
                    "shift": "18:00-22:00",
                    "confidence": 100,
                },
                "fri": {
                    "shift": "10:00-17:00",
                    "confidence": 100,
                },
                "sat": {
                    "shift": "18:00-22:30",
                    "confidence": 100,
                },
            },
        )
        return employee, pattern

    def test_under_target_score_is_higher(self):
        _employee, pattern = self.make_pattern(
            "Catherine",
            32.9,
        )

        under_score = hours_target_score(
            pattern,
            8.5,
            7,
        )
        near_score = hours_target_score(
            pattern,
            30,
            2,
        )

        self.assertGreater(under_score, near_score)

    def test_regular_employee_gets_more_hours(self):
        catherine, _ = self.make_pattern(
            "Catherine",
            32.9,
        )
        occasional, _ = self.make_pattern(
            "Occasional",
            12,
        )

        for weekday, signature in [
            (0, "10:00-16:30"),
            (3, "18:00-22:00"),
            (4, "10:00-17:00"),
            (5, "18:00-22:30"),
        ]:
            StaffingPattern.objects.create(
                weekday=weekday,
                department="restaurant",
                shift_signature=signature,
                average_required=1,
                weeks_seen=4,
                confidence=100,
            )

        roster = RosterWeek.objects.create(
            week_start=date(2026, 8, 10)
        )
        result = generate_business_roster(
            roster,
            uncertain_threshold=0,
        )

        catherine_hours = sum(
            shift.duration_hours
            for shift in roster.shifts.filter(
                employee=catherine
            )
        )
        occasional_hours = sum(
            shift.duration_hours
            for shift in roster.shifts.filter(
                employee=occasional
            )
        )

        self.assertGreater(
            catherine_hours,
            occasional_hours,
        )
        self.assertIn("balance_changes", result)
