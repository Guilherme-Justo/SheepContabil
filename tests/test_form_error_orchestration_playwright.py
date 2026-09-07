from __future__ import annotations

import pytest
from django.urls import reverse
from playwright.sync_api import sync_playwright

from core.automations.models import (
    AutomationModule,
    SC05Client,
)
from core.identity.models import User

pytestmark = [
    pytest.mark.django_db(transaction=True),
]


def test_form_error_orchestration_sc05_playwright(
    live_server,
    technology_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    """Verifica erro no SC-05 submetido via HTMX sem full reload e com foco no campo."""
    SC05Client.objects.create(
        name="Cliente Alfa Teste",
        document="12345678000190",
        external_reference="ALFA-001",
    )
    sc05 = modules["SC-05"]
    login_url = f"{live_server.url}{reverse('identity:login')}"
    module_url = (
        f"{live_server.url}{reverse('automations:module-detail', kwargs={'slug': sc05.slug})}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # Login
        page.goto(login_url)
        page.fill('input[name="username"]', "operador.tecnologia")
        page.fill('input[name="password"]', "safe-test-password")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Go to SC-05
        page.goto(module_url)
        page.wait_for_load_state("networkidle")

        # Track navigation to ensure NO full page reload happens
        navigated = False

        def on_framenavigated(frame):
            nonlocal navigated
            if frame == page.main_frame and frame.url != module_url:
                navigated = True

        page.on("framenavigated", on_framenavigated)

        # Submit the operation form without selecting a client
        page.click('button:has-text("Executar sequência")')
        page.wait_for_timeout(600)

        # Ensure no full page navigation happened
        assert not navigated, (
            "Submissão do formulário não deveria causar navegação/reload de página inteira"
        )

        # Check that error is displayed inside the swapped action panel
        action_panel = page.locator("#sc05-action-panel")
        assert action_panel.is_visible()

        # Verify that the client select has aria-invalid="true"
        client_select = page.locator("#id_client")
        assert client_select.get_attribute("aria-invalid") == "true"

        # Verify activeElement is the client select or inside the error panel
        focused_id = page.evaluate(
            "() => document.activeElement ? document.activeElement.id : null"
        )
        assert focused_id == "id_client", (
            f"Foco esperado em 'id_client', mas ativo era '{focused_id}'"
        )

        browser.close()


def test_form_error_orchestration_sc20_playwright(
    live_server,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    """Verifica erro no SC-20 focando o primeiro campo inválido e adicionando pulso visual."""
    sc20 = modules["SC-20"]
    login_url = f"{live_server.url}{reverse('identity:login')}"
    module_url = (
        f"{live_server.url}{reverse('automations:module-detail', kwargs={'slug': sc20.slug})}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # Login
        page.goto(login_url)
        page.fill('input[name="username"]', "operador")
        page.fill('input[name="password"]', "safe-test-password")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Go to SC-20
        page.goto(module_url)
        page.wait_for_load_state("networkidle")

        # Track navigation
        navigated = False

        def on_framenavigated(frame):
            nonlocal navigated
            if frame == page.main_frame and frame.url != module_url:
                navigated = True

        page.on("framenavigated", on_framenavigated)

        # Submit empty certificate form
        page.click('button:has-text("Cadastrar certificado")')
        page.wait_for_timeout(600)

        assert not navigated, "Submissão do certificado não deveria causar reload total"

        # Check card is visible
        card = page.locator("#sc20-certificate-form-card")
        assert card.is_visible()

        # First invalid control should be serial_number or client_name
        first_invalid = card.locator('[aria-invalid="true"]').first
        assert first_invalid.is_visible()

        # Check focus was placed on first invalid input
        focused_tag = page.evaluate(
            "() => document.activeElement ? document.activeElement.tagName : null"
        )
        assert focused_tag in ["INPUT", "SELECT"]

        browser.close()
