import pytest
from django.test import Client
from django.urls import reverse

from core.automations.models import AutomationModule, AutomationRun, RunStatus, RunTrigger
from core.identity.models import User

pytestmark = pytest.mark.django_db


def test_anonymous_user_is_redirected_to_real_login(client: Client) -> None:
    response = client.get(reverse("automations:dashboard"))

    assert response.status_code == 302
    assert response.url == f"{reverse('identity:login')}?next=/"


def test_invalid_credentials_show_safe_message(client: Client) -> None:
    response = client.post(
        reverse("identity:login"),
        {"username": "nao-existe", "password": "senha-invalida"},
    )

    assert response.status_code == 200
    assert "Usuário ou senha inválidos" in response.content.decode()


def test_administrator_sees_every_module(
    client: Client,
    administrator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(administrator)

    response = client.get(reverse("automations:dashboard"))

    assert response.status_code == 200
    html = response.content.decode()
    for code in modules:
        assert code in html


def test_operator_sees_only_modules_from_granted_area(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(processes_operator)

    response = client.get(reverse("automations:dashboard"))

    assert response.status_code == 200
    html = response.content.decode()
    assert "SC-20" in html
    assert "SC-04" not in html
    assert "SC-05" not in html
    assert "SC-06" not in html


def test_operator_cannot_open_a_module_outside_their_area(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(processes_operator)

    denied = client.get(
        reverse("automations:module-detail", kwargs={"slug": modules["SC-04"].slug})
    )
    allowed = client.get(
        reverse("automations:module-detail", kwargs={"slug": modules["SC-20"].slug})
    )

    assert denied.status_code == 404
    denied_html = denied.content.decode()
    assert "Página ou módulo não localizado" in denied_html
    assert "Política de Acesso e Segregação de Funções (RBAC)" in denied_html
    assert "Processos" in denied_html
    assert allowed.status_code == 200


def test_logout_requires_post(client: Client, administrator: User) -> None:
    client.force_login(administrator)

    get_response = client.get(reverse("identity:logout"))
    post_response = client.post(reverse("identity:logout"))

    assert get_response.status_code == 405
    assert post_response.status_code == 302
    assert post_response.url == reverse("identity:login")


def test_dashboard_modules_and_navigation_alphabetical_ordering(
    client: Client,
    administrator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(administrator)
    response = client.get(reverse("automations:dashboard"))
    assert response.status_code == 200

    module_codes = [m.code for m in response.context["modules"]]
    assert module_codes == ["SC-04", "SC-05", "SC-06", "SC-20"]

    nav_codes = [m.code for m in response.context["navigation_modules"]]
    assert nav_codes == ["SC-04", "SC-05", "SC-06", "SC-20"]


def test_dashboard_run_filters_and_pagination(
    client: Client,
    administrator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(administrator)

    # Create 10 runs across different modules, statuses and triggers
    for i in range(10):
        module_key = ["SC-04", "SC-05", "SC-06", "SC-20"][i % 4]
        status_val = RunStatus.SUCCEEDED if i % 2 == 0 else RunStatus.FAILED
        trigger_val = RunTrigger.MANUAL if i < 5 else RunTrigger.SCHEDULED
        AutomationRun.objects.create(
            module=modules[module_key],
            status=status_val,
            trigger=trigger_val,
        )

    # 1. Unfiltered: 7 items per page (DEFAULT_PAGE_SIZE), 2 pages total
    resp = client.get(reverse("automations:dashboard"))
    assert resp.status_code == 200
    assert resp.context["paginator"].num_pages == 2
    assert len(resp.context["page_obj"]) == 7
    assert resp.context["has_active_filters"] is False
    assert "Navegação das execuções" in resp.content.decode()

    # Page 2
    resp_p2 = client.get(f"{reverse('automations:dashboard')}?page=2")
    assert resp_p2.status_code == 200
    assert len(resp_p2.context["page_obj"]) == 3

    # 2. Filter by module SC-06
    resp_sc06 = client.get(f"{reverse('automations:dashboard')}?module=SC-06")
    assert resp_sc06.status_code == 200
    assert resp_sc06.context["has_active_filters"] is True
    for run in resp_sc06.context["page_obj"]:
        assert run.module.code == "SC-06"

    # 3. Filter by status SUCCEEDED
    resp_succeeded = client.get(f"{reverse('automations:dashboard')}?status={RunStatus.SUCCEEDED}")
    assert resp_succeeded.status_code == 200
    assert resp_succeeded.context["has_active_filters"] is True
    for run in resp_succeeded.context["page_obj"]:
        assert run.status == RunStatus.SUCCEEDED

    # 4. Filter by trigger MANUAL
    resp_manual = client.get(f"{reverse('automations:dashboard')}?trigger={RunTrigger.MANUAL}")
    assert resp_manual.status_code == 200
    assert resp_manual.context["has_active_filters"] is True
    for run in resp_manual.context["page_obj"]:
        assert run.trigger == RunTrigger.MANUAL
