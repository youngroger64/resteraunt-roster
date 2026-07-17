from django.contrib import admin
from .models import Employee

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("full_name", "external_id", "department", "can_work_restaurant", "can_work_bar", "is_active")
    list_filter = ("department", "is_active", "can_work_restaurant", "can_work_bar")
    search_fields = ("first_name", "last_name", "external_id")
