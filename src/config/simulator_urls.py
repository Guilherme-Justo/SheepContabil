from django.urls import include, path

from config import health

urlpatterns = [
    path("health/live", health.live, name="simulator-health-live"),
    path("health/ready", health.ready, name="simulator-health-ready"),
    path("", include("core.sc05_simulator.urls")),
]
