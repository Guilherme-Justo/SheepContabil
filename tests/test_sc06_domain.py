from __future__ import annotations

from copy import deepcopy

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory
from django.utils import timezone

from core.automations.admin import SocietaryBriefingAdmin
from core.automations.management.commands.seed_demo import SC06_SCHEMA_V1
from core.automations.models import (
    AutomationModule,
    BriefingTemplate,
    BriefingTemplateVersion,
    BriefingVersionStatus,
    RunStatus,
    SocietaryBriefing,
    SocietaryBriefingStatus,
)
from core.automations.sc06.rules import (
    evaluate_condition,
    format_answers,
    get_active_questions,
    sanitize_answers,
    validate_template_schema,
)
from core.automations.sc06.services import (
    cancel_briefing,
    complete_briefing,
    create_briefing,
    get_latest_published_version,
    save_briefing_draft,
)
from core.identity.models import User

pytestmark = pytest.mark.django_db


def _published_version(
    administrator: User,
    *,
    version: int = 1,
    schema: dict[str, object] | None = None,
) -> BriefingTemplateVersion:
    template, _ = BriefingTemplate.objects.get_or_create(
        code="societary-briefing",
        defaults={"name": "Briefing societário"},
    )
    return BriefingTemplateVersion.objects.create(
        template=template,
        version=version,
        schema=schema or deepcopy(SC06_SCHEMA_V1),
        status=BriefingVersionStatus.PUBLISHED,
        published_at=timezone.now(),
        created_by=administrator,
    )


def _complete_sp_opening_answers() -> dict[str, object]:
    return {
        "process_type": "opening",
        "client_state": "SP",
        "contact_email": "contato@example.test",
        "desired_company_name": "Horizonte Demo Ltda.",
        "main_activity": "Serviços empresariais exclusivamente sintéticos.",
        "proposed_address": "Rua Exemplo, 10, São Paulo/SP",
        "partner_names": "Pessoa Sócia de Teste",
        "has_married_partner": False,
        "desired_deadline": "2026-09-20",
    }


def test_schema_rejects_unknown_operators_and_forward_references() -> None:
    invalid_operator = deepcopy(SC06_SCHEMA_V1)
    invalid_operator["sections"][1]["visible_when"]["operator"] = "exec"
    with pytest.raises(ValidationError, match="operador não permitido"):
        validate_template_schema(invalid_operator)

    forward_reference = {
        "title": "Inválido",
        "sections": [
            {
                "id": "main",
                "title": "Principal",
                "questions": [
                    {
                        "id": "first",
                        "label": "Primeira",
                        "type": "text",
                        "visible_when": {
                            "field": "second",
                            "operator": "equals",
                            "value": "x",
                        },
                    },
                    {"id": "second", "label": "Segunda", "type": "text"},
                ],
            }
        ],
    }
    with pytest.raises(ValidationError, match="pergunta anterior"):
        validate_template_schema(forward_reference)


def test_schema_rejects_typos_and_condition_values_incompatible_with_the_driver() -> None:
    typo = deepcopy(SC06_SCHEMA_V1)
    typo["sections"][0]["questions"][0]["visibile_when"] = {}
    with pytest.raises(ValidationError, match="campo.*não permitido"):
        validate_template_schema(typo)

    wrong_boolean = deepcopy(SC06_SCHEMA_V1)
    wrong_boolean["sections"][4]["questions"][2]["visible_when"]["value"] = "true"
    with pytest.raises(ValidationError, match="valor booleano"):
        validate_template_schema(wrong_boolean)

    unknown_choice = deepcopy(SC06_SCHEMA_V1)
    unknown_choice["sections"][1]["visible_when"]["value"] = "unknown-process"
    with pytest.raises(ValidationError, match="valor incompatível"):
        validate_template_schema(unknown_choice)

    non_canonical_condition = deepcopy(SC06_SCHEMA_V1)
    non_canonical_condition["sections"][1]["visible_when"]["value"] = " opening "
    with pytest.raises(ValidationError, match="valor já normalizado"):
        validate_template_schema(non_canonical_condition)

    non_canonical_option = deepcopy(SC06_SCHEMA_V1)
    non_canonical_option["sections"][0]["questions"][0]["options"][0]["value"] = " opening "
    with pytest.raises(ValidationError, match="valor sem espaços externos"):
        validate_template_schema(non_canonical_option)


