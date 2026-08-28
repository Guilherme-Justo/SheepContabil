import pytest
from django.test import Client
from django.urls import reverse

from core.automations.models import AutomationModule
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
    assert allowed.status_code == 200


def test_logout_requires_post(client: Client, administrator: User) -> None:
    client.force_login(administrator)

    get_response = client.get(reverse("identity:logout"))
    post_response = client.post(reverse("identity:logout"))

    assert get_response.status_code == 405
    assert post_response.status_code == 302
    assert post_response.url == reverse("identity:login")
