import os
from logging.config import dictConfig

from celery import Celery, signals

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("sheepcontabil")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


def _configure_celery_logging(**kwargs: object) -> None:
    """Keep Celery and application records on the same JSON logging contract."""

    del kwargs
    from django.conf import settings

    dictConfig(settings.LOGGING)


signals.setup_logging.connect(_configure_celery_logging, weak=False)
