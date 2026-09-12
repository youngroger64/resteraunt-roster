import hashlib
from datetime import date, timedelta
from io import BytesIO
from django.contrib import messages
import xlsxwriter
from openpyxl import load_workbook
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.core.exceptions import ValidationError
from django.db.models import Case, IntegerField, Value, When
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from apps.employees.models import Department, Employee
from .forms import GeneratePatternRosterForm, RosterWeekForm
from .models import (
    EmployeePattern,
    OpenShift,
    PayrollRecord,
    PayrollWeek,
    RosterPurpose,
    RosterStatus,
    RosterWeek,
    Shift,
    StaffingPattern,
    ShiftResponse,
    StaffRosterNotice,
    ShiftResponseStatus,
    EmployeeScheduleProfile,
    OpenShiftRequest,
    OpenShiftRequestStatus,
    UnresolvedShift,
)
from .services.generator import (
    candidate_availability,
    compatible,
    copy_roster,
    generate_business_roster,
    parse_signature,
    rank_candidates,
    signature_duration,
    target_hours,
)
from .services.learner import learn_patterns
from .services.publisher import publish_roster
from apps.roster.services.clocking_import import import_clocking_patterns

from apps.roster.services.generator import copied_shift_availability_issues

from apps.roster.services.generator import replacement_suggestions_for_shift
from apps.roster.services.generator import shift_band, employee_typical_band


@login_required
def roster_list(request):
    rosters = (
        RosterWeek.objects
        .exclude(purpose=RosterPurpose.BASE)
        .order_by("-week_start")
    )
    return render(
        request,
        "roster/list.html",
        {"rosters": rosters},
    )


@login_required
def roster_create(request):
    """
    Create a weekly draft from one of four explicit starting points:

    - protected default/base roster
    - previous weekly roster
    - learned generator
    - blank roster
    """
    form = RosterWeekForm(request.POST or None)

    default_roster = (
        RosterWeek.objects
        .filter(purpose=RosterPurpose.BASE)
        .order_by("-updated_at")
        .first()
    )

    latest_weekly = (
        RosterWeek.objects
        .filter(purpose=RosterPurpose.WEEKLY)
        .order_by("-week_start")
        .first()
    )

    if request.method == "POST" and form.is_valid():
        week_start = form.cleaned_data["week_start"]

        existing = RosterWeek.objects.filter(
            week_start=week_start
        ).first()

        if existing:
            messages.error(
                request,
                "A roster already exists for that week.",
            )
            return redirect(
                "roster:detail",
                pk=existing.pk,
            )

        start_mode = request.POST.get(
            "start_mode",
            "default",
        )

        roster = form.save(commit=False)
        roster.purpose = RosterPurpose.WEEKLY
        roster.status = RosterStatus.DRAFT
        roster.save()

        # ----------------------------------------------------
        # DEFAULT
        # ----------------------------------------------------
        if start_mode == "default":
            if not default_roster:
                roster.delete()
                messages.error(
                    request,
                    "No default roster has been saved yet.",
                )
                return redirect("roster:create")

            copied = copy_roster(
                default_roster,
                roster,
            )

            issues = copied_shift_availability_issues(
                roster
            )

            request.session[
                f"copied_issues_{roster.pk}"
            ] = issues

            if issues:
                messages.warning(
                    request,
                    (
                        f"Draft created from the default roster. "
                        f"{copied} shifts copied; "
                        f"{len(issues)} need attention."
                    ),
                )
            else:
                messages.success(
                    request,
                    (
                        f"Draft created from the default roster. "
                        f"{copied} shifts copied."
                    ),
                )

        # ----------------------------------------------------
        # PREVIOUS WEEK
        # ----------------------------------------------------
        elif start_mode == "previous":
            previous = (
                RosterWeek.objects
                .filter(week_start__lt=week_start)
                .exclude(purpose=RosterPurpose.BASE)
                .order_by("-week_start", "-updated_at")
                .first()
            )

            if not previous:
                roster.delete()
                messages.error(
                    request,
                    "There is no previous weekly roster to copy.",
                )
                return redirect("roster:create")

            copied = copy_roster(
                previous,
                roster,
            )

            issues = copied_shift_availability_issues(
                roster
            )

            request.session[
                f"copied_issues_{roster.pk}"
            ] = issues

            if issues:
                messages.warning(
                    request,
                    (
                        f"Draft copied from {previous}. "
                        f"{copied} shifts copied; "
                        f"{len(issues)} need attention."
                    ),
                )
            else:
                messages.success(
                    request,
                    (
                        f"Draft copied from {previous}. "
                        f"{copied} shifts copied."
                    ),
                )

        # ----------------------------------------------------
        # GENERATE FROM LEARNING
        # ----------------------------------------------------
        elif start_mode == "generated":
            result = generate_business_roster(
                roster,
                uncertain_threshold=75,
            )

            messages.success(
                request,
                (
                    f"Draft generated from learning. "
                    f"{result['created']} shifts assigned; "
                    f"{result['open']} need a manager choice."
                ),
            )

        # ----------------------------------------------------
        # BLANK
        # ----------------------------------------------------
        elif start_mode == "blank":
            messages.success(
                request,
                "Blank draft created.",
            )

        else:
            roster.delete()
            messages.error(
                request,
                "Choose how you want to start the roster.",
            )
            return redirect("roster:create")

        return redirect(
            "roster:detail",
            pk=roster.pk,
        )

    return render(
        request,
        "roster/create.html",
        {
            "form": form,
            "default_roster": default_roster,
            "latest_weekly": latest_weekly,
        },
    )

@login_required
def learn(request):
    historic_count = RosterWeek.objects.filter(
        purpose=RosterPurpose.HISTORIC
    ).count()
    payroll_week_count = PayrollWeek.objects.count()
    payroll_record_count = PayrollRecord.objects.count()

    if request.method == "POST":
        result = learn_patterns()
        messages.success(
            request,
            (
                f"Learned {len(result['employees'])} employees, "
                f"{result['shift_templates']} recurring shift templates "
                f"and {result['staffing_patterns']} fallback staffing patterns."
            ),
        )
        return redirect("roster:patterns")

    return render(
        request,
        "roster/learn.html",
        {
            "historic_count": historic_count,
            "payroll_week_count": payroll_week_count,
            "payroll_record_count": payroll_record_count,
        },
    )

@login_required
def clocking_import(request):
    from apps.roster.models import (
        ClockingPatternImport,
        EmployeeClockingPattern,
    )

    result = None

    if request.method == "POST":
        roster_file = request.FILES.get("roster_patterns")
        day_file = request.FILES.get("day_patterns")

        if not roster_file or not day_file:
            messages.error(
                request,
                "Please choose both clocking CSV files.",
            )
        else:
            try:
                result = import_clocking_patterns(
                    roster_patterns_file=roster_file,
                    day_patterns_file=day_file,
                    source_name=roster_file.name,
                )

                messages.success(
                    request,
                    (
                        f"Clocking patterns imported for "
                        f"{result['imported']} employees. "
                        f"{len(result['unmatched'])} could not be matched."
                    ),
                )
            except Exception as exc:
                messages.error(
                    request,
                    f"Clocking import failed: {exc}",
                )

    latest = ClockingPatternImport.objects.first()

    patterns = (
        EmployeeClockingPattern.objects
        .select_related("employee", "import_batch")
        .order_by(
            "worker_type",
            "employee__first_name",
            "employee__last_name",
        )
    )

    return render(
        request,
        "roster/clocking_import.html",
        {
            "latest": latest,
            "clocking_patterns": patterns,
            "result": result,
        },
    )


