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

class RosterWeek(TimeStampedModel):
    week_start = models.DateField(unique=True)
    status = models.CharField(max_length=20, choices=RosterStatus.choices, default=RosterStatus.DRAFT)
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
        if not self.roster_week_id:
            return
        if not (self.roster_week.week_start <= self.date <= self.roster_week.week_end):
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
