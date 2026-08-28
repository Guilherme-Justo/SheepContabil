from django.http import HttpRequest

from core.automations.models import AutomationModule


def module_navigation(request: HttpRequest) -> dict[str, object]:
    if not request.user.is_authenticated:
        return {"navigation_modules": ()}
    modules = AutomationModule.objects.visible_to(request.user).select_related("area")
    return {"navigation_modules": modules}
