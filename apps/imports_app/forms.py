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


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)]


class PayrollUploadForm(forms.Form):
    files = MultipleFileField(
        label="Payroll files",
        help_text="Upload PDF, CSV, Excel or Word payroll files. You can select several at once.",
        widget=MultipleFileInput(attrs={"accept": ".pdf,.csv,.xlsx,.docx"}),
    )
