from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from apps.employees.models import Department, Employee
from .forms import GeneratePatternRosterForm, RosterWeekForm
from .models import EmployeePattern, OpenShift, RosterPurpose, RosterStatus, RosterWeek, Shift, StaffingPattern
from .services.generator import copy_roster, generate_business_roster, parse_signature
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
        roster, created_new = RosterWeek.objects.get_or_create(
            week_start=form.cleaned_data["week_start"],
            defaults={"purpose": RosterPurpose.WEEKLY},
        )
        if not created_new and (roster.shifts.exists() or roster.open_shifts.exists()):
            return render(
                request,
                "roster/generate_patterns.html",
                {"form": form, "existing_roster": roster},
            )

        threshold = 0 if form.cleaned_data["uncertain_choice"] == "best" else 75
        result = generate_business_roster(roster, uncertain_threshold=threshold)
        request.session[f"open_suggestions_{roster.pk}"] = result["suggestions"]
        messages.success(
            request,
            f"Generated {result['created']} assigned shift segments. "
            f"{result['open']} shifts need a manager choice.",
        )
        return redirect("roster:detail", pk=roster.pk)

    return render(request, "roster/generate_patterns.html", {"form": form})

@login_required
def roster_detail(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)
    days = [roster.week_start + timedelta(days=i) for i in range(7)]
    shift_map, hours = {}, {}
    for shift in roster.shifts.select_related("employee"):
        shift_map.setdefault((shift.employee_id, shift.date), []).append(shift)
        hours[shift.employee_id] = hours.get(shift.employee_id, 0) + shift.duration_hours

open_shifts = list(roster.open_shifts.all())
open_suggestions = request.session.get(f"open_suggestions_{roster.pk}", [])
    unresolved = request.session.get(f"unresolved_{roster.pk}", [])
    unresolved_map = {(int(i["employee_id"]), i["date"]): i for i in unresolved}
    rows = []
    for employee in Employee.objects.filter(is_active=True):
        cells = []
        for day in days:
            cells.append({
                "day":day,
                "shifts":shift_map.get((employee.id, day), []),
                "issue":unresolved_map.get((employee.id, day.isoformat())),
            })
        rows.append({"employee":employee,"cells":cells,"hours":round(hours.get(employee.id,0),2)})
    return render(request, "roster/detail.html", {
        "roster":roster,"days":days,"rows":rows,
        "departments":Department.choices,"unresolved_count":len(unresolved),"open_shifts":open_shifts,"open_suggestions":open_suggestions,
    })

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


@login_required
def assign_open_shift(request, pk, open_shift_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    roster = get_object_or_404(RosterWeek, pk=pk)
    open_shift = get_object_or_404(OpenShift, pk=open_shift_id, roster_week=roster)
    employee = get_object_or_404(Employee, pk=request.POST["employee_id"])

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
