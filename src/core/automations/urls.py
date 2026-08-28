from django.urls import path

from core.automations import views

app_name = "automations"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("modulos/<slug:slug>/", views.module_detail, name="module-detail"),
    path("execucoes/<uuid:run_id>/", views.run_detail, name="run-detail"),
]