@login_required
def pattern_list(request):
    return render(request, "roster/patterns.html", {
        "patterns": EmployeePattern.objects.select_related("employee", "employee__schedule_profile")
    })


@login_required
def save_schedule_profile(request, employee_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    employee = get_object_or_404(Employee, pk=employee_id)
    try:
        target_hours_value = max(0, float(request.POST.get("target_hours") or 0))
        target_days_value = min(7, max(0, int(request.POST.get("target_days") or 0)))
    except ValueError:
        messages.error(request, "Target hours and days must be numbers.")
        return redirect("roster:patterns")
    preferred_department = request.POST.get("preferred_department", "")
    if preferred_department not in {
        "",
        Department.RESTAURANT,
        Department.BAR,
        Department.KITCHEN,
        Department.WASHUP,
    }:
        return HttpResponseBadRequest("Invalid department")
    EmployeeScheduleProfile.objects.update_or_create(
        employee=employee,
        defaults={
            "target_hours": target_hours_value,
            "target_days": target_days_value,
            "preferred_department": preferred_department,
            "notes": request.POST.get("notes", "").strip(),
        },
    )
    messages.success(request, f"Normal scheduling rules saved for {employee.full_name}.")
    return redirect("roster:patterns")

@login_required
def generate_pattern_roster(request):
    """
    Prepare next week's roster from the most recent previous weekly roster.

    The manager's existing roster is the baseline. Employees, departments
    and exact shift times are copied forward unchanged. The system only
    highlights dated availability problems.
    """
    form = GeneratePatternRosterForm(request.POST or None)

    source_roster = None

    week_start = None
    if form.is_bound and form.is_valid():
        week_start = form.cleaned_data["week_start"]

        source_roster = (
            RosterWeek.objects
            .filter(
                purpose=RosterPurpose.WEEKLY,
                week_start__lt=week_start,
            )
            .order_by("-week_start")
            .first()
        )
    else:
        source_roster = (
            RosterWeek.objects
            .filter(purpose=RosterPurpose.WEEKLY)
            .order_by("-week_start")
            .first()
        )

    if request.method == "POST" and form.is_valid():
        week_start = form.cleaned_data["week_start"]

        existing = RosterWeek.objects.filter(
            week_start=week_start
        ).first()

        replace_existing = (
            request.POST.get("replace_existing") == "yes"
        )

        if existing and not replace_existing:
            return render(
                request,
                "roster/generate_patterns.html",
                {
                    "form": form,
                    "existing_roster": existing,
                    "source_roster": source_roster,
                },
            )

        if existing and existing.status == RosterStatus.PUBLISHED:
            messages.error(
                request,
                "That roster has already been published and will not be changed.",
            )
            return render(
                request,
                "roster/generate_patterns.html",
                {
                    "form": form,
                    "existing_roster": existing,
                    "published_existing": True,
                    "source_roster": source_roster,
                },
            )

        source_roster = (
            RosterWeek.objects
            .filter(
                purpose=RosterPurpose.WEEKLY,
                week_start__lt=week_start,
            )
            .exclude(pk=existing.pk if existing else None)
            .order_by("-week_start")
            .first()
        )

        if source_roster is None:
            messages.error(
                request,
                "There is no previous weekly roster to use as the starting point.",
            )
            return render(
                request,
                "roster/generate_patterns.html",
                {
                    "form": form,
                    "source_roster": None,
                },
            )

        if existing:
            roster = existing
            roster.shifts.all().delete()
            roster.open_shifts.all().delete()
        else:
            roster = RosterWeek.objects.create(
                week_start=week_start,
                purpose=RosterPurpose.WEEKLY,
            )

        copied = copy_roster(source_roster, roster)

        issues = copied_shift_availability_issues(roster)
        request.session[f"copied_issues_{roster.pk}"] = issues

        roster.notes = (
            f"Prepared from {source_roster}. "
            f"Original employees and shift times copied forward."
        )
        roster.save(update_fields=["notes"])

        if issues:
            messages.warning(
                request,
                (
                    f"Next week prepared. {copied} shifts kept from the "
                    f"previous roster; {len(issues)} need attention."
                ),
            )
        else:
            messages.success(
                request,
                (
                    f"Next week prepared. {copied} shifts copied from the "
                    f"previous roster with no availability problems."
                ),
            )

        return redirect("roster:detail", pk=roster.pk)

    return render(
        request,
        "roster/generate_patterns.html",
        {
            "form": form,
            "source_roster": source_roster,
        },
    )

@login_required
def roster_detail(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)

    copied_issues = request.session.get(
        f"copied_issues_{pk}",
        [],
    )

    # Attach replacement suggestions to genuine copied-roster
    # availability problems.
    for issue in copied_issues:
        problem_shift = roster.shifts.filter(
            pk=issue.get("shift_id")
        ).first()

        if problem_shift is None:
            issue["suggestions"] = []
            continue

        issue["suggestions"] = replacement_suggestions_for_shift(
            roster,
            problem_shift,
            limit=5,
        )
    days = [roster.week_start + timedelta(days=i) for i in range(7)]

    shifts = list(roster.shifts.select_related("employee"))
    shift_map = {}
    employee_hours = {}
    scheduled_employee_ids = set()

    unresolved_employee_ids = set(
        roster.unresolved_shifts.values_list(
            "employee_id",
            flat=True,
        )
    )

    for shift in shifts:
        shift_map.setdefault((shift.employee_id, shift.date), []).append(shift)
        employee_hours[shift.employee_id] = (
            employee_hours.get(shift.employee_id, 0) + shift.duration_hours
        )
        scheduled_employee_ids.add(shift.employee_id)

    show_all = request.GET.get("show") == "all"

    visible_employee_ids = (
        scheduled_employee_ids
        | unresolved_employee_ids
    )

    if show_all:
        # Show all currently active staff plus anyone already
        # scheduled on this roster, even if their employee
        # record has since been marked inactive.
        active_ids = set(
            Employee.objects.filter(is_active=True)
            .values_list("pk", flat=True)
        )
        employees = Employee.objects.filter(
            pk__in=(active_ids | visible_employee_ids)
        )
    else:
        # A scheduled employee must always appear on the roster.
        employees = Employee.objects.filter(
            pk__in=visible_employee_ids
        )

    employees = employees.annotate(
        area_order=Case(
            When(department=Department.RESTAURANT, then=Value(0)),
            When(department=Department.BAR, then=Value(1)),
            When(department=Department.KITCHEN, then=Value(2)),
            When(department=Department.WASHUP, then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        )
    ).order_by(
        "area_order",
        "first_name",
        "last_name",
    )

    patterns = list(
        EmployeePattern.objects.select_related("employee")
    )
    patterns_by_employee = {
        pattern.employee_id: pattern
        for pattern in patterns
    }

    current_hours = {}
    current_days = {}
    employee_days = set()
    for shift in shifts:
        key = (shift.employee_id, shift.department)
        current_hours[key] = current_hours.get(key, 0.0) + shift.duration_hours
        employee_days.add((shift.employee_id, shift.department, shift.date))

    for employee_id, department, shift_date in employee_days:
        key = (employee_id, department)
        current_days[key] = current_days.get(key, 0) + 1

    open_choice_groups = {
        Department.RESTAURANT: [],
        Department.BAR: [],
        Department.KITCHEN: [],
        Department.WASHUP: [],
    }

    for open_shift in roster.open_shifts.all():
        ranked = rank_candidates(
            roster=roster,
            patterns=patterns,
            weekday=open_shift.date.weekday(),
            department=open_shift.department,
            signature=(
                open_shift.source_signature
                or open_shift.display_time.replace("–", "-")
            ),
            current_hours=current_hours,
            current_days=current_days,
            shift_date=open_shift.date,
        )

        top_choices = [
            {
                "employee_id": item["pattern"].employee_id,
                "name": item["pattern"].employee.full_name,
                "reasons": item["reasons"],
                "possible_split": item["possible_split"],
            }
            for item in ranked[:5]
        ]

        other_available = [
            item["pattern"].employee
            for item in ranked[5:]
        ]

        open_choice_groups.setdefault(
            open_shift.department,
            [],
        ).append(
            {
                "shift": open_shift,
                "choices": top_choices,
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
        {
            "department": Department.KITCHEN,
            "label": "Kitchen",
            "items": open_choice_groups.get(
                Department.KITCHEN,
                [],
            ),
        },
        {
            "department": Department.WASHUP,
            "label": "Wash up",
            "items": open_choice_groups.get(
                Department.WASHUP,
                [],
            ),
        },
    ]
    # Persistent incomplete assignments, e.g. manager wrote "Kitchen"
    # but did not provide actual start/end times.
    unresolved = list(
        roster.unresolved_shifts
        .select_related("employee")
        .order_by("date", "employee__first_name", "employee__last_name")
    )

    unresolved_map = {
        (item.employee_id, item.date): item
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
                        (employee.id, day)
                    ),
                }
            )

        pattern = patterns_by_employee.get(employee.id)
        worked_hours = round(
            employee_hours.get(employee.id, 0),
            2,
        )

        if pattern:
            learned_target = round(float(target_hours(pattern, employee.department)), 1)
            payroll_target = round(float(pattern.payroll_average_hours), 1)
        else:
            learned_target = None
            payroll_target = None

        if learned_target is None or learned_target < 8:
            allocation_status = ""
        elif worked_hours < learned_target * 0.80:
            allocation_status = "Needs hours"
        elif worked_hours > learned_target + 2.5:
            allocation_status = "Over target"
        else:
            allocation_status = "On target"

        rows.append(
            {
                "employee": employee,
                "cells": cells,
                "hours": worked_hours,
                "target_hours": learned_target,
                "payroll_target_hours": payroll_target,
                "allocation_status": allocation_status,
            }
        )

    # Employee requests that need manager attention. The assigned shift remains
    # unchanged until the manager explicitly chooses a replacement.
    shift_change_requests = []
    for response in ShiftResponse.objects.filter(
        shift__roster_week=roster,
        status=ShiftResponseStatus.CANNOT_WORK,
    ).select_related("shift", "employee"):
        shift = response.shift
        signature = f"{shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')}"
        ranked = rank_candidates(
            roster=roster,
            patterns=patterns,
            weekday=shift.date.weekday(),
            department=shift.department,
            signature=signature,
            current_hours=current_hours,
            current_days=current_days,
            shift_date=shift.date,
        )
        choices = []
        for item in ranked:
            candidate = item["pattern"].employee
            if candidate.pk == response.employee_id:
                continue
            choices.append({
                "employee_id": candidate.pk,
                "name": candidate.full_name,
                "reasons": item["reasons"],
            })
            if len(choices) == 5:
                break
        shift_change_requests.append({
            "response": response,
            "shift": shift,
            "choices": choices,
        })


    # --------------------------------------------------------
    # Optional requests from staff who lost a shift but asked
    # to keep their hours. These are NOT roster problems.
    #
    # Group by employee and distinguish:
    #   GOOD MATCH  = resembles their normal/lost shift times
    #   POSSIBLE    = technically available, but unusual
    # --------------------------------------------------------
    replacement_hour_requests = []

    hour_responses = list(
        ShiftResponse.objects
        .filter(
            shift__roster_week=roster,
            status=ShiftResponseStatus.RESOLVED,
            wants_replacement_shift=True,
        )
        .select_related("employee", "shift")
        .order_by(
            "employee__first_name",
            "employee__last_name",
            "shift__date",
        )
    )

    responses_by_employee = {}

    for response in hour_responses:
        responses_by_employee.setdefault(
            response.employee_id,
            [],
        ).append(response)

    for employee_id, responses in responses_by_employee.items():
        employee = responses[0].employee
        pattern = patterns_by_employee.get(employee_id)

        lost_shifts = [response.shift for response in responses]

        lost_hours = sum(
            float(shift.duration_hours)
            for shift in lost_shifts
        )

        lost_bands = set()

        for lost_shift in lost_shifts:
            lost_signature = (
                f"{lost_shift.start_time.strftime('%H:%M')}-"
                f"{lost_shift.end_time.strftime('%H:%M')}"
            )
            lost_bands.add(
                shift_band(lost_signature)
            )

        possible = []

        for open_shift in roster.open_shifts.all().order_by(
            "date",
            "start_time",
        ):
            if not compatible(employee, open_shift.department):
                continue

            signature = (
                open_shift.source_signature
                or open_shift.display_time.replace("–", "-")
            )

            availability = candidate_availability(
                roster=roster,
                employee=employee,
                shift_date=open_shift.date,
                signature=signature,
            )

            if not availability["available"]:
                continue

            proposed_band = shift_band(signature)

            score = 0
            reasons = []

            # Strongest signal: compare real start times with shifts
            # the employee gave up. Evening/closing shifts should still
            # match even if one crosses our "evening" / "late" boundary.
            proposed_start_minutes = (
                open_shift.start_time.hour * 60
                + open_shift.start_time.minute
            )

            closest_start_difference = None

            for lost_shift in lost_shifts:
                if lost_shift.department != open_shift.department:
                    continue

                lost_start_minutes = (
                    lost_shift.start_time.hour * 60
                    + lost_shift.start_time.minute
                )

                difference = abs(
                    proposed_start_minutes - lost_start_minutes
                )

                if (
                    closest_start_difference is None
                    or difference < closest_start_difference
                ):
                    closest_start_difference = difference

            if (
                closest_start_difference is not None
                and closest_start_difference <= 180
            ):
                score += 120
                reasons.append(
                    "Similar time to the shift they gave up"
                )

            # Existing band comparison remains useful as a secondary signal.
            elif proposed_band in lost_bands:
                score += 100
                reasons.append(
                    "Similar time to the shift they gave up"
                )

            # Also compare against their learned normal time for that day.
            if pattern:
                normal_band = employee_typical_band(
                    pattern,
                    open_shift.date.weekday(),
                )

                if (
                    normal_band != "unknown"
                    and proposed_band == normal_band
                ):
                    score += 70
                    reasons.append("Usually works this time")

                # Treat neighbouring evening/late bands as the same
                # real-world closing pattern.
                elif {
                    normal_band,
                    proposed_band,
                } == {"evening", "late"}:
                    score += 60
                    reasons.append(
                        "Close to their usual evening time"
                    )

            if not reasons:
                reasons.append(
                    "Available, but not their usual shift time"
                )

            possible.append(
                {
                    "shift": open_shift,
                    "score": score,
                    "reasons": reasons,
                    "is_good_match": score >= 70,
                }
            )

        possible.sort(
            key=lambda item: (
                -item["score"],
                item["shift"].date,
                item["shift"].start_time,
            )
        )

        good_matches = [
            item
            for item in possible
            if item["is_good_match"]
        ]

        other_possible = [
            item
            for item in possible
            if not item["is_good_match"]
        ]

        replacement_hour_requests.append(
            {
                "employee": employee,
                "responses": responses,
                "response": responses[0],
                "lost_shifts": lost_shifts,
                "lost_hours": lost_hours,
                "good_matches": good_matches[:3],
                "other_possible": other_possible[:5],
            }
        )

    open_shift_requests = list(
        OpenShiftRequest.objects.filter(
            open_shift__roster_week=roster,
            status=OpenShiftRequestStatus.REQUESTED,
        ).select_related("open_shift", "employee")
    )

    manager_employee_choices = list(
        Employee.objects
        .filter(is_active=True)
        .order_by("first_name", "last_name")
        .values("id", "first_name", "last_name")
    )

    return render(
        request,
        "roster/detail.html",
        {
            "roster": roster,
            "copied_issues": copied_issues,
            "days": days,
            "rows": rows,
            "departments": Department.choices,
            "unresolved_count": len(unresolved),
            "open_shift_groups": open_shift_groups,
            "show_all": show_all,
            "scheduled_employee_count": len(scheduled_employee_ids),
            "shift_change_requests": shift_change_requests,
            "replacement_hour_requests": replacement_hour_requests,
            "open_shift_requests": open_shift_requests,
            "manager_employee_choices": manager_employee_choices,
            "previous_week": roster.week_start - timedelta(days=7),
            "next_week": roster.week_start + timedelta(days=7),
        },
    )




@login_required
def roster_excel(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)
    days = [
        roster.week_start + timedelta(days=index)
        for index in range(7)
    ]
    shifts = list(
        roster.shifts.select_related("employee").order_by(
            "department",
            "employee__first_name",
            "employee__last_name",
            "date",
            "segment",
        )
    )
    patterns = list(
        EmployeePattern.objects.select_related("employee")
    )

    # Existing roster, keyed by employee/day.
    shift_map = {}
    current_hours = {}
    current_days = {}
    employee_days = set()
    scheduled_ids = set()
    department_hours = {}

    for shift in shifts:
        shift_map.setdefault(
            (shift.employee_id, shift.date),
            [],
        ).append(shift)
        scheduled_ids.add(shift.employee_id)
        key = (shift.employee_id, shift.department)
        department_hours[key] = (
            department_hours.get(key, 0.0)
            + shift.duration_hours
        )
        current_hours[key] = (
            current_hours.get(key, 0.0)
            + shift.duration_hours
        )
        employee_days.add(
            (shift.employee_id, shift.department, shift.date)
        )

    for employee_id, department, shift_date in employee_days:
        key = (employee_id, department)
        current_days[key] = current_days.get(key, 0) + 1

    # Provisional assignments live only in the workbook until import.
    provisional = {}
    provisional_days = set()
    impossible = []

    for open_shift in roster.open_shifts.all().order_by(
        "department",
        "date",
        "start_time",
    ):
        signature = (
            open_shift.source_signature
            or open_shift.display_time.replace("–", "-")
        )
        ranked = rank_candidates(
            roster=roster,
            patterns=patterns,
            weekday=open_shift.date.weekday(),
            department=open_shift.department,
            signature=signature,
            current_hours=current_hours,
            current_days=current_days,
            shift_date=open_shift.date,
        )

        chosen = None
        for item in ranked:
            employee_id = item["pattern"].employee_id
            # Keep the draft simple: do not provisionally give the same
            # employee two separate unresolved jobs on the same day.
            if (employee_id, open_shift.date) in provisional_days:
                continue
            chosen = item
            break

        if chosen is None:
            impossible.append((open_shift, signature))
            continue

        pattern = chosen["pattern"]
        scheduled_ids.add(pattern.employee_id)
        provisional.setdefault(
            (pattern.employee_id, open_shift.date),
            [],
        ).append(signature)
        provisional_days.add(
            (pattern.employee_id, open_shift.date)
        )

        key = (pattern.employee_id, open_shift.department)
        draft_hours = signature_duration(signature)
        department_hours[key] = (
            department_hours.get(key, 0.0)
            + draft_hours
        )
        current_hours[key] = (
            current_hours.get(key, 0.0)
            + draft_hours
        )
        current_days[key] = current_days.get(key, 0) + 1

    # Show every active employee who is actually part of the generated draft.
    employees = list(
        Employee.objects.filter(
            is_active=True,
            id__in=scheduled_ids,
        ).order_by(
            "department",
            "first_name",
            "last_name",
        )
    )
    row_department = {}
    for employee in employees:
        restaurant_hours = department_hours.get(
            (employee.id, Department.RESTAURANT), 0.0
        )
        bar_hours = department_hours.get(
            (employee.id, Department.BAR), 0.0
        )
        if bar_hours > restaurant_hours:
            row_department[employee.id] = Department.BAR
        elif restaurant_hours > 0:
            row_department[employee.id] = Department.RESTAURANT
        else:
            row_department[employee.id] = employee.department

    output = BytesIO()
    workbook = xlsxwriter.Workbook(
        output,
        {"in_memory": True},
    )

    title_format = workbook.add_format(
        {"bold": True, "font_size": 16}
    )
    section_format = workbook.add_format(
        {"bold": True, "bg_color": "#EAF0FF", "border": 1}
    )
    header_format = workbook.add_format(
        {"bold": True, "border": 1, "align": "center"}
    )
    name_format = workbook.add_format({"bold": True, "border": 1})
    cell_format = workbook.add_format(
        {"border": 1, "align": "center", "valign": "vcenter"}
    )
    replace_format = workbook.add_format({"border": 1})
    note_format = workbook.add_format(
        {"font_color": "#667085", "italic": True}
    )

    sheet = workbook.add_worksheet("Roster")
    sheet.hide_gridlines(2)
    sheet.freeze_panes(3, 2)
    sheet.set_column("A:A", 23)
    sheet.set_column("B:B", 23)
    sheet.set_column("C:I", 15)
    sheet.set_column("J:J", 11)

    sheet.merge_range(
        "A1:J1",
        f"Week ending {roster.week_end:%d %B %Y}",
        title_format,
    )
    sheet.write(
        "A2",
        "Draft roster. Leave it as it is, or use Replace with / edit a shift and upload it back.",
        note_format,
    )

    headers = ["Employee", "Replace with"]
    headers += [f"{day:%a}\n{day:%d %b}" for day in days]
    headers.append("Hours")
    for col, header in enumerate(headers):
        sheet.write(2, col, header, header_format)

    lists_sheet = workbook.add_worksheet("_Lists")
    lists_sheet.hide()
    meta_sheet = workbook.add_worksheet("_Meta")
    meta_sheet.hide()
    meta_sheet.write("A1", "roster_id")
    meta_sheet.write("B1", roster.pk)
    meta_sheet.write("A2", "week_start")
    meta_sheet.write("B2", roster.week_start.isoformat())
    meta_sheet.write("A3", "format")
    meta_sheet.write("B3", "manager-workbook-v1")
    meta_sheet.write_row(4, 0, ["excel_row", "employee_id", "department"])

    # Replacement lists are department-specific and deliberately simple.
    replacement_ranges = {}
    list_col = 0
    for department in (Department.RESTAURANT, Department.BAR):
        if department == Department.BAR:
            candidates = Employee.objects.filter(
                is_active=True,
                can_work_bar=True,
            ).order_by("first_name", "last_name")
        else:
            candidates = Employee.objects.filter(
                is_active=True,
                can_work_restaurant=True,
            ).order_by("first_name", "last_name")

        names = [employee.full_name for employee in candidates]
        if not names:
            continue
        for list_row, name in enumerate(names):
            lists_sheet.write(list_row, list_col, name)
        col_letter = xlsxwriter.utility.xl_col_to_name(list_col)
        range_name = f"Replacement_{department.title()}"
        workbook.define_name(
            range_name,
            f"='_Lists'!${col_letter}$1:${col_letter}${len(names)}",
        )
        replacement_ranges[department] = range_name
        list_col += 1

    row = 3
    meta_row = 5
    for department, label in (
        (Department.RESTAURANT, "Restaurant"),
        (Department.BAR, "Bar"),
    ):
        department_employees = [
            employee for employee in employees
            if row_department.get(employee.id) == department
        ]
        if not department_employees:
            continue

        sheet.merge_range(row, 0, row, 9, label, section_format)
        row += 1

        for employee in department_employees:
            sheet.write(row, 0, employee.full_name, name_format)
            sheet.write_blank(row, 1, None, replace_format)
            range_name = replacement_ranges.get(department)
            if range_name:
                sheet.data_validation(
                    row, 1, row, 1,
                    {
                        "validate": "list",
                        "source": f"={range_name}",
                        "input_title": "Replace employee",
                        "input_message": "Leave blank, or choose another suitable employee.",
                    },
                )

            total_hours = 0.0
            for day_col, day in enumerate(days, start=2):
                values = []
                employee_shifts = sorted(
                    shift_map.get((employee.id, day), []),
                    key=lambda item: item.segment,
                )
                for shift in employee_shifts:
                    values.append(
                        f"{shift.start_time:%H:%M}-{shift.end_time:%H:%M}"
                    )
                    total_hours += shift.duration_hours

                for signature in provisional.get((employee.id, day), []):
                    values.append(signature)
                    total_hours += signature_duration(signature)

                sheet.write(
                    row,
                    day_col,
                    ", ".join(values) if values else "OFF",
                    cell_format,
                )

            sheet.write_number(row, 9, round(total_hours, 1), cell_format)
            meta_sheet.write_row(
                meta_row,
                0,
                [row + 1, employee.id, department],
            )
            meta_row += 1
            row += 1

    if impossible:
        # This should be rare; keep it on the same sheet without creating a
        # separate manager workflow.
        row += 1
        sheet.write(row, 0, "Unassigned", section_format)
        row += 1
        for open_shift, signature in impossible:
            sheet.write(row, 0, "UNASSIGNED", name_format)
            day_col = (open_shift.date - roster.week_start).days + 2
            if 2 <= day_col <= 8:
                sheet.write(row, day_col, signature, cell_format)
            row += 1

    workbook.close()
    output.seek(0)

    filename = f"roster-week-ending-{roster.week_end:%Y-%m-%d}.xlsx"
    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@transaction.atomic
def roster_excel_import(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    if roster.status == RosterStatus.PUBLISHED:
        messages.error(request, "Published rosters cannot be replaced from Excel.")
        return redirect("roster:detail", pk=pk)

    upload = request.FILES.get("workbook")
    if not upload:
        messages.error(request, "Choose the completed Excel roster first.")
        return redirect("roster:detail", pk=pk)

    try:
        workbook = load_workbook(upload, data_only=False)
    except Exception:
        messages.error(request, "That file is not a readable Excel workbook.")
        return redirect("roster:detail", pk=pk)

    if "Roster" not in workbook.sheetnames or "_Meta" not in workbook.sheetnames:
        messages.error(request, "Use the Manager Workbook downloaded from this roster.")
        return redirect("roster:detail", pk=pk)

    sheet = workbook["Roster"]
    meta = workbook["_Meta"]
    if meta["B1"].value != roster.pk:
        messages.error(request, "That workbook belongs to a different roster week.")
        return redirect("roster:detail", pk=pk)

    rows = []
    meta_row = 6
    while meta.cell(meta_row, 1).value:
        excel_row = int(meta.cell(meta_row, 1).value)
        employee_id = int(meta.cell(meta_row, 2).value)
        department = str(meta.cell(meta_row, 3).value)
        original = Employee.objects.filter(pk=employee_id, is_active=True).first()
        if original is None:
            messages.error(request, "An employee in this workbook no longer exists.")
            return redirect("roster:detail", pk=pk)

        replacement_name = str(sheet.cell(excel_row, 2).value or "").strip()
        employee = original
        if replacement_name:
            candidates = Employee.objects.filter(is_active=True)
            if department == Department.BAR:
                candidates = candidates.filter(can_work_bar=True)
            else:
                candidates = candidates.filter(can_work_restaurant=True)
            matches = [
                candidate for candidate in candidates
                if candidate.full_name == replacement_name
            ]
            if len(matches) != 1:
                messages.error(
                    request,
                    f"Could not uniquely match replacement employee '{replacement_name}'.",
                )
                return redirect("roster:detail", pk=pk)
            employee = matches[0]

        day_values = []
        for day_index in range(7):
            raw = sheet.cell(excel_row, day_index + 3).value
            value = str(raw or "OFF").strip()
            day_values.append(value)

        rows.append((employee, department, day_values))
        meta_row += 1

    # Replacements may point at an employee who already has a row. That is
    # intentional: their schedules are merged on import, then checked for
    # overlaps before anything is written to the database.
    parsed_rows = []
    try:
        for employee, department, day_values in rows:
            parsed_days = []
            for day_index, value in enumerate(day_values):
                if value.lower() in {"off", "-", "none", ""}:
                    parsed_days.append([])
                    continue
                parsed_days.append(parse_signature(value))
            parsed_rows.append((employee, department, parsed_days))
    except Exception:
        messages.error(
            request,
            "One of the shift cells is not valid. Use 09:00-17:00, a comma-separated split shift, or OFF.",
        )
        return redirect("roster:detail", pk=pk)

    merged = {}
    for employee, department, parsed_days in parsed_rows:
        for day_index, parsed in enumerate(parsed_days):
            if not parsed:
                continue
            shift_date = roster.week_start + timedelta(days=day_index)
            merged.setdefault(
                (employee.id, department, shift_date),
                {"employee": employee, "segments": []},
            )["segments"].extend(parsed)

    # Validate combined rows before replacing the live draft.
    for item in merged.values():
        intervals = []
        for _segment, start, end in item["segments"]:
            start_minutes = start.hour * 60 + start.minute
            end_minutes = end.hour * 60 + end.minute
            if end_minutes <= start_minutes:
                end_minutes += 24 * 60
            intervals.append((start_minutes, end_minutes))
        intervals.sort()
        for previous, current in zip(intervals, intervals[1:]):
            if previous[1] > current[0]:
                messages.error(
                    request,
                    "A replacement would give an employee overlapping shifts. Change the replacement and upload again.",
                )
                return redirect("roster:detail", pk=pk)

    roster.shifts.all().delete()
    roster.open_shifts.all().delete()

    created = 0
    for (_employee_id, department, shift_date), item in merged.items():
        employee = item["employee"]
        for segment_index, (_segment, start, end) in enumerate(
            item["segments"], start=1
        ):
            Shift.objects.create(
                roster_week=roster,
                employee=employee,
                department=department,
                date=shift_date,
                start_time=start,
                end_time=end,
                segment=segment_index,
                source="manual",
                confidence=100,
                notes="Imported from Manager Workbook",
            )
            created += 1

    messages.success(
        request,
        f"Manager Workbook applied. {created} shift segments imported.",
    )
    return redirect("roster:detail", pk=pk)
@login_required
@transaction.atomic
def save_cell(request, pk):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    roster = get_object_or_404(RosterWeek, pk=pk)
    employee = get_object_or_404(
        Employee,
        pk=request.POST["employee_id"],
    )

    try:
        shift_date = date.fromisoformat(request.POST["date"])
    except (ValueError, TypeError):
        message = "Invalid roster date."

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse(
                {"ok": False, "error": message},
                status=400,
            )

        messages.error(request, message)
        return redirect("roster:detail", pk=pk)

    shift_text = request.POST.get("shift_text", "").strip()
    normalized = shift_text.lower().replace(" ", "")

    area_map = {
        "restaurant": Department.RESTAURANT,
        "bar": Department.BAR,
        "kitchen": Department.KITCHEN,
        "washup": Department.WASHUP,
        "wash-up": Department.WASHUP,
    }

    requested_department = area_map.get(normalized)

    department = (
        request.POST.get("department")
        or employee.department
    )

    is_off = (
        not shift_text
        or normalized in {"off", "-", "none"}
    )

    # --------------------------------------------------------
    # AREA-ONLY ASSIGNMENT
    # e.g. manager types KITCHEN without exact hours.
    # --------------------------------------------------------
    if requested_department:
        Shift.objects.filter(
            roster_week=roster,
            employee=employee,
            date=shift_date,
        ).delete()

        UnresolvedShift.objects.update_or_create(
            roster_week=roster,
            employee=employee,
            date=shift_date,
            department=requested_department,
            defaults={
                "suggested_start_time": None,
                "suggested_end_time": None,
                "reason": "",
            },
        )

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse(
                {
                    "ok": True,
                    "off": False,
                    "area_only": True,
                    "department_label": requested_department.replace("_", " ").upper(),
                    "display": [],
                }
            )

        return redirect("roster:detail", pk=pk)

    # --------------------------------------------------------
    # NORMAL TIMED SHIFT / OFF
    # --------------------------------------------------------
    parsed = []

    if not is_off:
        try:
            parsed = list(parse_signature(shift_text))
        except Exception:
            message = (
                "Enter 09:00-17:00, a split shift, "
                "OFF, or an area such as KITCHEN."
            )

            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse(
                    {"ok": False, "error": message},
                    status=400,
                )

            messages.error(request, message)
            return redirect("roster:detail", pk=pk)

    Shift.objects.filter(
        roster_week=roster,
        employee=employee,
        date=shift_date,
    ).delete()

    created = []

    if is_off:
        UnresolvedShift.objects.filter(
            roster_week=roster,
            employee=employee,
            date=shift_date,
        ).delete()
    else:
        for segment, start_time, end_time in parsed:
            shift = Shift.objects.create(
                roster_week=roster,
                employee=employee,
                department=department,
                date=shift_date,
                start_time=start_time,
                end_time=end_time,
                segment=segment,
                source="manual",
                confidence=100,
            )
            created.append(shift)

        UnresolvedShift.objects.filter(
            roster_week=roster,
            employee=employee,
            date=shift_date,
        ).delete()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse(
            {
                "ok": True,
                "off": is_off,
                "area_only": False,
                "display": [
                    shift.display_time
                    for shift in created
                ],
            }
        )

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
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    roster = get_object_or_404(RosterWeek, pk=pk)

    try:
        publish_roster(roster, request.user)
    except ValidationError as exc:
        message = (
            exc.messages[0]
            if getattr(exc, "messages", None)
            else str(exc)
        )
        messages.error(request, message)
        return redirect("roster:detail", pk=pk)

    messages.success(request, "Roster published.")
    return redirect("roster:detail", pk=pk)



def _employee_can_take_open_shift(roster, open_shift, employee):
    if not compatible(employee, open_shift.department):
        return (
            False,
            f"This employee cannot work "
            f"{open_shift.get_department_display()}.",
        )

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
def dismiss_replacement_hours(request, pk, response_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    roster = get_object_or_404(RosterWeek, pk=pk)

    response = get_object_or_404(
        ShiftResponse,
        pk=response_id,
        shift__roster_week=roster,
        status=ShiftResponseStatus.RESOLVED,
        wants_replacement_shift=True,
    )

    employee_name = response.employee.full_name

    response.wants_replacement_shift = False
    response.save(
        update_fields=[
            "wants_replacement_shift",
            "updated_at",
        ]
    )

    messages.info(
        request,
        f"No replacement hours will be arranged for {employee_name}.",
    )

    return redirect("roster:detail", pk=pk)


@login_required
def give_replacement_open_shift(
    request,
    pk,
    response_id,
    open_shift_id,
):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    roster = get_object_or_404(RosterWeek, pk=pk)

    response = get_object_or_404(
        ShiftResponse.objects.select_related("employee"),
        pk=response_id,
        shift__roster_week=roster,
        status=ShiftResponseStatus.RESOLVED,
        wants_replacement_shift=True,
    )

    employee = response.employee

    open_shift = get_object_or_404(
        OpenShift,
        pk=open_shift_id,
        roster_week=roster,
    )

    if (
        open_shift.department == Department.BAR
        and not employee.can_work_bar
    ):
        messages.error(request, "That employee cannot work Bar.")
        return redirect("roster:detail", pk=pk)

    if (
        open_shift.department == Department.RESTAURANT
        and not employee.can_work_restaurant
    ):
        messages.error(
            request,
            "That employee cannot work Restaurant.",
        )
        return redirect("roster:detail", pk=pk)

    signature = (
        open_shift.source_signature
        or open_shift.display_time.replace("–", "-")
    )

    availability = candidate_availability(
        roster=roster,
        employee=employee,
        shift_date=open_shift.date,
        signature=signature,
    )

    if not availability["available"]:
        messages.error(
            request,
            (
                f"{employee.full_name} cannot take that shift: "
                f"{availability['reason']}"
            ),
        )
        return redirect("roster:detail", pk=pk)

    parts = parse_signature(signature)

    # An employee may legitimately work more than one separated shift
    # on the same day. The database uses segment numbers to distinguish
    # those shifts, so choose the next available segment rather than
    # colliding with an existing segment=1 shift.
    used_segments = set(
        Shift.objects.filter(
            roster_week=roster,
            employee=employee,
            date=open_shift.date,
        ).values_list("segment", flat=True)
    )

    for _parsed_segment, start, end in parts:
        segment = 1

        while segment in used_segments:
            segment += 1

        new_shift = Shift.objects.create(
            roster_week=roster,
            employee=employee,
            department=open_shift.department,
            date=open_shift.date,
            start_time=start,
            end_time=end,
            segment=segment,
            source="replacement_hours",
            confidence=100,
            notes="Replacement hours requested by employee",
        )

        if roster.status == RosterStatus.PUBLISHED:
            StaffRosterNotice.objects.create(
                employee=employee,
                roster_week=roster,
                message=(
                    f"New shift: "
                    f"{new_shift.date.strftime('%a %d %b')} · "
                    f"{new_shift.start_time.strftime('%H:%M')}–"
                    f"{new_shift.end_time.strftime('%H:%M')}."
                ),
            )

        used_segments.add(segment)

    open_shift.delete()

    response.wants_replacement_shift = False
    response.save(
        update_fields=[
            "wants_replacement_shift",
            "updated_at",
        ]
    )

    messages.success(
        request,
        f"{employee.full_name} has been given the replacement shift.",
    )

    return redirect("roster:detail", pk=pk)


@login_required
def replace_requested_shift(request, pk, response_id, employee_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    roster = get_object_or_404(RosterWeek, pk=pk)
    response = get_object_or_404(
        ShiftResponse.objects.select_related("shift", "employee"),
        pk=response_id,
        shift__roster_week=roster,
        status=ShiftResponseStatus.CANNOT_WORK,
    )
    replacement = get_object_or_404(Employee, pk=employee_id, is_active=True)
    shift = response.shift
    signature = f"{shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')}"
    availability = candidate_availability(
        roster=roster,
        employee=replacement,
        shift_date=shift.date,
        signature=signature,
    )
    if not availability["available"]:
        messages.error(request, f"{replacement.full_name} is not available: {availability['reason']}")
        return redirect("roster:detail", pk=pk)
    if shift.department == Department.BAR and not replacement.can_work_bar:
        messages.error(request, "That employee cannot work Bar.")
        return redirect("roster:detail", pk=pk)
    if shift.department == Department.RESTAURANT and not replacement.can_work_restaurant:
        messages.error(request, "That employee cannot work Restaurant.")
        return redirect("roster:detail", pk=pk)

    original_employee = response.employee
    original_name = original_employee.full_name
    old_display = (
        f"{shift.date.strftime('%a %d %b')} · "
        f"{shift.start_time.strftime('%H:%M')}–"
        f"{shift.end_time.strftime('%H:%M')}"
    )

    shift.employee = replacement
    shift.source = "manager_rearrange"
    shift.notes = (shift.notes + " | " if shift.notes else "") + f"Reassigned from {original_name}"
    shift.save(update_fields=["employee", "source", "notes", "updated_at"])

    # If the replacement employee previously gave up a shift in this
    # roster and asked for replacement hours, this newly assigned shift
    # satisfies one outstanding request.
    outstanding_hours_request = (
        ShiftResponse.objects
        .filter(
            shift__roster_week=roster,
            employee=replacement,
            status=ShiftResponseStatus.RESOLVED,
            wants_replacement_shift=True,
        )
        .order_by("created_at")
        .first()
    )

    if outstanding_hours_request:
        outstanding_hours_request.wants_replacement_shift = False
        outstanding_hours_request.save(
            update_fields=[
                "wants_replacement_shift",
                "updated_at",
            ]
        )

    response.status = ShiftResponseStatus.RESOLVED
    response.resolved_at = timezone.now()
    response.resolved_by = request.user
    response.save(update_fields=["status", "resolved_at", "resolved_by", "updated_at"])

    if roster.status == RosterStatus.PUBLISHED:
        StaffRosterNotice.objects.create(
            employee=original_employee,
            roster_week=roster,
            message=f"Your {old_display} shift was changed.",
        )

        StaffRosterNotice.objects.create(
            employee=replacement,
            roster_week=roster,
            message=f"New shift: {old_display}.",
        )

    messages.success(request, f"Shift moved from {original_name} to {replacement.full_name}.")
    return redirect("roster:detail", pk=pk)


@login_required
def approve_open_shift_request(request, pk, request_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    roster = get_object_or_404(RosterWeek, pk=pk)
    shift_request = get_object_or_404(
        OpenShiftRequest.objects.select_related("open_shift", "employee"),
        pk=request_id,
        open_shift__roster_week=roster,
        status=OpenShiftRequestStatus.REQUESTED,
    )
    open_shift = shift_request.open_shift
    employee = shift_request.employee
    allowed, reason = _employee_can_take_open_shift(roster, open_shift, employee)
    if not allowed:
        messages.error(request, f"{employee.full_name} is no longer available: {reason}")
        return redirect("roster:detail", pk=pk)

    parts = parse_signature(open_shift.source_signature or open_shift.display_time.replace("–", "-"))
    for segment, start, end in parts:
        Shift.objects.create(
            roster_week=roster, employee=employee, department=open_shift.department,
            date=open_shift.date, start_time=start, end_time=end, segment=segment,
            source="staff_request", confidence=100,
        )
    open_shift.delete()
    messages.success(request, f"Approved {employee.full_name} for the open shift.")
    return redirect("roster:detail", pk=pk)


@login_required
def decline_open_shift_request(request, pk, request_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    roster = get_object_or_404(RosterWeek, pk=pk)
    shift_request = get_object_or_404(
        OpenShiftRequest, pk=request_id, open_shift__roster_week=roster
    )
    shift_request.status = OpenShiftRequestStatus.DECLINED
    shift_request.decided_at = timezone.now()
    shift_request.decided_by = request.user
    shift_request.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])
    messages.info(request, "Open shift request declined.")
    return redirect("roster:detail", pk=pk)


@login_required
def roster_delete(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)
    shift_count = roster.shifts.count()
    open_shift_count = roster.open_shifts.count()

    if request.method == "POST":
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
def use_replacement(request, pk, shift_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    roster = get_object_or_404(RosterWeek, pk=pk)

    shift = get_object_or_404(
        Shift,
        pk=shift_id,
        roster_week=roster,
    )

    employee_id = request.POST.get("employee_id")

    employee = get_object_or_404(
        Employee,
        pk=employee_id,
        is_active=True,
    )

    # Recalculate valid suggestions at the moment the manager clicks.
    valid = replacement_suggestions_for_shift(
        roster,
        shift,
        limit=20,
    )

    valid_ids = {
        item["employee_id"]
        for item in valid
    }

    if employee.pk not in valid_ids:
        messages.error(
            request,
            "That employee is no longer available for this shift.",
        )
        return redirect("roster:detail", pk=roster.pk)

    old_employee = shift.employee

    shift.employee = employee
    shift.source = "manager"
    shift.confidence = 100
    shift.save(
        update_fields=[
            "employee",
            "source",
            "confidence",
        ]
    )

    # Remove this resolved issue from the session.
    session_key = f"copied_issues_{roster.pk}"

    issues = request.session.get(session_key, [])

    issues = [
        issue
        for issue in issues
        if issue.get("shift_id") != shift.pk
    ]

    request.session[session_key] = issues
    request.session.modified = True

    messages.success(
        request,
        (
            f"{employee.full_name} will cover "
            f"{old_employee.full_name}'s shift."
        ),
    )

    return redirect("roster:detail", pk=roster.pk)


@login_required
def edit_shift(request, pk, shift_id):
    roster = get_object_or_404(RosterWeek, pk=pk)
    shift = get_object_or_404(
        Shift.objects.select_related("employee"),
        pk=shift_id,
        roster_week=roster,
    )

    if request.method == "POST":
        employee_id = request.POST.get("employee_id")
        start_value = request.POST.get("start_time")
        end_value = request.POST.get("end_time")

        if not employee_id or not start_value or not end_value:
            messages.error(
                request,
                "Choose an employee and enter both start and finish times.",
            )
            return redirect("roster:edit_shift", pk=pk, shift_id=shift_id)

        employee = get_object_or_404(Employee, pk=employee_id)

        try:
            from datetime import time
            start_parts = [int(x) for x in start_value.split(":")]
            end_parts = [int(x) for x in end_value.split(":")]

            start_time = time(start_parts[0], start_parts[1])
            end_time = time(end_parts[0], end_parts[1])
        except Exception:
            messages.error(request, "Enter valid times.")
            return redirect("roster:edit_shift", pk=pk, shift_id=shift_id)

        shift.employee = employee
        shift.start_time = start_time
        shift.end_time = end_time
        shift.source = "manager"
        shift.confidence = 100
        shift.save(
            update_fields=[
                "employee",
                "start_time",
                "end_time",
                "source",
                "confidence",
            ]
        )

        messages.success(
            request,
            f"Shift updated for {employee.full_name}.",
        )
        return redirect("roster:detail", pk=roster.pk)

    employees = (
        Employee.objects
        .filter(is_active=True)
        .order_by("first_name", "last_name")
    )

    return render(
        request,
        "roster/edit_shift.html",
        {
            "roster": roster,
            "shift": shift,
            "employees": employees,
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
    request.session.pop(f"unresolved_{roster.pk}", None)

    result = generate_business_roster(
        roster,
        uncertain_threshold=75,
    )

    messages.success(
        request,
        f"Draft regenerated. {result['created']} assigned shift segments; "
        f"{result['open']} shifts need a choice.",
    )
    return redirect("roster:detail", pk=roster.pk)


@login_required
def set_default_roster(request, pk):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    source = get_object_or_404(
        RosterWeek,
        pk=pk,
        purpose=RosterPurpose.WEEKLY,
    )

    # A protected Base roster lives on a reserved historical
    # week. It is not shown in the normal weekly roster list.
    from datetime import date

    base_week_start = date(2000, 1, 3)

    base = (
        RosterWeek.objects
        .filter(purpose=RosterPurpose.BASE)
        .first()
    )

    if base is None:
        # Guard against the reserved date already being used.
        existing = RosterWeek.objects.filter(
            week_start=base_week_start
        ).first()

        if existing:
            messages.error(
                request,
                "The reserved default-roster date is already in use.",
            )
            return redirect(
                "roster:detail",
                pk=source.pk,
            )

        base = RosterWeek.objects.create(
            week_start=base_week_start,
            purpose=RosterPurpose.BASE,
            status=RosterStatus.DRAFT,
            notes="Protected default roster",
        )
    else:
        base.shifts.all().delete()
        base.open_shifts.all().delete()

    copied = copy_roster(
        source,
        base,
    )

    # Old weekly is_default flags are no longer authoritative.
    RosterWeek.objects.filter(
        purpose=RosterPurpose.WEEKLY,
        is_default=True,
    ).update(is_default=False)

    messages.success(
        request,
        (
            f"Default roster updated from {source}. "
            f"{copied} shifts saved to the protected default."
        ),
    )

    return redirect(
        "roster:detail",
        pk=source.pk,
    )

@login_required
def add_shift(request, pk):
    roster = get_object_or_404(RosterWeek, pk=pk)

    employees = (
        Employee.objects
        .filter(is_active=True)
        .order_by("first_name", "last_name")
    )

    if request.method == "POST":
        employee = get_object_or_404(
            Employee,
            pk=request.POST.get("employee_id"),
            is_active=True,
        )

        date_text = request.POST.get("date", "")
        start_text = request.POST.get("start_time", "")
        end_text = request.POST.get("end_time", "")
        department = request.POST.get("department", "")

        try:
            shift_date = date.fromisoformat(date_text)
        except ValueError:
            messages.error(request, "Choose a valid date.")
            return redirect("roster:add_shift", pk=pk)

        if not (
            roster.week_start
            <= shift_date
            <= roster.week_end
        ):
            messages.error(
                request,
                "That date is outside this roster week.",
            )
            return redirect("roster:add_shift", pk=pk)

        if department not in {
            Department.RESTAURANT,
            Department.BAR,
        }:
            messages.error(
                request,
                "Choose Restaurant or Bar.",
            )
            return redirect("roster:add_shift", pk=pk)

        if (
            department == Department.BAR
            and not employee.can_work_bar
        ):
            messages.error(
                request,
                f"{employee.full_name} cannot work Bar.",
            )
            return redirect("roster:add_shift", pk=pk)

        if (
            department == Department.RESTAURANT
            and not employee.can_work_restaurant
        ):
            messages.error(
                request,
                f"{employee.full_name} cannot work Restaurant.",
            )
            return redirect("roster:add_shift", pk=pk)

        signature = f"{start_text}-{end_text}"

        try:
            parts = parse_signature(signature)
        except Exception:
            messages.error(
                request,
                "Enter a valid start and finish time.",
            )
            return redirect("roster:add_shift", pk=pk)

        availability = candidate_availability(
            roster=roster,
            employee=employee,
            shift_date=shift_date,
            signature=signature,
        )

        if not availability["available"]:
            messages.error(
                request,
                (
                    f"{employee.full_name} cannot take that shift: "
                    f"{availability['reason']}"
                ),
            )
            return redirect("roster:add_shift", pk=pk)

        used_segments = set(
            Shift.objects.filter(
                roster_week=roster,
                employee=employee,
                date=shift_date,
            ).values_list("segment", flat=True)
        )

        created = []

        for _parsed_segment, start, end in parts:
            segment = 1

            while segment in used_segments:
                segment += 1

            shift = Shift.objects.create(
                roster_week=roster,
                employee=employee,
                department=department,
                date=shift_date,
                start_time=start,
                end_time=end,
                segment=segment,
                source="manual",
                confidence=100,
                notes="Added by manager",
            )

            used_segments.add(segment)
            created.append(shift)

        messages.success(
            request,
            (
                f"Added shift for {employee.full_name}: "
                f"{shift_date:%a %d %b} "
                f"{start_text}–{end_text}."
            ),
        )

        return redirect(
            "roster:detail",
            pk=roster.pk,
        )

    return render(
        request,
        "roster/add_shift.html",
        {
            "roster": roster,
            "employees": employees,
            "departments": [
                (Department.RESTAURANT, "Restaurant"),
                (Department.BAR, "Bar"),
            ],
        },
    )

@login_required
def roster_live_status(request, pk):
    """
    Lightweight manager-page polling endpoint.

    Returns a stable fingerprint of staff-request state so the
    roster page can refresh itself when an employee submits or
    changes a shift response.
    """
    roster = get_object_or_404(RosterWeek, pk=pk)

    responses = (
        ShiftResponse.objects
        .filter(shift__roster_week=roster)
        .order_by("pk")
        .values_list(
            "pk",
            "status",
            "wants_replacement_shift",
            "updated_at",
        )
    )

    pieces = [
        f"{pk}:{status}:{int(wants_hours)}:{updated_at.isoformat()}"
        for pk, status, wants_hours, updated_at in responses
    ]

    fingerprint = hashlib.sha256(
        "|".join(pieces).encode("utf-8")
    ).hexdigest()

    return JsonResponse(
        {
            "fingerprint": fingerprint,
            "cannot_work": ShiftResponse.objects.filter(
                shift__roster_week=roster,
                status=ShiftResponseStatus.CANNOT_WORK,
            ).count(),
            "replacement_hours": ShiftResponse.objects.filter(
                shift__roster_week=roster,
                status=ShiftResponseStatus.RESOLVED,
                wants_replacement_shift=True,
            ).count(),
        }
    )