def test_condition_dsl_evaluates_leaf_and_composite_operators() -> None:
    answers = {"state": "RJ", "married": True, "kind": "opening"}
    condition = {
        "operator": "all",
        "conditions": [
            {"field": "state", "operator": "not_equals", "value": "SP"},
            {
                "operator": "any",
                "conditions": [
                    {"field": "married", "operator": "equals", "value": True},
                    {"field": "kind", "operator": "in", "value": ["change"]},
                ],
            },
        ],
    }

    assert evaluate_condition(condition, answers) is True
    assert evaluate_condition(condition, {**answers, "married": False}) is False


def test_server_activates_required_interstate_and_marriage_questions() -> None:
    answers = {
        **_complete_sp_opening_answers(),
        "client_state": "RJ",
        "has_married_partner": True,
        "unknown": "must disappear",
        "current_cnpj": "hidden branch",
    }
    with pytest.raises(ValidationError) as captured:
        sanitize_answers(SC06_SCHEMA_V1, answers, require_complete=True)

    assert set(captured.value.error_dict) == {
        "origin_registry",
        "origin_registration",
        "married_partner_name",
        "marriage_regime",
    }

    answers.update(
        {
            "origin_registry": "JUCERJA",
            "origin_registration": "DEMO-001",
            "married_partner_name": "Pessoa Sócia de Teste",
            "marriage_regime": "partial_community",
        }
    )
    sanitized = sanitize_answers(SC06_SCHEMA_V1, answers, require_complete=True)

    assert "unknown" not in sanitized
    assert "current_cnpj" not in sanitized
    assert sanitized["has_married_partner"] is True
    labels = {
        item["question_id"]: item["display_value"]
        for item in format_answers(SC06_SCHEMA_V1, sanitized)
    }
    assert labels["marriage_regime"] == "Comunhão parcial de bens"


def test_sp_unmarried_path_does_not_require_hidden_blocks() -> None:
    sanitized = sanitize_answers(
        SC06_SCHEMA_V1,
        {
            **_complete_sp_opening_answers(),
            "origin_registry": "hidden",
            "married_partner_name": "hidden",
        },
        require_complete=True,
    )
    active_ids = {question["id"] for question in get_active_questions(SC06_SCHEMA_V1, sanitized)}

    assert "origin_registry" not in active_ids
    assert "married_partner_name" not in active_ids
    assert "origin_registry" not in sanitized
    assert "married_partner_name" not in sanitized


def test_published_template_version_is_immutable_and_latest_is_selected(
    administrator: User,
) -> None:
    first = _published_version(administrator, version=1)
    second = _published_version(administrator, version=2)

    assert get_latest_published_version() == second
    first.schema = {"title": "Mutação indevida", "sections": []}
    with pytest.raises(ValidationError, match="imutável"):
        first.save()


