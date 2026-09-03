from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from config import health

urlpatterns = [
    path("health/live", health.live, name="health-live"),
    path("health/ready", health.ready, name="health-ready"),
    path("internal-admin/", admin.site.urls),
    path("conta/", include("core.identity.urls")),
    path("", include("core.automations.urls")),
]

if settings.DEBUG:
    urlpatterns += [
        path("404/", TemplateView.as_view(template_name="404.html"), name="preview-404"),
    ]
