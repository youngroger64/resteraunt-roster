from django.contrib import admin
from .models import RosterWeek, Shift

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
