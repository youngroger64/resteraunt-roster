from datetime import date, datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.roster.models import RosterPurpose, RosterWeek


def monday_of_week(value):
    return value - timedelta(days=value.weekday())


@login_required
def index(request):
    # No date supplied = current week.
    requested = request.GET.get("week")

    if requested:
        try:
            selected_week = datetime.strptime(
                requested,
                "%Y-%m-%d",
            ).date()
            selected_week = monday_of_week(selected_week)
        except ValueError:
            selected_week = monday_of_week(date.today())
    else:
        selected_week = monday_of_week(date.today())

    roster = (
        RosterWeek.objects
        .filter(week_start=selected_week)
        .order_by("-updated_at")
        .first()
    )

    # If this week already exists, the roster IS the dashboard.
    if roster:
        return redirect("roster:detail", pk=roster.pk)

    previous_week = selected_week - timedelta(days=7)
    next_week = selected_week + timedelta(days=7)

    previous_roster = (
        RosterWeek.objects
        .filter(week_start__lt=selected_week)
        .exclude(purpose=RosterPurpose.BASE)
        .order_by("-week_start", "-updated_at")
        .first()
    )

    default_roster = (
        RosterWeek.objects
        .filter(purpose=RosterPurpose.BASE)
        .order_by("-updated_at")
        .first()
    )

    return render(
        request,
        "dashboard/index.html",
        {
            "selected_week": selected_week,
            "previous_week": previous_week,
            "next_week": next_week,
            "previous_roster": previous_roster,
            "default_roster": default_roster,
        },
    )
