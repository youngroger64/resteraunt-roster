from django.urls import path
from . import views

app_name = "employees"
urlpatterns = [
    path("", views.employee_list, name="list"),
    path("new/", views.employee_create, name="create"),
    path("<int:pk>/edit/", views.employee_edit, name="edit"),
]
