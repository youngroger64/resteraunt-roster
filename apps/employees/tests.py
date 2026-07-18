from datetime import date, time
from django.test import TestCase
from apps.roster.models import RosterWeek, Shift
from .models import Employee
from .services import merge_employees


class EmployeeTests(TestCase):
    def test_full_name(self):
        employee = Employee(first_name="Cori", last_name="Test")
        self.assertEqual(employee.full_name, "Cori Test")


class EmployeeMergeTests(TestCase):
    def test_merge_moves_shifts_and_deletes_source(self):
        source = Employee.objects.create(first_name="Jack")
        target = Employee.objects.create(
            first_name="Jacqueline",
            last_name="Morrissey",
            external_id="226",
        )
        roster = RosterWeek.objects.create(week_start=date(2026, 7, 20))
        shift = Shift.objects.create(
            roster_week=roster,
            employee=source,
            department="restaurant",
            date=date(2026, 7, 20),
            start_time=time(9),
            end_time=time(17),
        )

        result = merge_employees(source, target, "keep_target")

        shift.refresh_from_db()
        self.assertEqual(shift.employee, target)
        self.assertFalse(Employee.objects.filter(pk=source.pk).exists())
        self.assertEqual(result["moved"], 1)
