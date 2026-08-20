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
    is_default = models.BooleanField(
        default=False,
        help_text="Use this roster as the normal weekly starting template.",
    )
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



class ClockingPatternImport(TimeStampedModel):
    """
    One imported snapshot from the clocking application.

    These imports describe overall employee working behaviour. They must not
    be used to infer Restaurant/Kitchen/Bar eligibility because the clocking
    system does not reliably identify the department worked.
    """
    source_name = models.CharField(max_length=255, blank=True)
    period_label = models.CharField(max_length=120, blank=True)
    imported_rows = models.PositiveIntegerField(default=0)
    notes = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.source_name or f"Clocking import {self.pk}"


class EmployeeClockingPattern(TimeStampedModel):
    class WorkerType(models.TextChoices):
        CORE = "core", "Core"
        REGULAR_PART_TIME = "regular_part_time", "Regular part-time"
        OCCASIONAL = "occasional", "Occasional"
        INSUFFICIENT = "insufficient", "Insufficient history"

    employee = models.OneToOneField(
        Employee,
        on_delete=models.CASCADE,
        related_name="clocking_pattern",
    )

    import_batch = models.ForeignKey(
        ClockingPatternImport,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_patterns",
    )

    weeks_observed = models.PositiveSmallIntegerField(default=0)
    active_weeks = models.PositiveSmallIntegerField(default=0)

    average_weekly_hours = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
    )

    average_shifts_per_active_week = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
    )

    typical_start_time = models.TimeField(null=True, blank=True)
    typical_end_time = models.TimeField(null=True, blank=True)

    weekday_counts = models.JSONField(default=dict, blank=True)

    worker_type = models.CharField(
        max_length=30,
        choices=WorkerType.choices,
        default=WorkerType.INSUFFICIENT,
    )

    confidence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["employee__first_name", "employee__last_name"]

    @property
    def activity_ratio(self):
        if not self.weeks_observed:
            return 0
        return self.active_weeks / self.weeks_observed

    def __str__(self):
        return f"Clocking pattern for {self.employee}"


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


class EmployeeScheduleProfile(TimeStampedModel):
    """Manager-approved scheduling defaults. Learned patterns remain evidence, not policy."""
    employee = models.OneToOneField(
        Employee, on_delete=models.CASCADE, related_name="schedule_profile"
    )
    target_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    target_days = models.PositiveSmallIntegerField(default=0)
    preferred_department = models.CharField(
        max_length=20, choices=Department.choices, blank=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["employee__first_name", "employee__last_name"]

    def __str__(self):
        return f"Schedule profile for {self.employee}"


class AvailabilityException(TimeStampedModel):
    """A dated exception to an employee's normal/learned availability."""
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="availability_exceptions"
    )
    date = models.DateField()
    unavailable = models.BooleanField(default=False)
    available_from = models.TimeField(null=True, blank=True)
    available_until = models.TimeField(null=True, blank=True)
    note = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["date", "employee__first_name", "employee__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "date"],
                name="unique_employee_availability_exception_date",
            )
        ]

    def clean(self):
        if self.unavailable and (self.available_from or self.available_until):
            raise ValidationError(
                "An unavailable day cannot also have an availability time window."
            )
        if not self.unavailable and bool(self.available_from) != bool(self.available_until):
            raise ValidationError("Set both available-from and available-until times.")

    def __str__(self):
        if self.unavailable:
            return f"{self.employee} unavailable {self.date}"
        if self.available_from and self.available_until:
            return (
                f"{self.employee} available {self.date} "
                f"{self.available_from:%H:%M}-{self.available_until:%H:%M}"
            )
        return f"{self.employee} availability note {self.date}"



class StaffRosterNotice(TimeStampedModel):
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="roster_notices",
    )
    roster_week = models.ForeignKey(
        RosterWeek,
        on_delete=models.CASCADE,
        related_name="staff_notices",
    )
    message = models.CharField(max_length=250)
    seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.employee}: {self.message}"


class ShiftResponseStatus(models.TextChoices):
    SEEN = "seen", "Seen"
    CONFIRMED = "confirmed", "Confirmed"
    CANNOT_WORK = "cannot_work", "Cannot work"
    RESOLVED = "resolved", "Resolved"


class ShiftResponse(TimeStampedModel):
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="responses")
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="shift_responses"
    )
    status = models.CharField(
        max_length=20,
        choices=ShiftResponseStatus.choices,
        default=ShiftResponseStatus.SEEN,
    )
    reason = models.CharField(max_length=250, blank=True)
    wants_replacement_shift = models.BooleanField(
        default=False,
        help_text="Employee cannot work this shift but would like replacement hours.",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_shift_responses",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["shift", "employee"], name="unique_employee_shift_response"
            )
        ]

    def clean(self):
        if self.shift_id and self.employee_id and self.shift.employee_id != self.employee_id:
            raise ValidationError("Employees can only respond to their own assigned shift.")

    def __str__(self):
        return f"{self.employee}: {self.shift} — {self.get_status_display()}"


class OpenShiftRequestStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    DECLINED = "declined", "Declined"


class OpenShiftRequest(TimeStampedModel):
    open_shift = models.ForeignKey(
        OpenShift, on_delete=models.CASCADE, related_name="requests"
    )
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="open_shift_requests"
    )
    status = models.CharField(
        max_length=20,
        choices=OpenShiftRequestStatus.choices,
        default=OpenShiftRequestStatus.REQUESTED,
    )
    note = models.CharField(max_length=250, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="decided_open_shift_requests",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["open_shift", "employee"],
                name="unique_employee_open_shift_request",
            )
        ]

    def __str__(self):
        return f"{self.employee}: {self.open_shift} — {self.get_status_display()}"
