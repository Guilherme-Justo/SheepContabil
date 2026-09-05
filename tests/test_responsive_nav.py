import pytest
from django.test import Client
from django.urls import reverse

from core.identity.models import User

pytestmark = pytest.mark.django_db


def test_authenticated_page_contains_responsive_hamburger_and_drawer(
    client: Client,
    administrator: User,
) -> None:
    client.force_login(administrator)
    response = client.get(reverse("automations:dashboard"))

    assert response.status_code == 200
    html = response.content.decode()

    # Botão hambúrguer no topbar com acessibilidade WCAG
    assert "mobile-menu-button" in html
    assert "toggleMobileMenu()" in html
    assert ':aria-expanded="mobileMenuOpen"' in html
    assert 'aria-controls="portal-sidebar"' in html

    # Backdrop translúcido
    assert "mobileMenuOpen" in html
    assert "backdrop-blur-xs" in html
    assert '@click="closeMobileMenu()"' in html

    # Barra lateral identificada e com botão de fechar móvel
    assert 'id="portal-sidebar"' in html
    assert "mobile-open" in html
    assert 'aria-label="Menu principal"' in html
    assert 'aria-label="Fechar menu lateral"' in html

    # Tecla Escape configurada para fechar drawer
    assert '@keydown.escape.window="closeMobileMenu()"' in html


def test_sidebar_branding_and_floating_edge_handle(
    client: Client,
    administrator: User,
) -> None:
    client.force_login(administrator)
    response = client.get(reverse("automations:dashboard"))

    assert response.status_code == 200
    html = response.content.decode()

    # 1. Wrapper estrutural do sidebar
    assert "portal-sidebar-wrapper" in html

    # 2. Branding com symbol-dark.png para estado colapsado e logo-dark.png para expandido
    assert "brand/logo-dark.png" in html
    assert "brand/symbol-dark.png" in html

    # 3. Floating Edge Handle na borda direita do sidebar
    assert "sidebar-edge-handle" in html
    assert '@click="toggleSidebar()"' in html
    assert ':aria-expanded="!sidebarCollapsed"' in html
    assert "sidebarCollapsed ? 'rotate-180' : ''" in html

    # 4. Topo do sidebar limpo (sem botão duplicado no cabeçalho para desktop)
    before_header = html.split('class="sidebar-header"')[0]
    assert '<button type="button" @click="toggleSidebar()"' not in before_header

