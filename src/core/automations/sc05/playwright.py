from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from functools import partial
from types import TracebackType
from typing import Any, TypeVar
from urllib.parse import urljoin, urlparse

from django.conf import settings
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import (
    Error as PlaywrightError,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from core.automations.models import SC05Action, SC05Portal
from core.automations.sc05.contracts import (
    PortalAuthenticationError,
    PortalEvidence,
    PortalGateway,
    PortalOperationError,
    PortalState,
    PortalStateConflictError,
    PortalTimeoutError,
    SC05ConfigurationError,
)
from core.automations.sc05.services import BLOCKED_TASK_OWNER

_T = TypeVar("_T")


class PlaywrightPortalSession:
    """One browser per saga, isolated on a thread away from Django's sync ORM."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_ms: int,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.username = username
        self.password = password
        self.timeout_ms = timeout_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._gateways: dict[SC05Portal, PortalGateway] = {
            SC05Portal.FILES: _AccountPortalGateway(self, SC05Portal.FILES),
            SC05Portal.ACCOUNTING: _AccountPortalGateway(self, SC05Portal.ACCOUNTING),
            SC05Portal.TASKS: _TasksPortalGateway(self),
        }

    def __enter__(self) -> PlaywrightPortalSession:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sc05-rpa")
        try:
            self._run(self._open)
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            if isinstance(exc, PlaywrightTimeoutError):
                raise PortalTimeoutError(
                    "O simulador não respondeu durante a autenticação do robô."
                ) from exc
            raise PortalOperationError(
                "O navegador RPA não conseguiu abrir o simulador.",
                code="browser_start_failed",
                transient=True,
            ) from exc
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        executor = self._executor
        if executor is None:
            return
        with suppress(Exception):
            executor.submit(self._close).result()
        executor.shutdown(wait=True, cancel_futures=True)
        self._executor = None

    def _open(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            locale="pt-BR",
            viewport={"width": 1440, "height": 1000},
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        self._login()

    def _close(self) -> None:
        for resource in (self._context, self._browser, self._playwright):
            if resource is None:
                continue
            with suppress(PlaywrightError):
                resource.stop() if isinstance(resource, Playwright) else resource.close()
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    def _run(self, call: Callable[[], _T]) -> _T:
        executor = self._executor
        if executor is None:
            raise SC05ConfigurationError("A sessão de navegador do SC-05 não foi iniciada.")
        return executor.submit(call).result()

    def gateway(self, portal: SC05Portal) -> PortalGateway:
        try:
            return self._gateways[portal]
        except KeyError as exc:
            raise SC05ConfigurationError("O portal solicitado não possui adapter RPA.") from exc

    @property
    def _page_required(self) -> Page:
        if self._page is None:
            raise SC05ConfigurationError("A sessão de navegador do SC-05 não foi iniciada.")
        return self._page

    def navigate(self, path: str) -> None:
        self._run(partial(self._navigate, path))

    def _navigate(self, path: str) -> None:
        try:
            response = self._page_required.goto(
                urljoin(self.base_url, path.lstrip("/")),
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
        except PlaywrightTimeoutError as exc:
            raise PortalTimeoutError() from exc
        except PlaywrightError as exc:
            raise PortalOperationError(
                "O sistema simulado está temporariamente indisponível.",
                code="portal_unavailable",
                transient=True,
            ) from exc
        if response is not None and response.status >= 500:
            raise PortalOperationError(
                "O sistema simulado respondeu com indisponibilidade.",
                code="portal_server_error",
                transient=True,
            )
        if "/login/" in self._page_required.url and path.strip("/") != "login":
            raise PortalAuthenticationError()

    def screenshot(self, *, test_id: str) -> bytes:
        return self._run(partial(self._screenshot, test_id=test_id))

    def _screenshot(self, *, test_id: str) -> bytes:
        try:
            target = self._page_required.get_by_test_id(test_id)
            _ensure_exactly_one(target)
            return target.screenshot(type="png")
        except PlaywrightTimeoutError as exc:
            raise PortalTimeoutError(
                "A captura da evidência visual excedeu o tempo limite."
            ) from exc
        except PlaywrightError as exc:
            raise PortalOperationError(
                "O navegador não conseguiu capturar a evidência visual.",
                code="screenshot_failed",
                transient=True,
            ) from exc

    def submit(
        self,
        *,
        prefix: str,
        action: SC05Action,
        scenario: str,
        phase: str,
    ) -> None:
        self._run(
            partial(
                self._submit,
                prefix=prefix,
                action=action,
                scenario=scenario,
                phase=phase,
            )
        )

    def _submit(
        self,
        *,
        prefix: str,
        action: SC05Action,
        scenario: str,
        phase: str,
    ) -> None:
        action_value = str(action)
        try:
            self._page_required.get_by_test_id(f"{prefix}-{action_value}-scenario").evaluate(
                "(element, value) => { element.value = value; }",
                scenario,
            )
            self._page_required.get_by_test_id(f"{prefix}-{action_value}-phase").evaluate(
                "(element, value) => { element.value = value; }",
                phase,
            )
            self._page_required.get_by_test_id(f"{prefix}-{action_value}-submit").click(
                timeout=self.timeout_ms
            )
            self._page_required.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise PortalTimeoutError() from exc
        except PlaywrightError as exc:
            raise PortalOperationError(
                "A interface do sistema mudou e o robô não encontrou a ação esperada.",
                code="portal_selector_changed",
            ) from exc
        error = self._page_required.get_by_test_id("operation-error")
        if error.count():
            message = error.first.inner_text().strip()
            screenshot = self._error_screenshot(error.first)
            if "tempo limite" in message.lower() or "timeout" in scenario:
                raise PortalTimeoutError(message, screenshot=screenshot)
            raise PortalOperationError(
                message or "O sistema recusou a operação.",
                code="portal_rejected_operation",
                screenshot=screenshot,
            )
        if "/login/" in self._page_required.url:
            raise PortalAuthenticationError()

    def _error_screenshot(self, error: Locator) -> bytes:
        try:
            return error.screenshot(type="png")
        except (PlaywrightTimeoutError, PlaywrightError):
            return b""

    def read_account_state(self, *, portal: str, client_reference: str) -> PortalState:
        return self._run(
            partial(
                self._read_account_state,
                portal=portal,
                client_reference=client_reference,
            )
        )

    def _read_account_state(self, *, portal: str, client_reference: str) -> PortalState:
        status = _required_text(
            self._page_required.get_by_test_id(f"{portal}-{client_reference}-status")
        )
        if status == "BLOCKED":
            return {"blocked": True}
        if status == "ACTIVE":
            return {"blocked": False}
        raise PortalStateConflictError("O sistema retornou um estado de conta desconhecido.")

    def read_tasks_state(self, *, client_reference: str) -> PortalState:
        return self._run(partial(self._read_tasks_state, client_reference=client_reference))

    def _read_tasks_state(self, *, client_reference: str) -> PortalState:
        client_row = self._page_required.get_by_test_id(f"tasks-client-{client_reference}-row")
        _ensure_exactly_one(client_row)
        client_state = _required_text(
            client_row.get_by_test_id(f"tasks-client-{client_reference}-active-state")
        )
        if client_state not in {"ACTIVE", "INACTIVE"}:
            raise PortalStateConflictError(
                "O sistema de tarefas retornou um estado desconhecido para o cliente."
            )
        task_rows = client_row.locator("tbody tr[data-testid^='task-']")
        tasks: list[dict[str, Any]] = []
        for row in task_rows.all():
            reference = _required_text(row.locator("[data-testid$='-reference']"))
            state = _required_text(row.get_by_test_id(f"task-{reference}-state"))
            assignee = _required_text(row.get_by_test_id(f"task-{reference}-assignee"))
            if state not in {"OPEN", "CLOSED"}:
                raise PortalStateConflictError(
                    "O sistema de tarefas retornou um estado desconhecido."
                )
            tasks.append(
                {
                    "reference": reference,
                    "assignee": assignee,
                    "is_open": state == "OPEN",
                }
            )
        tasks.sort(key=lambda item: str(item["reference"]))
        return {"client_active": client_state == "ACTIVE", "tasks": tasks}

    def _login(self) -> None:
        self._navigate("login/")
        try:
            self._page_required.get_by_test_id("simulator-username").fill(self.username)
            self._page_required.get_by_test_id("simulator-password").fill(self.password)
            self._page_required.get_by_test_id("simulator-login-submit").click(
                timeout=self.timeout_ms
            )
            self._page_required.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise PortalTimeoutError(
                "O simulador não respondeu durante a autenticação do robô."
            ) from exc
        except PlaywrightError as exc:
            raise PortalAuthenticationError() from exc
        if (
            self._page_required.get_by_test_id("login-error").count()
            or "/login/" in self._page_required.url
        ):
            raise PortalAuthenticationError()


class _AccountPortalGateway:
    def __init__(self, session: PlaywrightPortalSession, portal: SC05Portal) -> None:
        if portal not in {SC05Portal.FILES, SC05Portal.ACCOUNTING}:
            raise ValueError("Account gateway supports only files and accounting portals.")
        self._session = session
        self._selected_portal = portal
        self.portal = str(portal)

    def inspect(
        self,
        *,
        client_reference: str,
        scenario: str,
        phase: str,
    ) -> PortalEvidence:
        del scenario, phase
        self._session.navigate(f"{self.portal}/")
        state = self._read_state(client_reference)
        return PortalEvidence(
            state=state,
            screenshot=self._session.screenshot(test_id=f"{self.portal}-{client_reference}-row"),
        )

    def apply(
        self,
        *,
        client_reference: str,
        action: SC05Action,
        scenario: str,
        phase: str,
    ) -> PortalEvidence:
        self._session.navigate(f"{self.portal}/")
        self._session.submit(
            prefix=f"{self.portal}-{client_reference}",
            action=action,
            scenario=scenario,
            phase=phase,
        )
        state = self._read_state(client_reference)
        return PortalEvidence(
            state=state,
            screenshot=self._session.screenshot(test_id=f"{self.portal}-{client_reference}-row"),
        )

    def restore(
        self,
        *,
        client_reference: str,
        expected_current_state: PortalState,
        target_state: PortalState,
        scenario: str,
        phase: str,
    ) -> PortalEvidence:
        current = self.inspect(
            client_reference=client_reference,
            scenario=scenario,
            phase=phase,
        ).state
        if current != expected_current_state:
            raise PortalStateConflictError()
        blocked = target_state.get("blocked")
        if not isinstance(blocked, bool) or set(target_state) != {"blocked"}:
            raise PortalStateConflictError("O snapshot da conta é inválido para restauração.")
        action = SC05Action.BLOCK if blocked else SC05Action.UNBLOCK
        result = self.apply(
            client_reference=client_reference,
            action=action,
            scenario=scenario,
            phase=phase,
        )
        if result.state != target_state:
            raise PortalStateConflictError()
        return result

    def _read_state(self, client_reference: str) -> PortalState:
        return self._session.read_account_state(
            portal=self.portal,
            client_reference=client_reference,
        )


class _TasksPortalGateway:
    portal = str(SC05Portal.TASKS)

    def __init__(self, session: PlaywrightPortalSession) -> None:
        self._session = session

    def inspect(
        self,
        *,
        client_reference: str,
        scenario: str,
        phase: str,
    ) -> PortalEvidence:
        del scenario, phase
        self._session.navigate("tasks/")
        state = self._read_state(client_reference)
        return PortalEvidence(
            state=state,
            screenshot=self._session.screenshot(test_id=f"tasks-client-{client_reference}-row"),
        )

    def apply(
        self,
        *,
        client_reference: str,
        action: SC05Action,
        scenario: str,
        phase: str,
    ) -> PortalEvidence:
        self._session.navigate("tasks/")
        self._session.submit(
            prefix=f"tasks-client-{client_reference}",
            action=action,
            scenario=scenario,
            phase=phase,
        )
        state = self._read_state(client_reference)
        return PortalEvidence(
            state=state,
            screenshot=self._session.screenshot(test_id=f"tasks-client-{client_reference}-row"),
        )

    def restore(
        self,
        *,
        client_reference: str,
        expected_current_state: PortalState,
        target_state: PortalState,
        scenario: str,
        phase: str,
    ) -> PortalEvidence:
        current = self.inspect(
            client_reference=client_reference,
            scenario=scenario,
            phase=phase,
        ).state
        if current != expected_current_state:
            raise PortalStateConflictError()
        action = (
            SC05Action.BLOCK
            if _target_represents_blocked_tasks(target_state)
            else SC05Action.UNBLOCK
        )
        result = self.apply(
            client_reference=client_reference,
            action=action,
            scenario=scenario,
            phase=phase,
        )
        if result.state != target_state:
            raise PortalStateConflictError()
        return result

    def _read_state(self, client_reference: str) -> PortalState:
        return self._session.read_tasks_state(client_reference=client_reference)


def _required_text(locator: Locator) -> str:
    try:
        _ensure_exactly_one(locator)
        value = locator.inner_text().strip()
    except PlaywrightTimeoutError as exc:
        raise PortalTimeoutError() from exc
    except PlaywrightError as exc:
        raise PortalOperationError(
            "A interface do sistema mudou e um campo esperado não foi encontrado.",
            code="portal_selector_changed",
        ) from exc
    if not value:
        raise PortalStateConflictError("O sistema retornou um campo obrigatório vazio.")
    return value


def _ensure_exactly_one(locator: Locator) -> None:
    count = locator.count()
    if count != 1:
        raise PortalOperationError(
            "A interface não identificou o cliente de forma única.",
            code="portal_client_not_unique",
        )


def _target_represents_blocked_tasks(target_state: PortalState) -> bool:
    if target_state.get("client_active") is not True:
        raise PortalStateConflictError("O snapshot não preserva o cliente ativo em tarefas.")
    tasks = target_state.get("tasks")
    if not isinstance(tasks, list):
        raise PortalStateConflictError("O snapshot das tarefas é inválido.")
    open_tasks = [task for task in tasks if isinstance(task, dict) and task.get("is_open") is True]
    return bool(open_tasks) and all(
        task.get("assignee") == BLOCKED_TASK_OWNER for task in open_tasks
    )


def build_playwright_session() -> PlaywrightPortalSession:
    base_url = str(settings.SC05_SIMULATOR_BASE_URL).strip()
    username = str(settings.SC05_SIMULATOR_USERNAME).strip()
    password = str(settings.SC05_SIMULATOR_PASSWORD)
    timeout_ms = int(settings.SC05_RPA_TIMEOUT_MS)
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SC05ConfigurationError("A URL privada do simulador SC-05 é inválida.")
    if not username or not password:
        raise SC05ConfigurationError(
            "As credenciais sintéticas do simulador não estão configuradas."
        )
    if timeout_ms < 1_000 or timeout_ms > 120_000:
        raise SC05ConfigurationError("O timeout do RPA deve ficar entre 1 e 120 segundos.")
    return PlaywrightPortalSession(
        base_url=base_url,
        username=username,
        password=password,
        timeout_ms=timeout_ms,
    )
