from __future__ import annotations

import urllib.parse
from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core.automations.models import (
    AutomationModule,
    CertificateCommunication,
    CertificateStatus,
    CommunicationAttempt,
    CommunicationChannel,
    CommunicationStatus,
    DigitalCertificate,
)
from core.automations.sc20.services import create_sc20_run
from core.identity.models import User

pytestmark = pytest.mark.django_db


def _cert_whatsapp(
    *,
    serial: str = "WPP-01",
    phone: str = "+55 (11) 99999-2002",
    days: int = 15,
) -> DigitalCertificate:
    return DigitalCertificate.objects.create(
        serial_number=serial,
        client_name="Aurora Serviços Fictícios",
        client_document="12.345.678/0001-90",
        responsible_name="Ana Paula",
        contact_email="ana@aurora.example.test",
        contact_phone=phone,
        preferred_channel=CommunicationChannel.WHATSAPP,
        valid_until=timezone.localdate() + timedelta(days=days),
        status=CertificateStatus.ACTIVE,
    )


def test_certificate_has_whatsapp_flag() -> None:
    with_phone = _cert_whatsapp(serial="WPP-PHONE", phone="+55 11 99999-0000")
    without_phone = DigitalCertificate.objects.create(
        serial_number="NO-PHONE",
        client_name="Sem Telefone Ltda.",
        client_document="12345678000190",
        responsible_name="Gestor",
        contact_email="sem@telefone.local",
        contact_phone="",
        preferred_channel=CommunicationChannel.EMAIL,
        valid_until=timezone.localdate() + timedelta(days=10),
        status=CertificateStatus.ACTIVE,
    )

    assert with_phone.has_whatsapp is True
    assert without_phone.has_whatsapp is False


def test_certificate_whatsapp_url_normalization_and_message_content() -> None:
    cert = _cert_whatsapp(serial="WPP-NORM", phone="(11) 98888-7777", days=14)

    url = cert.whatsapp_url()

    # Normalizou 11 dígitos acrescentando DDI 55
    assert url.startswith("https://api.whatsapp.com/send?phone=5511988887777&text=")

    # Decodifica o texto para validar conteúdo limpo Clean B2B sem emojis de 4 bytes
    encoded_text = url.split("&text=")[1]
    decoded = urllib.parse.unquote(encoded_text)

    # Garante ausência dos emojis que causam corrupção e substituição por 
    assert "🔔" not in decoded
    assert "📋" not in decoded
    assert "💡" not in decoded
    assert "\ufffd" not in decoded

    # Cabeçalhos executivos corporativos
    assert "*SheepContabil · Monitoramento de Certificados Digitais*" in decoded
    assert "*Dados do Certificado:*" in decoded
    assert "*Orientação para Renovação:*" in decoded
    assert "• Empresa: *Aurora Serviços Fictícios*" in decoded
    assert "• Documento: 12.345.678/0001-90" in decoded
    assert "Ana Paula" in decoded
    assert "WPP-NORM" in decoded
    assert "Vencimento Crítico" in decoded
    assert "Autoridade Certificadora" in decoded


def test_certificate_whatsapp_url_sandbox_override() -> None:
    cert = _cert_whatsapp(serial="WPP-SANDBOX", phone="+55 11 99999-2002", days=25)

    with override_settings(SC20_WHATSAPP_OVERRIDE_TO="5521977776666"):
        url = cert.whatsapp_url()

    # Destinatário do link foi redirecionado para o número do sandbox
    assert url.startswith("https://api.whatsapp.com/send?phone=5521977776666&text=")

    encoded_text = url.split("&text=")[1]
    decoded = urllib.parse.unquote(encoded_text)

    assert "AMBIENTE DE TESTE SHEEPCONTABIL" in decoded
    assert "Destinatário original: +55 11 99999-2002" in decoded
    assert "Aurora Serviços Fictícios" in decoded


