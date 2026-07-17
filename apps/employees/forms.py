from django import forms
from .models import Employee

class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            "external_id", "first_name", "last_name", "department",
            "can_work_restaurant", "can_work_bar", "is_active", "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
