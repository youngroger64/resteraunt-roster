from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Case, IntegerField, Value, When
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from apps.employees.models import Department, Employee
from .forms import GeneratePatternRosterForm, RosterWeekForm
from .models import EmployeePattern, OpenShift, RosterPurpose, RosterStatus, RosterWeek, Shift, StaffingPattern
from .services.generator import candidate_availability, copy_roster, generate_business_roster, parse_signature
from .services.learner import learn_patterns
from .services.publisher import publish_roster

@login_required
def roster_list(request):
    return render(request, "roster/list.html", {"rosters":RosterWeek.objects.all()})

@login_required
def roster_create(request):
    form = RosterWeekForm(request.POST or None)
    latest = RosterWeek.objects.filter(purpose=RosterPurpose.WEEKLY).first()
    if request.method == "POST" and form.is_valid():
        roster = form.save(commit=False)
        roster.purpose = RosterPurpose.WEEKLY
        roster.save()
        if request.POST.get("copy_latest") and latest:
            copy_roster(latest, roster)
        return redirect("roster:detail", pk=roster.pk)
    return render(request, "roster/create.html", {"form":form,"latest":latest})

@login_required
def learn(request):
    historic_count = RosterWeek.objects.filter(purpose=RosterPurpose.HISTORIC).count()
    if request.method == "POST":
        result = learn_patterns()
        messages.success(request, f"Learned {len(result['employees'])} employees and {result['staffing_patterns']} business staffing patterns.")
        return redirect("roster:patterns")
    return render(request, "roster/learn.html", {"historic_count":historic_count})

@login_required
def pattern_list(request):
    return render(request, "roster/patterns.html", {
        "patterns":EmployeePattern.objects.select_related("employee")
    })

@login_required
def generate_pattern_roster(request):
    form = GeneratePatternRosterForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        week_start = form.cleaned_data["week_start"]
        existing = RosterWeek.objects.filter(week_start=week_start).first()
        replace_existing = request.POST.get("replace_existing") == "yes"

        if existing and not replace_existing:
            return render(
                request,
                "roster/generate_patterns.html",
                {
                    "form": form,
                    "existing_roster": existing,
                },
            )

        if existing:
            if existing.status == RosterStatus.PUBLISHED:
                messages.error(
                    request,
                    "That roster is published. Choose: open it, or create another week.",
                )
                return render(
                    request,
                    "roster/generate_patterns.html",
                    {
                        "form": form,
                        "existing_roster": existing,
                        "published_existing": True,
                    },
                )

            roster = existing
            roster.shifts.all().delete()
            roster.open_shifts.all().delete()
            request.session.pop(f"open_suggestions_{roster.pk}", None)
            request.session.pop(f"unresolved_{roster.pk}", None)
        else:
            roster = RosterWeek.objects.create(
                week_start=week_start,
                purpose=RosterPurpose.WEEKLY,
            )

        threshold = (
            0
            if form.cleaned_data["uncertain_choice"] == "best"
            else 75
        )

        result = generate_business_roster(
            roster,
            uncertain_threshold=threshold,
        )
        request.session[f"open_suggestions_{roster.pk}"] = result[
            "suggestions"
        ]

        if existing:
            messages.success(
                request,
                f"Draft replaced with {result['created']} assigned shift segments. "
                f"{result['open']} shifts need a choice.",
            )
        else:
            messages.success(
                request,
                f"Generated {result['created']} assigned shift segments. "
                f"{result['open']} shifts need a choice.",
            )

        return redirect("roster:detail", pk=roster.pk)

    return render(
        request,
        "roster/generate_patterns.html",
        {"form": form},
    )

