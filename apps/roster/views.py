from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from apps.employees.models import Department, Employee
from .forms import RosterWeekForm
from .models import RosterStatus, RosterWeek, Shift
from .services.generator import copy_roster
from .services.publisher import publish_roster

@login_required
def roster_list(request):
    return render(request, "roster/list.html", {"rosters": RosterWeek.objects.all()})

@login_required
def roster_create(request):
    form = RosterWeekForm(request.POST or None)
    latest = RosterWeek.objects.first()
    if request.method == "POST" and form.is_valid():
        roster = form.save()
        if request.POST.get("copy_latest") and latest and latest.pk != roster.pk:
            count = copy_roster(latest, roster)
            messages.success(request, f"Draft created with {count} copied shifts.")
        else:
            messages.success(request, "Blank draft created.")
        return redirect("roster:detail", pk=roster.pk)
    return render(request, "roster/create.html", {"form": form, "latest": latest})

@login_required
def roster_detail(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)
    employees = Employee.objects.filter(is_active=True)
    days = [roster.week_start + timedelta(days=i) for i in range(7)]
    shifts = roster.shifts.select_related("employee").all()
    shift_map = {}
    employee_hours = {}
    for shift in shifts:
        shift_map.setdefault((shift.employee_id, shift.date), []).append(shift)
        employee_hours[shift.employee_id] = employee_hours.get(shift.employee_id, 0) + shift.duration_hours

    rows = []
    for employee in employees:
        cells = []
        for day in days:
            cells.append({"day": day, "shifts": shift_map.get((employee.id, day), [])})
        rows.append({"employee": employee, "cells": cells, "hours": round(employee_hours.get(employee.id, 0), 2)})

    return render(request, "roster/detail.html", {
        "roster": roster, "days": days, "rows": rows,
        "departments": Department.choices,
    })

@login_required
def save_cell(request, pk):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    roster = get_object_or_404(RosterWeek, pk=pk)
    if roster.status != RosterStatus.DRAFT:
        messages.error(request, "Published rosters cannot be edited.")
        return redirect("roster:detail", pk=pk)

    employee = get_object_or_404(Employee, pk=request.POST.get("employee_id"))
    date = request.POST.get("date")
    department = request.POST.get("department") or employee.department
    shift_text = request.POST.get("shift_text", "").strip()

    Shift.objects.filter(roster_week=roster, employee=employee, date=date).delete()
    if shift_text and shift_text.lower() not in {"off", "-", "none"}:
        parts = [x.strip() for x in shift_text.replace("&", ",").split(",") if x.strip()]
        segment = 1
        for part in parts:
            separator = "-" if "-" in part else "–" if "–" in part else None
            if not separator:
                continue
            start, end = [x.strip() for x in part.split(separator, 1)]
            def normalise(value):
                value = value.lower().replace(".", ":").replace(" ", "")
                for suffix in ("am", "pm"):
                    if value.endswith(suffix):
                        value = value[:-2]
                if ":" not in value:
                    value += ":00"
                h, m = value.split(":", 1)
                h = int(h)
                m = int(m)
                return f"{h:02d}:{m:02d}"
            try:
                Shift.objects.create(
                    roster_week=roster,
                    employee=employee,
                    department=department,
                    date=date,
                    start_time=normalise(start),
                    end_time=normalise(end),
                    segment=segment,
                    source="manual",
                    confidence=100,
                )
                segment += 1
            except (ValueError, TypeError):
                messages.error(request, f"Could not understand '{part}'. Use 09:00-17:00.")
                return redirect("roster:detail", pk=pk)

    messages.success(request, f"{employee.full_name}'s shift updated.")
    return redirect("roster:detail", pk=pk)

@login_required
def roster_publish(request, pk):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    roster = get_object_or_404(RosterWeek, pk=pk)
    publish_roster(roster, request.user)
    messages.success(request, "Roster published.")
    return redirect("roster:detail", pk=pk)

@login_required
def roster_print(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)
    return roster_detail(request, pk)
