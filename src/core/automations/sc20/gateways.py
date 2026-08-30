from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    recipient: str
    channel: str
    subject: str
    body: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: bool
    provider_message_id: str = ""
    error_message: str = ""


class NotificationGateway(Protocol):
    def send(self, message: NotificationMessage) -> DeliveryResult: ...


class SimulatedNotificationGateway:
    """Deterministic adapter used by the challenge environment.

    Recipients containing ``falha`` or ending in the reserved ``.invalid``
    domain fail on their first attempt and recover on an explicit retry. This
    exercises the transient failure path without contacting anyone.
    """

    def send(self, message: NotificationMessage) -> DeliveryResult:
        recipient = message.recipient.strip().lower()
        if not recipient:
            return DeliveryResult(
                delivered=False,
                error_message="Contato não informado para o canal selecionado.",
            )
        first_attempt = message.idempotency_key.rsplit(":", maxsplit=1)[-1] == "1"
        if first_attempt and ("falha" in recipient or recipient.endswith(".invalid")):
            return DeliveryResult(
                delivered=False,
                error_message="Entrega simulada indisponível; uma nova tentativa pode ser feita.",
            )
        digest = hashlib.sha256(message.idempotency_key.encode()).hexdigest()[:16]
        return DeliveryResult(delivered=True, provider_message_id=f"sim-{digest}")
