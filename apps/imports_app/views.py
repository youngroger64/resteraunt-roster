from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from .forms import RosterUploadForm, UploadForm
from .services import import_employees, import_roster

@login_required
def index(request):
    return render(request, "imports/index.html")

@login_required
def employees(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        count, warnings = import_employees(form.cleaned_data["file"])
        messages.success(request, f"Imported or updated {count} employees.")
        return redirect("employees:list")
    return render(request, "imports/upload.html", {"form":form,"title":"Import employees"})

@login_required
def roster(request):
    form = RosterUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            roster_week, count, issues = import_roster(
                form.cleaned_data["file"],
                form.cleaned_data["week_start"],
                form.cleaned_data["purpose"],
            )
        except ValueError:
            messages.error(request, "No readable date. Choose a Monday below, or choose another file.")
            return render(request, "imports/upload_roster.html", {"form":form})
        messages.success(request, f"Imported {count} shift segments.")
        if issues:
            messages.warning(request, f"{len(issues)} cells were unclear and left blank. Choose: edit them, or leave them OFF.")
        return redirect("roster:detail", pk=roster_week.pk)
    return render(request, "imports/upload_roster.html", {"form":form})
