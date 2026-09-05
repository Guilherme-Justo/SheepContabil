from __future__ import annotations

import smtplib
from datetime import timedelta
from typing import cast
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core.automations.models import (
    AutomationModule,
    CertificateStatus,
    CommunicationChannel,
    CommunicationStatus,
    DigitalCertificate,
    RunStatus,
)
from core.automations.sc20.gateways import (
    DjangoEmailNotificationGateway,
    NotificationMessage,
    SimulatedNotificationGateway,
    get_sc20_gateway,
)
from core.automations.sc20.services import create_sc20_run, execute_sc20
from core.identity.models import User

pytestmark = pytest.mark.django_db


def _sample_certificate(
    *,
    serial: str = "TEST-CERT-01",
    email: str = "cliente@empresa.com.br",
    days: int = 15,
    channel: str = CommunicationChannel.EMAIL,
) -> DigitalCertificate:
    return DigitalCertificate.objects.create(
        serial_number=serial,
        client_name=f"Empresa Teste {serial}",
        client_document="12345678000190",
        responsible_name="Gestor Responsável",
        contact_email=email,
        contact_phone="+55 11 99999-8888",
        preferred_channel=channel,
        valid_until=timezone.localdate() + timedelta(days=days),
        status=CertificateStatus.ACTIVE,
    )


def test_django_email_gateway_sends_multipart_email_with_templates() -> None:
    gateway = DjangoEmailNotificationGateway(
        from_email="SheepContabil <avisos@sheepcontabil.local>",
        recipient_override="",
    )
    valid_date = timezone.localdate() + timedelta(days=10)
    message = NotificationMessage(
        recipient="contato@empresa.com.br",
        channel="email",
        subject="Certificado digital próximo do vencimento",
        body="O certificado de Alfa vence em 10 dias.",
        idempotency_key="test-key-01",
        extra_context={
            "client_name": "Alfa Participações Ltda.",
            "client_document": "12.345.678/0001-90",
            "responsible_name": "Juliana Gestora",
            "valid_until": valid_date,
            "days_remaining": 10,
            "serial_number": "ALFA-001",
        },
    )

    result = gateway.send(message)

    assert result.delivered is True
    assert result.error_message == ""
    assert result.provider_message_id.startswith("smtp-")

    assert len(mail.outbox) == 1
    sent = cast(EmailMultiAlternatives, mail.outbox[0])
    assert sent.to == ["contato@empresa.com.br"]
    assert sent.from_email == "SheepContabil <avisos@sheepcontabil.local>"
    assert sent.subject == "Certificado digital próximo do vencimento"
    assert sent.extra_headers["X-SheepContabil-Module"] == "SC-20"
    assert sent.extra_headers["X-SheepContabil-Idempotency-Key"] == "test-key-01"

    # Verifica renderização do texto puro
    assert "Alfa Participações Ltda." in sent.body
    assert "Juliana Gestora" in sent.body
    assert "12.345.678/0001-90" in sent.body
    assert "10 dia(s)" in sent.body

    # Verifica alternativa HTML
    assert len(sent.alternatives) == 1
    html_content, mimetype = sent.alternatives[0]
    assert mimetype == "text/html"
    assert "Sheep" in html_content
    assert "Contabil" in html_content
    assert "Alfa Participações Ltda." in html_content
    assert "Vencimento Crítico" in html_content  # 10 dias <= 15 dias gera badge crítico
    assert "12.345.678/0001-90" in html_content


def test_django_email_gateway_applies_sandbox_recipient_override() -> None:
    gateway = DjangoEmailNotificationGateway(
        from_email="SheepContabil <avisos@sheepcontabil.local>",
        recipient_override="homologacao@sheepcontabil.local",
    )
    valid_date = timezone.localdate() + timedelta(days=25)
    message = NotificationMessage(
        recipient="cliente.sintetico@example.test",
        channel="email",
        subject="Certificado digital próximo do vencimento",
        body="O certificado de Beta vence em 25 dias.",
        idempotency_key="test-key-sandbox",
        extra_context={
            "client_name": "Beta Comércio",
            "client_document": "98.765.432/0001-10",
            "responsible_name": "Roberto Fiscal",
            "valid_until": valid_date,
            "days_remaining": 25,
            "serial_number": "BETA-002",
        },
    )

    result = gateway.send(message)

    assert result.delivered is True
    assert len(mail.outbox) == 1
    sent = cast(EmailMultiAlternatives, mail.outbox[0])

    # Destinatário real foi interceptado para o endereço de homologação
    assert sent.to == ["homologacao@sheepcontabil.local"]
    # Assunto foi anotado com o destinatário original
    expected_subject = (
        "[TESTE SC-20 · Para: cliente.sintetico@example.test] "
        "Certificado digital próximo do vencimento"
    )
    assert sent.subject == expected_subject

    # O texto e o HTML contêm o aviso de sandbox
    assert "HOMOLOGAÇÃO" in sent.body.upper()
    assert "cliente.sintetico@example.test" in sent.body

    html_content, _ = sent.alternatives[0]
    assert "Ambiente de Homologação / Teste" in html_content
    assert "cliente.sintetico@example.test" in html_content
    assert "Atenção: restam 25 dias" in html_content  # 25 dias <= 30 dias gera badge de atenção


def test_django_email_gateway_whatsapp_falls_back_to_simulated() -> None:
    gateway = DjangoEmailNotificationGateway()
    message = NotificationMessage(
        recipient="+55 11 98888-7777",
        channel="whatsapp",
        subject="Aviso WhatsApp",
        body="Mensagem WhatsApp",
        idempotency_key="key-wpp:1",
    )

    result = gateway.send(message)

    assert result.delivered is True
    assert result.provider_message_id.startswith("sim-")
    assert len(mail.outbox) == 0


def test_django_email_gateway_handles_empty_recipient() -> None:
    gateway = DjangoEmailNotificationGateway(recipient_override="")
    message = NotificationMessage(
        recipient="   ",
        channel="email",
        subject="Sem destinatário",
        body="Corpo",
        idempotency_key="key-empty:1",
    )

    result = gateway.send(message)

    assert result.delivered is False
    assert "não informado" in result.error_message
    assert len(mail.outbox) == 0


def test_django_email_gateway_handles_gmail_auth_error_with_app_password_advice() -> None:
    gateway = DjangoEmailNotificationGateway()
    message = NotificationMessage(
        recipient="teste@empresa.com",
        channel="email",
        subject="Teste Auth",
        body="Corpo",
        idempotency_key="key-auth:1",
    )

    with patch("django.core.mail.EmailMultiAlternatives.send") as mock_send:
        mock_send.side_effect = smtplib.SMTPAuthenticationError(
            535, b"Authentication credentials invalid"
        )
        result = gateway.send(message)

    assert result.delivered is False
    assert "Falha de autenticação no Gmail SMTP (535)" in result.error_message
    assert "Senha de Aplicativo" in result.error_message


def test_django_email_gateway_handles_network_timeout() -> None:
    gateway = DjangoEmailNotificationGateway()
    message = NotificationMessage(
        recipient="teste@empresa.com",
        channel="email",
        subject="Teste Timeout",
        body="Corpo",
        idempotency_key="key-timeout:1",
    )

    with patch("django.core.mail.EmailMultiAlternatives.send") as mock_send:
        mock_send.side_effect = TimeoutError("timed out")
        result = gateway.send(message)

    assert result.delivered is False
    assert "Tempo limite de conexão esgotado" in result.error_message


def test_get_sc20_gateway_factory() -> None:
    with override_settings(SC20_NOTIFICATION_BACKEND="simulated"):
        gw_sim = get_sc20_gateway()
        assert isinstance(gw_sim, SimulatedNotificationGateway)

    with override_settings(SC20_NOTIFICATION_BACKEND="email"):
        gw_email = get_sc20_gateway()
        assert isinstance(gw_email, DjangoEmailNotificationGateway)


def test_execute_sc20_end_to_end_with_email_gateway(
    modules: dict[str, AutomationModule],
) -> None:
    _sample_certificate(serial="END2END-EMAIL", days=20)
    gateway = DjangoEmailNotificationGateway(recipient_override="sandbox@sheepcontabil.local")

    run = create_sc20_run(triggered_by=None, base_date=timezone.localdate())
    result = execute_sc20(run.id, gateway=gateway)

    assert result.selected == 1
    assert result.sent == 1
    assert result.failed == 0

    run.refresh_from_db()
    assert run.status == RunStatus.SUCCEEDED
    assert len(mail.outbox) == 1

    attempt = run.communication_attempts.get()
    assert attempt.status == CommunicationStatus.SENT
    assert attempt.payload["backend"] == "email"
    assert attempt.payload["synthetic"] is False
    assert attempt.provider_message_id.startswith("smtp-")


def test_sc20_views_shows_email_guidance_when_backend_is_email(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(processes_operator)
    url = reverse("automations:module-detail", kwargs={"slug": modules["SC-20"].slug})

    with override_settings(
        SC20_NOTIFICATION_BACKEND="email",
        SC20_EMAIL_OVERRIDE_TO="sandbox.operador@sheepcontabil.local",
    ):
        response = client.get(url)
        assert response.status_code == 200
        html = response.content.decode()
        assert "Envio real de e-mails ativado (Gmail SMTP)." in html
        assert "sandbox.operador@sheepcontabil.local" in html
