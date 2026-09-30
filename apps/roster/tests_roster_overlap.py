from datetime import date, time

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from apps.employees.models import Employee
from apps.roster.models import (
    EmployeePattern,
    RosterWeek,
    Shift,
    ShiftResponse,
    ShiftResponseStatus,
)
from apps.roster.services.generator import find_roster_overlap


WEEK = date(2026, 9, 28)  # Monday
FRIDAY = date(2026, 10, 2)
SATURDAY = date(2026, 10, 3)


class WeekOverlapTests(TestCase):
    def setUp(self):
        self.roster = RosterWeek.objects.create(week_start=WEEK)
        self.employee = Employee.objects.create(
            first_name="Daniel",
            last_name="Parker",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.existing = Shift.objects.create(
            roster_week=self.roster,
            employee=self.employee,
            department="restaurant",
            date=SATURDAY,
            start_time=time(12, 0),
            end_time=time(18, 0),
        )

    def overlaps(self, day, start, end, ignore_pk=None):
        return find_roster_overlap(
            self.roster,
            self.employee,
            day,
            time(*start),
            time(*end),
            ignore_pk=ignore_pk,
        )

    def test_contained_overlap(self):
        self.assertIsNotNone(self.overlaps(SATURDAY, (13, 0), (17, 0)))

    def test_overlap_at_the_beginning(self):
        self.assertIsNotNone(self.overlaps(SATURDAY, (10, 0), (13, 0)))

    def test_overlap_at_the_end(self):
        self.assertIsNotNone(self.overlaps(SATURDAY, (17, 0), (20, 0)))

    def test_identical_interval(self):
        self.assertEqual(
            self.overlaps(SATURDAY, (12, 0), (18, 0)).pk,
            self.existing.pk,
        )

    def test_back_to_back_is_allowed(self):
        self.assertIsNone(self.overlaps(SATURDAY, (18, 0), (22, 0)))

    def test_overnight_overlap_into_the_next_day(self):
        Shift.objects.create(
            roster_week=self.roster,
            employee=self.employee,
            department="restaurant",
            date=FRIDAY,
            start_time=time(20, 0),
            end_time=time(2, 0),
        )
        self.assertIsNotNone(self.overlaps(SATURDAY, (1, 0), (9, 0)))

    def test_overnight_back_to_back_is_allowed(self):
        Shift.objects.create(
            roster_week=self.roster,
            employee=self.employee,
            department="restaurant",
            date=FRIDAY,
            start_time=time(20, 0),
            end_time=time(2, 0),
        )
        self.assertIsNone(self.overlaps(SATURDAY, (2, 0), (8, 0)))

    def test_the_shift_being_edited_is_ignored(self):
        self.assertIsNone(
            self.overlaps(
                SATURDAY,
                (12, 0),
                (18, 0),
                ignore_pk=self.existing.pk,
            )
        )


class CannotWorkOverlapTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="manager",
            password="secret",
        )
        self.client.force_login(self.user)
        self.roster = RosterWeek.objects.create(week_start=WEEK)
        self.cori = Employee.objects.create(
            first_name="Cori",
            last_name="Hamm",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.daniel = Employee.objects.create(
            first_name="Daniel",
            last_name="Parker",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.free = Employee.objects.create(
            first_name="Free",
            last_name="Person",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.later = Employee.objects.create(
            first_name="Later",
            last_name="Person",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.unlearned = Employee.objects.create(
            first_name="Unlearned",
            last_name="Person",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.shift = Shift.objects.create(
            roster_week=self.roster,
            employee=self.cori,
            department="restaurant",
            date=SATURDAY,
            start_time=time(13, 0),
            end_time=time(17, 0),
        )
        Shift.objects.create(
            roster_week=self.roster,
            employee=self.daniel,
            department="restaurant",
            date=SATURDAY,
            start_time=time(12, 0),
            end_time=time(18, 0),
        )
        Shift.objects.create(
            roster_week=self.roster,
            employee=self.later,
            department="restaurant",
            date=SATURDAY,
            start_time=time(17, 0),
            end_time=time(21, 0),
        )
        for employee in (self.daniel, self.free, self.later):
            EmployeePattern.objects.create(
                employee=employee,
                normal_department="restaurant",
                weeks_seen=4,
                average_weekly_hours=20,
                average_days_worked=3,
            )
        self.response_row = ShiftResponse.objects.create(
            shift=self.shift,
            employee=self.cori,
            status=ShiftResponseStatus.CANNOT_WORK,
        )

    def replace(self, employee):
        return self.client.post(
            reverse(
                "roster:replace_requested_shift",
                args=[self.roster.pk, self.response_row.pk, employee.pk],
            )
        )

    def recommendation_names(self):
        response = self.client.get(
            reverse("roster:detail", args=[self.roster.pk])
        )
        html = response.content.decode()
        start = html.find('class="ranked-candidates"')
        end = html.find("Give shift to someone else")
        return html[start:end]

    def test_manual_replacement_rejects_an_overlap(self):
        response = self.replace(self.daniel)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.employee, self.cori)
        self.assertTrue(any(
            "Daniel Parker is already rostered during this time." == message.message
            for message in get_messages(response.wsgi_request)
        ))

    def test_manual_replacement_allows_back_to_back_without_a_pattern(self):
        Shift.objects.create(
            roster_week=self.roster,
            employee=self.unlearned,
            department="restaurant",
            date=SATURDAY,
            start_time=time(17, 0),
            end_time=time(21, 0),
            segment=2,
        )
        response = self.replace(self.unlearned)
        self.assertEqual(response.status_code, 302)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.employee, self.unlearned)

    def test_recommendations_hide_overlaps_and_keep_back_to_back(self):
        section = self.recommendation_names()
        self.assertIn("Free Person", section)
        self.assertIn("Later Person", section)
        self.assertNotIn("Daniel Parker", section)
        self.assertIn("Unlearned Person", section)


class DraftShiftOverlapTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="manager",
            password="secret",
        )
        self.client.force_login(self.user)
        self.roster = RosterWeek.objects.create(week_start=WEEK)
        self.daniel = Employee.objects.create(
            first_name="Daniel",
            last_name="Parker",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.existing = Shift.objects.create(
            roster_week=self.roster,
            employee=self.daniel,
            department="restaurant",
            date=SATURDAY,
            start_time=time(12, 0),
            end_time=time(18, 0),
        )
        self.other = Shift.objects.create(
            roster_week=self.roster,
            employee=self.daniel,
            department="restaurant",
            date=FRIDAY,
            start_time=time(20, 0),
            end_time=time(2, 0),
        )

    def test_draft_add_rejects_an_overlap(self):
        response = self.client.post(
            reverse("roster:add_shift", args=[self.roster.pk]),
            {
                "employee_id": self.daniel.pk,
                "date": SATURDAY.isoformat(),
                "start_time": "13:00",
                "end_time": "17:00",
                "department": "restaurant",
            },
        )
        self.assertEqual(Shift.objects.filter(employee=self.daniel).count(), 2)
        self.assertTrue(any(
            "Daniel Parker is already rostered during this time." == message.message
            for message in get_messages(response.wsgi_request)
        ))

    def test_draft_add_allows_back_to_back(self):
        response = self.client.post(
            reverse("roster:add_shift", args=[self.roster.pk]),
            {
                "employee_id": self.daniel.pk,
                "date": SATURDAY.isoformat(),
                "start_time": "18:00",
                "end_time": "22:00",
                "department": "restaurant",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Shift.objects.filter(
                employee=self.daniel,
                date=SATURDAY,
                start_time=time(18, 0),
                end_time=time(22, 0),
            ).exists()
        )

    def test_draft_edit_rejects_an_overlap_and_ignores_itself(self):
        response = self.client.post(
            reverse(
                "roster:edit_shift",
                args=[self.roster.pk, self.other.pk],
            ),
            {
                "employee_id": self.daniel.pk,
                "start_time": "20:00",
                "end_time": "18:00",
            },
        )
        self.other.refresh_from_db()
        self.assertEqual(self.other.date, FRIDAY)
        self.assertEqual(self.other.start_time, time(20, 0))
        self.assertTrue(any(
            "Daniel Parker is already rostered during this time." == message.message
            for message in get_messages(response.wsgi_request)
        ))

        kept = self.client.post(
            reverse(
                "roster:edit_shift",
                args=[self.roster.pk, self.existing.pk],
            ),
            {
                "employee_id": self.daniel.pk,
                "start_time": "12:30",
                "end_time": "16:00",
            },
        )
        self.assertEqual(kept.status_code, 302)
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.start_time, time(12, 30))
        self.assertEqual(self.existing.end_time, time(16, 0))


class RemainingOverlapPathTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="overlap-manager",
            password="secret",
        )
        self.client.force_login(self.user)
        self.roster = RosterWeek.objects.create(week_start=WEEK)
        self.daniel = Employee.objects.create(
            first_name="Daniel",
            last_name="Parker",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.friday = Shift.objects.create(
            roster_week=self.roster,
            employee=self.daniel,
            department="restaurant",
            date=FRIDAY,
            start_time=time(20, 0),
            end_time=time(2, 0),
        )
        self.saturday = Shift.objects.create(
            roster_week=self.roster,
            employee=self.daniel,
            department="restaurant",
            date=SATURDAY,
            start_time=time(12, 0),
            end_time=time(18, 0),
        )

    def save_cell(self, day, text):
        return self.client.post(
            reverse("roster:save_cell", args=[self.roster.pk]),
            {
                "employee_id": self.daniel.pk,
                "date": day.isoformat(),
                "department": "restaurant",
                "shift_text": text,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_draft_cell_rejects_an_overnight_overlap(self):
        response = self.save_cell(SATURDAY, "01:00-09:00")
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Daniel Parker is already rostered during this time.",
            response.json()["error"],
        )
        self.saturday.refresh_from_db()
        self.assertEqual(self.saturday.start_time, time(12, 0))
        self.assertEqual(self.saturday.end_time, time(18, 0))

    def test_draft_cell_ignores_the_shift_being_replaced(self):
        response = self.save_cell(SATURDAY, "12:30-16:00")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        saved = Shift.objects.get(employee=self.daniel, date=SATURDAY)
        self.assertEqual(saved.start_time, time(12, 30))
        self.assertEqual(saved.end_time, time(16, 0))
        self.assertTrue(Shift.objects.filter(pk=self.friday.pk).exists())

    def test_draft_cell_allows_back_to_back_after_an_overnight(self):
        self.saturday.delete()
        response = self.save_cell(SATURDAY, "02:00-08:00")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Shift.objects.filter(
                employee=self.daniel,
                date=SATURDAY,
                start_time=time(2, 0),
                end_time=time(8, 0),
            ).exists()
        )

    def test_adjust_rejects_an_overlap_and_keeps_the_request(self):
        response_row = ShiftResponse.objects.create(
            shift=self.saturday,
            employee=self.daniel,
            status=ShiftResponseStatus.CANNOT_WORK,
        )
        response = self.client.post(
            reverse(
                "roster:adjust_requested_shift",
                args=[self.roster.pk, response_row.pk],
            ),
            {"start_time": "01:00", "end_time": "09:00"},
        )
        self.assertEqual(response.status_code, 302)
        self.saturday.refresh_from_db()
        self.assertEqual(self.saturday.start_time, time(12, 0))
        self.assertEqual(self.saturday.end_time, time(18, 0))
        response_row.refresh_from_db()
        self.assertEqual(response_row.status, ShiftResponseStatus.CANNOT_WORK)
        self.assertTrue(any(
            "Daniel Parker is already rostered during this time." == message.message
            for message in get_messages(response.wsgi_request)
        ))

    def test_adjust_allows_back_to_back(self):
        early = Shift.objects.create(
            roster_week=self.roster,
            employee=self.daniel,
            department="restaurant",
            date=SATURDAY,
            start_time=time(8, 0),
            end_time=time(12, 0),
            segment=2,
        )
        response_row = ShiftResponse.objects.create(
            shift=early,
            employee=self.daniel,
            status=ShiftResponseStatus.CANNOT_WORK,
        )
        response = self.client.post(
            reverse(
                "roster:adjust_requested_shift",
                args=[self.roster.pk, response_row.pk],
            ),
            {"start_time": "18:00", "end_time": "22:00"},
        )
        self.assertEqual(response.status_code, 302)
        early.refresh_from_db()
        self.assertEqual(early.start_time, time(18, 0))
        self.assertEqual(early.end_time, time(22, 0))
        response_row.refresh_from_db()
        self.assertEqual(response_row.status, ShiftResponseStatus.RESOLVED)
