from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

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
        contact_email="clara@beta.example.test",
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

        # Intercepta chamadas externas para o WhatsApp para testes determinísticos
        context.route(
            "https://api.whatsapp.com/**",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="<html><body><h1>WhatsApp Web Simulado</h1></body></html>",
            ),
        )
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

        # Verifica existência do botão de WhatsApp e formatação de telefone na tabela
        whatsapp_pill = page.locator("a.sc20-whatsapp-pill").first
        assert whatsapp_pill.is_visible()
        assert whatsapp_pill.locator("svg").is_visible()
        assert "+55 (11) 98888-7777" in page.content()
        assert "clara@beta.example.test" in page.content()
        assert page.locator(".sc20-pref-pill").first.is_visible()
        assert page.locator(".sc20-wpp-icon svg").first.is_visible()
        assert "💬" not in page.content()
        pill_href = whatsapp_pill.get_attribute("href") or ""
        assert "https://api.whatsapp.com/send?phone=5561991365756" in pill_href
        decoded_href = unquote(pill_href)
        assert "Beta" in decoded_href
        assert "🔔" not in decoded_href
        assert "📋" not in decoded_href
        assert "💡" not in decoded_href
        assert "\ufffd" not in decoded_href
        assert "*SheepContabil · Monitoramento de Certificados Digitais*" in decoded_href
        assert "*Dados do Certificado:*" in decoded_href
        assert "*Orientação para Renovação:*" in decoded_href

        # Testa a máscara de documento no formulário do SC-20
        doc_input = page.locator('input[name="client_document"]')
        assert doc_input.is_visible()
        doc_input.fill("12345678000190")
        assert doc_input.input_value() == "12.345.678/0001-90"

        # Testa o seletor de país e a máscara de telefone dinâmica em tempo real no Chromium
        country_select = page.locator('select[name="phone_country"]')
        assert country_select.is_visible()
        assert country_select.input_value() == "55"

        phone_input = page.locator('input[name="contact_phone"]')
        assert phone_input.is_visible()
        phone_input.fill("61991365756")
        assert phone_input.input_value() == "(61) 99136-5756"

        # Troca de país para Portugal (+351) e valida troca de placeholder e máscara livre
        country_select.select_option("351")
        assert phone_input.get_attribute("placeholder") == "912 345 678"
        phone_input.fill("912345678")
        assert phone_input.input_value() == "912345678"

        # Retorna para Brasil (+55)
        country_select.select_option("55")
        assert phone_input.get_attribute("placeholder") == "(11) 99999-0000"
        phone_input.fill("61991365756")
        assert phone_input.input_value() == "(61) 99136-5756"

        # Testa a reatividade Alpine.js na migração de obrigatoriedade dos canais
        email_req_asterisk = page.locator('label[for="id_contact_email"] span.text-carmim')
        phone_req_asterisk = page.locator('label[for="id_contact_phone"] span.text-carmim')
        channel_select = page.locator('select[name="preferred_channel"]')

        # Estado inicial (Canal = E-mail): E-mail com asterisco, Telefone sem
        expect(email_req_asterisk).to_be_visible()
        expect(phone_req_asterisk).not_to_be_visible()

        # Troca para WhatsApp: Telefone ganha asterisco, E-mail perde
        channel_select.select_option("whatsapp")
        expect(phone_req_asterisk).to_be_visible()
        expect(email_req_asterisk).not_to_be_visible()

        # Retorna para E-mail
        channel_select.select_option("email")
        expect(email_req_asterisk).to_be_visible()
        expect(phone_req_asterisk).not_to_be_visible()

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
        assert "+55 (11) 98888-7777" in content
        assert "💬" not in content

        # Na tabela de tentativas da execução, o botão de WhatsApp deve estar disponível com SVG
        whatsapp_attempt_link = page.locator("a.sc20-whatsapp-pill").first
        assert whatsapp_attempt_link.is_visible()
        assert whatsapp_attempt_link.locator("svg").is_visible()

        # Clica no link do WhatsApp e valida a nova aba
        with context.expect_page() as new_page_info:
            whatsapp_attempt_link.click()
        whatsapp_page = new_page_info.value
        assert "https://api.whatsapp.com/send?phone=5561991365756" in whatsapp_page.url

        # Salva screenshot da tela de evidências da execução
        if SCREENSHOT_DIR.exists():
            page.screenshot(
                path=str(SCREENSHOT_DIR / "playwright_sc20_02_run_detail.png"),
                full_page=True,
            )

        browser.close()
