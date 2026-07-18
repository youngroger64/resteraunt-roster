from datetime import date, time
from django.test import TestCase
from apps.employees.models import Employee
from .models import RosterWeek, Shift

class ShiftTests(TestCase):
    def test_duration_hours(self):
        employee = Employee.objects.create(first_name="Cori")
        roster = RosterWeek.objects.create(week_start=date(2026, 6, 22))
        shift = Shift.objects.create(
            roster_week=roster, employee=employee, department="restaurant",
            date=date(2026, 6, 22), start_time=time(9), end_time=time(17)
        )
        self.assertEqual(shift.duration_hours, 8)
