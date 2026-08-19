
from django.test import TestCase

from apps.roster.models import EmployeeClockingPattern
from apps.roster.services.clocking_import import classify_worker


class ClockingImportClassificationTests(TestCase):

    def test_full_time_regular_worker_is_core(self):
        result = classify_worker(
            weeks_observed=6,
            active_weeks=6,
            avg_shifts_active=4.7,
            avg_hours_active=38,
        )
        self.assertEqual(
            result,
            EmployeeClockingPattern.WorkerType.CORE,
        )

    def test_regular_two_day_worker_is_regular_part_time(self):
        result = classify_worker(
            weeks_observed=6,
            active_weeks=5,
            avg_shifts_active=2.2,
            avg_hours_active=12,
        )
        self.assertEqual(
            result,
            EmployeeClockingPattern.WorkerType.REGULAR_PART_TIME,
        )

    def test_regular_one_day_worker_is_not_discarded(self):
        result = classify_worker(
            weeks_observed=6,
            active_weeks=4,
            avg_shifts_active=1.0,
            avg_hours_active=5,
        )
        self.assertEqual(
            result,
            EmployeeClockingPattern.WorkerType.REGULAR_PART_TIME,
        )

    def test_dara_style_worker_is_occasional_not_discarded(self):
        result = classify_worker(
            weeks_observed=6,
            active_weeks=2,
            avg_shifts_active=1.0,
            avg_hours_active=4.75,
        )
        self.assertEqual(
            result,
            EmployeeClockingPattern.WorkerType.OCCASIONAL,
        )

    def test_single_week_has_insufficient_history(self):
        result = classify_worker(
            weeks_observed=6,
            active_weeks=1,
            avg_shifts_active=1.0,
            avg_hours_active=6,
        )
        self.assertEqual(
            result,
            EmployeeClockingPattern.WorkerType.INSUFFICIENT,
        )
