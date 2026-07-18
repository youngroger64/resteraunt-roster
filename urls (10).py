from django import forms
from .models import Employee


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            "external_id",
            "first_name",
            "last_name",
            "department",
            "can_work_restaurant",
            "can_work_bar",
            "is_active",
            "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class EmployeeMergeForm(forms.Form):
    target_employee = forms.ModelChoiceField(
        label="Merge into",
        queryset=Employee.objects.none(),
        help_text="This employee will remain. The duplicate will be removed.",
    )
    conflict_choice = forms.ChoiceField(
        label="If both employees have a shift in the same place",
        choices=[
            ("keep_target", "Keep the selected employee's shift"),
            ("use_source", "Use the duplicate employee's shift"),
        ],
        initial="keep_target",
    )

    def __init__(self, *args, source_employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Employee.objects.filter(is_active=True)
        if source_employee:
            queryset = queryset.exclude(pk=source_employee.pk)
        self.fields["target_employee"].queryset = queryset.order_by(
            "first_name", "last_name"
        )
