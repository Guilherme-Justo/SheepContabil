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
