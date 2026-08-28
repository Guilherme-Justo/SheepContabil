from django.contrib import admin
from django.urls import include, path

from config import health

urlpatterns = [
    path("health/live", health.live, name="health-live"),
    path("health/ready", health.ready, name="health-ready"),
    path("internal-admin/", admin.site.urls),
    path("conta/", include("core.identity.urls")),
    path("", include("core.automations.urls")),
]
