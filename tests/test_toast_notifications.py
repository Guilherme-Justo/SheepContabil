import pytest
from django.contrib.messages.storage.base import Message
from django.template import Context, Template
from django.test import RequestFactory

from core.identity.models import User

pytestmark = pytest.mark.django_db


def test_toast_notifications_rendered_in_base_template(administrator: User) -> None:
    factory = RequestFactory()
    request = factory.get("/")
    request.user = administrator

    success_msg = Message(
        level=25,
        message="Certificado cadastrado com sucesso.",
        extra_tags="success",
    )
    error_msg = Message(
        level=40,
        message="Falha ao processar arquivo.",
        extra_tags="error",
    )

    template = Template('{% extends "base.html" %}{% block content %}<p>conteudo</p>{% endblock %}')
    rendered = template.render(
        Context(
            {
                "request": request,
                "messages": [success_msg, error_msg],
            }
        )
    )

    # Verifica o container flutuante e atributos de acessibilidade
    assert 'id="toast-container"' in rendered
    assert 'aria-label="Notificações do sistema"' in rendered
    assert 'aria-live="polite"' in rendered

    # Verifica toast de sucesso
    assert "toast-notification toast-success" in rendered
    assert 'role="status"' in rendered
    assert "Sucesso" in rendered
    assert "Certificado cadastrado com sucesso." in rendered
    assert "duration: 5000" in rendered

    # Verifica toast de erro
    assert "toast-notification toast-error" in rendered
    assert 'role="alert"' in rendered
    assert "Erro" in rendered
    assert "Falha ao processar arquivo." in rendered
    assert "duration: 8000" in rendered

    # Interatividade e fechamento
    assert 'aria-label="Dispensar notificação"' in rendered
    assert '@click="dismiss()"' in rendered
    assert '@mouseenter="pauseTimer()"' in rendered
    assert '@mouseleave="startTimer()"' in rendered


def test_toast_container_not_rendered_when_no_messages(administrator: User) -> None:
    factory = RequestFactory()
    request = factory.get("/")
    request.user = administrator

    template = Template('{% extends "base.html" %}{% block content %}<p>conteudo</p>{% endblock %}')
    rendered = template.render(
        Context(
            {
                "request": request,
                "messages": [],
            }
        )
    )

    assert 'id="toast-container"' not in rendered
