from datetime import date, time, timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.employees.models import Department, Employee
from apps.roster.models import RosterWeek, Shift

class Command(BaseCommand):
    help = "Create a small demo roster."

    def handle(self, *args, **options):
        people = [
            ("Cori", Department.RESTAURANT),
            ("Jack", Department.RESTAURANT),
            ("Fiona", Department.RESTAURANT),
            ("Donal", Department.BAR),
            ("Adam", Department.BAR),
        ]
        employees = []
        for name, dept in people:
            employee, _ = Employee.objects.get_or_create(
                first_name=name,
                defaults={
                    "department": dept,
                    "can_work_restaurant": dept == Department.RESTAURANT,
                    "can_work_bar": dept == Department.BAR,
                },
            )
            employees.append(employee)

        today = date.today()
        monday = today - timedelta(days=today.weekday())
        roster, _ = RosterWeek.objects.get_or_create(week_start=monday)
        for emp in employees:
            for offset in [0, 2, 4, 5]:
                Shift.objects.get_or_create(
                    roster_week=roster,
                    employee=emp,
                    department=emp.department,
                    date=monday + timedelta(days=offset),
                    segment=1,
                    defaults={"start_time": time(9, 0), "end_time": time(17, 0), "source": "demo"},
                )
        self.stdout.write(self.style.SUCCESS("Demo data ready."))
