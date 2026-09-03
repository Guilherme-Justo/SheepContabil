import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import include, path, reverse

from core.sc05_simulator.models import (
    SimulatorClient,
    SimulatorServiceAccount,
    SimulatorSystem,
    SimulatorTask,
)
from core.sc05_simulator.views import BLOCKED_TASK_ASSIGNEE

urlpatterns = [
    path(
        "simulator/",
        include(
            ("core.sc05_simulator.urls", "sc05_simulator"),
            namespace="sc05_simulator",
        ),
    )
]

pytestmark = [pytest.mark.django_db, pytest.mark.urls(__name__)]


@pytest.fixture
def portal_client(settings) -> Client:
    settings.SC05_SIMULATOR_USERNAME = "robot"
    settings.SC05_SIMULATOR_PASSWORD = "safe-synthetic-password"
    browser = Client()
    response = browser.post(
        reverse("sc05_simulator:login"),
        {"username": "robot", "password": "safe-synthetic-password"},
    )
    assert response.status_code == 302
    return browser


@pytest.fixture
def simulator_client() -> SimulatorClient:
    return SimulatorClient.objects.create(
        external_id="aurora-demo",
        name="Aurora Demonstração Ltda.",
        document="12345678000190",
    )


def test_health_is_public_and_login_protects_portals(client: Client, settings) -> None:
    settings.SC05_SIMULATOR_USERNAME = "robot"
    settings.SC05_SIMULATOR_PASSWORD = "safe-synthetic-password"

    health = client.get(reverse("sc05_simulator:health"))
    protected = client.get(reverse("sc05_simulator:files"))
    invalid = client.post(
        reverse("sc05_simulator:login"),
        {"username": "robot", "password": "wrong"},
    )

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "sc05-simulator"}
    assert protected.status_code == 302
    assert protected.url == reverse("sc05_simulator:login")
    assert invalid.status_code == 200
    assert 'data-testid="login-error"' in invalid.content.decode()


def test_login_handles_unicode_and_limits_failures_per_session(client: Client, settings) -> None:
    settings.SC05_SIMULATOR_USERNAME = "robot"
    settings.SC05_SIMULATOR_PASSWORD = "safe-synthetic-password"

    unicode_attempt = client.post(
        reverse("sc05_simulator:login"),
        {"username": "robô", "password": "senha- inválida"},
    )
    assert unicode_attempt.status_code == 200

    last = unicode_attempt
    for _ in range(4):
        last = client.post(
            reverse("sc05_simulator:login"),
            {"username": "robot", "password": "wrong"},
        )
    assert last.status_code == 429
    assert "Muitas tentativas" in last.content.decode()


