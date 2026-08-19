from django.urls import path
from . import views, staff_views
app_name = "roster"
urlpatterns = [
    path("staff/", staff_views.staff_portal, name="staff"),
    path("staff/shift/<int:shift_id>/respond/", staff_views.staff_shift_response, name="staff_shift_response"),
    path("staff/open/<int:open_shift_id>/request/", staff_views.staff_open_shift_request, name="staff_open_shift_request"),
    path("staff/availability/", staff_views.staff_availability, name="staff_availability"),
    path("", views.roster_list, name="list"),
    path("new/", views.roster_create, name="create"),
    path("learn/", views.learn, name="learn"),
    path("patterns/", views.pattern_list, name="patterns"),
    path("patterns/<int:employee_id>/save/", views.save_schedule_profile, name="save_schedule_profile"),
    path("generate-from-patterns/", views.generate_pattern_roster, name="generate_patterns"),
    path("<int:pk>/", views.roster_detail, name="detail"),
    path("<int:pk>/excel/", views.roster_excel, name="excel"),
    path("<int:pk>/excel/import/", views.roster_excel_import, name="excel_import"),
    path("<int:pk>/cell/", views.save_cell, name="save_cell"),
    path("<int:pk>/use-suggestion/", views.use_suggestion, name="use_suggestion"),
    path("<int:pk>/open/<int:open_shift_id>/assign/", views.assign_open_shift, name="assign_open_shift"),
    path("<int:pk>/open/<int:open_shift_id>/assign/<int:employee_id>/", views.assign_suggested_employee, name="assign_suggested_employee"),
    path("<int:pk>/change-request/<int:response_id>/replace/<int:employee_id>/", views.replace_requested_shift, name="replace_requested_shift"),
    path("<int:pk>/open-request/<int:request_id>/approve/", views.approve_open_shift_request, name="approve_open_shift_request"),
    path("<int:pk>/open-request/<int:request_id>/decline/", views.decline_open_shift_request, name="decline_open_shift_request"),
    path("<int:pk>/regenerate/", views.roster_regenerate, name="regenerate"),
    path("<int:pk>/delete/", views.roster_delete, name="delete"),
    path("<int:pk>/publish/", views.roster_publish, name="publish"),
]
