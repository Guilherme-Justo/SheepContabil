from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Self

from core.automations.models import SC05Action, SC05Portal

PortalState = dict[str, Any]


class SC05Error(Exception):
    """Safe operational error that can be presented in the portal."""

    code = "sc05_error"
    safe_message = "A automação não conseguiu concluir a operação."
    transient = False

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.safe_message)
        self.safe_message = message or type(self).safe_message


class SC05ConfigurationError(SC05Error):
    code = "configuration_error"
    safe_message = "O ambiente RPA do SC-05 não está configurado."


class PortalOperationError(SC05Error):
    code = "portal_operation_error"
    safe_message = "Um dos sistemas não confirmou a alteração."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        transient: bool = False,
        screenshot: bytes = b"",
    ) -> None:
        super().__init__(message)
        if code:
            self.code = code
        self.transient = transient
        self.screenshot = screenshot


class PortalTimeoutError(PortalOperationError):
    code = "portal_timeout"
    safe_message = "O sistema demorou além do limite e não confirmou a alteração."
    transient = True


class PortalAuthenticationError(PortalOperationError):
    code = "portal_authentication"
    safe_message = "O sistema recusou a autenticação do robô."


class PortalStateConflictError(PortalOperationError):
    code = "portal_state_conflict"
    safe_message = (
        "O estado do sistema mudou depois da captura; a automação não sobrescreveu a alteração."
    )


class ArtifactStorageError(SC05Error):
    code = "artifact_storage_error"
    safe_message = "A evidência visual não pôde ser preservada no storage privado."
    transient = True


@dataclass(frozen=True, slots=True)
class PortalEvidence:
    state: PortalState
    screenshot: bytes


@dataclass(frozen=True, slots=True)
class StoredScreenshot:
    key: str
    sha256: str
    byte_size: int
    content_type: str = "image/png"


@dataclass(frozen=True, slots=True)
class SC05ExecutionResult:
    applied: int
    unchanged: int
    compensated: int
    failed: int
    partially_failed: bool


class PortalGateway(Protocol):
    portal: str

    def inspect(
        self,
        *,
        client_reference: str,
        scenario: str,
        phase: str,
    ) -> PortalEvidence: ...

    def apply(
        self,
        *,
        client_reference: str,
        action: SC05Action,
        scenario: str,
        phase: str,
    ) -> PortalEvidence: ...

    def restore(
        self,
        *,
        client_reference: str,
        expected_current_state: PortalState,
        target_state: PortalState,
        scenario: str,
        phase: str,
    ) -> PortalEvidence: ...


class PortalGatewaySession(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...

    def gateway(self, portal: SC05Portal) -> PortalGateway: ...


class ScreenshotStorage(Protocol):
    def put(self, *, key: str, content: bytes) -> StoredScreenshot: ...

    def get(self, *, key: str) -> bytes: ...
