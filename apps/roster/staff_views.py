from datetime import date, timedelta

from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone

from apps.employees.models import Employee
from .models import (
    AvailabilityException,
    OpenShift,
    OpenShiftRequest,
    OpenShiftRequestStatus,
    RosterStatus,
    Shift,
    ShiftResponse,
    ShiftResponseStatus,
    StaffRosterNotice,
    UnresolvedShift,
)
from .services.generator import candidate_availability

SESSION_KEY = "staff_employee_id"


def _staff_employee(request):
    employee_id = request.session.get(SESSION_KEY)
    if not employee_id:
        return None
    employee = Employee.objects.filter(pk=employee_id, is_active=True).first()
    if not employee:
        request.session.pop(SESSION_KEY, None)
    return employee


def staff_portal(request):
    """Employee-facing prototype that mirrors the clocking app's identify-once flow."""
    if request.method == "POST" and request.POST.get("reset_staff_session") == "yes":
        request.session.pop(SESSION_KEY, None)
        return redirect("roster:staff")

    employee = _staff_employee(request)


    if (
        employee
        and request.method == "POST"
        and request.POST.get("view_roster") == "yes"
    ):
        StaffRosterNotice.objects.filter(
            employee=employee,
            seen_at__isnull=True,
        ).update(seen_at=timezone.now())

        request.session["staff_roster_open"] = True
        return redirect("roster:staff")


    # UI-only clock state for this roster sandbox. It deliberately does not write
    # attendance/payroll records; the live clock app remains authoritative.
    if employee and request.method == "POST" and request.POST.get("clock_action"):
        action = request.POST.get("clock_action")
        key = f"test_clock_state_{employee.pk}"
        current = request.session.get(key, "CLOCKED_OUT")
        transitions = {
            ("CLOCKED_OUT", "IN"): "WORKING",
            ("WORKING", "BREAK_START"): "ON_BREAK",
            ("ON_BREAK", "BREAK_END"): "WORKING",
            ("WORKING", "OUT"): "CLOCKED_OUT",
            ("ON_BREAK", "OUT"): "CLOCKED_OUT",
        }
        next_state = transitions.get((current, action))
        if next_state:
            request.session[key] = next_state
            messages.success(request, f"Test clock state: {next_state.replace('_', ' ').title()}.")
        else:
            messages.error(request, "That clock action is not valid from the current test state.")
        return redirect("roster:staff")

    if not employee and request.method == "POST":
        external_id = request.POST.get("employee_number", "").strip()
        employee = Employee.objects.filter(
            external_id=external_id,
            is_active=True,
        ).first()
        if not employee:
            messages.error(request, "Employee number not recognised.")
        else:
            request.session[SESSION_KEY] = employee.pk
            return redirect("roster:staff")

    if not employee:
        return render(request, "roster/staff_portal.html", {"employee": None})

    today = date.today()
    horizon = today + timedelta(days=35)

    unseen_notices = list(
        StaffRosterNotice.objects.filter(
            employee=employee,
            seen_at__isnull=True,
        ).select_related("roster_week")[:5]
    )

    show_roster = request.session.pop(
        "staff_roster_open",
        False,
    )
    shifts = list(
        Shift.objects.filter(
            employee=employee,
            roster_week__status=RosterStatus.PUBLISHED,
            date__gte=today,
            date__lte=horizon,
        )
        .select_related("roster_week")
        .prefetch_related("responses")
        .order_by("date", "start_time")
    )

    area_only_shifts = list(
        UnresolvedShift.objects.filter(
            employee=employee,
            roster_week__status=RosterStatus.PUBLISHED,
            date__gte=today,
            date__lte=horizon,
        )
        .select_related("roster_week")
        .order_by("date")
    )

    response_by_shift = {
        response.shift_id: response
        for response in ShiftResponse.objects.filter(
            employee=employee,
            shift_id__in=[shift.pk for shift in shifts],
        )
    }
    shift_rows = [
        {"shift": shift, "response": response_by_shift.get(shift.pk)}
        for shift in shifts
    ]

    open_shifts = []
    for open_shift in OpenShift.objects.filter(
        roster_week__status=RosterStatus.PUBLISHED,
        date__gte=today,
        date__lte=horizon,
    ).order_by("date", "start_time"):
        signature = open_shift.source_signature or open_shift.display_time.replace("–", "-")
        availability = candidate_availability(
            roster=open_shift.roster_week,
            employee=employee,
            shift_date=open_shift.date,
            signature=signature,
        )
        if availability["available"]:
            request_obj = OpenShiftRequest.objects.filter(
                open_shift=open_shift, employee=employee
            ).first()
            open_shifts.append(
                {
                    "shift": open_shift,
                    "request": request_obj,
                    "availability_reason": availability["reason"],
                }
            )

    exceptions = AvailabilityException.objects.filter(
        employee=employee,
        date__gte=today,
        date__lte=horizon,
    )

    test_clock_state = request.session.get(f"test_clock_state_{employee.pk}", "CLOCKED_OUT")
    clock_actions = {
        "CLOCKED_OUT": ["IN"],
        "WORKING": ["BREAK_START", "OUT"],
        "ON_BREAK": ["BREAK_END", "OUT"],
    }[test_clock_state]

    return render(
        request,
        "roster/staff_portal.html",
        {
            "employee": employee,
            "test_clock_state": test_clock_state,
            "clock_actions": clock_actions,
            "shift_rows": shift_rows,
            "area_only_shifts": area_only_shifts,
            "open_shift_rows": open_shifts,
            "availability_exceptions": exceptions,
            "today": today,
            "unseen_notices": unseen_notices,
            "show_roster": show_roster,
        },
    )


