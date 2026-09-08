import dj_database_url

from .base import *  # noqa: F403

DEBUG = False
ENABLE_ERROR_PREVIEWS = True
_test_database_url = env("TEST_DATABASE_URL", default="").strip()  # noqa: F405
DATABASES = (
    {"default": dj_database_url.parse(_test_database_url, conn_max_age=0)}
    if _test_database_url
    else {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
)
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
SC20_NOTIFICATION_BACKEND = "simulated"
SC20_EMAIL_OVERRIDE_TO = ""
SC20_WHATSAPP_OVERRIDE_TO = ""
