from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from apps.employees.models import Employee
from apps.roster.models import RosterStatus, RosterWeek, Shift

@login_required
def index(request):
    draft = RosterWeek.objects.filter(status=RosterStatus.DRAFT).first()
    published = RosterWeek.objects.filter(status=RosterStatus.PUBLISHED).first()
    context = {
        "employee_count": Employee.objects.filter(is_active=True).count(),
        "draft": draft,
        "published": published,
        "draft_shift_count": Shift.objects.filter(roster_week=draft).count() if draft else 0,
    }
    return render(request, "dashboard/index.html", context)
