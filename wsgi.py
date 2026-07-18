from datetime import date

from django.test import TestCase

from apps.employees.models import Employee
from apps.roster.models import (
    EmployeePattern,
    RosterWeek,
    StaffingPattern,
)
from apps.roster.services.generator import generate_business_roster


class GeneratorLimitTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(
            first_name="Cori",
            last_name="Hamm",
            department="restaurant",
            can_work_restaurant=True,
            can_work_bar=False,
        )
        self.pattern = EmployeePattern.objects.create(
            employee=self.employee,
            weeks_seen=4,
            normal_department="restaurant",
            average_weekly_hours=40.25,
            average_days_worked=4.5,
            consistency=60,
            day_probabilities={
                "mon": 100,
                "tue": 100,
                "wed": 100,
                "thu": 100,
                "fri": 100,
                "sat": 100,
                "sun": 100,
            },
            typical_shifts={
                key: {
                    "shift": "08:30-16:30",
                    "confidence": 100,
                }
                for key in [
                    "mon",
                    "tue",
                    "wed",
                    "thu",
                    "fri",
                    "sat",
                    "sun",
                ]
            },
        )

        for weekday in range(7):
            StaffingPattern.objects.create(
                weekday=weekday,
                department="restaurant",
                shift_signature="08:30-16:30",
                average_required=1,
                weeks_seen=4,
                confidence=100,
            )

    def test_employee_not_assigned_more_than_target_days(self):
        roster = RosterWeek.objects.create(
            week_start=date(2026, 8, 3)
        )

        result = generate_business_roster(
            roster,
            uncertain_threshold=0,
        )

        worked_days = roster.shifts.values("date").distinct().count()
        self.assertLessEqual(worked_days, 5)
        self.assertGreaterEqual(result["open"], 2)

    def test_employee_not_pushed_far_above_normal_hours(self):
        roster = RosterWeek.objects.create(
            week_start=date(2026, 8, 10)
        )

        generate_business_roster(
            roster,
            uncertain_threshold=0,
        )

        total_hours = sum(
            shift.duration_hours for shift in roster.shifts.all()
        )
        self.assertLessEqual(total_hours, 42.75)
