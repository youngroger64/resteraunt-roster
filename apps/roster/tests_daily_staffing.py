from datetime import date, time

from django.test import TestCase

from apps.employees.models import Department, Employee
from apps.roster.models import (
    DailyStaffingPattern,
    EmployeePattern,
    RosterPurpose,
    RosterWeek,
    Shift,
    StaffingPattern,
)
from apps.roster.services.generator import generate_business_roster
from apps.roster.services.learner import learn_patterns


class DailyStaffingDemandTests(TestCase):
    def _employee(self, name):
        employee = Employee.objects.create(
            first_name=name,
            department=Department.RESTAURANT,
            can_work_restaurant=True,
        )
        EmployeePattern.objects.create(
            employee=employee,
            normal_department=Department.RESTAURANT,
            average_weekly_hours=30,
            restaurant_target_hours=30,
            average_days_worked=5,
            day_probabilities={"thu": 100, "sat": 100},
            typical_shifts={
                "thu": {
                    "shift": "10:00-17:00",
                    "confidence": 100,
                },
                "sat": {
                    "shift": "10:00-17:00",
                    "confidence": 100,
                },
            },
        )
        return employee

    def test_learning_counts_distinct_staff_per_day(self):
        employees = [
            Employee.objects.create(
                first_name=f"Worker{i}",
                department=Department.RESTAURANT,
                can_work_restaurant=True,
            )
            for i in range(8)
        ]
        for week_offset in (0, 7, 14):
            roster = RosterWeek.objects.create(
                week_start=date(2026, 7, 6 + week_offset),
                purpose=RosterPurpose.HISTORIC,
            )
            saturday = roster.week_start.replace(
                day=roster.week_start.day + 5
            )
            for employee in employees:
                Shift.objects.create(
                    roster_week=roster,
                    employee=employee,
                    department=Department.RESTAURANT,
                    date=saturday,
                    start_time=time(10),
                    end_time=time(17),
                )

        learn_patterns()
        learned = DailyStaffingPattern.objects.get(
            weekday=5,
            department=Department.RESTAURANT,
        )
        self.assertEqual(learned.typical_headcount, 8)

    def test_generator_reaches_daily_headcount(self):
        for index in range(8):
            self._employee(f"Worker{index}")

        DailyStaffingPattern.objects.create(
            weekday=5,
            department=Department.RESTAURANT,
            typical_headcount=8,
            minimum_headcount=7,
            band_counts={"day": 8},
            weeks_seen=4,
            confidence=100,
        )
        StaffingPattern.objects.create(
            weekday=5,
            department=Department.RESTAURANT,
            shift_signature="10:00-17:00",
            average_required=1,
            weeks_seen=4,
            confidence=100,
        )

        roster = RosterWeek.objects.create(
            week_start=date(2026, 8, 10)
        )
        generate_business_roster(
            roster,
            uncertain_threshold=0,
        )

        count = (
            roster.shifts.filter(
                date=date(2026, 8, 15),
                department=Department.RESTAURANT,
            )
            .values("employee_id")
            .distinct()
            .count()
        )
        self.assertEqual(count, 8)
