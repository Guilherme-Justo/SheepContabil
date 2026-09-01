from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from secrets import compare_digest
from typing import Concatenate, cast

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.sc05_simulator.models import (
    SimulatorClient,
    SimulatorServiceAccount,
    SimulatorSystem,
    SimulatorTask,
)

SESSION_KEY = "sc05_simulator_authenticated"
LOGIN_FAILURES_KEY = "sc05_simulator_login_failures"
MAX_LOGIN_FAILURES = 5
BLOCKED_TASK_ASSIGNEE = "BLOQUEADO_INADIMPLENCIA"

SCENARIOS = {
    "",
    "happy_path",
    "fail_tasks_apply",
    "timeout_accounting_apply",
    "fail_tasks_apply_and_files_compensation",
}
PHASES = {"apply", "compensation"}
ACTIONS = {"block", "unblock"}


def simulator_login_required[**P](
    view_func: Callable[Concatenate[HttpRequest, P], HttpResponse],
) -> Callable[Concatenate[HttpRequest, P], HttpResponse]:
    @wraps(view_func)
    def wrapped(request: HttpRequest, *args: P.args, **kwargs: P.kwargs) -> HttpResponse:
        if not request.session.get(SESSION_KEY, False):
            return redirect("sc05_simulator:login")
        return view_func(request, *args, **kwargs)

    return cast(Callable[Concatenate[HttpRequest, P], HttpResponse], wrapped)


@require_GET
def health(request: HttpRequest) -> JsonResponse:
    del request
    return JsonResponse({"status": "ok", "service": "sc05-simulator"})


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    if request.session.get(SESSION_KEY, False):
        return redirect("sc05_simulator:files")
    return redirect("sc05_simulator:login")


@require_http_methods(["GET", "POST"])
def login(request: HttpRequest) -> HttpResponse:
    error = ""
    status = 200
    if request.method == "POST":
        failures = int(request.session.get(LOGIN_FAILURES_KEY, 0))
        if failures >= MAX_LOGIN_FAILURES:
            return render(
                request,
                "sc05_simulator/login.html",
                {
                    "login_error": "Muitas tentativas inválidas nesta sessão.",
                    "simulator_authenticated": False,
                },
                status=429,
            )
        username = request.POST.get("username", "")[:256]
        password = request.POST.get("password", "")[:512]
        expected_username = str(getattr(settings, "SC05_SIMULATOR_USERNAME", ""))
        expected_password = str(getattr(settings, "SC05_SIMULATOR_PASSWORD", ""))
        configured = bool(expected_username and expected_password)
        valid_username = compare_digest(username.encode(), expected_username.encode())
        valid_password = compare_digest(password.encode(), expected_password.encode())
        if configured and valid_username and valid_password:
            request.session.cycle_key()
            request.session[SESSION_KEY] = True
            request.session.pop(LOGIN_FAILURES_KEY, None)
            return redirect("sc05_simulator:files")
        failures += 1
        request.session[LOGIN_FAILURES_KEY] = failures
        if failures >= MAX_LOGIN_FAILURES:
            error = "Muitas tentativas inválidas nesta sessão."
            status = 429
        else:
            error = "Usuário ou senha inválidos."
    return render(
        request,
        "sc05_simulator/login.html",
        {"login_error": error, "simulator_authenticated": False},
        status=status,
    )


@require_POST
@simulator_login_required
def logout(request: HttpRequest) -> HttpResponse:
    request.session.pop(SESSION_KEY, None)
    request.session.cycle_key()
    return redirect("sc05_simulator:login")


@require_GET
@simulator_login_required
def files_portal(request: HttpRequest) -> HttpResponse:
    return _render_service_portal(request, system=SimulatorSystem.FILES)


@require_GET
@simulator_login_required
def accounting_portal(request: HttpRequest) -> HttpResponse:
    return _render_service_portal(request, system=SimulatorSystem.ACCOUNTING)


@require_POST
@simulator_login_required
def files_action(request: HttpRequest, external_id: str, action: str) -> HttpResponse:
    return _service_account_action(
        request,
        system=SimulatorSystem.FILES,
        external_id=external_id,
        action=action,
    )


@require_POST
@simulator_login_required
def accounting_action(request: HttpRequest, external_id: str, action: str) -> HttpResponse:
    return _service_account_action(
        request,
        system=SimulatorSystem.ACCOUNTING,
        external_id=external_id,
        action=action,
    )


@require_GET
@simulator_login_required
def tasks_portal(request: HttpRequest) -> HttpResponse:
    return _render_tasks_portal(request)


@require_POST
@simulator_login_required
def tasks_action(request: HttpRequest, external_id: str, action: str) -> HttpResponse:
    command = _command(request, action=action)
    if isinstance(command, str):
        return _render_tasks_portal(request, operation_error=command, status=400)
    scenario, phase = command
    fault = _fault(system="tasks", scenario=scenario, phase=phase)
    if fault is not None:
        message, status = fault
        return _render_tasks_portal(request, operation_error=message, status=status)

    with transaction.atomic():
        try:
            client = SimulatorClient.objects.select_for_update().get(external_id=external_id)
        except SimulatorClient.DoesNotExist as exc:
            raise Http404("Cliente não encontrado no portal de tarefas.") from exc
        tasks = list(
            SimulatorTask.objects.select_for_update().filter(client=client).order_by("reference")
        )
        if action == "block":
            changed = _block_open_tasks(tasks)
            operation = "bloqueadas"
        else:
            inconsistent = any(
                task.assignee == BLOCKED_TASK_ASSIGNEE and not task.previous_assignee.strip()
                for task in tasks
            )
            if inconsistent:
                return _render_tasks_portal(
                    request,
                    operation_error=(
                        "Não foi possível desbloquear: uma ou mais tarefas estão sem "
                        "responsável anterior."
                    ),
                    status=409,
                )
            changed = _unblock_tasks(tasks)
            operation = "restauradas"

    messages.success(
        request,
        f"Operação concluída: {changed} tarefa(s) {operation} para {client.name}.",
    )
    return redirect("sc05_simulator:tasks")


