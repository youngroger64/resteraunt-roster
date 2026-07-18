from django.urls import path
from . import views

app_name = "imports"
urlpatterns = [
    path("", views.index, name="index"),
    path("employees/", views.employees, name="employees"),
    path("roster/", views.roster, name="roster"),
]
