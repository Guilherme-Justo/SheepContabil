from __future__ import annotations

from copy import deepcopy
from io import BytesIO

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader

from core.automations.management.commands.seed_demo import SC06_SCHEMA_V1
from core.automations.models import (
    AutomationModule,
    AutomationRun,
    BriefingTemplate,
    BriefingTemplateVersion,
    BriefingVersionStatus,
    RunStatus,
    SocietaryBriefing,
    SocietaryBriefingStatus,
)
from core.automations.sc06.services import cancel_briefing, complete_briefing, create_briefing
from core.identity.models import User

pytestmark = pytest.mark.django_db


def _publish_template(administrator: User) -> BriefingTemplateVersion:
    template = BriefingTemplate.objects.create(
        code="societary-briefing",
        name="Briefing societário",
    )
    return BriefingTemplateVersion.objects.create(
        template=template,
        version=1,
        schema=deepcopy(SC06_SCHEMA_V1),
        status=BriefingVersionStatus.PUBLISHED,
        published_at=timezone.now(),
        created_by=administrator,
    )


def _module_url(modules: dict[str, AutomationModule]) -> str:
    return reverse(
        "automations:module-detail",
        kwargs={"slug": modules["SC-06"].slug},
    )


def _sp_opening_payload() -> dict[str, str]:
    return {
        "action": "complete",
        "process_type": "opening",
        "client_state": "SP",
        "contact_email": "contato@example.test",
        "desired_company_name": "Horizonte Demo Ltda.",
        "main_activity": "Serviços empresariais exclusivamente sintéticos.",
        "proposed_address": "Rua Exemplo, 10, São Paulo/SP",
        "partner_names": "Pessoa Sócia de Teste",
        "has_married_partner": "false",
        "desired_deadline": "2026-09-20",
        "additional_notes": "",
    }


def test_societary_operator_sees_sc06_but_process_operator_does_not(
    client: Client,
    modules: dict[str, AutomationModule],
    societary_operator: User,
    processes_operator: User,
    administrator: User,
) -> None:
    _publish_template(administrator)
    client.force_login(societary_operator)
    allowed = client.get(_module_url(modules))

    client.force_login(processes_operator)
    denied = client.get(_module_url(modules))

    assert allowed.status_code == 200
    assert "Iniciar briefing societário" in allowed.content.decode()
    assert "reavaliadas pelo servidor" in allowed.content.decode()
    assert denied.status_code == 404


def test_start_creates_draft_and_traceable_running_execution(
    client: Client,
    modules: dict[str, AutomationModule],
    societary_operator: User,
    administrator: User,
) -> None:
    _publish_template(administrator)
    client.force_login(societary_operator)

    response = client.post(
        _module_url(modules),
        {
            "action": "start",
            "client_name": " Empresa Formulário ",
            "client_document": "12.345.678/0001-90",
        },
    )

    briefing = SocietaryBriefing.objects.select_related("run").get()
    assert response.status_code == 302
    assert response.url == reverse(
        "automations:sc06-briefing-detail",
        kwargs={"briefing_id": briefing.id},
    )
    assert briefing.client_name == "Empresa Formulário"
    assert briefing.client_document == "12345678000190"
    assert briefing.run.status == RunStatus.RUNNING
    assert briefing.run.triggered_by == societary_operator


def test_draft_save_discards_unknown_and_hidden_answers(
    client: Client,
    modules: dict[str, AutomationModule],
    societary_operator: User,
    administrator: User,
) -> None:
    _publish_template(administrator)
    briefing = create_briefing(
        client_name="Cliente Rascunho",
        client_document="12345678000190",
        created_by=societary_operator,
    )
    client.force_login(societary_operator)

    response = client.post(
        reverse(
            "automations:sc06-briefing-detail",
            kwargs={"briefing_id": briefing.id},
        ),
        {
            "action": "save",
            "process_type": "opening",
            "client_state": "SP",
            "has_married_partner": "false",
            "married_partner_name": "Campo oculto malicioso",
            "origin_registry": "Campo oculto malicioso",
            "unknown": "Campo não configurado",
        },
    )

    briefing.refresh_from_db()
    assert response.status_code == 302
    assert briefing.answers == {
        "process_type": "opening",
        "client_state": "SP",
        "has_married_partner": False,
    }
    assert briefing.status == SocietaryBriefingStatus.DRAFT


