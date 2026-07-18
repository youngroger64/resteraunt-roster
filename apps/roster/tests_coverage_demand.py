from datetime import date
from django.test import TestCase
from apps.employees.models import Employee
from apps.roster.models import CoveragePattern, EmployeePattern, RosterWeek, StaffingPattern
from apps.roster.services.generator import generate_business_roster

class CoverageDemandTests(TestCase):
    def setUp(self):
        data = [
            ("Catherine", "10:00-16:00"),
            ("Jack", "08:30-17:00"),
            ("Mia", "10:00-16:30"),
            ("Extra", "10:00-17:00"),
        ]
        for name, signature in data:
            employee = Employee.objects.create(
                first_name=name,
                department="restaurant",
                can_work_restaurant=True,
            )
            EmployeePattern.objects.create(
                employee=employee,
                weeks_seen=4,
                normal_department="restaurant",
                average_weekly_hours=40,
                average_days_worked=5,
                consistency=80,
                day_probabilities={"fri":100},
                typical_shifts={"fri":{"shift":signature,"confidence":100}},
            )
            StaffingPattern.objects.create(
                weekday=4,
                department="restaurant",
                shift_signature=signature,
                average_required=1,
                weeks_seen=4,
                confidence=100,
            )

        for slot in range(10*60, 16*60, 30):
            CoveragePattern.objects.create(
                weekday=4,
                department="restaurant",
                slot_minute=slot,
                average_required=3,
                weeks_seen=4,
                confidence=100,
            )
        for slot in [510, 540, 570, 960, 990]:
            CoveragePattern.objects.create(
                weekday=4,
                department="restaurant",
                slot_minute=slot,
                average_required=1,
                weeks_seen=4,
                confidence=100,
            )

    def test_redundant_fourth_shift_is_skipped(self):
        roster = RosterWeek.objects.create(week_start=date(2026,7,27))
        result = generate_business_roster(roster, uncertain_threshold=0)
        friday_people = roster.shifts.filter(
            date=date(2026,7,31)
        ).values("employee_id").distinct().count()
        self.assertEqual(friday_people, 3)
        self.assertEqual(result["open"], 0)
