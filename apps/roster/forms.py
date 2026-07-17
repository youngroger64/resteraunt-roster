from django import forms
from .models import RosterWeek, Shift

class RosterWeekForm(forms.ModelForm):
    class Meta:
        model = RosterWeek
        fields = ["week_start", "notes"]
        widgets = {"week_start": forms.DateInput(attrs={"type": "date"})}

class ShiftForm(forms.ModelForm):
    class Meta:
        model = Shift
        fields = ["department", "date", "start_time", "end_time", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }
