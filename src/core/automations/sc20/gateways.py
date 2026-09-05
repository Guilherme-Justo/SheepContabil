from __future__ import annotations

import hashlib
import logging
import smtplib
from dataclasses import dataclass, field
from typing import Any, Protocol

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    recipient: str
    channel: str
    subject: str
    body: str
    idempotency_key: str
    extra_context: dict[str, Any] = field(default_factory=dict)


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


class DjangoEmailNotificationGateway:
    """Dispatches notifications via Django email subsystem with Gmail SMTP and sandbox support."""

    def __init__(
        self,
        *,
        from_email: str | None = None,
        recipient_override: str | None = None,
        site_url: str | None = None,
    ) -> None:
        self.from_email = from_email or getattr(
            settings, "DEFAULT_FROM_EMAIL", "SheepContabil <avisos@sheepcontabil.local>"
        )
        self.recipient_override = (
            recipient_override
            if recipient_override is not None
            else getattr(settings, "SC20_EMAIL_OVERRIDE_TO", "")
        ).strip()
        self.site_url = site_url if site_url is not None else getattr(settings, "SITE_URL", "")

    def send(self, message: NotificationMessage) -> DeliveryResult:
        if message.channel != "email":
            # Para canais sem envio direto (ex: WhatsApp), recai no simulador determinístico
            return SimulatedNotificationGateway().send(message)

        recipient = message.recipient.strip()
        if not recipient:
            return DeliveryResult(
                delivered=False,
                error_message="Contato de e-mail não informado para o certificado.",
            )

        override_applied = bool(self.recipient_override)
        destination_email = self.recipient_override if override_applied else recipient

        subject = message.subject
        if override_applied:
            subject = f"[TESTE SC-20 · Para: {recipient}] {subject}"

        template_context = {
            **message.extra_context,
            "original_recipient": recipient,
            "destination_email": destination_email,
            "override_applied": override_applied,
            "site_url": self.site_url,
            "subject": subject,
            "body": message.body,
        }

        try:
            html_content: str | None = render_to_string(
                "automations/emails/sc20_certificate_expiry.html",
                template_context,
            )
            text_content: str = render_to_string(
                "automations/emails/sc20_certificate_expiry.txt",
                template_context,
            )
        except Exception as template_err:
            logger.warning(
                "Falha ao renderizar template de e-mail SC-20 (%s). Usando texto simples.",
                template_err,
            )
            text_content = message.body
            html_content = None

        email = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=self.from_email,
            to=[destination_email],
        )
        if html_content:
            email.attach_alternative(html_content, "text/html")

        email.extra_headers["X-SheepContabil-Module"] = "SC-20"
        email.extra_headers["X-SheepContabil-Idempotency-Key"] = message.idempotency_key

        try:
            email.send(fail_silently=False)
            message_id = (
                getattr(email, "extra_headers", {}).get("Message-ID")
                or f"smtp-{hashlib.sha256(message.idempotency_key.encode()).hexdigest()[:16]}"
            )
            return DeliveryResult(delivered=True, provider_message_id=str(message_id))
        except smtplib.SMTPAuthenticationError:
            logger.exception(
                "Falha de autenticação SMTP ao enviar aviso SC-20 para %s", destination_email
            )
            return DeliveryResult(
                delivered=False,
                error_message=(
                    "Falha de autenticação no Gmail SMTP (535). "
                    "Certifique-se de usar uma 'Senha de Aplicativo' de 16 caracteres gerada "
                    "na Conta Google (https://myaccount.google.com/apppasswords) e não a "
                    "sua senha pessoal."
                ),
            )
        except TimeoutError:
            logger.exception("Timeout de rede ao enviar e-mail SC-20 para %s", destination_email)
            return DeliveryResult(
                delivered=False,
                error_message=(
                    "Tempo limite de conexão esgotado ao contatar o servidor SMTP do Gmail "
                    "(smtp.gmail.com:587)."
                ),
            )
        except smtplib.SMTPRecipientsRefused as recip_err:
            logger.exception("Destinatário recusado pelo SMTP: %s", recip_err)
            return DeliveryResult(
                delivered=False,
                error_message=f"O servidor de e-mail recusou o destinatário {destination_email}.",
            )
        except Exception as exc:
            logger.exception(
                "Erro ao disparar e-mail SC-20 via Django Email Backend: %s", exc
            )
            return DeliveryResult(
                delivered=False,
                error_message=f"Erro de entrega via SMTP: {exc}",
            )


def get_sc20_gateway() -> NotificationGateway:
    backend = getattr(settings, "SC20_NOTIFICATION_BACKEND", "simulated").strip().lower()
    if backend == "email":
        return DjangoEmailNotificationGateway()
    return SimulatedNotificationGateway()
