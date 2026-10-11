from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from apps.employees.models import Employee
from apps.roster.models import RosterWeek
from .forms import PayrollUploadForm, RosterUploadForm, UploadForm
from .payroll import import_payroll
from .services import (
    RosterImportError,
    apply_roster_plan,
    import_employees,
    prepare_roster_import,
)

ROSTER_IMPORT_SESSION = "roster_import_plan"


def _format_import_issue(issue):
    employee = issue.get("employee") or "Unknown employee"
    shift_date = issue.get("date") or "unknown date"
    value = issue.get("value") or ""
    message = issue.get("message") or "Not imported"
    return f"{employee} · {shift_date} · “{value}” · {message}"


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

def _finish_roster_import(request, roster_week, count, issues):
    skipped = [issue for issue in issues if issue.get("skipped")]
    if skipped:
        messages.warning(
            request,
            (
                f"Imported {count} shift segments. "
                f"{len(skipped)} cell(s) were skipped."
            ),
        )
    else:
        messages.success(request, f"Imported {count} shift segments.")
    for issue in issues:
        messages.warning(request, _format_import_issue(issue))
    return redirect("roster:detail", pk=roster_week.pk)


def _replacement_summary(week_start):
    """Shifts and leave already on this draft, or None when there is nothing to replace."""
    roster = RosterWeek.objects.filter(week_start=week_start).first()
    if roster is None:
        return None
    shifts = roster.shifts.count()
    leave = roster.annual_leave.count()
    if not shifts and not leave:
        return None
    return {"shifts": shifts, "leave": leave}


def _unresolved_people(plan):
    return [
        person for person in plan["people"] if person["status"] == "unresolved"
    ]


def _resolution_page(request, plan, error=""):
    return render(request, "imports/resolve_names.html", {
        "plan": plan,
        "unresolved": _unresolved_people(plan),
        "employees": Employee.objects.all(),
        "replacement": _replacement_summary(plan["week_start"]),
        "error": error,
    })


def _confirm_page(request, plan, error=""):
    return render(request, "imports/confirm_replace.html", {
        "plan": plan,
        "replacement": _replacement_summary(plan["week_start"]),
        "error": error,
    })


def _choice_error(plan, resolutions):
    missing = []
    for person in _unresolved_people(plan):
        choice = resolutions.get(person["key"]) or {}
        action = str(choice.get("action") or "").strip()
        employee_id = str(choice.get("employee_id") or "").strip()
        if action not in {"use_existing", "create", "roster_only"}:
            missing.append(person["name"])
        elif action == "use_existing" and not employee_id:
            missing.append(person["name"])
    if missing:
        return "Choose an option for every name: " + ", ".join(missing) + "."
    return ""


def _hold_plan(request, plan):
    request.session[ROSTER_IMPORT_SESSION] = plan


def _apply_held_plan(request, plan, resolutions):
    try:
        roster_week, count, issues = apply_roster_plan(plan, resolutions)
    except RosterImportError as exc:
        if _unresolved_people(plan):
            return _resolution_page(request, plan, error=str(exc))
        return _confirm_page(request, plan, error=str(exc))
    except ValueError as exc:
        if _unresolved_people(plan):
            return _resolution_page(request, plan, error=str(exc))
        return _confirm_page(request, plan, error=str(exc))
    request.session.pop(ROSTER_IMPORT_SESSION, None)
    return _finish_roster_import(request, roster_week, count, issues)


@login_required
def roster(request):
    form = RosterUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            plan = prepare_roster_import(
                form.cleaned_data["file"],
                form.cleaned_data["week_start"],
                form.cleaned_data["purpose"],
            )
        except RosterImportError as exc:
            messages.error(request, str(exc))
            return render(request, "imports/upload_roster.html", {"form": form})
        except ValueError as exc:
            text = str(exc)
            if text == "No readable week date.":
                text = "No readable date. Choose a Monday below, or choose another file."
            messages.error(request, text)
            return render(request, "imports/upload_roster.html", {"form": form})
        _hold_plan(request, plan)
        if _unresolved_people(plan):
            return _resolution_page(request, plan)
        if _replacement_summary(plan["week_start"]):
            return _confirm_page(request, plan)
        return _apply_held_plan(request, plan, {})
    return render(request, "imports/upload_roster.html", {"form": form})


@login_required
def roster_resolve(request):
    plan = request.session.get(ROSTER_IMPORT_SESSION)
    if request.method != "POST" or not plan:
        if plan and request.method == "GET":
            if _unresolved_people(plan):
                return _resolution_page(request, plan)
            if _replacement_summary(plan["week_start"]):
                return _confirm_page(request, plan)
        messages.error(
            request,
            "Upload the roster again. Nothing is waiting for a decision.",
        )
        return redirect("imports:roster")
    if request.POST.get("cancel"):
        request.session.pop(ROSTER_IMPORT_SESSION, None)
        return redirect("imports:roster")

    resolutions = {}
    for person in _unresolved_people(plan):
        resolutions[person["key"]] = {
            "action": request.POST.get(f"choice-{person['key']}", ""),
            "employee_id": request.POST.get(f"employee-{person['key']}", ""),
        }
    choice_error = _choice_error(plan, resolutions)
    if choice_error:
        return _resolution_page(request, plan, error=choice_error)
    replacement = _replacement_summary(plan["week_start"])
    if replacement and request.POST.get("confirm_replace") != "yes":
        error = "Confirm that this upload replaces the existing draft."
        if _unresolved_people(plan):
            return _resolution_page(request, plan, error=error)
        return _confirm_page(request, plan, error=error)
    return _apply_held_plan(request, plan, resolutions)


@login_required
def payroll(request):
    form = PayrollUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        files = form.cleaned_data["files"]
        imported_records = 0
        imported_weeks = 0
        issue_count = 0
        for payroll_file in files:
            try:
                _week, count, issues = import_payroll(payroll_file)
            except ValueError as exc:
                messages.error(request, f"{payroll_file.name}: {exc}")
                continue
            imported_weeks += 1
            imported_records += count
            issue_count += len(issues)
        if imported_weeks:
            messages.success(request, f"Imported {imported_records} payroll records from {imported_weeks} week(s). Run Relearn now.")
        if issue_count:
            messages.warning(request, f"{issue_count} salary/blank rows were ignored for hourly targets.")
        return redirect("roster:learn")
    return render(request, "imports/upload.html", {"form": form, "title": "Import payroll history"})