@login_required
def roster_detail(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)
    days = [roster.week_start + timedelta(days=i) for i in range(7)]

    shifts = list(roster.shifts.select_related("employee"))
    shift_map = {}
    employee_hours = {}
    scheduled_employee_ids = set()

    for shift in shifts:
        shift_map.setdefault((shift.employee_id, shift.date), []).append(shift)
        employee_hours[shift.employee_id] = (
            employee_hours.get(shift.employee_id, 0) + shift.duration_hours
        )
        scheduled_employee_ids.add(shift.employee_id)

    show_all = request.GET.get("show") == "all"

    employees = Employee.objects.filter(is_active=True)
    if not show_all:
        employees = employees.filter(pk__in=scheduled_employee_ids)

    employees = employees.annotate(
        area_order=Case(
            When(department=Department.RESTAURANT, then=Value(0)),
            When(department=Department.BAR, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
    ).order_by("area_order", "first_name", "last_name")

    open_suggestions = request.session.get(
        f"open_suggestions_{roster.pk}",
        [],
    )
    
    suggestion_map = {
        item.get("open_shift_id"): item
        for item in open_suggestions
    }
    
    open_choice_groups = {
        Department.RESTAURANT: [],
        Department.BAR: [],
    }
    
    all_active_employees = list(
        Employee.objects.filter(is_active=True).order_by(
            "first_name",
            "last_name",
        )
    )
    
    for open_shift in roster.open_shifts.all():
        suggestion = suggestion_map.get(open_shift.pk, {})
        available_ids = set(
            suggestion.get("available_employee_ids", [])
        )
    
        other_available = [
            employee
            for employee in all_active_employees
            if employee.pk in available_ids
        ]
    
        open_choice_groups.setdefault(
            open_shift.department,
            [],
        ).append(
            {
                "shift": open_shift,
                "suggestion": suggestion,
                "other_available": other_available,
            }
        )
    
    open_shift_groups = [
        {
            "department": Department.RESTAURANT,
            "label": "Restaurant",
            "items": open_choice_groups.get(
                Department.RESTAURANT,
                [],
            ),
        },
        {
            "department": Department.BAR,
            "label": "Bar",
            "items": open_choice_groups.get(
                Department.BAR,
                [],
            ),
        },
    ]
    unresolved = request.session.get(f"unresolved_{roster.pk}", [])
    unresolved_map = {
        (int(item["employee_id"]), item["date"]): item
        for item in unresolved
    }

    rows = []
    for employee in employees:
        cells = []
        for day in days:
            cells.append(
                {
                    "day": day,
                    "shifts": shift_map.get((employee.id, day), []),
                    "issue": unresolved_map.get(
                        (employee.id, day.isoformat())
                    ),
                }
            )
        rows.append(
            {
                "employee": employee,
                "cells": cells,
                "hours": round(employee_hours.get(employee.id, 0), 2),
            }
        )

    return render(
        request,
        "roster/detail.html",
        {
            "roster": roster,
            "days": days,
            "rows": rows,
            "departments": Department.choices,
            "unresolved_count": len(unresolved),
            "open_shift_groups": open_shift_groups,
            "open_suggestions": open_suggestions,
            "show_all": show_all,
            "scheduled_employee_count": len(scheduled_employee_ids),
        },
    )

@login_required
def save_cell(request, pk):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    roster = get_object_or_404(RosterWeek, pk=pk)
    employee = get_object_or_404(Employee, pk=request.POST["employee_id"])
    date = request.POST["date"]
    text = request.POST.get("shift_text","").strip()
    Shift.objects.filter(roster_week=roster, employee=employee, date=date).delete()
    if text and text.lower() not in {"off","-","none"}:
        try:
            for segment, start, end in parse_signature(text):
                Shift.objects.create(
                    roster_week=roster, employee=employee,
                    department=request.POST.get("department") or employee.department,
                    date=date, start_time=start, end_time=end, segment=segment,
                    source="manual", confidence=100,
                )
        except Exception:
            messages.error(request, "Choose: enter 09:00-17:00, enter a split shift, or type OFF.")
            return redirect("roster:detail", pk=pk)
    key = f"unresolved_{roster.pk}"
    request.session[key] = [
        i for i in request.session.get(key, [])
        if not (int(i["employee_id"]) == employee.id and i["date"] == date)
    ]
    return redirect("roster:detail", pk=pk)

@login_required
def use_suggestion(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)
    employee = get_object_or_404(Employee, pk=request.POST["employee_id"])
    date = request.POST["date"]
    suggestion = request.POST.get("suggestion","OFF")
    for segment, start, end in parse_signature(suggestion):
        Shift.objects.create(
            roster_week=roster, employee=employee, department=employee.department,
            date=date, start_time=start, end_time=end, segment=segment,
            source="learned", confidence=int(request.POST.get("confidence",0)),
        )
    key = f"unresolved_{roster.pk}"
    request.session[key] = [
        i for i in request.session.get(key, [])
        if not (int(i["employee_id"]) == employee.id and i["date"] == date)
    ]
    return redirect("roster:detail", pk=pk)

@login_required
def roster_publish(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)
    publish_roster(roster, request.user)
    return redirect("roster:detail", pk=pk)



def _employee_can_take_open_shift(roster, open_shift, employee):
    if open_shift.department == Department.BAR:
        if not employee.can_work_bar:
            return False, "This employee cannot work Bar."
    elif not employee.can_work_restaurant:
        return False, "This employee cannot work Restaurant."

    availability = candidate_availability(
        roster=roster,
        employee=employee,
        shift_date=open_shift.date,
        signature=(
            open_shift.source_signature
            or open_shift.display_time.replace("–", "-")
        ),
    )

    if not availability["available"]:
        return False, availability["reason"]

    return True, availability["reason"]


@login_required
def assign_open_shift(request, pk, open_shift_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    roster = get_object_or_404(RosterWeek, pk=pk)
    open_shift = get_object_or_404(OpenShift, pk=open_shift_id, roster_week=roster)
    employee = get_object_or_404(Employee, pk=request.POST["employee_id"])

    allowed, reason = _employee_can_take_open_shift(
        roster,
        open_shift,
        employee,
    )
    if not allowed:
        messages.warning(
            request,
            f"{employee.full_name} is not available: {reason}",
        )
        return redirect("roster:detail", pk=pk)


    parts = parse_signature(open_shift.source_signature or open_shift.display_time.replace("–", "-"))
    for segment, start, end in parts:
        Shift.objects.create(
            roster_week=roster,
            employee=employee,
            department=open_shift.department,
            date=open_shift.date,
            start_time=start,
            end_time=end,
            segment=segment,
            source="manager_choice",
            confidence=100,
        )
    open_shift.delete()
    messages.success(request, f"Assigned to {employee.full_name}.")
    return redirect("roster:detail", pk=pk)


@login_required
def assign_suggested_employee(request, pk, open_shift_id, employee_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    roster = get_object_or_404(RosterWeek, pk=pk)
    open_shift = get_object_or_404(
        OpenShift,
        pk=open_shift_id,
        roster_week=roster,
    )
    employee = get_object_or_404(Employee, pk=employee_id)

    allowed, reason = _employee_can_take_open_shift(
        roster,
        open_shift,
        employee,
    )
    if not allowed:
        messages.warning(
            request,
            f"{employee.full_name} is not available: {reason}",
        )
        return redirect("roster:detail", pk=pk)


    parts = parse_signature(
        open_shift.source_signature
        or open_shift.display_time.replace("–", "-")
    )

    for segment, start, end in parts:
        Shift.objects.create(
            roster_week=roster,
            employee=employee,
            department=open_shift.department,
            date=open_shift.date,
            start_time=start,
            end_time=end,
            segment=segment,
            source="manager_choice",
            confidence=100,
        )

    open_shift.delete()
    messages.success(request, f"Assigned to {employee.full_name}.")
    return redirect("roster:detail", pk=pk)


@login_required
def roster_delete(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)
    shift_count = roster.shifts.count()
    open_shift_count = roster.open_shifts.count()

    if request.method == "POST":
        if roster.status == RosterStatus.PUBLISHED:
            messages.error(
                request,
                "Published rosters cannot be deleted here.",
            )
            return redirect("roster:detail", pk=roster.pk)

        purpose = roster.get_purpose_display()
        label = str(roster)
        roster.delete()

        messages.success(
            request,
            f"{label} deleted. Removed {shift_count} shifts "
            f"and {open_shift_count} open shifts.",
        )

        if purpose == "Historic roster":
            messages.info(
                request,
                "Run Learning again because the historic evidence changed.",
            )

        return redirect("roster:list")

    return render(
        request,
        "roster/delete.html",
        {
            "roster": roster,
            "shift_count": shift_count,
            "open_shift_count": open_shift_count,
        },
    )


@login_required
def roster_regenerate(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)

    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    if roster.status == RosterStatus.PUBLISHED:
        messages.error(
            request,
            "Published rosters cannot be regenerated.",
        )
        return redirect("roster:detail", pk=roster.pk)

    if roster.purpose != RosterPurpose.WEEKLY:
        messages.error(
            request,
            "Historic and base rosters are evidence. Choose: delete it, or leave it unchanged.",
        )
        return redirect("roster:detail", pk=roster.pk)

    roster.shifts.all().delete()
    roster.open_shifts.all().delete()
    request.session.pop(f"open_suggestions_{roster.pk}", None)
    request.session.pop(f"unresolved_{roster.pk}", None)

    result = generate_business_roster(
        roster,
        uncertain_threshold=75,
    )
    request.session[f"open_suggestions_{roster.pk}"] = result[
        "suggestions"
    ]

    messages.success(
        request,
        f"Draft regenerated. {result['created']} assigned shift segments; "
        f"{result['open']} shifts need a choice.",
    )
    return redirect("roster:detail", pk=roster.pk)
