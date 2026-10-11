import re
from datetime import date, time
from io import BytesIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from apps.employees.models import Employee, EmployeeNameAlias
from apps.roster.models import AnnualLeave, RosterStatus, RosterWeek, Shift
from apps.roster.services.clocking_publish import (
    ClockingPublishError,
    build_clocking_roster_payload,
    publish_week_to_clocking,
)

from .services import (
    HOUSEKEEPING_NOTE,
    RosterImportError,
    UnresolvedRosterNames,
    apply_roster_plan,
    import_roster,
)

WEEK = date(2026, 10, 12)


def _workbook(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet["B2"] = "W/C 12/10/26"
    sheet["C2"] = "Monday"
    sheet["D2"] = "Tue"
    sheet["E2"] = "Wed"
    sheet["F2"] = "Thurs"
    sheet["G2"] = "Friday"
    sheet["H2"] = "Sat"
    sheet["I2"] = "Sun"
    for offset, row in enumerate(rows, start=3):
        for column, value in enumerate(row, start=2):
            sheet.cell(offset, column, value)
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    stream.name = "roster.xlsx"
    return stream


class NameResolutionTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("manager", password="secret")
        self.client.force_login(user)

    def upload(self, rows, follow=False):
        stream = _workbook(rows)
        upload = SimpleUploadedFile(
            "roster.xlsx",
            stream.getvalue(),
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
        return self.client.post(
            reverse("imports:roster"),
            {"purpose": "historic", "file": upload},
            follow=follow,
        )

    def resolve(self, choices, follow=True):
        return self.client.post(
            reverse("imports:roster_resolve"),
            choices,
            follow=follow,
        )

    def test_pending_names_do_not_create_employees_or_change_the_week(self):
        week = RosterWeek.objects.create(week_start=WEEK)
        kept = Shift.objects.create(
            roster_week=week,
            employee=Employee.objects.create(
                external_id="300",
                first_name="Zara",
                last_name="Quinn",
            ),
            department="restaurant",
            date=WEEK,
            start_time=time(9, 0),
            end_time=time(17, 0),
            source="imported",
            notes="Already on the week",
        )
        response = self.upload([
            ["Restaurant"],
            ["Chloe Martin", "11.30-16.00 hk", "AL", "off", "off", "off", "off", "off"],
        ])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing is saved until every name has a choice.")
        self.assertContains(response, "Use existing employee")
        self.assertContains(response, "Create new employee")
        self.assertContains(response, "Roster only — exclude from payroll")
        self.assertIsNone(re.search(
            r'<input[^>]*type="radio"[^>]*\bchecked\b',
            response.content.decode(),
        ))
        self.assertIsNone(re.search(
            r'<input[^>]*\bchecked\b[^>]*type="radio"',
            response.content.decode(),
        ))
        kept.refresh_from_db()
        self.assertEqual(kept.notes, "Already on the week")
        self.assertEqual(Shift.objects.count(), 1)
        self.assertEqual(AnnualLeave.objects.count(), 0)
        self.assertEqual(Employee.objects.count(), 1)
        self.assertEqual(EmployeeNameAlias.objects.count(), 0)

    def test_chloe_martin_uses_existing_chloe_and_reimports(self):
        Employee.objects.create(
            pk=62,
            external_id="235",
            first_name="Chloe",
            last_name="",
        )
        Employee.objects.create(
            external_id="300",
            first_name="Zara",
            last_name="Quinn",
        )
        response = self.upload([
            ["Restaurant"],
            ["Chloe Martin", "11.30-16.00 hk", "AL", "off", "off", "off", "off", "off"],
        ])
        self.assertContains(response, "Chloe")
        self.assertContains(response, "roster 62")
        self.assertContains(response, "clocking 235")
        self.assertContains(response, "Zara Quinn")
        self.assertContains(response, "clocking 300")
        self.assertEqual(Shift.objects.count(), 0)

        response = self.resolve({
            "choice-chloe martin": "use_existing",
            "employee-chloe martin": "62",
        })
        self.assertContains(response, "Imported 1 shift segments.")
        chloe = Employee.objects.get(pk=62)
        self.assertEqual(chloe.external_id, "235")
        self.assertEqual(chloe.first_name, "Chloe")
        self.assertEqual(Employee.objects.filter(first_name="Chloe").count(), 1)
        self.assertFalse(Employee.objects.filter(last_name="Martin").exists())
        shift = Shift.objects.get()
        self.assertEqual(shift.employee_id, 62)
        self.assertEqual(shift.notes, HOUSEKEEPING_NOTE)
        self.assertEqual(shift.department, "restaurant")
        leave = AnnualLeave.objects.get()
        self.assertEqual(leave.employee_id, 62)
        self.assertEqual(leave.date, date(2026, 10, 13))
        self.assertEqual(
            EmployeeNameAlias.objects.get().normalised_name,
            "chloe martin",
        )
        self.assertEqual(EmployeeNameAlias.objects.get().employee_id, 62)

        import_roster(_workbook([
            ["Restaurant"],
            ["Chloe Martin", "off", "off", "12.00-16.00", "off", "off", "off", "off"],
        ]))
        self.assertEqual(Employee.objects.filter(first_name="Chloe").count(), 1)
        self.assertEqual(Shift.objects.get().employee_id, 62)
        self.assertEqual(Shift.objects.get().date, date(2026, 10, 14))
        self.assertEqual(AnnualLeave.objects.count(), 0)
        chloe.refresh_from_db()
        self.assertEqual(chloe.external_id, "235")

    def test_sophia_uses_existing_sophia_and_keeps_clocking_238(self):
        Employee.objects.create(
            pk=72,
            external_id="238",
            first_name="Sophia",
            last_name="Moldovan",
        )
        response = self.upload([
            ["Restaurant"],
            ["Sophia", "off", "off", "off", "off", "off", "off", "11.00-19.30 k"],
        ])
        self.assertContains(response, "Sophia Moldovan")
        self.assertContains(response, "roster 72")
        self.assertContains(response, "clocking 238")
        self.assertEqual(Employee.objects.count(), 1)
        self.assertEqual(Shift.objects.count(), 0)

        self.resolve({
            "choice-sophia": "use_existing",
            "employee-sophia": "72",
        })
        sophia = Employee.objects.get(pk=72)
        self.assertEqual(sophia.external_id, "238")
        self.assertEqual(Employee.objects.filter(first_name="Sophia").count(), 1)
        shift = Shift.objects.get()
        self.assertEqual(shift.employee_id, 72)
        self.assertEqual(shift.department, "kitchen")
        self.assertEqual(shift.date, date(2026, 10, 18))
        self.assertEqual(
            EmployeeNameAlias.objects.get(normalised_name="sophia").employee_id,
            72,
        )

        import_roster(_workbook([
            ["Restaurant"],
            ["Sophia", "11.00-19.30 k", "off", "off", "off", "off", "off", "off"],
        ]))
        self.assertEqual(Employee.objects.filter(first_name="Sophia").count(), 1)
        sophia.refresh_from_db()
        self.assertEqual(sophia.external_id, "238")
        self.assertEqual(Shift.objects.get().employee_id, 72)
        self.assertEqual(Shift.objects.get().date, WEEK)

    def test_roster_only_reuses_dan_deegan_44_and_keeps_shift_and_leave(self):
        Employee.objects.create(
            pk=45,
            external_id="210",
            first_name="Dan",
            last_name="Deegan",
        )
        dan = Employee.objects.create(
            pk=44,
            external_id="",
            first_name="Dan",
            last_name="Deegan",
        )
        response = self.upload([
            ["Bar"],
            ["Dan Deegan", "20.00-00.30", "AL", "off", "off", "off", "off", "off"],
        ])
        self.assertContains(response, "matches more than one employee")
        self.assertContains(response, "roster 44")
        self.assertContains(response, "clocking 210")
        self.assertEqual(Shift.objects.count(), 0)
        self.assertEqual(AnnualLeave.objects.count(), 0)
        self.assertEqual(Employee.objects.filter(last_name="Deegan").count(), 2)

        self.resolve({
            "choice-dan deegan": "roster_only",
            "employee-dan deegan": "44",
        })
        dan.refresh_from_db()
        decoy = Employee.objects.get(pk=45)
        self.assertTrue(dan.exclude_from_clocking)
        self.assertEqual(dan.external_id, "")
        self.assertFalse(decoy.exclude_from_clocking)
        self.assertEqual(Shift.objects.get().employee_id, 44)
        self.assertEqual(AnnualLeave.objects.get().employee_id, 44)
        self.assertEqual(AnnualLeave.objects.get().date, date(2026, 10, 13))
        self.assertEqual(Employee.objects.filter(last_name="Deegan").count(), 2)
        self.assertEqual(
            EmployeeNameAlias.objects.get(normalised_name="dan deegan").employee_id,
            44,
        )
        payload = build_clocking_roster_payload(RosterWeek.objects.get())
        self.assertEqual(payload["shifts"], [])
        self.assertEqual(payload["annual_leave"], [])
        self.assertEqual(payload["roster_only"], [])

        import_roster(_workbook([
            ["Bar"],
            ["Dan Deegan", "off", "20.00-00.30", "off", "off", "off", "off", "off"],
        ]))
        dan.refresh_from_db()
        self.assertTrue(dan.exclude_from_clocking)
        self.assertEqual(Employee.objects.filter(last_name="Deegan").count(), 2)
        self.assertEqual(
            list(Shift.objects.values_list("employee_id", "date")),
            [(44, date(2026, 10, 13))],
        )

    def test_create_makes_a_roster_employee_without_a_clocking_id(self):
        self.upload([
            ["Restaurant"],
            ["Aimee Lennon", "11.30-16.00", "off", "off", "off", "off", "off", "off"],
        ])
        self.assertEqual(Employee.objects.count(), 0)
        self.resolve({
            "choice-aimee lennon": "create",
            "employee-aimee lennon": "",
        })
        employee = Employee.objects.get(first_name="Aimee", last_name="Lennon")
        self.assertEqual(employee.external_id, "")
        self.assertFalse(employee.exclude_from_clocking)
        self.assertEqual(Shift.objects.get().employee_id, employee.pk)
        with self.assertRaises(ClockingPublishError) as raised:
            publish_week_to_clocking(RosterWeek.objects.get())
        self.assertIn("Aimee Lennon", str(raised.exception))

        import_roster(_workbook([
            ["Restaurant"],
            ["Aimee Lennon", "off", "11.30-16.00", "off", "off", "off", "off", "off"],
        ]))
        self.assertEqual(Employee.objects.filter(first_name="Aimee").count(), 1)
        employee.refresh_from_db()
        self.assertEqual(employee.external_id, "")
        self.assertFalse(employee.exclude_from_clocking)

    def test_roster_only_can_create_an_excluded_employee(self):
        self.upload([
            ["Bar"],
            ["Niall Byrne", "18.00-23.00", "AL", "off", "off", "off", "off", "off"],
        ])
        self.resolve({
            "choice-niall byrne": "roster_only",
            "employee-niall byrne": "",
        })
        employee = Employee.objects.get(first_name="Niall", last_name="Byrne")
        self.assertTrue(employee.exclude_from_clocking)
        self.assertEqual(employee.external_id, "")
        self.assertEqual(Shift.objects.get().employee_id, employee.pk)
        self.assertEqual(AnnualLeave.objects.get().employee_id, employee.pk)
        payload = build_clocking_roster_payload(RosterWeek.objects.get())
        self.assertEqual(payload["shifts"], [])
        self.assertEqual(payload["annual_leave"], [])
        self.assertEqual(payload["roster_only"], [])

    def test_ambiguous_first_names_are_not_chosen_silently(self):
        first = Employee.objects.create(
            external_id="501",
            first_name="Sophia",
            last_name="One",
        )
        Employee.objects.create(
            external_id="502",
            first_name="Sophia",
            last_name="Two",
        )
        response = self.upload([
            ["Restaurant"],
            ["Sophia", "11.00-19.30", "off", "off", "off", "off", "off", "off"],
        ])
        self.assertContains(response, "matches more than one employee")
        self.assertContains(response, "clocking 501")
        self.assertContains(response, "clocking 502")
        self.assertEqual(Shift.objects.count(), 0)
        self.assertEqual(Employee.objects.filter(first_name="Sophia").count(), 2)

        self.resolve({
            "choice-sophia": "use_existing",
            "employee-sophia": str(first.pk),
        })
        self.assertEqual(Employee.objects.filter(first_name="Sophia").count(), 2)
        self.assertEqual(Shift.objects.get().employee_id, first.pk)
        import_roster(_workbook([
            ["Restaurant"],
            ["Sophia", "off", "11.00-19.30", "off", "off", "off", "off", "off"],
        ]))
        self.assertEqual(Employee.objects.filter(first_name="Sophia").count(), 2)
        self.assertEqual(Shift.objects.get().employee_id, first.pk)

    def test_missing_choice_writes_nothing(self):
        week = RosterWeek.objects.create(week_start=WEEK)
        kept = Shift.objects.create(
            roster_week=week,
            employee=Employee.objects.create(
                external_id="229",
                first_name="Clea",
                last_name="Whelan",
            ),
            department="restaurant",
            date=WEEK,
            start_time=time(9, 0),
            end_time=time(17, 0),
            source="imported",
        )
        self.upload([
            ["Restaurant"],
            ["Chloe Martin", "11.30-16.00", "off", "off", "off", "off", "off", "off"],
            ["Aimee Lennon", "11.30-16.00", "off", "off", "off", "off", "off", "off"],
        ])
        response = self.resolve({
            "choice-chloe martin": "create",
            "employee-chloe martin": "",
        }, follow=False)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose an option for every name: Aimee Lennon.")
        kept.refresh_from_db()
        self.assertEqual(Shift.objects.count(), 1)
        self.assertFalse(Employee.objects.filter(first_name="Chloe").exists())
        self.assertFalse(Employee.objects.filter(first_name="Aimee").exists())

    def test_conflicting_alias_does_not_change_the_week(self):
        chloe = Employee.objects.create(
            pk=62,
            external_id="235",
            first_name="Chloe",
            last_name="",
        )
        other = Employee.objects.create(
            external_id="236",
            first_name="Chloe",
            last_name="Other",
        )
        week = RosterWeek.objects.create(week_start=WEEK)
        kept = Shift.objects.create(
            roster_week=week,
            employee=chloe,
            department="restaurant",
            date=WEEK,
            start_time=time(9, 0),
            end_time=time(12, 0),
            source="imported",
        )
        with self.assertRaises(UnresolvedRosterNames) as raised:
            import_roster(_workbook([
                ["Restaurant"],
                ["Chloe Martin", "11.30-16.00", "off", "off", "off", "off", "off", "off"],
            ]))
        EmployeeNameAlias.objects.create(
            normalised_name="chloe martin",
            employee=other,
        )
        with self.assertRaises(RosterImportError):
            apply_roster_plan(raised.exception.plan, {
                "chloe martin": {"action": "use_existing", "employee_id": "62"},
            })
        kept.refresh_from_db()
        self.assertEqual(kept.start_time, time(9, 0))
        self.assertEqual(Shift.objects.count(), 1)
        self.assertEqual(EmployeeNameAlias.objects.get().employee_id, other.pk)
        self.assertEqual(Employee.objects.filter(first_name="Chloe").count(), 2)

    def test_failed_apply_rolls_back_the_week_and_new_employee(self):
        fiona = Employee.objects.create(
            external_id="207",
            first_name="Fiona",
            last_name="Conway",
        )
        week = RosterWeek.objects.create(week_start=WEEK, purpose="weekly")
        kept = Shift.objects.create(
            roster_week=week,
            employee=fiona,
            department="restaurant",
            date=WEEK,
            start_time=time(8, 30),
            end_time=time(16, 30),
            source="imported",
            notes="Before the failed import",
        )
        with mock.patch(
            "apps.imports_app.services.Shift.objects.create",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                import_roster(
                    _workbook([
                        ["Restaurant"],
                        ["Fiona Conway", "12.00-18.00", "off", "off", "off", "off", "off", "off"],
                        ["Aimee Lennon", "11.30-16.00", "AL", "off", "off", "off", "off", "off"],
                    ]),
                    resolutions={
                        "aimee lennon": {"action": "create", "employee_id": ""},
                    },
                )
        kept.refresh_from_db()
        self.assertEqual(kept.notes, "Before the failed import")
        self.assertEqual(kept.start_time, time(8, 30))
        self.assertEqual(Shift.objects.count(), 1)
        self.assertEqual(AnnualLeave.objects.count(), 0)
        self.assertFalse(Employee.objects.filter(first_name="Aimee").exists())
        self.assertEqual(EmployeeNameAlias.objects.count(), 0)
        week.refresh_from_db()
        self.assertEqual(week.purpose, "weekly")

    def test_missing_hardcoded_alias_target_does_not_create_or_replace(self):
        fiona = Employee.objects.create(
            external_id="207",
            first_name="Fiona",
            last_name="Conway",
        )
        week = RosterWeek.objects.create(week_start=WEEK)
        kept = Shift.objects.create(
            roster_week=week,
            employee=fiona,
            department="restaurant",
            date=WEEK,
            start_time=time(8, 30),
            end_time=time(16, 30),
            source="imported",
        )
        with self.assertRaises(UnresolvedRosterNames) as raised:
            import_roster(_workbook([
                ["Wash up"],
                ["Nataliia Moldovan", "10.00-14.00 hk", "off", "off", "off", "off", "off", "off"],
            ]))
        kept.refresh_from_db()
        self.assertFalse(Employee.objects.filter(last_name="Moldovan").exists())
        with self.assertRaises(RosterImportError):
            apply_roster_plan(raised.exception.plan, {
                "nataliia moldovan": {"action": "create", "employee_id": ""},
            })
        with self.assertRaises(RosterImportError):
            apply_roster_plan(raised.exception.plan, {
                "nataliia moldovan": {
                    "action": "use_existing",
                    "employee_id": str(fiona.pk),
                },
            })
        kept.refresh_from_db()
        self.assertEqual(kept.start_time, time(8, 30))
        self.assertFalse(Employee.objects.filter(last_name="Moldovan").exists())
        self.assertEqual(EmployeeNameAlias.objects.count(), 0)

    def test_published_week_is_not_replaced(self):
        employee = Employee.objects.create(
            external_id="229",
            first_name="Clea",
            last_name="Whelan",
        )
        week = RosterWeek.objects.create(
            week_start=WEEK,
            status=RosterStatus.PUBLISHED,
        )
        kept = Shift.objects.create(
            roster_week=week,
            employee=employee,
            department="restaurant",
            date=WEEK,
            start_time=time(9, 0),
            end_time=time(17, 0),
            source="manual",
            notes="Published week",
        )
        response = self.upload([
            ["Wash up"],
            ["Clea Whelan", "18.00-23.30", "off", "off", "off", "off", "off", "off"],
        ])
        self.assertContains(response, "Published rosters cannot be replaced from Excel.")
        kept.refresh_from_db()
        self.assertEqual(kept.notes, "Published week")
        self.assertEqual(Shift.objects.count(), 1)
        week.refresh_from_db()
        self.assertEqual(week.status, RosterStatus.PUBLISHED)

    def test_superseded_week_is_not_replaced(self):
        employee = Employee.objects.create(
            external_id="229",
            first_name="Clea",
            last_name="Whelan",
        )
        week = RosterWeek.objects.create(
            week_start=WEEK,
            status=RosterStatus.SUPERSEDED,
        )
        Shift.objects.create(
            roster_week=week,
            employee=employee,
            department="restaurant",
            date=WEEK,
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        with self.assertRaises(RosterImportError):
            import_roster(_workbook([
                ["Restaurant"],
                ["Clea Whelan", "18.00-23.30", "off", "off", "off", "off", "off", "off"],
            ]))
        self.assertEqual(Shift.objects.get().start_time, time(9, 0))
        week.refresh_from_db()
        self.assertEqual(week.status, RosterStatus.SUPERSEDED)

    def test_reupload_replaces_the_whole_draft_and_leaves_other_weeks(self):
        employee = Employee.objects.create(
            external_id="229",
            first_name="Clea",
            last_name="Whelan",
            exclude_from_clocking=True,
        )
        week = RosterWeek.objects.create(week_start=WEEK)
        Shift.objects.create(
            roster_week=week,
            employee=employee,
            department="restaurant",
            date=WEEK,
            segment=1,
            start_time=time(9, 0),
            end_time=time(14, 0),
            source="manual",
            notes="Added by manager",
        )
        Shift.objects.create(
            roster_week=week,
            employee=employee,
            department="restaurant",
            date=WEEK,
            segment=2,
            start_time=time(18, 0),
            end_time=time(22, 0),
            source="imported",
        )
        AnnualLeave.objects.create(
            roster_week=week,
            employee=employee,
            date=date(2026, 10, 13),
        )
        other_week = RosterWeek.objects.create(
            week_start=date(2026, 10, 5),
            status=RosterStatus.PUBLISHED,
        )
        other = Shift.objects.create(
            roster_week=other_week,
            employee=employee,
            department="restaurant",
            date=date(2026, 10, 5),
            start_time=time(9, 0),
            end_time=time(17, 0),
            source="manual",
            notes="Other week",
        )
        _roster, count, issues = import_roster(_workbook([
            ["Restaurant"],
            ["Clea Whelan", "12.00-16.00, 17.00-21.00", "AL", "off", "off", "18.00-23.30", "off", "off"],
        ]))
        self.assertEqual(count, 3)
        self.assertEqual(issues, [])
        self.assertEqual(
            list(
                Shift.objects.filter(roster_week=week)
                .order_by("date", "segment")
                .values_list("date", "segment", "source", "start_time")
            ),
            [
                (WEEK, 1, "imported", time(12, 0)),
                (WEEK, 2, "imported", time(17, 0)),
                (date(2026, 10, 16), 1, "imported", time(18, 0)),
            ],
        )
        self.assertEqual(
            list(AnnualLeave.objects.values_list("date", flat=True)),
            [date(2026, 10, 13)],
        )
        other.refresh_from_db()
        self.assertEqual(other.notes, "Other week")
        self.assertEqual(other_week.status, RosterStatus.PUBLISHED)
        employee.refresh_from_db()
        self.assertTrue(employee.exclude_from_clocking)
        week.refresh_from_db()
        self.assertEqual(week.status, RosterStatus.DRAFT)

    def test_reupload_warns_and_waits_for_confirmation(self):
        employee = Employee.objects.create(
            external_id="229",
            first_name="Clea",
            last_name="Whelan",
            exclude_from_clocking=True,
        )
        week = RosterWeek.objects.create(week_start=WEEK)
        Shift.objects.create(
            roster_week=week,
            employee=employee,
            department="restaurant",
            date=WEEK,
            start_time=time(9, 0),
            end_time=time(17, 0),
            source="manual",
            notes="Added by manager",
        )
        AnnualLeave.objects.create(
            roster_week=week,
            employee=employee,
            date=date(2026, 10, 14),
        )
        response = self.upload([
            ["Restaurant"],
            ["Clea Whelan", "off", "off", "off", "18.00-23.30", "off", "off", "off"],
        ])
        self.assertContains(response, "Replace this draft?")
        self.assertContains(response, "1 shift")
        self.assertContains(response, "1 annual leave day")
        self.assertContains(response, "Replace the existing draft shifts and annual leave")
        self.assertIsNone(re.search(
            r'name="confirm_replace"[^>]*\bchecked\b',
            response.content.decode(),
        ))
        self.assertEqual(Shift.objects.get().notes, "Added by manager")
        self.assertEqual(AnnualLeave.objects.count(), 1)

        refused = self.resolve({})
        self.assertContains(refused, "Confirm that this upload replaces the existing draft.")
        self.assertEqual(Shift.objects.get().notes, "Added by manager")
        self.assertEqual(AnnualLeave.objects.get().date, date(2026, 10, 14))

        replaced = self.resolve({"confirm_replace": "yes"})
        self.assertContains(replaced, "Imported 1 shift segments.")
        self.assertEqual(Shift.objects.get().date, date(2026, 10, 15))
        self.assertEqual(Shift.objects.get().source, "imported")
        self.assertEqual(AnnualLeave.objects.count(), 0)
        employee.refresh_from_db()
        self.assertTrue(employee.exclude_from_clocking)
        week.refresh_from_db()
        self.assertEqual(week.status, RosterStatus.DRAFT)

    def test_unresolved_reupload_confirms_before_replacing_the_draft(self):
        Employee.objects.create(
            pk=62,
            external_id="235",
            first_name="Chloe",
            last_name="",
        )
        week = RosterWeek.objects.create(week_start=WEEK)
        Shift.objects.create(
            roster_week=week,
            employee=Employee.objects.create(
                external_id="229",
                first_name="Clea",
                last_name="Whelan",
            ),
            department="restaurant",
            date=WEEK,
            segment=1,
            start_time=time(9, 0),
            end_time=time(12, 0),
            source="imported",
        )
        Shift.objects.create(
            roster_week=week,
            employee_id=Shift.objects.get().employee_id,
            department="washup",
            date=WEEK,
            segment=2,
            start_time=time(18, 0),
            end_time=time(22, 0),
            source="manual",
        )
        response = self.upload([
            ["Restaurant"],
            ["Chloe Martin", "11.30-16.00, 18.00-22.00", "off", "off", "off", "off", "off", "off"],
        ])
        self.assertContains(response, "Use existing employee")
        self.assertContains(response, "2 shifts")
        self.assertEqual(Shift.objects.count(), 2)
        self.assertFalse(Employee.objects.filter(last_name="Martin").exists())

        waiting = self.resolve({
            "choice-chloe martin": "use_existing",
            "employee-chloe martin": "62",
        }, follow=False)
        self.assertContains(waiting, "Confirm that this upload replaces the existing draft.")
        self.assertEqual(Shift.objects.count(), 2)

        self.resolve({
            "choice-chloe martin": "use_existing",
            "employee-chloe martin": "62",
            "confirm_replace": "yes",
        })
        self.assertEqual(
            list(
                Shift.objects.order_by("segment").values_list(
                    "employee_id", "segment", "start_time",
                )
            ),
            [(62, 1, time(11, 30)), (62, 2, time(18, 0))],
        )
        self.assertEqual(Employee.objects.get(pk=62).external_id, "235")
