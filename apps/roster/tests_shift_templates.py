from datetime import date, time

from django.test import TestCase

from apps.employees.models import Department, Employee
from apps.roster.models import (
    DailyStaffingPattern,
    EmployeePattern,
    RosterPurpose,
    RosterWeek,
    Shift,
    ShiftTemplatePattern,
)
from apps.roster.services.generator import (
    generate_business_roster,
    plausible_generated_signature,
)
from apps.roster.services.learner import learn_patterns


class ShiftTemplateLearningTests(TestCase):
    def test_repeated_saturday_shifts_become_templates(self):
        employees = [
            Employee.objects.create(
                first_name=f"Worker{index}",
                department=Department.RESTAURANT,
                can_work_restaurant=True,
            )
            for index in range(8)
        ]

        for week_index in range(4):
            roster = RosterWeek.objects.create(
                week_start=date(
                    2026,
                    6,
                    22 + week_index * 7,
                ),
                purpose=RosterPurpose.HISTORIC,
            )
            saturday = roster.week_start.replace(
                day=roster.week_start.day + 5
            )

            for employee in employees[:4]:
                Shift.objects.create(
                    roster_week=roster,
                    employee=employee,
                    department=Department.RESTAURANT,
                    date=saturday,
                    start_time=time(10),
                    end_time=time(17),
                )

            for employee in employees[4:]:
                Shift.objects.create(
                    roster_week=roster,
                    employee=employee,
                    department=Department.RESTAURANT,
                    date=saturday,
                    start_time=time(18),
                    end_time=time(22, 30),
                )

        learn_patterns()

        templates = ShiftTemplatePattern.objects.filter(
            weekday=5,
            department=Department.RESTAURANT,
        )

        self.assertTrue(
            templates.filter(
                shift_signature="10:00-17:00",
                typical_count=4,
            ).exists()
        )
        self.assertTrue(
            templates.filter(
                shift_signature="18:00-22:30",
                typical_count=4,
            ).exists()
        )


class TemplateFirstGenerationTests(TestCase):
    def _pattern(self, index):
        employee = Employee.objects.create(
            first_name=f"Person{index}",
            department=Department.RESTAURANT,
            can_work_restaurant=True,
        )
        EmployeePattern.objects.create(
            employee=employee,
            normal_department=Department.RESTAURANT,
            average_weekly_hours=30,
            restaurant_target_hours=30,
            average_days_worked=5,
            day_probabilities={"sat": 100},
            typical_shifts={
                "sat": {
                    "shift": "10:00-17:00",
                    "confidence": 100,
                },
            },
        )

    def test_generator_recreates_eight_person_saturday(self):
        for index in range(8):
            self._pattern(index)

        DailyStaffingPattern.objects.create(
            weekday=5,
            department=Department.RESTAURANT,
            typical_headcount=8,
            minimum_headcount=7,
            band_counts={
                "day": 4,
                "evening": 4,
            },
            weeks_seen=4,
            confidence=100,
        )
        ShiftTemplatePattern.objects.create(
            weekday=5,
            department=Department.RESTAURANT,
            shift_signature="10:00-17:00",
            typical_count=4,
            weeks_seen=4,
            confidence=100,
        )
        ShiftTemplatePattern.objects.create(
            weekday=5,
            department=Department.RESTAURANT,
            shift_signature="18:00-22:30",
            typical_count=4,
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

        saturday = date(2026, 8, 15)
        people = (
            roster.shifts.filter(
                date=saturday,
                department=Department.RESTAURANT,
            )
            .values("employee_id")
            .distinct()
            .count()
        )
        self.assertEqual(people, 8)

    def test_sixteen_hour_single_shift_is_rejected(self):
        self.assertFalse(
            plausible_generated_signature(
                "09:00-01:00"
            )
        )
