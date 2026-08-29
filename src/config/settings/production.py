import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)  # noqa: F405
# Railway reaches health checks over the service's private HTTP network. These
# endpoints expose no sensitive data and must answer directly so deploys can be
# promoted while every user-facing route continues to require HTTPS.
SECURE_REDIRECT_EXEMPT = [r"^health/(?:live|ready)$"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Railway validates deploys through this host and exposes the generated public
# domain at runtime. Keeping both explicit avoids an unsafe wildcard host.
ALLOWED_HOSTS = [*ALLOWED_HOSTS, "healthcheck.railway.app"]  # noqa: F405
railway_public_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
if railway_public_domain:
    ALLOWED_HOSTS.append(railway_public_domain)  # noqa: F405
    CSRF_TRUSTED_ORIGINS.append(f"https://{railway_public_domain}")  # noqa: F405

if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":  # noqa: F405
    raise ImproperlyConfigured("DATABASE_URL com PostgreSQL e obrigatoria em producao.")
if SECRET_KEY == "unsafe-local-development-key":  # noqa: F405
    raise ImproperlyConfigured("DJANGO_SECRET_KEY deve ser configurada em producao.")
