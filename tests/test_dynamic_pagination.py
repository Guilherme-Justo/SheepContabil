from copy import deepcopy
from typing import TYPE_CHECKING

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.automations.context_processors import DEFAULT_PAGE_SIZE, PAGE_SIZE_CHOICES
from core.automations.management.commands.seed_demo import SC06_SCHEMA_V1
from core.automations.models import (
    AutomationModule,
    AutomationRun,
    BriefingTemplate,
    BriefingTemplateVersion,
    BriefingVersionStatus,
    CertificateStatus,
    DigitalCertificate,
    RunStatus,
    RunTrigger,
    SC05Action,
    SC05Client,
    SC05Operation,
    SC05Scenario,
    SocietaryBriefing,
    SocietaryBriefingStatus,
)
from core.automations.views import _build_per_page_query_params, _extract_page_size
from core.identity.models import User

if TYPE_CHECKING:
    pass

pytestmark = pytest.mark.django_db


# ==============================================================================
# 1. Unit Tests: _extract_page_size & _build_per_page_query_params
# ==============================================================================


def test_extract_page_size_valid_choices() -> None:
    rf = RequestFactory()
    for choice in PAGE_SIZE_CHOICES:
        request = rf.get(f"/?per_page={choice}")
        assert _extract_page_size(request, "per_page") == choice


def test_extract_page_size_default_when_missing() -> None:
    rf = RequestFactory()
    request = rf.get("/")
    assert _extract_page_size(request, "per_page") == DEFAULT_PAGE_SIZE
    assert _extract_page_size(request, "per_page", default=15) == 15


@pytest.mark.parametrize(
    "invalid_val",
    [
        "0",
        "-1",
        "1",
        "6",
        "8",
        "100",
        "999999",
        "invalid",
        "drop table",
        "<script>",
        "",
    ],
)
def test_extract_page_size_dos_defense_fallback(invalid_val: str) -> None:
    """Valores fora da whitelist ou maliciosos sofrem fallback seguro para DEFAULT_PAGE_SIZE."""
    rf = RequestFactory()
    request = rf.get(f"/?per_page={invalid_val}")
    assert _extract_page_size(request, "per_page") == DEFAULT_PAGE_SIZE


def test_extract_page_size_custom_param_name() -> None:
    rf = RequestFactory()
    request = rf.get("/?clients_per_page=25&per_page=5")
    assert _extract_page_size(request, "clients_per_page") == 25
    assert _extract_page_size(request, "per_page") == 5


def test_build_per_page_query_params_strips_page_and_per_page() -> None:
    rf = RequestFactory()
    request = rf.get("/?page=3&per_page=15&sort=-created_at&status=done")
    result = _build_per_page_query_params(request, page_key="page", per_page_key="per_page")

    # Deve conter sort e status, mas NÃO page e NÃO per_page
    assert "page=3" not in result
    assert "per_page=15" not in result
    assert "sort=-created_at" in result
    assert "status=done" in result
    assert result.startswith("&")


def test_build_per_page_query_params_custom_keys() -> None:
    rf = RequestFactory()
    request = rf.get("/?clients_page=2&clients_per_page=5&page=1&per_page=15&sort=name")
    result = _build_per_page_query_params(
        request, page_key="clients_page", per_page_key="clients_per_page"
    )

    assert "clients_page=" not in result
    assert "clients_per_page=" not in result
    assert "page=1" in result
    assert "per_page=15" in result
    assert "sort=name" in result


# ==============================================================================
# 2. Integration Tests: Dashboard Pagination
# ==============================================================================


