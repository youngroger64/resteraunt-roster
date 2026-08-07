from datetime import date

from django.test import TestCase

from apps.employees.models import Department, Employee
from apps.roster.models import EmployeePattern, RosterWeek
from apps.roster.services.generator import rank_candidates


class SoftCandidateRankingTests(TestCase):
    def test_normal_days_and_hours_do_not_remove_available_employee(self):
        employee = Employee.objects.create(
            first_name="Available",
            last_name="Person",
            department=Department.RESTAURANT,
            can_work_restaurant=True,
        )
        pattern = EmployeePattern.objects.create(
            employee=employee,
            normal_department=Department.RESTAURANT,
            average_weekly_hours=20,
            restaurant_target_hours=20,
            average_days_worked=2,
            day_probabilities={"thu": 10},
            typical_shifts={
                "thu": {
                    "shift": "18:00-22:00",
                    "confidence": 20,
                }
            },
        )
        roster = RosterWeek.objects.create(
            week_start=date(2026, 8, 10)
        )

        ranked = rank_candidates(
            roster=roster,
            patterns=[pattern],
            weekday=3,
            department=Department.RESTAURANT,
            signature="08:30-16:30",
            current_hours={
                (employee.id, Department.RESTAURANT): 20.0
            },
            current_days={
                (employee.id, Department.RESTAURANT): 2
            },
            shift_date=date(2026, 8, 13),
        )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(
            ranked[0]["pattern"].employee_id,
            employee.id,
        )