@require_POST
def staff_shift_response(request, shift_id):
    employee = _staff_employee(request)
    if not employee:
        return redirect("roster:staff")

    shift = get_object_or_404(
        Shift,
        pk=shift_id,
        employee=employee,
        roster_week__status=RosterStatus.PUBLISHED,
    )
    action = request.POST.get("action")
    if action not in {ShiftResponseStatus.CONFIRMED, ShiftResponseStatus.CANNOT_WORK}:
        return HttpResponseBadRequest("Invalid response")

    response, _ = ShiftResponse.objects.update_or_create(
        shift=shift,
        employee=employee,
        defaults={
            "status": action,
            "reason": request.POST.get("reason", "").strip(),
            "wants_replacement_shift": (
                action == ShiftResponseStatus.CANNOT_WORK
                and request.POST.get("wants_replacement_shift") == "yes"
            ),
            "resolved_at": None,
            "resolved_by": None,
        },
    )
    if response.status == ShiftResponseStatus.CANNOT_WORK:
        if response.wants_replacement_shift:
            messages.warning(
                request,
                "Manager notified. We also told them you'd like another shift if possible.",
            )
        else:
            messages.warning(
                request,
                "Manager notified. Your shift stays assigned until it is rearranged.",
            )
    else:
        messages.success(request, "Shift confirmed.")
    return redirect("roster:staff")


@require_POST
def staff_open_shift_request(request, open_shift_id):
    employee = _staff_employee(request)
    if not employee:
        return redirect("roster:staff")
    open_shift = get_object_or_404(
        OpenShift,
        pk=open_shift_id,
        roster_week__status=RosterStatus.PUBLISHED,
    )
    signature = open_shift.source_signature or open_shift.display_time.replace("–", "-")
    availability = candidate_availability(
        roster=open_shift.roster_week,
        employee=employee,
        shift_date=open_shift.date,
        signature=signature,
    )
    if not availability["available"]:
        messages.error(request, f"That shift is no longer available to you: {availability['reason']}")
        return redirect("roster:staff")

    OpenShiftRequest.objects.update_or_create(
        open_shift=open_shift,
        employee=employee,
        defaults={"status": OpenShiftRequestStatus.REQUESTED},
    )
    messages.success(request, "Shift requested. The manager can approve it from the roster.")
    return redirect("roster:staff")


@require_POST
def staff_availability(request):
    employee = _staff_employee(request)
    if not employee:
        return redirect("roster:staff")

    date_text = request.POST.get("date", "")
    try:
        exception_date = date.fromisoformat(date_text)
    except ValueError:
        return HttpResponseBadRequest("Invalid date")

    if exception_date < date.today():
        messages.error(request, "Availability changes must be for today or a future date.")
        return redirect("roster:staff")

    mode = request.POST.get("mode")
    if mode == "clear":
        AvailabilityException.objects.filter(employee=employee, date=exception_date).delete()
        messages.success(request, "Normal availability restored for that date.")
        return redirect("roster:staff")

    if mode == "unavailable":
        defaults = {
            "unavailable": True,
            "available_from": None,
            "available_until": None,
            "note": request.POST.get("note", "").strip(),
        }
    elif mode == "window":
        from_text = request.POST.get("available_from", "")
        until_text = request.POST.get("available_until", "")
        if not from_text or not until_text:
            messages.error(request, "Choose both an available-from and available-until time.")
            return redirect("roster:staff")
        defaults = {
            "unavailable": False,
            "available_from": from_text,
            "available_until": until_text,
            "note": request.POST.get("note", "").strip(),
        }
    else:
        return HttpResponseBadRequest("Invalid availability mode")

    AvailabilityException.objects.update_or_create(
        employee=employee,
        date=exception_date,
        defaults=defaults,
    )
    messages.success(request, "Availability exception saved.")
    return redirect("roster:staff")
