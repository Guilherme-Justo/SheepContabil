from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import sync_playwright

from core.automations.models import (
    AutomationModule,
    CertificateStatus,
    CommunicationChannel,
    DigitalCertificate,
)
from core.identity.models import User

pytestmark = [
    pytest.mark.django_db(transaction=True),
]

SCREENSHOT_DIR = Path(
    os.getenv(
        "PLAYWRIGHT_SCREENSHOT_DIR",
        r"C:\Users\guilh\.gemini\antigravity\brain\b46412c7-1118-42b9-89a8-659ca4e96ca5",
    )
)


def test_sc20_dispatch_flow_with_playwright(
    live_server,
    processes_operator: User,
    modules: dict[str, AutomationModule],
    settings,
) -> None:
    """End-to-end browser verification of SC-20 dispatch and WhatsApp links."""
    settings.SC20_NOTIFICATION_BACKEND = "email"
    settings.SC20_EMAIL_OVERRIDE_TO = "guilherme15rj@gmail.com"
    settings.SC20_WHATSAPP_OVERRIDE_TO = "5561991365756"
    today = timezone.localdate()

    # 1. Criação de certificados de teste
    DigitalCertificate.objects.create(
        serial_number="PW-EMAIL-001",
        client_name="Alpha Engenharia Ltda",
        client_document="11222333000188",
        responsible_name="Roberto Diretor",
        contact_email="roberto@alpha.example.test",
        preferred_channel=CommunicationChannel.EMAIL,
        valid_until=today + timedelta(days=12),
        status=CertificateStatus.ACTIVE,
    )

    DigitalCertificate.objects.create(
        serial_number="PW-WPP-002",
        client_name="Beta Distribuidora S/A",
        client_document="44555666000199",
        responsible_name="Clara Operações",
        contact_phone="+55 11 98888-7777",
        preferred_channel=CommunicationChannel.WHATSAPP,
        valid_until=today + timedelta(days=20),
        status=CertificateStatus.ACTIVE,
    )

    sc20 = modules["SC-20"]
    login_url = f"{live_server.url}{reverse('identity:login')}"
    module_path = reverse("automations:module-detail", kwargs={"slug": sc20.slug})
    module_url = f"{live_server.url}{module_path}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})

        # Intercepta qualquer chamada externa para o wa.me para testes determinísticos
        context.route(
            "https://wa.me/**",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="<html><body><h1>WhatsApp Web Simulado</h1></body></html>",
            ),
        )

        page = context.new_page()

        # Step 1: Login
        page.goto(login_url)
        page.fill('input[name="username"]', "operador")
        page.fill('input[name="password"]', "safe-test-password")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Step 2: Navegar até o módulo SC-20
        page.goto(module_url)
        page.wait_for_load_state("networkidle")

        assert "SC-20" in page.content()
        assert "Alpha Engenharia Ltda" in page.content()
        assert "Beta Distribuidora S/A" in page.content()
        assert "Envio real de e-mails ativado" in page.content()
        assert "guilherme15rj@gmail.com" in page.content()

        # Verifica existência do botão de WhatsApp na tabela de certificados
        whatsapp_pill = page.locator("a.sc20-whatsapp-pill").first
        assert whatsapp_pill.is_visible()
        pill_href = whatsapp_pill.get_attribute("href") or ""
        assert "https://wa.me/5561991365756" in pill_href
        assert "Beta" in unquote(pill_href)

        # Testa a máscara de telefone em tempo real no Chromium
        phone_input = page.locator('input[name="contact_phone"]')
        assert phone_input.is_visible()
        phone_input.fill("+5561991365756")
        assert phone_input.input_value() == "+55 (61) 99136-5756"

        # Salva screenshot do painel SC-20 antes do disparo
        if SCREENSHOT_DIR.exists():
            page.screenshot(
                path=str(SCREENSHOT_DIR / "playwright_sc20_01_dashboard.png"),
                full_page=True,
            )

        # Step 3: Executar o disparo da verificação mensal
        execute_button = page.locator('button[name="action"][value="execute"]')
        assert execute_button.is_visible()
        execute_button.click()

        # Aguarda redirecionamento para o detalhe da execução
        page.wait_for_url("**/execucoes/**")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "Execução" in content
        assert "SC-20" in content
        assert "Alpha Engenharia Ltda" in content
        assert "Beta Distribuidora S/A" in content
        assert "E-mail enviado via Gmail SMTP." in content

        # Na tabela de tentativas da execução, o botão de WhatsApp deve estar disponível
        whatsapp_attempt_link = page.locator("a.sc20-whatsapp-pill").first
        assert whatsapp_attempt_link.is_visible()

        # Clica no link do WhatsApp e valida a nova aba
        with context.expect_page() as new_page_info:
            whatsapp_attempt_link.click()
        whatsapp_page = new_page_info.value
        assert "https://wa.me/5561991365756" in whatsapp_page.url

        # Salva screenshot da tela de evidências da execução
        if SCREENSHOT_DIR.exists():
            page.screenshot(
                path=str(SCREENSHOT_DIR / "playwright_sc20_02_run_detail.png"),
                full_page=True,
            )

        browser.close()
