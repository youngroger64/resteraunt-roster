from datetime import datetime, timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from apps.core.models import TimeStampedModel
from apps.employees.models import Department, Employee

class RosterStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    SUPERSEDED = "superseded", "Superseded"

class RosterPurpose(models.TextChoices):
    BASE = "base", "Base roster"
    HISTORIC = "historic", "Historic roster"
    WEEKLY = "weekly", "Weekly roster"

class RosterWeek(TimeStampedModel):
    week_start = models.DateField(unique=True)
    status = models.CharField(max_length=20, choices=RosterStatus.choices, default=RosterStatus.DRAFT)
    purpose = models.CharField(max_length=20, choices=RosterPurpose.choices, default=RosterPurpose.WEEKLY)
    version = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="published_rosters"
    )

    class Meta:
        ordering = ["-week_start"]

    @property
    def week_end(self):
        return self.week_start + timedelta(days=6)

    def __str__(self):
        return f"Week ending {self.week_end:%d %B %Y}"

class Shift(TimeStampedModel):
    roster_week = models.ForeignKey(RosterWeek, on_delete=models.CASCADE, related_name="shifts")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="roster_shifts")
    department = models.CharField(max_length=20, choices=Department.choices)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    segment = models.PositiveSmallIntegerField(default=1)
    source = models.CharField(max_length=20, default="manual")
    confidence = models.PositiveSmallIntegerField(default=100)
    notes = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["date", "department", "start_time", "employee__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["roster_week", "employee", "date", "segment"],
                name="unique_shift_segment_per_employee_day",
            )
        ]

    def clean(self):
        if self.roster_week_id and not (self.roster_week.week_start <= self.date <= self.roster_week.week_end):
            raise ValidationError("Shift date must fall inside the selected roster week.")

    @property
    def duration_hours(self):
        start = datetime.combine(self.date, self.start_time)
        end = datetime.combine(self.date, self.end_time)
        if end <= start:
            end += timedelta(days=1)
        return round((end - start).total_seconds() / 3600, 2)

    @property
    def display_time(self):
        return f"{self.start_time.strftime('%H:%M')}–{self.end_time.strftime('%H:%M')}"

    def __str__(self):
        return f"{self.employee} — {self.date} {self.display_time}"

class EmployeePattern(TimeStampedModel):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="learned_pattern")
    weeks_seen = models.PositiveSmallIntegerField(default=0)
    normal_department = models.CharField(max_length=20, choices=Department.choices, blank=True)
    average_weekly_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    payroll_average_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    restaurant_target_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    bar_target_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    average_days_worked = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    consistency = models.PositiveSmallIntegerField(default=0)
    day_probabilities = models.JSONField(default=dict, blank=True)
    typical_shifts = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["employee__first_name", "employee__last_name"]

    def __str__(self):
        return f"Pattern for {self.employee}"


class StaffingPattern(TimeStampedModel):
    weekday = models.PositiveSmallIntegerField()
    department = models.CharField(max_length=20, choices=Department.choices)
    shift_signature = models.CharField(max_length=120)
    average_required = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    weeks_seen = models.PositiveSmallIntegerField(default=0)
    confidence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["weekday", "department", "shift_signature"]
        constraints = [
            models.UniqueConstraint(
                fields=["weekday", "department", "shift_signature"],
                name="unique_staffing_pattern",
            )
        ]

    def __str__(self):
        return f"{self.get_department_display()} day {self.weekday}: {self.shift_signature}"


class OpenShift(TimeStampedModel):
    roster_week = models.ForeignKey(
        RosterWeek, on_delete=models.CASCADE, related_name="open_shifts"
    )
    department = models.CharField(max_length=20, choices=Department.choices)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    source_signature = models.CharField(max_length=120, blank=True)
    confidence = models.PositiveSmallIntegerField(default=0)
    notes = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["date", "department", "start_time"]

    @property
    def display_time(self):
        return f"{self.start_time.strftime('%H:%M')}–{self.end_time.strftime('%H:%M')}"

    def __str__(self):
        return f"Open {self.get_department_display()} shift {self.date} {self.display_time}"




class ShiftTemplatePattern(TimeStampedModel):
    weekday = models.PositiveSmallIntegerField()
    department = models.CharField(
        max_length=20,
        choices=Department.choices,
    )
    shift_signature = models.CharField(max_length=120)
    typical_count = models.PositiveSmallIntegerField(default=1)
    weeks_seen = models.PositiveSmallIntegerField(default=0)
    confidence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = [
            "weekday",
            "department",
            "-confidence",
            "shift_signature",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "weekday",
                    "department",
                    "shift_signature",
                ],
                name="unique_shift_template_pattern",
            )
        ]

    def __str__(self):
        return (
            f"{self.get_department_display()} day {self.weekday}: "
            f"{self.shift_signature} x{self.typical_count}"
        )


class DailyStaffingPattern(TimeStampedModel):
    weekday = models.PositiveSmallIntegerField()
    department = models.CharField(
        max_length=20,
        choices=Department.choices,
    )
    typical_headcount = models.PositiveSmallIntegerField(default=0)
    minimum_headcount = models.PositiveSmallIntegerField(default=0)
    band_counts = models.JSONField(default=dict, blank=True)
    weeks_seen = models.PositiveSmallIntegerField(default=0)
    confidence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["weekday", "department"]
        constraints = [
            models.UniqueConstraint(
                fields=["weekday", "department"],
                name="unique_daily_staffing_pattern",
            )
        ]

    def __str__(self):
        return (
            f"{self.get_department_display()} day {self.weekday}: "
            f"{self.typical_headcount} staff"
        )


class CoveragePattern(TimeStampedModel):
    weekday = models.PositiveSmallIntegerField()
    department = models.CharField(max_length=20, choices=Department.choices)
    slot_minute = models.PositiveSmallIntegerField()
    average_required = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    weeks_seen = models.PositiveSmallIntegerField(default=0)
    confidence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["weekday", "department", "slot_minute"]
        constraints = [
            models.UniqueConstraint(
                fields=["weekday", "department", "slot_minute"],
                name="unique_coverage_pattern",
            )
        ]

    @property
    def slot_label(self):
        minute = self.slot_minute % 1440
        return f"{minute // 60:02d}:{minute % 60:02d}"

    def __str__(self):
        return (
            f"{self.get_department_display()} day {self.weekday} "
            f"{self.slot_label}: {self.average_required}"
        )


class PayrollWeek(TimeStampedModel):
    week_end = models.DateField(unique=True)
    source_name = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-week_end"]

    @property
    def week_start(self):
        return self.week_end - timedelta(days=6)

    def __str__(self):
        return f"Payroll week ending {self.week_end:%d %B %Y}"


class PayrollRecord(TimeStampedModel):
    payroll_week = models.ForeignKey(
        PayrollWeek,
        on_delete=models.CASCADE,
        related_name="records",
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="payroll_records",
    )
    ordinary_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    sunday_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    overtime_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    total_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    notes = models.CharField(max_length=500, blank=True)
    source_row = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["payroll_week__week_end", "employee__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["payroll_week", "employee"],
                name="unique_payroll_employee_week",
            )
        ]

    def __str__(self):
        return f"{self.employee} — {self.payroll_week.week_end}: {self.total_hours}h"
