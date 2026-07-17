from django.urls import path
from . import views

app_name = "roster"
urlpatterns = [
    path("", views.roster_list, name="list"),
    path("new/", views.roster_create, name="create"),
    path("<int:pk>/", views.roster_detail, name="detail"),
    path("<int:pk>/cell/", views.save_cell, name="save_cell"),
    path("<int:pk>/publish/", views.roster_publish, name="publish"),
]
