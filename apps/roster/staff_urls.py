from django.urls import path
from . import staff_views

app_name = "roster_staff"

urlpatterns = [
    path("handoff/", staff_views.staff_handoff, name="handoff"),
    path("", staff_views.staff_portal, name="staff"),
    path(
        "shift/<int:shift_id>/respond/",
        staff_views.staff_shift_response,
        name="staff_shift_response",
    ),
    path(
        "open/<int:open_shift_id>/request/",
        staff_views.staff_open_shift_request,
        name="staff_open_shift_request",
    ),
    path(
        "availability/",
        staff_views.staff_availability,
        name="staff_availability",
    ),
]
