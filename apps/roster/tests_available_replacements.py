from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.employees.models import Employee
from apps.roster.models import (
    AvailabilityException,
    EmployeePattern,
    RosterWeek,
    Shift,
    ShiftResponse,
    ShiftResponseStatus,
)
from apps.roster.services.generator import rank_available_replacements


WEEK = date(2026, 9, 28)
FRIDAY = date(2026, 10, 2)
SATURDAY = date(2026, 10, 3)


class AvailableReplacementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="manager",
            password="secret",
        )
        self.client.force_login(self.user)
        self.roster = RosterWeek.objects.create(week_start=WEEK)
        self.absent = Employee.objects.create(
            first_name="Absent",
            last_name="Cook",
            department="kitchen",
            can_work_kitchen=True,
        )

    def cover(self, department, day, start, end):
        shift = Shift.objects.create(
            roster_week=self.roster,
            employee=self.absent,
            department=department,
            date=day,
            start_time=time(*start),
            end_time=time(*end),
        )
        response = ShiftResponse.objects.create(
            shift=shift,
            employee=self.absent,
            status=ShiftResponseStatus.CANNOT_WORK,
        )
        return shift, response

    def names(self):
        page = self.client.get(
            reverse("roster:detail", args=[self.roster.pk])
        )
        html = page.content.decode()
        start = html.find("Available staff")
        end = html.find("Give shift to someone else")
        return html[start:end]

    def test_candidates_appear_with_no_learned_patterns(self):
        self.cover("restaurant", SATURDAY, (12, 0), (18, 0))
        Employee.objects.create(
            first_name="Free",
            last_name="Server",
            department="restaurant",
            can_work_restaurant=True,
        )
        self.assertEqual(EmployeePattern.objects.count(), 0)
        self.assertIn("Free Server", self.names())
        self.assertIn("Available staff", self.names())

    def test_unavailable_and_overlapping_staff_are_excluded(self):
        self.cover("restaurant", SATURDAY, (12, 0), (18, 0))
        busy = Employee.objects.create(
            first_name="Busy",
            last_name="Server",
            department="restaurant",
            can_work_restaurant=True,
        )
        Shift.objects.create(
            roster_week=self.roster,
            employee=busy,
            department="restaurant",
            date=SATURDAY,
            start_time=time(13, 0),
            end_time=time(17, 0),
            segment=2,
        )
        away = Employee.objects.create(
            first_name="Away",
            last_name="Server",
            department="restaurant",
            can_work_restaurant=True,
        )
        AvailabilityException.objects.create(
            employee=away,
            date=SATURDAY,
            unavailable=True,
        )
        section = self.names()
        self.assertNotIn("Busy Server", section)
        self.assertNotIn("Away Server", section)

    def test_back_to_back_staff_are_included(self):
        shift, _response = self.cover(
            "restaurant",
            SATURDAY,
            (2, 0),
            (8, 0),
        )
        neighbour = Employee.objects.create(
            first_name="Neighbour",
            last_name="Server",
            department="restaurant",
            can_work_restaurant=True,
        )
        Shift.objects.create(
            roster_week=self.roster,
            employee=neighbour,
            department="restaurant",
            date=FRIDAY,
            start_time=time(20, 0),
            end_time=time(2, 0),
            segment=2,
        )
        self.assertIn("Neighbour Server", self.names())
        self.assertEqual(shift.start_time, time(2, 0))

    def test_kitchen_and_bar_rank_by_work_area_without_a_pattern(self):
        self.cover("kitchen", SATURDAY, (12, 0), (18, 0))
        kitchen = Employee.objects.create(
            first_name="Ada",
            last_name="Chef",
            department="kitchen",
            can_work_kitchen=True,
        )
        restaurant = Employee.objects.create(
            first_name="Bea",
            last_name="Floor",
            department="restaurant",
            can_work_restaurant=True,
            can_work_kitchen=True,
        )
        bar = Employee.objects.create(
            first_name="Cal",
            last_name="Tap",
            department="bar",
            can_work_bar=True,
            can_work_kitchen=True,
        )
        Employee.objects.create(
            first_name="No",
            last_name="Kitchen",
            department="restaurant",
            can_work_restaurant=True,
            can_work_kitchen=False,
        )
        section = self.names()
        self.assertLess(section.find("Ada Chef"), section.find("Bea Floor"))
        self.assertLess(section.find("Bea Floor"), section.find("Cal Tap"))
        self.assertNotIn("No Kitchen", section)
        self.assertIn("Kitchen", section)
        self.assertEqual(EmployeePattern.objects.count(), 0)
        self.assertTrue(kitchen.can_work_kitchen)
        self.assertTrue(restaurant.can_work_kitchen)
        self.assertTrue(bar.can_work_kitchen)

    def test_bar_shift_prefers_bar_then_restaurant(self):
        self.cover("bar", SATURDAY, (18, 0), (23, 0))
        restaurant = Employee.objects.create(
            first_name="Bea",
            last_name="Floor",
            department="restaurant",
            can_work_restaurant=True,
            can_work_bar=True,
        )
        bar = Employee.objects.create(
            first_name="Cal",
            last_name="Tap",
            department="bar",
            can_work_bar=True,
        )
        section = self.names()
        self.assertLess(section.find("Cal Tap"), section.find("Bea Floor"))
        self.assertTrue(restaurant.can_work_bar)
        self.assertTrue(bar.can_work_bar)

    def test_learned_pattern_ranks_higher_inside_the_same_area(self):
        self.cover("restaurant", SATURDAY, (12, 0), (18, 0))
        plain = Employee.objects.create(
            first_name="Plain",
            last_name="Server",
            department="restaurant",
            can_work_restaurant=True,
        )
        learned = Employee.objects.create(
            first_name="Learned",
            last_name="Server",
            department="restaurant",
            can_work_restaurant=True,
        )
        EmployeePattern.objects.create(
            employee=learned,
            normal_department="restaurant",
            typical_shifts={"sat": {"shift": "12:00-18:00", "confidence": 80}},
            weeks_seen=4,
        )
        section = self.names()
        self.assertLess(
            section.find("Learned Server"),
            section.find("Plain Server"),
        )
        self.assertIn("Usually works this shift", section)
        self.assertTrue(plain.is_active)

    def test_manual_replacement_still_rejects_an_overlap(self):
        _shift, response = self.cover(
            "restaurant",
            SATURDAY,
            (12, 0),
            (18, 0),
        )
        busy = Employee.objects.create(
            first_name="Busy",
            last_name="Server",
            department="restaurant",
            can_work_restaurant=True,
        )
        Shift.objects.create(
            roster_week=self.roster,
            employee=busy,
            department="restaurant",
            date=SATURDAY,
            start_time=time(10, 0),
            end_time=time(14, 0),
            segment=2,
        )
        result = self.client.post(
            reverse(
                "roster:replace_requested_shift",
                args=[self.roster.pk, response.pk, busy.pk],
            )
        )
        self.assertEqual(result.status_code, 302)
        self.assertEqual(
            Shift.objects.get(pk=_shift.pk).employee,
            self.absent,
        )

    def test_ranker_uses_week_overlap_for_an_overnight_clash(self):
        night = Employee.objects.create(
            first_name="Night",
            last_name="Cook",
            department="kitchen",
            can_work_kitchen=True,
        )
        Shift.objects.create(
            roster_week=self.roster,
            employee=night,
            department="kitchen",
            date=FRIDAY,
            start_time=time(20, 0),
            end_time=time(2, 0),
        )
        ranked = rank_available_replacements(
            roster=self.roster,
            employees=Employee.objects.filter(is_active=True),
            patterns_by_employee={},
            department="kitchen",
            signature="01:00-09:00",
            shift_date=SATURDAY,
        )
        self.assertNotIn(night.pk, [item["employee"].pk for item in ranked])