def _service_account_action(
    request: HttpRequest,
    *,
    system: SimulatorSystem,
    external_id: str,
    action: str,
) -> HttpResponse:
    command = _command(request, action=action)
    if isinstance(command, str):
        return _render_service_portal(
            request,
            system=system,
            operation_error=command,
            status=400,
        )
    scenario, phase = command
    fault = _fault(system=system, scenario=scenario, phase=phase)
    if fault is not None:
        message, status = fault
        return _render_service_portal(
            request,
            system=system,
            operation_error=message,
            status=status,
        )

    with transaction.atomic():
        try:
            account = (
                SimulatorServiceAccount.objects.select_for_update()
                .select_related("client")
                .get(client__external_id=external_id, system=system)
            )
        except SimulatorServiceAccount.DoesNotExist as exc:
            raise Http404("Conta não encontrada neste portal.") from exc
        target_state = action == "block"
        changed = account.is_blocked != target_state
        if changed:
            account.is_blocked = target_state
            account.save(update_fields=("is_blocked",))

    state_label = "bloqueada" if target_state else "ativa"
    suffix = "" if changed else " (estado já confirmado)"
    messages.success(
        request,
        f"Conta de {account.client.name} {state_label}{suffix}.",
    )
    return redirect(_service_url_name(system))


def _block_open_tasks(tasks: list[SimulatorTask]) -> int:
    changed = 0
    for task in tasks:
        if not task.is_open or task.assignee == BLOCKED_TASK_ASSIGNEE:
            continue
        task.previous_assignee = task.assignee
        task.assignee = BLOCKED_TASK_ASSIGNEE
        task.save(update_fields=("assignee", "previous_assignee"))
        changed += 1
    return changed


def _unblock_tasks(tasks: list[SimulatorTask]) -> int:
    changed = 0
    for task in tasks:
        if task.assignee != BLOCKED_TASK_ASSIGNEE:
            continue
        task.assignee = task.previous_assignee
        task.previous_assignee = ""
        task.save(update_fields=("assignee", "previous_assignee"))
        changed += 1
    return changed


def _command(request: HttpRequest, *, action: str) -> tuple[str, str] | str:
    if action not in ACTIONS:
        return "Ação inválida."
    scenario = request.POST.get("scenario", "").strip()
    phase = request.POST.get("phase", "").strip() or "apply"
    if scenario not in SCENARIOS:
        return "Cenário de demonstração inválido."
    if phase not in PHASES:
        return "Fase da operação inválida."
    return scenario, phase


def _fault(*, system: str, scenario: str, phase: str) -> tuple[str, int] | None:
    if scenario == "fail_tasks_apply" and system == "tasks" and phase == "apply":
        return "O portal de tarefas recusou a operação antes de aplicar alterações.", 409
    if (
        scenario == "timeout_accounting_apply"
        and system == SimulatorSystem.ACCOUNTING
        and phase == "apply"
    ):
        return "O portal contábil excedeu o tempo limite antes de aplicar alterações.", 504
    if scenario == "fail_tasks_apply_and_files_compensation":
        if system == "tasks" and phase == "apply":
            return "O portal de tarefas recusou a operação antes de aplicar alterações.", 409
        if system == SimulatorSystem.FILES and phase == "compensation":
            return "O portal de arquivos recusou a compensação antes de alterar a conta.", 409
    return None


def _render_service_portal(
    request: HttpRequest,
    *,
    system: SimulatorSystem,
    operation_error: str = "",
    status: int = 200,
) -> HttpResponse:
    accounts = SimulatorServiceAccount.objects.filter(system=system).select_related("client")
    rows = [
        {
            "account": account,
            "client": account.client,
            "dom_prefix": f"{system}-{account.client.external_id}",
        }
        for account in accounts
    ]
    return render(
        request,
        "sc05_simulator/service_portal.html",
        {
            "simulator_authenticated": True,
            "system": str(system),
            "system_label": SimulatorSystem(system).label,
            "action_url_name": f"sc05_simulator:{system}-action",
            "rows": rows,
            "operation_error": operation_error,
        },
        status=status,
    )


def _render_tasks_portal(
    request: HttpRequest,
    *,
    operation_error: str = "",
    status: int = 200,
) -> HttpResponse:
    clients = (
        SimulatorClient.objects.prefetch_related("tasks").filter(tasks__isnull=False).distinct()
    )
    rows = [
        {
            "client": client,
            "tasks": list(client.tasks.all()),
            "is_blocked": any(
                task.assignee == BLOCKED_TASK_ASSIGNEE for task in client.tasks.all()
            ),
        }
        for client in clients
    ]
    return render(
        request,
        "sc05_simulator/tasks_portal.html",
        {
            "simulator_authenticated": True,
            "rows": rows,
            "blocked_assignee": BLOCKED_TASK_ASSIGNEE,
            "operation_error": operation_error,
        },
        status=status,
    )


def _service_url_name(system: SimulatorSystem) -> str:
    return f"sc05_simulator:{system}"
