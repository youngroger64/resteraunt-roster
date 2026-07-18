from datetime import date, time
from django.test import TestCase
from apps.employees.models import Employee
from apps.roster.models import EmployeePattern, RosterWeek, Shift
from apps.roster.services.generator import candidate_availability, score_candidate

class TimeMatchingTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(
            first_name="Donal", last_name="Deegan",
            department="bar", can_work_bar=True,
            can_work_restaurant=True,
        )
        self.pattern = EmployeePattern.objects.create(
            employee=self.employee, weeks_seen=4,
            normal_department="bar", average_weekly_hours=36,
            average_days_worked=4, consistency=70,
            day_probabilities={"mon":100},
            typical_shifts={"mon":{"shift":"20:00-01:00","confidence":100}},
        )
        self.roster = RosterWeek.objects.create(week_start=date(2026,8,3))

    def test_evening_worker_not_ranked_for_morning(self):
        score = score_candidate(
            self.pattern, 0, "restaurant", "08:30-17:00",
            0, 0, {"available":True,"possible_split":False},
        )
        self.assertEqual(score, -999)

    def test_unlearned_second_shift_not_ranked(self):
        Shift.objects.create(
            roster_week=self.roster, employee=self.employee,
            department="bar", date=date(2026,8,3),
            start_time=time(8,30), end_time=time(16,30),
        )
        availability = candidate_availability(
            self.roster, self.employee, date(2026,8,3), "18:00-22:00"
        )
        score = score_candidate(
            self.pattern, 0, "bar", "18:00-22:00",
            8, 1, availability,
        )
        self.assertEqual(score, -999)
