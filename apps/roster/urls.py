from django.urls import path
from . import views
app_name = "roster"
urlpatterns = [
    path("", views.roster_list, name="list"),
    path("new/", views.roster_create, name="create"),
    path("learn/", views.learn, name="learn"),
    path("patterns/", views.pattern_list, name="patterns"),
    path("generate-from-patterns/", views.generate_pattern_roster, name="generate_patterns"),
    path("<int:pk>/", views.roster_detail, name="detail"),
    path("<int:pk>/cell/", views.save_cell, name="save_cell"),
    path("<int:pk>/use-suggestion/", views.use_suggestion, name="use_suggestion"),
    path("<int:pk>/open/<int:open_shift_id>/assign/", views.assign_open_shift, name="assign_open_shift"),
    path("<int:pk>/open/<int:open_shift_id>/assign/<int:employee_id>/", views.assign_suggested_employee, name="assign_suggested_employee"),
    path("<int:pk>/regenerate/", views.roster_regenerate, name="regenerate"),
    path("<int:pk>/delete/", views.roster_delete, name="delete"),
    path("<int:pk>/publish/", views.roster_publish, name="publish"),
]
