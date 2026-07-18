from datetime import date

from django.test import TestCase

from apps.employees.models import Employee
from apps.roster.models import (
    EmployeePattern,
    RosterWeek,
    StaffingPattern,
)
from apps.roster.services.generator import generate_business_roster


class RankedSuggestionTests(TestCase):
    def setUp(self):
        for index, name in enumerate(
            ["Fiona", "Cori", "Paige", "Catherine", "Rebecca", "Other"]
        ):
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
                consistency=70,
                day_probabilities={"mon": 100 - index * 10},
                typical_shifts={
                    "mon": {
                        "shift": "10:00-16:30",
                        "confidence": 100 - index * 5,
                    }
                },
            )

        StaffingPattern.objects.create(
            weekday=0,
            department="restaurant",
            shift_signature="10:00-16:30",
            average_required=1,
            weeks_seen=4,
            confidence=100,
        )

    def test_open_shift_contains_five_ranked_choices(self):
        roster = RosterWeek.objects.create(
            week_start=date(2026, 8, 3)
        )

        result = generate_business_roster(
            roster,
            uncertain_threshold=999,
        )

        self.assertEqual(result["open"], 1)
        self.assertEqual(len(result["suggestions"][0]["choices"]), 5)
        self.assertEqual(
            result["suggestions"][0]["choices"][0]["name"],
            "Fiona",
        )
        self.assertTrue(
            result["suggestions"][0]["choices"][0]["reasons"]
        )
