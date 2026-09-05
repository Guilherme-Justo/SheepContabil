from django.http import HttpRequest

from core.automations.models import AutomationModule

PAGE_SIZE_CHOICES: tuple[int, ...] = (5, 10, 15, 20, 25, 50)
DEFAULT_PAGE_SIZE: int = 5


def module_navigation(request: HttpRequest) -> dict[str, object]:
    data: dict[str, object] = {
        "page_size_choices": PAGE_SIZE_CHOICES,
        "default_page_size": DEFAULT_PAGE_SIZE,
    }
    if not request.user.is_authenticated:
        data["navigation_modules"] = ()
        return data
    modules = (
        AutomationModule.objects.visible_to(request.user).select_related("area").order_by("code")
    )
    data["navigation_modules"] = modules
    return data