def test_server_blocks_completion_when_conditional_requirements_are_missing(
    client: Client,
    modules: dict[str, AutomationModule],
    societary_operator: User,
    administrator: User,
) -> None:
    _publish_template(administrator)
    briefing = create_briefing(
        client_name="Cliente Condicional",
        client_document="12345678000190",
        created_by=societary_operator,
    )
    client.force_login(societary_operator)
    payload = {
        **_sp_opening_payload(),
        "client_state": "RJ",
        "has_married_partner": "true",
    }

    response = client.post(
        reverse(
            "automations:sc06-briefing-detail",
            kwargs={"briefing_id": briefing.id},
        ),
        payload,
    )

    briefing.refresh_from_db()
    briefing.run.refresh_from_db()
    html = response.content.decode()
    assert response.status_code == 200
    assert "Revise o briefing antes de continuar." in html
    assert html.count("Este campo é obrigatório") >= 4
    assert briefing.status == SocietaryBriefingStatus.DRAFT
    assert briefing.run.status == RunStatus.RUNNING


def test_completion_exposes_read_only_result_run_evidence_and_pdf(
    client: Client,
    modules: dict[str, AutomationModule],
    societary_operator: User,
    processes_operator: User,
    administrator: User,
) -> None:
    _publish_template(administrator)
    briefing = create_briefing(
        client_name="Cliente Consolidado",
        client_document="12345678000190",
        created_by=administrator,
    )
    detail_url = reverse(
        "automations:sc06-briefing-detail",
        kwargs={"briefing_id": briefing.id},
    )
    pdf_url = reverse(
        "automations:sc06-briefing-pdf",
        kwargs={"briefing_id": briefing.id},
    )
    client.force_login(societary_operator)

    completed_response = client.post(detail_url, _sp_opening_payload())
    read_only_page = client.get(detail_url)
    briefing.refresh_from_db()
    briefing.run.refresh_from_db()
    run_page = client.get(reverse("automations:run-detail", kwargs={"run_id": briefing.run_id}))
    pdf_response = client.get(pdf_url)

    assert completed_response.status_code == 302
    assert briefing.status == SocietaryBriefingStatus.COMPLETED
    assert briefing.created_by == administrator
    assert briefing.completed_by == societary_operator
    assert briefing.run.status == RunStatus.SUCCEEDED
    assert "Briefing concluído e preservado" in read_only_page.content.decode()
    assert societary_operator.label in read_only_page.content.decode()
    assert "Briefing societário vinculado" in run_page.content.decode()
    assert pdf_response.status_code == 200
    assert pdf_response["Content-Type"] == "application/pdf"
    assert pdf_response["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in pdf_response["Content-Disposition"]
    reader = PdfReader(BytesIO(pdf_response.content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Cliente Consolidado" in text
    assert societary_operator.label in text
    assert "Horizonte Demo Ltda." in text
    assert "Comunhão" not in text

    client.force_login(processes_operator)
    assert client.get(detail_url).status_code == 404
    assert client.get(pdf_url).status_code == 404


def test_draft_pdf_and_repeat_completion_fail_safely(
    client: Client,
    modules: dict[str, AutomationModule],
    societary_operator: User,
    administrator: User,
) -> None:
    _publish_template(administrator)
    briefing = create_briefing(
        client_name="Cliente Protegido",
        client_document="12345678901",
        created_by=societary_operator,
    )
    detail_url = reverse(
        "automations:sc06-briefing-detail",
        kwargs={"briefing_id": briefing.id},
    )
    pdf_url = reverse(
        "automations:sc06-briefing-pdf",
        kwargs={"briefing_id": briefing.id},
    )
    client.force_login(societary_operator)

    assert client.get(pdf_url).status_code == 400
    complete_briefing(
        briefing.id,
        _sp_opening_payload(),
        completed_by=societary_operator,
    )
    repeated = client.post(detail_url, _sp_opening_payload())

    assert repeated.status_code == 400
    assert AutomationRun.objects.count() == 1


def test_cancel_draft_action_via_post_and_redirects(
    client: Client,
    modules: dict[str, AutomationModule],
    societary_operator: User,
    administrator: User,
) -> None:
    _publish_template(administrator)
    briefing = create_briefing(
        client_name="Caso Para Cancelar",
        client_document="12345678901",
        created_by=societary_operator,
    )
    detail_url = reverse(
        "automations:sc06-briefing-detail",
        kwargs={"briefing_id": briefing.id},
    )
    client.force_login(societary_operator)

    # POST action=cancel
    response = client.post(detail_url, {"action": "cancel"})
    expected_redirect = reverse("automations:module-detail", kwargs={"slug": modules["SC-06"].slug})
    assert response.url == expected_redirect

    briefing.refresh_from_db()
    assert briefing.status == SocietaryBriefingStatus.CANCELLED
    assert briefing.run.status == RunStatus.CANCELLED

    # Reading detail page shows cancelled banner and fieldset disabled
    detail_page = client.get(detail_url)
    assert detail_page.status_code == 200
    content = detail_page.content.decode()
    assert "Briefing cancelado e arquivado" in content
    assert "<fieldset disabled" in content


def test_sc06_detail_pagination_and_formatted_document(
    client: Client,
    modules: dict[str, AutomationModule],
    societary_operator: User,
    administrator: User,
) -> None:
    _publish_template(administrator)
    for i in range(8):
        create_briefing(
            client_name=f"Cliente {i + 1:02d}",
            client_document=f"{i:011d}",
            created_by=societary_operator,
        )

    module_url = reverse("automations:module-detail", kwargs={"slug": modules["SC-06"].slug})
    client.force_login(societary_operator)

    # Page 1
    resp1 = client.get(module_url)
    assert resp1.status_code == 200
    assert resp1.context["paginator"].num_pages == 2
    assert len(resp1.context["page_obj"]) == 6
    assert resp1.context["page_obj"].number == 1
    assert "Navegação dos casos societários" in resp1.content.decode()

    # Page 2
    resp2 = client.get(f"{module_url}?page=2")
    assert resp2.status_code == 200
    assert len(resp2.context["page_obj"]) == 2
    assert resp2.context["page_obj"].number == 2


def test_sc06_cases_filters_and_htmx_pagination(
    client: Client,
    modules: dict[str, AutomationModule],
    societary_operator: User,
    administrator: User,
) -> None:
    _publish_template(administrator)
    b1 = create_briefing(
        client_name="Alfa Consultoria",
        client_document="11122233344",
        created_by=societary_operator,
    )
    b2 = create_briefing(
        client_name="Beta Contabilidade",
        client_document="55566677788",
        created_by=societary_operator,
    )
    b3 = create_briefing(
        client_name="Gama Tech",
        client_document="99988877766",
        created_by=societary_operator,
    )
    cancel_briefing(b3.id, cancelled_by=societary_operator)

    module_url = reverse("automations:module-detail", kwargs={"slug": modules["SC-06"].slug})
    client.force_login(societary_operator)

    # 1. Busca por nome parcial
    resp_q_name = client.get(f"{module_url}?q=Alfa")
    assert resp_q_name.status_code == 200
    assert len(resp_q_name.context["page_obj"]) == 1
    assert resp_q_name.context["page_obj"][0].id == b1.id
    assert resp_q_name.context["has_active_filters"] is True

    resp_q_beta = client.get(f"{module_url}?q=Beta")
    assert resp_q_beta.status_code == 200
    assert len(resp_q_beta.context["page_obj"]) == 1
    assert resp_q_beta.context["page_obj"][0].id == b2.id

    # 2. Busca por documento formatado ou dígitos
    resp_q_doc = client.get(f"{module_url}?q=999.888")
    assert resp_q_doc.status_code == 200
    assert len(resp_q_doc.context["page_obj"]) == 1
    assert resp_q_doc.context["page_obj"][0].id == b3.id

    # 3. Filtro por status cancelado
    resp_cancelled = client.get(f"{module_url}?status={SocietaryBriefingStatus.CANCELLED}")
    assert resp_cancelled.status_code == 200
    assert len(resp_cancelled.context["page_obj"]) == 1
    assert resp_cancelled.context["page_obj"][0].status == SocietaryBriefingStatus.CANCELLED

    # 4. Atributos HTMX presentes no HTML da página
    html = resp_cancelled.content.decode()
    assert 'hx-target="#sc06-cases-region"' in html
    assert 'hx-swap="outerHTML show:none"' in html
    assert 'hx-push-url="true"' in html
    assert "Limpar" in html
