from datetime import date, time
from django.test import TestCase
from apps.employees.models import Employee
from apps.roster.models import EmployeePattern, RosterWeek, StaffingPattern
from apps.roster.services.generator import generate_business_roster

class BusinessGeneratorTests(TestCase):
    def test_generates_high_confidence_assignment(self):
        employee = Employee.objects.create(
            first_name="Donal",
            department="bar",
            can_work_bar=True,
            can_work_restaurant=False,
        )
        EmployeePattern.objects.create(
            employee=employee,
            weeks_seen=4,
            normal_department="bar",
            average_weekly_hours=36,
            average_days_worked=4,
            consistency=80,
            day_probabilities={"mon":100},
            typical_shifts={"mon":{"shift":"17:00-23:00","confidence":100}},
        )
        StaffingPattern.objects.create(
            weekday=0,
            department="bar",
            shift_signature="17:00-23:00",
            average_required=1,
            weeks_seen=4,
            confidence=100,
        )
        roster = RosterWeek.objects.create(week_start=date(2026, 8, 3))
        result = generate_business_roster(roster)
        self.assertEqual(result["open"], 0)
        self.assertTrue(roster.shifts.filter(employee=employee).exists())