def test_briefing_lifecycle_sanitizes_draft_and_finalizes_common_run(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    _published_version(administrator)
    briefing = create_briefing(
        client_name=" Cliente de Teste ",
        client_document="12345678000190",
        created_by=administrator,
    )

    assert briefing.status == SocietaryBriefingStatus.DRAFT
    assert briefing.run.status == RunStatus.RUNNING
    assert briefing.run.started_at is not None
    assert briefing.template_version.version == 1

    draft = save_briefing_draft(
        briefing.id,
        {
            "process_type": "opening",
            "client_state": "SP",
            "has_married_partner": False,
            "married_partner_name": "must disappear",
            "origin_registry": "must disappear",
        },
    )
    assert draft.answers == {
        "process_type": "opening",
        "client_state": "SP",
        "has_married_partner": False,
    }

    completed = complete_briefing(
        briefing.id,
        _complete_sp_opening_answers(),
        completed_by=administrator,
    )
    completed.run.refresh_from_db()
    assert completed.status == SocietaryBriefingStatus.COMPLETED
    assert completed.completed_at is not None
    assert completed.completed_by == administrator
    assert completed.run.status == RunStatus.SUCCEEDED
    assert completed.run.finished_at == completed.completed_at
    assert completed.run.metadata["briefing_id"] == str(completed.id)
    assert completed.run.metadata["completed_by_id"] == administrator.pk

    completed.client_name = "Mutação indevida"
    with pytest.raises(ValidationError, match="imutável"):
        completed.save()

    with pytest.raises(ProtectedError, match="evidência imutável"), transaction.atomic():
        completed.delete()
    with pytest.raises(ProtectedError, match="evidência imutável"), transaction.atomic():
        SocietaryBriefing.objects.filter(pk=completed.pk).delete()
    assert SocietaryBriefing.objects.filter(pk=completed.pk).exists()

    administrator.is_staff = True
    administrator.is_superuser = True
    administrator.save(update_fields=("is_staff", "is_superuser"))
    request = RequestFactory().get("/admin/automations/societarybriefing/")
    request.user = administrator
    model_admin = SocietaryBriefingAdmin(SocietaryBriefing, AdminSite())
    assert model_admin.has_delete_permission(request, completed) is False
    assert "delete_selected" not in model_admin.get_actions(request)


def test_incomplete_completion_preserves_draft_and_running_execution(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    _published_version(administrator)
    briefing = create_briefing(
        client_name="Cliente Incompleto",
        client_document="12345678901",
        created_by=administrator,
    )

    with pytest.raises(ValidationError) as captured:
        complete_briefing(
            briefing.id,
            {
                "process_type": "contract_change",
                "client_state": "SP",
                "has_married_partner": False,
            },
            completed_by=administrator,
        )

    assert "alteration_summary" in captured.value.error_dict
    briefing.refresh_from_db()
    briefing.run.refresh_from_db()
    assert briefing.status == SocietaryBriefingStatus.DRAFT
    assert briefing.run.status == RunStatus.RUNNING


def test_married_partner_must_belong_to_declared_partners(
    administrator: User,
) -> None:
    _published_version(administrator)
    answers = {
        **_complete_sp_opening_answers(),
        "partner_names": "Carlos Drumond de Andrade\nClarice Lispector",
        "has_married_partner": True,
        "married_partner_name": "Estranho Nao Cadastrado",
        "marriage_regime": "partial_community",
    }
    with pytest.raises(ValidationError) as captured:
        sanitize_answers(SC06_SCHEMA_V1, answers, require_complete=True)

    assert "married_partner_name" in captured.value.error_dict
    assert "deve coincidir com um dos nomes declarados" in str(
        captured.value.error_dict["married_partner_name"][0]
    )

    # With declared partner name:
    answers["married_partner_name"] = "Clarice Lispector"
    sanitized = sanitize_answers(SC06_SCHEMA_V1, answers, require_complete=True)
    assert sanitized["married_partner_name"] == "Clarice Lispector"


def test_cancel_briefing_lifecycle_and_document_formatting(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    _published_version(administrator)
    briefing = create_briefing(
        client_name="Cliente Para Cancelamento",
        client_document="12345678901",
        created_by=administrator,
    )
    assert briefing.status == SocietaryBriefingStatus.DRAFT
    assert briefing.formatted_client_document == "123.456.789-01"

    cnpj_briefing = create_briefing(
        client_name="Empresa CNPJ",
        client_document="12345678000199",
        created_by=administrator,
    )
    assert cnpj_briefing.formatted_client_document == "12.345.678/0001-99"

    cancelled = cancel_briefing(briefing.id, cancelled_by=administrator)
    assert cancelled.status == SocietaryBriefingStatus.CANCELLED
    assert cancelled.run.status == RunStatus.CANCELLED
    assert cancelled.run.finished_at is not None
    assert "cancelado" in cancelled.run.summary.lower()

    # Cannot cancel already cancelled briefing:
    with pytest.raises(ValidationError):
        cancel_briefing(briefing.id, cancelled_by=administrator)
