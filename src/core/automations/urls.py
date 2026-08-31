from django.urls import path

from core.automations import views

app_name = "automations"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("modulos/<slug:slug>/", views.module_detail, name="module-detail"),
    path(
        "briefings-societarios/<uuid:briefing_id>/",
        views.sc06_briefing_detail,
        name="sc06-briefing-detail",
    ),
    path(
        "briefings-societarios/<uuid:briefing_id>/pdf/",
        views.sc06_briefing_pdf,
        name="sc06-briefing-pdf",
    ),
    path("execucoes/<uuid:run_id>/", views.run_detail, name="run-detail"),
]