def test_service_account_is_unique_per_client_and_system(
    simulator_client: SimulatorClient,
) -> None:
    SimulatorServiceAccount.objects.create(
        client=simulator_client,
        system=SimulatorSystem.FILES,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        SimulatorServiceAccount.objects.create(
            client=simulator_client,
            system=SimulatorSystem.FILES,
        )


def test_files_block_and_unblock_are_idempotent(
    portal_client: Client,
    simulator_client: SimulatorClient,
) -> None:
    account = SimulatorServiceAccount.objects.create(
        client=simulator_client,
        system=SimulatorSystem.FILES,
    )
    block_url = reverse(
        "sc05_simulator:files-action",
        kwargs={"external_id": simulator_client.external_id, "action": "block"},
    )
    unblock_url = reverse(
        "sc05_simulator:files-action",
        kwargs={"external_id": simulator_client.external_id, "action": "unblock"},
    )

    assert portal_client.post(block_url, {"scenario": "", "phase": "apply"}).status_code == 302
    assert portal_client.post(block_url, {"scenario": "", "phase": "apply"}).status_code == 302
    account.refresh_from_db()
    assert account.is_blocked is True

    assert portal_client.post(unblock_url, {"scenario": "", "phase": "apply"}).status_code == 302
    assert portal_client.post(unblock_url, {"scenario": "", "phase": "apply"}).status_code == 302
    account.refresh_from_db()
    assert account.is_blocked is False


def test_accounting_timeout_fails_before_mutation(
    portal_client: Client,
    simulator_client: SimulatorClient,
) -> None:
    account = SimulatorServiceAccount.objects.create(
        client=simulator_client,
        system=SimulatorSystem.ACCOUNTING,
    )
    url = reverse(
        "sc05_simulator:accounting-action",
        kwargs={"external_id": simulator_client.external_id, "action": "block"},
    )

    response = portal_client.post(
        url,
        {"scenario": "timeout_accounting_apply", "phase": "apply"},
    )

    account.refresh_from_db()
    assert response.status_code == 504
    assert account.is_blocked is False
    assert 'data-testid="operation-error"' in response.content.decode()


def test_task_block_changes_only_open_tasks_and_preserves_backup(
    portal_client: Client,
    simulator_client: SimulatorClient,
) -> None:
    open_task = SimulatorTask.objects.create(
        reference="TASK-OPEN",
        client=simulator_client,
        title="Tarefa aberta",
        assignee="maria.operadora",
    )
    closed_task = SimulatorTask.objects.create(
        reference="TASK-CLOSED",
        client=simulator_client,
        title="Tarefa encerrada",
        assignee="joao.operador",
        is_open=False,
    )
    url = reverse(
        "sc05_simulator:tasks-action",
        kwargs={"external_id": simulator_client.external_id, "action": "block"},
    )

    assert portal_client.post(url, {"scenario": "", "phase": "apply"}).status_code == 302
    assert portal_client.post(url, {"scenario": "", "phase": "apply"}).status_code == 302

    open_task.refresh_from_db()
    closed_task.refresh_from_db()
    simulator_client.refresh_from_db()
    assert simulator_client.is_active is True
    assert open_task.assignee == BLOCKED_TASK_ASSIGNEE
    assert open_task.previous_assignee == "maria.operadora"
    assert closed_task.assignee == "joao.operador"
    assert closed_task.previous_assignee == ""


def test_task_unblock_restores_every_backup_including_closed_tasks(
    portal_client: Client,
    simulator_client: SimulatorClient,
) -> None:
    task = SimulatorTask.objects.create(
        reference="TASK-BLOCKED",
        client=simulator_client,
        title="Tarefa bloqueada e posteriormente encerrada",
        assignee=BLOCKED_TASK_ASSIGNEE,
        previous_assignee="maria.operadora",
        is_open=False,
    )
    url = reverse(
        "sc05_simulator:tasks-action",
        kwargs={"external_id": simulator_client.external_id, "action": "unblock"},
    )

    response = portal_client.post(url, {"scenario": "", "phase": "apply"})

    task.refresh_from_db()
    assert response.status_code == 302
    assert task.assignee == "maria.operadora"
    assert task.previous_assignee == ""


def test_task_unblock_without_backup_is_atomic_and_safe(
    portal_client: Client,
    simulator_client: SimulatorClient,
) -> None:
    valid = SimulatorTask.objects.create(
        reference="TASK-VALID",
        client=simulator_client,
        title="Tarefa com backup",
        assignee=BLOCKED_TASK_ASSIGNEE,
        previous_assignee="maria.operadora",
    )
    invalid = SimulatorTask.objects.create(
        reference="TASK-INVALID",
        client=simulator_client,
        title="Tarefa sem backup",
        assignee=BLOCKED_TASK_ASSIGNEE,
        previous_assignee="",
    )
    url = reverse(
        "sc05_simulator:tasks-action",
        kwargs={"external_id": simulator_client.external_id, "action": "unblock"},
    )

    response = portal_client.post(url, {"scenario": "", "phase": "apply"})

    valid.refresh_from_db()
    invalid.refresh_from_db()
    assert response.status_code == 409
    assert valid.assignee == BLOCKED_TASK_ASSIGNEE
    assert valid.previous_assignee == "maria.operadora"
    assert invalid.assignee == BLOCKED_TASK_ASSIGNEE
    assert "sem responsável anterior" in response.content.decode()


def test_task_apply_failure_happens_before_mutation(
    portal_client: Client,
    simulator_client: SimulatorClient,
) -> None:
    task = SimulatorTask.objects.create(
        reference="TASK-FAIL",
        client=simulator_client,
        title="Tarefa preservada",
        assignee="maria.operadora",
    )
    url = reverse(
        "sc05_simulator:tasks-action",
        kwargs={"external_id": simulator_client.external_id, "action": "block"},
    )

    response = portal_client.post(url, {"scenario": "fail_tasks_apply", "phase": "apply"})

    task.refresh_from_db()
    assert response.status_code == 409
    assert task.assignee == "maria.operadora"
    assert task.previous_assignee == ""


def test_combined_scenario_fails_tasks_apply_and_files_compensation(
    portal_client: Client,
    simulator_client: SimulatorClient,
) -> None:
    account = SimulatorServiceAccount.objects.create(
        client=simulator_client,
        system=SimulatorSystem.FILES,
        is_blocked=True,
    )
    task = SimulatorTask.objects.create(
        reference="TASK-COMBINED",
        client=simulator_client,
        title="Tarefa preservada",
        assignee="maria.operadora",
    )
    task_url = reverse(
        "sc05_simulator:tasks-action",
        kwargs={"external_id": simulator_client.external_id, "action": "block"},
    )
    files_url = reverse(
        "sc05_simulator:files-action",
        kwargs={"external_id": simulator_client.external_id, "action": "unblock"},
    )
    payload = {
        "scenario": "fail_tasks_apply_and_files_compensation",
        "phase": "apply",
    }

    task_response = portal_client.post(task_url, payload)
    files_response = portal_client.post(files_url, {**payload, "phase": "compensation"})

    task.refresh_from_db()
    account.refresh_from_db()
    assert task_response.status_code == 409
    assert files_response.status_code == 409
    assert task.assignee == "maria.operadora"
    assert account.is_blocked is True


def test_portals_expose_stable_dom_contract(
    portal_client: Client,
    simulator_client: SimulatorClient,
) -> None:
    SimulatorServiceAccount.objects.create(
        client=simulator_client,
        system=SimulatorSystem.FILES,
    )
    SimulatorTask.objects.create(
        reference="TASK-DOM",
        client=simulator_client,
        title="Contrato DOM",
        assignee="maria.operadora",
    )

    files_html = portal_client.get(reverse("sc05_simulator:files")).content.decode()
    tasks_html = portal_client.get(reverse("sc05_simulator:tasks")).content.decode()

    assert 'data-testid="files-aurora-demo-status"' in files_html
    assert 'data-testid="files-aurora-demo-block-scenario"' in files_html
    assert 'data-testid="files-aurora-demo-block-phase"' in files_html
    assert 'data-testid="task-TASK-DOM-assignee"' in tasks_html
    assert 'data-testid="tasks-client-aurora-demo-block-submit"' in tasks_html
    assert 'data-testid="tasks-client-aurora-demo-active-state"' in tasks_html
    assert "ACTIVE" in tasks_html


def test_simulator_settings_declare_isolated_cookie_namespace() -> None:
    import ast
    from pathlib import Path

    settings_file = (
        Path(__file__).resolve().parent.parent / "src" / "config" / "settings" / "simulator.py"
    )
    tree = ast.parse(settings_file.read_text(encoding="utf-8"))
    assignments = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant)
    }
    assert assignments.get("SESSION_COOKIE_NAME") == "sc05_sim_sessionid"
    assert assignments.get("CSRF_COOKIE_NAME") == "sc05_sim_csrftoken"


def test_portal_login_sets_isolated_session_cookie(settings) -> None:
    settings.SC05_SIMULATOR_USERNAME = "robot"
    settings.SC05_SIMULATOR_PASSWORD = "safe-synthetic-password"
    settings.SESSION_COOKIE_NAME = "sc05_sim_sessionid"

    client = Client()
    response = client.post(
        reverse("sc05_simulator:login"),
        {"username": "robot", "password": "safe-synthetic-password"},
    )
    assert response.status_code == 302
    assert "sc05_sim_sessionid" in response.cookies
