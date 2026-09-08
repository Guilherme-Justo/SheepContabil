from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.automations.management.commands.seed_demo import SC06_SCHEMA_V1
from core.automations.models import (
    AutomationModule,
    BriefingTemplate,
    BriefingTemplateVersion,
    BriefingVersionStatus,
    SC05Action,
    SC05Client,
    SC05Scenario,
)
from core.automations.sc04.contracts import StoredObject
from core.identity.models import User

pytestmark = pytest.mark.django_db


@dataclass
class MemoryStorage:
    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)

    def put_bytes(self, *, key: str, content: bytes, content_type: str) -> StoredObject:
        self.objects.setdefault(key, (content, content_type))
        return StoredObject(key=key, byte_size=len(content), content_type=content_type)

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key][0]


@pytest.fixture
def auth_client(administrator: User) -> Client:
    client = Client()
    client.force_login(administrator)
    return client


def test_sc04_upload_htmx_validation_error(
    auth_client: Client, modules: dict[str, AutomationModule]
) -> None:
    """Valida que requisição HTMX com erro retorna status 200 com o card de upload."""
    url = reverse("automations:sc04-upload")
    response = auth_client.post(
        url,
        data={},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    html = response.content.decode("utf-8")
    assert 'id="sc04-upload-card"' in html
    assert 'aria-invalid="true"' in html


def test_sc04_upload_htmx_success_redirect(
    auth_client: Client,
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valida que upload com sucesso sob HTMX retorna status 200 com header HX-Redirect."""
    storage = MemoryStorage()
    monkeypatch.setattr("core.automations.sc04.services.build_object_storage", lambda: storage)
    monkeypatch.setattr(
        "core.automations.dispatching.run_sc04_task.apply_async",
        lambda *, args, task_id: None,
    )

    url = reverse("automations:sc04-upload")
    sample_file = SimpleUploadedFile(
        "nfse.txt", b"NUMERO=123\nVALOR=100", content_type="text/plain"
    )
    response = auth_client.post(
        url,
        data={"attachment": sample_file, "confirm_synthetic": "on"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert "HX-Redirect" in response.headers
    assert "/documentos/" in response.headers["HX-Redirect"]


def test_sc05_operation_htmx_validation_error(
    auth_client: Client, modules: dict[str, AutomationModule]
) -> None:
    """Valida que submissão do SC-05 com erro sob HTMX retorna status 200 com aria-invalid."""
    sc05 = modules["SC-05"]
    url = reverse("automations:module-detail", kwargs={"slug": sc05.slug})
    response = auth_client.post(
        url,
        data={"request_key": str(uuid4())},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    html = response.content.decode("utf-8")
    assert 'id="sc05-action-panel"' in html
    assert 'aria-invalid="true"' in html


def test_sc05_operation_htmx_success_redirect(
    auth_client: Client,
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valida execução do SC-05 com sucesso sob HTMX retornando 200 com HX-Redirect."""
    monkeypatch.setattr(
        "core.automations.dispatching.run_sc05_task.apply_async",
        lambda *, args, task_id: None,
    )
    sc05 = modules["SC-05"]
    client_obj = SC05Client.objects.create(
        name="Cliente Teste",
        document="12345678000199",
        external_reference="CLI-001",
    )
    url = reverse("automations:module-detail", kwargs={"slug": sc05.slug})
    response = auth_client.post(
        url,
        data={
            "client": str(client_obj.id),
            "action": SC05Action.BLOCK.value,
            "scenario": SC05Scenario.HAPPY_PATH.value,
            "request_key": str(uuid4()),
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert "HX-Redirect" in response.headers
    assert "/execucoes/" in response.headers["HX-Redirect"]


def test_sc06_start_htmx_validation_error(
    auth_client: Client, modules: dict[str, AutomationModule]
) -> None:
    """Valida que início de briefing SC-06 com erro sob HTMX retorna 200 com painel e erros."""
    sc06 = modules["SC-06"]
    url = reverse("automations:module-detail", kwargs={"slug": sc06.slug})
    response = auth_client.post(
        url,
        data={"action": "start", "client_name": "", "client_document": ""},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    html = response.content.decode("utf-8")
    assert 'id="sc06-start-panel"' in html
    assert 'aria-invalid="true"' in html


def test_sc06_start_htmx_success_redirect(
    auth_client: Client,
    administrator: User,
    modules: dict[str, AutomationModule],
) -> None:
    """Valida início de briefing SC-06 com sucesso sob HTMX retornando 200 com HX-Redirect."""
    template = BriefingTemplate.objects.create(
        code="societary-briefing",
        name="Briefing societário",
    )
    BriefingTemplateVersion.objects.create(
        template=template,
        version=1,
        schema=deepcopy(SC06_SCHEMA_V1),
        status=BriefingVersionStatus.PUBLISHED,
        published_at=timezone.now(),
        created_by=administrator,
    )

    sc06 = modules["SC-06"]
    url = reverse("automations:module-detail", kwargs={"slug": sc06.slug})
    response = auth_client.post(
        url,
        data={
            "action": "start",
            "client_name": "Empresa Teste",
            "client_document": "12.345.678/0001-90",
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert "HX-Redirect" in response.headers
    assert "briefing" in response.headers["HX-Redirect"].lower()


def test_sc20_create_certificate_htmx_validation_error(
    auth_client: Client, modules: dict[str, AutomationModule]
) -> None:
    """Valida que cadastro de certificado com erro sob HTMX retorna status 200 com card e erros."""
    sc20 = modules["SC-20"]
    url = reverse("automations:module-detail", kwargs={"slug": sc20.slug})
    response = auth_client.post(
        url,
        data={"action": "create_certificate", "client_name": ""},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    html = response.content.decode("utf-8")
    assert 'id="sc20-certificate-form-card"' in html
    assert 'aria-invalid="true"' in html
