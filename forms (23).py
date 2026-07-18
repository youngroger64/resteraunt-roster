from django import forms
from apps.roster.models import RosterPurpose

class UploadForm(forms.Form):
    file = forms.FileField(help_text="Excel .xlsx file")

class RosterUploadForm(UploadForm):
    purpose = forms.ChoiceField(
        label="Use this roster as",
        choices=[
            (RosterPurpose.HISTORIC, "Historic week — use it to learn"),
            (RosterPurpose.BASE, "Base roster — copy it when generating"),
        ],
        initial=RosterPurpose.HISTORIC,
    )
    week_start = forms.DateField(
        required=False,
        label="Week begins",
        help_text="Leave blank to read the week-ending date from the file.",
        widget=forms.DateInput(attrs={"type":"date"}),
    )