def test_communication_attempt_whatsapp_properties(
    modules: dict[str, AutomationModule],
) -> None:
    cert = _cert_whatsapp(serial="WPP-ATTEMPT", phone="11999998888", days=10)
    run = create_sc20_run(triggered_by=None, base_date=timezone.localdate())

    comm = CertificateCommunication.objects.create(
        certificate=cert,
        certificate_valid_until=cert.valid_until,
        channel=CommunicationChannel.WHATSAPP,
        recipient=cert.contact_phone,
        policy_key="sc20-60-days-v1",
        first_run=run,
        latest_run=run,
    )
    attempt = CommunicationAttempt.objects.create(
        communication=comm,
        run=run,
        sequence=1,
        status=CommunicationStatus.SENT,
        recipient=cert.contact_phone,
        provider_message_id="sim-12345",
        payload={"synthetic": True},
    )

    assert attempt.is_whatsapp is True
    assert attempt.whatsapp_url.startswith("https://api.whatsapp.com/send?phone=5511999998888")


def test_sc20_page_renders_whatsapp_button(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    _cert_whatsapp(serial="WPP-UI-TEST", phone="+55 11 99999-2002", days=10)
    client.force_login(processes_operator)

    url = reverse("automations:module-detail", kwargs={"slug": modules["SC-20"].slug})
    response = client.get(url)

    assert response.status_code == 200
    html = response.content.decode()

    # O botão de WhatsApp deve estar presente na tabela de certificados
    assert "sc20-whatsapp-pill" in html
    assert "https://api.whatsapp.com/send?phone=5511999992002" in html
    assert 'target="_blank"' in html


def test_run_detail_page_renders_whatsapp_link_for_whatsapp_attempt(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    cert = _cert_whatsapp(serial="WPP-RUN-TEST", phone="+55 11 99999-2002", days=10)
    run = create_sc20_run(triggered_by=processes_operator, base_date=timezone.localdate())

    comm = CertificateCommunication.objects.create(
        certificate=cert,
        certificate_valid_until=cert.valid_until,
        channel=CommunicationChannel.WHATSAPP,
        recipient=cert.contact_phone,
        policy_key="sc20-60-days-v1",
        first_run=run,
        latest_run=run,
    )
    CommunicationAttempt.objects.create(
        communication=comm,
        run=run,
        sequence=1,
        status=CommunicationStatus.SENT,
        recipient=cert.contact_phone,
        provider_message_id="sim-wpp-001",
        payload={"synthetic": True, "backend": "whatsapp"},
    )

    client.force_login(processes_operator)
    detail_url = reverse("automations:run-detail", kwargs={"run_id": run.id})
    response = client.get(detail_url)

    assert response.status_code == 200
    html = response.content.decode()

    # Na tabela de tentativas da execução deve haver o botão de WhatsApp
    assert "https://api.whatsapp.com/send?phone=5511999992002" in html
    assert "sc20-whatsapp-pill" in html


def test_certificate_whatsapp_url_formats_unformatted_document() -> None:
    cert = _cert_whatsapp(serial="WPP-DOC-FMT", phone="+55 11 99999-1111", days=10)
    cert.client_document = "11222333000181"
    cert.save()

    url = cert.whatsapp_url()
    decoded = urllib.parse.unquote_plus(url)
    assert "• Documento: 11.222.333/0001-81" in decoded

    cert.client_document = "12345678901"
    cert.save()
    url_cpf = cert.whatsapp_url()
    decoded_cpf = urllib.parse.unquote_plus(url_cpf)
    assert "• Documento: 123.456.789-01" in decoded_cpf


def test_certificate_whatsapp_url_international_numbers() -> None:
    cert_us = _cert_whatsapp(serial="WPP-US", phone="+1 202 555-0199", days=10)
    url_us = cert_us.whatsapp_url()
    assert url_us.startswith("https://api.whatsapp.com/send?phone=12025550199&text=")

    cert_pt = _cert_whatsapp(serial="WPP-PT", phone="+351 912 345 678", days=10)
    url_pt = cert_pt.whatsapp_url()
    assert url_pt.startswith("https://api.whatsapp.com/send?phone=351912345678&text=")
