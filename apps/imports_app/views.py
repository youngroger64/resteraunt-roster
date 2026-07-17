from datetime import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from .forms import UploadForm
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
        for warning in warnings:
            messages.warning(request, warning)
        return redirect("employees:list")
    return render(request, "imports/upload.html", {"form": form, "title": "Import employees"})

@login_required
def roster(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            week_start = datetime.strptime(request.POST.get("week_start", ""), "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, "Choose the Monday for this roster.")
            return render(request, "imports/upload_roster.html", {"form": form})
        roster_week, count, warnings = import_roster(form.cleaned_data["file"], week_start)
        messages.success(request, f"Imported {count} shift segments.")
        for warning in warnings:
            messages.warning(request, warning)
        return redirect("roster:detail", pk=roster_week.pk)
    return render(request, "imports/upload_roster.html", {"form": form})
