from django.apps import AppConfig


class AutomationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core.automations"
    verbose_name = "Automações"

    def ready(self) -> None:
        from core.automations import (
            checks,  # noqa: F401
            signals,  # noqa: F401
        )
