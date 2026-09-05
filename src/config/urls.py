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

if settings.DEBUG or getattr(settings, "ENABLE_ERROR_PREVIEWS", False):
    urlpatterns += [
        path("403/", TemplateView.as_view(template_name="403.html"), name="preview-403"),
        path("404/", TemplateView.as_view(template_name="404.html"), name="preview-404"),
        path("500/", TemplateView.as_view(template_name="500.html"), name="preview-500"),
    ]