def test_dashboard_pagination_dynamic_page_size(
    client: Client,
    administrator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(administrator)
    module = modules["SC-04"]

    # Cria 12 execuções
    now = timezone.now()
    for _ in range(12):
        AutomationRun.objects.create(
            module=module,
            status=RunStatus.SUCCEEDED,
            trigger=RunTrigger.MANUAL,
            created_at=now,
        )

    url = reverse("automations:dashboard")

    # Default per_page = 7 -> 2 páginas (7 na primeira, 5 na segunda)
    resp = client.get(url)
    assert resp.status_code == 200
    assert len(resp.context["page_obj"]) == 7
    assert resp.context["paginator"].num_pages == 2
    html = resp.content.decode()
    assert "<select" in html
    assert 'name="per_page"' in html
    assert '<option value="7" selected>7</option>' in html

    # per_page = 5 -> 3 páginas (5 na primeira, 5 na segunda, 2 na terceira)
    resp_5 = client.get(f"{url}?per_page=5")
    assert resp_5.status_code == 200
    assert len(resp_5.context["page_obj"]) == 5
    assert resp_5.context["paginator"].num_pages == 3
    html_5 = resp_5.content.decode()
    assert '<option value="5" selected>5</option>' in html_5

    # per_page = 15 -> 1 página contendo todas as 12 execuções
    resp_15 = client.get(f"{url}?per_page=15")
    assert resp_15.status_code == 200
    assert len(resp_15.context["page_obj"]) == 12
    assert resp_15.context["paginator"].num_pages == 1
    html_15 = resp_15.content.decode()
    assert '<option value="15" selected>15</option>' in html_15

    # Ataque DoS de memória: per_page=999999 -> fallback seguro para 7
    resp_dos = client.get(f"{url}?per_page=999999")
    assert resp_dos.status_code == 200
    assert len(resp_dos.context["page_obj"]) == 7
    assert resp_dos.context["paginator"].num_pages == 2
    assert resp_dos.context["paginator"].per_page == 7


def test_dashboard_pagination_preserves_filters_and_sort(
    client: Client,
    administrator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(administrator)
    module = modules["SC-04"]

    now = timezone.now()
    for _ in range(10):
        AutomationRun.objects.create(
            module=module,
            status=RunStatus.SUCCEEDED,
            trigger=RunTrigger.MANUAL,
            created_at=now,
        )

    url = reverse("automations:dashboard")
    resp = client.get(f"{url}?sort=-created_at&status=succeeded&per_page=5")
    assert resp.status_code == 200
    html = resp.content.decode()

    # O select deve ter hx-get resetando para page=1 e preservando sort e status
    assert 'hx-get="?page=1' in html
    assert "sort=-created_at" in resp.context["per_page_query_params"]
    assert "status=succeeded" in resp.context["per_page_query_params"]
    assert "per_page" not in resp.context["per_page_query_params"]


# ==============================================================================
# 3. Integration Tests: SC-05 Multi-Table Independent Pagination
# ==============================================================================


def test_sc05_multi_table_independent_pagination(
    client: Client,
    administrator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(administrator)
    module = modules["SC-05"]

    # Cria 10 clientes e 10 operações
    for i in range(10):
        c = SC05Client.objects.create(
            external_reference=f"cli-{i}",
            name=f"Cliente Sintético {i}",
            document=f"1234567800010{i}",
        )
        run = AutomationRun.objects.create(
            module=module,
            status=RunStatus.SUCCEEDED,
            trigger=RunTrigger.MANUAL,
        )
        SC05Operation.objects.create(
            run=run,
            client=c,
            action=SC05Action.BLOCK,
            scenario=SC05Scenario.HAPPY_PATH,
        )

    url = reverse("automations:module-detail", kwargs={"slug": module.slug})

    # Paginação independente: clients_per_page=5 e per_page=15
    resp = client.get(f"{url}?clients_per_page=5&per_page=15")
    assert resp.status_code == 200
    assert len(resp.context["clients_page_obj"]) == 5
    assert resp.context["clients_paginator"].num_pages == 2
    assert len(resp.context["page_obj"]) == 10
    assert resp.context["paginator"].num_pages == 1

    html = resp.content.decode()
    # Dropdown de clientes tem name="clients_per_page" e 5 selecionado
    assert 'name="clients_per_page"' in html
    # Dropdown de operações tem name="per_page" e 15 selecionado
    assert 'name="per_page"' in html


# ==============================================================================
# 4. Integration Tests: SC-20 Three-Table Independent Pagination
# ==============================================================================


def test_sc20_independent_pagination(
    client: Client,
    administrator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(administrator)
    module = modules["SC-20"]

    now = timezone.now()
    # Cria 8 certificados e 8 execuções
    for i in range(8):
        DigitalCertificate.objects.create(
            serial_number=f"CERT-{i}",
            client_name=f"Empresa {i}",
            client_document=f"0000000000010{i}",
            responsible_name=f"Responsável {i}",
            status=CertificateStatus.ACTIVE,
            valid_until=(now + timezone.timedelta(days=60)).date(),
        )
        AutomationRun.objects.create(
            module=module,
            status=RunStatus.SUCCEEDED,
            trigger=RunTrigger.MANUAL,
            created_at=now,
        )

    url = reverse("automations:module-detail", kwargs={"slug": module.slug})
    resp = client.get(f"{url}?per_page=5&runs_per_page=25")
    assert resp.status_code == 200
    assert len(resp.context["certificates"]) == 5
    assert resp.context["certificates_paginator"].num_pages == 2
    assert len(resp.context["runs"]) == 8
    assert resp.context["runs_paginator"].num_pages == 1


# ==============================================================================
# 5. Integration Tests: SC-06 Pagination
# ==============================================================================


def test_sc06_pagination(
    client: Client,
    administrator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(administrator)
    module = modules["SC-06"]

    template = BriefingTemplate.objects.create(
        code="sc06-test",
        name="Template Teste",
    )
    template_version = BriefingTemplateVersion.objects.create(
        template=template,
        version=1,
        schema=deepcopy(SC06_SCHEMA_V1),
        status=BriefingVersionStatus.PUBLISHED,
        published_at=timezone.now(),
        created_by=administrator,
    )

    for i in range(9):
        run = AutomationRun.objects.create(
            module=module,
            status=RunStatus.SUCCEEDED,
            trigger=RunTrigger.MANUAL,
        )
        SocietaryBriefing.objects.create(
            template_version=template_version,
            run=run,
            client_name=f"Cliente {i}",
            client_document=f"0000000000010{i}",
            status=SocietaryBriefingStatus.DRAFT,
            created_by=administrator,
        )

    url = reverse("automations:module-detail", kwargs={"slug": module.slug})
    resp = client.get(f"{url}?per_page=5")
    assert resp.status_code == 200
    assert len(resp.context["page_obj"]) == 5
    assert resp.context["paginator"].num_pages == 2
