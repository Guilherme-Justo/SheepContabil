from django.urls import path

from core.sc05_simulator import views

app_name = "sc05_simulator"

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("files/", views.files_portal, name="files"),
    path(
        "files/clients/<slug:external_id>/<slug:action>/",
        views.files_action,
        name="files-action",
    ),
    path("accounting/", views.accounting_portal, name="accounting"),
    path(
        "accounting/clients/<slug:external_id>/<slug:action>/",
        views.accounting_action,
        name="accounting-action",
    ),
    path("tasks/", views.tasks_portal, name="tasks"),
    path(
        "tasks/clients/<slug:external_id>/<slug:action>/",
        views.tasks_action,
        name="tasks-action",
    ),
]
