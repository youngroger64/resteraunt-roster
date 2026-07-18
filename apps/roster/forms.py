from django import forms
from .models import RosterWeek

class RosterWeekForm(forms.ModelForm):
    class Meta:
        model = RosterWeek
        fields = ["week_start","notes"]
        widgets = {"week_start": forms.DateInput(attrs={"type":"date"})}

class GeneratePatternRosterForm(forms.Form):
    week_start = forms.DateField(label="Week begins", widget=forms.DateInput(attrs={"type":"date"}))
    uncertain_choice = forms.ChoiceField(
        label="When the pattern is uncertain",
        choices=[
            ("blank","Leave it blank for the manager"),
            ("best","Use the best suggestion"),
        ],
        initial="blank",
    )
