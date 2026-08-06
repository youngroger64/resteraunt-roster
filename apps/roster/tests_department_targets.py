from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.employees.models import Department, Employee
from apps.roster.models import EmployeePattern, PayrollRecord, PayrollWeek, RosterPurpose, RosterWeek, Shift
from apps.roster.services.generator import automatic_department_eligible, target_hours
from apps.roster.services.learner import learn_patterns


class DepartmentTargetTests(TestCase):
    def test_restaurant_employee_not_auto_used_for_bar_without_history(self):
        employee = Employee.objects.create(
            first_name="Paige",
            last_name="Boyhan",
            department=Department.RESTAURANT,
            can_work_restaurant=True,
            can_work_bar=True,
        )
        pattern = EmployeePattern.objects.create(
            employee=employee,
            normal_department=Department.RESTAURANT,
            average_weekly_hours=30,
            payroll_average_hours=30,
            restaurant_target_hours=25,
            bar_target_hours=0,
        )
        self.assertFalse(automatic_department_eligible(pattern, Department.BAR))
        self.assertTrue(automatic_department_eligible(pattern, Department.RESTAURANT))

    def test_kitchen_hours_do_not_inflate_restaurant_target(self):
        employee = Employee.objects.create(
            first_name="Cori",
            last_name="Hamm",
            department=Department.RESTAURANT,
            can_work_restaurant=True,
        )
        for offset in (0, 7):
            roster = RosterWeek.objects.create(
                week_start=date(2026, 7, 6 + offset),
                purpose=RosterPurpose.HISTORIC,
            )
            Shift.objects.create(
                roster_week=roster,
                employee=employee,
                department=Department.RESTAURANT,
                date=roster.week_start,
                start_time="08:30",
                end_time="16:30",
            )
        week = PayrollWeek.objects.create(week_end=date(2026, 7, 12))
        PayrollRecord.objects.create(
            payroll_week=week,
            employee=employee,
            total_hours=Decimal("46.00"),
        )
        learn_patterns()
        pattern = EmployeePattern.objects.get(employee=employee)
        self.assertEqual(float(pattern.payroll_average_hours), 46.0)
        self.assertEqual(float(pattern.restaurant_target_hours), 8.0)
        self.assertEqual(target_hours(pattern, Department.RESTAURANT), 8.0)
