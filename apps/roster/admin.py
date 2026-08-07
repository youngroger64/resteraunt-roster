from django.contrib import admin
from .models import (
    CoveragePattern,
    DailyStaffingPattern,
    OpenShift,
    PayrollRecord,
    PayrollWeek,
    RosterWeek,
    Shift,
    ShiftTemplatePattern,
    StaffingPattern,
)

class ShiftInline(admin.TabularInline):
    model = Shift
    extra = 0

@admin.register(RosterWeek)
class RosterWeekAdmin(admin.ModelAdmin):
    list_display = ("week_start", "week_end", "status", "version", "published_at")
    list_filter = ("status",)
    inlines = [ShiftInline]

@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("employee", "date", "department", "start_time", "end_time", "confidence")
    list_filter = ("department", "date", "source")
    search_fields = ("employee__first_name", "employee__last_name")


@admin.register(StaffingPattern)
class StaffingPatternAdmin(admin.ModelAdmin):
    list_display = ("weekday", "department", "shift_signature", "average_required", "confidence")
    list_filter = ("weekday", "department")


@admin.register(OpenShift)
class OpenShiftAdmin(admin.ModelAdmin):
    list_display = ("date", "department", "start_time", "end_time", "confidence")
    list_filter = ("date", "department")


@admin.register(CoveragePattern)
class CoveragePatternAdmin(admin.ModelAdmin):
    list_display = ("weekday", "department", "slot_label", "average_required", "confidence")
    list_filter = ("weekday", "department")


@admin.register(PayrollWeek)
class PayrollWeekAdmin(admin.ModelAdmin):
    list_display = ("week_end", "source_name")

@admin.register(PayrollRecord)
class PayrollRecordAdmin(admin.ModelAdmin):
    list_display = ("employee", "payroll_week", "total_hours", "ordinary_hours", "sunday_hours")
    list_filter = ("payroll_week",)
    search_fields = ("employee__first_name", "employee__last_name")


@admin.register(DailyStaffingPattern)
class DailyStaffingPatternAdmin(admin.ModelAdmin):
    list_display = (
        "weekday",
        "department",
        "typical_headcount",
        "minimum_headcount",
        "confidence",
    )
    list_filter = ("weekday", "department")



@admin.register(ShiftTemplatePattern)
class ShiftTemplatePatternAdmin(admin.ModelAdmin):
    list_display = (
        "weekday",
        "department",
        "shift_signature",
        "typical_count",
        "confidence",
    )
    list_filter = ("weekday", "department")
