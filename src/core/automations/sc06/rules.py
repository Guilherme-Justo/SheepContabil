from __future__ import annotations

import re
from collections.abc import Mapping
from contextlib import suppress
from datetime import date
from typing import cast

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

QUESTION_TYPES = frozenset({"text", "textarea", "choice", "boolean", "date", "email"})
LEAF_OPERATORS = frozenset({"equals", "not_equals", "in"})
COMPOSITE_OPERATORS = frozenset({"all", "any"})

_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_BOOLEAN_TRUE = frozenset({"1", "true", "on", "yes", "sim"})
_BOOLEAN_FALSE = frozenset({"0", "false", "off", "no", "nao", "não"})
_MAX_CONDITION_DEPTH = 10
_ROOT_KEYS = frozenset({"title", "description", "sections"})
_SECTION_KEYS = frozenset({"id", "title", "description", "visible_when", "questions"})
_QUESTION_KEYS = frozenset(
    {
        "id",
        "label",
        "type",
        "required",
        "help_text",
        "placeholder",
        "options",
        "visible_when",
        "validation",
    }
)


def validate_template_schema(schema: object) -> None:
    """Validate the small declarative schema supported by SC-06.

    Conditions may only reference questions declared before the conditional
    section/question. Besides preventing cycles, this makes visibility
    deterministic when hidden answers are discarded by the server.
    """

    root = _require_mapping(schema, "O schema do briefing deve ser um objeto JSON.")
    _validate_allowed_keys(root, _ROOT_KEYS, "O schema")
    _require_non_empty_text(root.get("title"), "O schema deve informar um título.")
    _validate_optional_text(root, "description", "A descrição do schema deve ser textual.")
    sections = _require_list(
        root.get("sections"),
        "O schema deve conter uma lista não vazia de seções.",
    )
    if not sections:
        raise ValidationError("O schema deve conter ao menos uma seção.")

    section_ids: set[str] = set()
    question_positions: dict[str, int] = {}
    question_definitions: dict[str, Mapping[str, object]] = {}
    normalized_sections: list[Mapping[str, object]] = []
    position = 0

    for section_index, raw_section in enumerate(sections, start=1):
        section = _require_mapping(
            raw_section,
            f"A seção {section_index} deve ser um objeto JSON.",
        )
        _validate_allowed_keys(section, _SECTION_KEYS, f"A seção {section_index}")
        section_id = _validate_identifier(
            section.get("id"),
            f"A seção {section_index} deve ter um identificador válido.",
        )
        if section_id in section_ids:
            raise ValidationError(f'A seção "{section_id}" está duplicada no schema.')
        section_ids.add(section_id)
        _require_non_empty_text(
            section.get("title"),
            f'A seção "{section_id}" deve informar um título.',
        )
        _validate_optional_text(
            section,
            "description",
            f'A descrição da seção "{section_id}" deve ser textual.',
        )
        questions = _require_list(
            section.get("questions"),
            f'A seção "{section_id}" deve conter uma lista de perguntas.',
        )
        if not questions:
            raise ValidationError(f'A seção "{section_id}" deve conter ao menos uma pergunta.')

        for raw_question in questions:
            question = _require_mapping(
                raw_question,
                f'As perguntas da seção "{section_id}" devem ser objetos JSON.',
            )
            question_id = _validate_identifier(
                question.get("id"),
                f'Uma pergunta da seção "{section_id}" possui identificador inválido.',
            )
            if question_id in question_positions:
                raise ValidationError(f'A pergunta "{question_id}" está duplicada no schema.')
            question_positions[question_id] = position
            question_definitions[question_id] = question
            position += 1
            _validate_question(question, question_id=question_id)
        normalized_sections.append(section)

    position = 0
    for section in normalized_sections:
        section_id = cast(str, section["id"])
        section_condition = section.get("visible_when")
        if section_condition is not None:
            _validate_condition(
                section_condition,
                known_questions=question_positions,
                question_definitions=question_definitions,
                before_position=position,
                context=f'a seção "{section_id}"',
            )
        questions = cast(list[object], section["questions"])
        for raw_question in questions:
            question = cast(Mapping[str, object], raw_question)
            question_id = cast(str, question["id"])
            condition = question.get("visible_when")
            if condition is not None:
                _validate_condition(
                    condition,
                    known_questions=question_positions,
                    question_definitions=question_definitions,
                    before_position=position,
                    context=f'a pergunta "{question_id}"',
                )
            position += 1


def evaluate_condition(condition: object | None, answers: Mapping[str, object]) -> bool:
    """Evaluate one previously validated condition without executing arbitrary code."""

    if condition is None:
        return True
    node = _require_mapping(condition, "A condição deve ser um objeto JSON.")
    operator = node.get("operator")
    if operator in COMPOSITE_OPERATORS:
        children = _require_list(
            node.get("conditions"),
            "Uma condição composta deve conter condições filhas.",
        )
        if not children:
            raise ValidationError("Uma condição composta deve conter condições filhas.")
        results = [evaluate_condition(child, answers) for child in children]
        return all(results) if operator == "all" else any(results)
    if operator not in LEAF_OPERATORS:
        raise ValidationError("A condição possui um operador não permitido.")

    field = node.get("field")
    if not isinstance(field, str) or not field:
        raise ValidationError("A condição deve referenciar uma pergunta válida.")
    actual = answers.get(field)
    expected = node.get("value")
    if operator == "equals":
        return actual == expected
    if operator == "not_equals":
        return actual != expected
    values = _require_list(expected, 'O operador "in" exige uma lista de valores.')
    return actual in values


def get_active_sections(
    schema: Mapping[str, object],
    answers: Mapping[str, object],
) -> list[dict[str, object]]:
    """Return active sections, each containing only its active questions."""

    validate_template_schema(schema)
    effective_answers: dict[str, object] = {}
    active_sections: list[dict[str, object]] = []
    for section in _schema_sections(schema):
        if not evaluate_condition(section.get("visible_when"), effective_answers):
            continue
        active_questions: list[dict[str, object]] = []
        for question in _section_questions(section):
            if not evaluate_condition(question.get("visible_when"), effective_answers):
                continue
            question_copy = dict(question)
            active_questions.append(question_copy)
            question_id = cast(str, question["id"])
            if question_id in answers:
                # Invalid input must not activate dependent branches.
                with suppress(ValidationError):
                    effective_answers[question_id] = _coerce_answer(
                        question,
                        answers[question_id],
                    )
        section_copy = dict(section)
        section_copy["questions"] = active_questions
        active_sections.append(section_copy)
    return active_sections


def get_active_questions(
    schema: Mapping[str, object],
    answers: Mapping[str, object],
) -> list[dict[str, object]]:
    """Return active questions in stable schema order."""

    return [
        question
        for section in get_active_sections(schema, answers)
        for question in cast(list[dict[str, object]], section["questions"])
    ]


def sanitize_answers(
    schema: Mapping[str, object],
    answers: Mapping[str, object],
    *,
    require_complete: bool = False,
) -> dict[str, object]:
    """Normalize active answers and discard unknown or hidden fields.

    Drafts may omit fields. Completion uses ``require_complete=True`` and
    receives field-addressable validation errors suitable for forms and APIs.
    """

    validate_template_schema(schema)
    sanitized: dict[str, object] = {}
    errors: dict[str, list[str]] = {}

    for section in _schema_sections(schema):
        if not evaluate_condition(section.get("visible_when"), sanitized):
            continue
        for question in _section_questions(section):
            if not evaluate_condition(question.get("visible_when"), sanitized):
                continue
            question_id = cast(str, question["id"])
            if question_id not in answers or _is_empty(answers[question_id]):
                if require_complete and bool(question.get("required", False)):
                    errors[question_id] = ["Este campo é obrigatório."]
                continue
            try:
                sanitized[question_id] = _coerce_answer(question, answers[question_id])
            except ValidationError as exc:
                errors[question_id] = list(exc.messages)

    if require_complete and sanitized.get("has_married_partner") is True:
        married_partner = sanitized.get("married_partner_name")
        partner_names = sanitized.get("partner_names")
        if (
            married_partner
            and partner_names
            and isinstance(married_partner, str)
            and isinstance(partner_names, str)
        ):
            if not _is_partner_declared(married_partner, partner_names):
                errors.setdefault("married_partner_name", []).append(
                    f"O sócio casado informado ('{married_partner}') deve coincidir com um dos nomes declarados no quadro societário."
                )

    if errors:
        raise ValidationError(errors)
    return sanitized


def _is_partner_declared(married_partner: str, partner_names: str) -> bool:
    if not married_partner or not partner_names:
        return True
    married_norm = married_partner.strip().lower()
    partners_norm = partner_names.strip().lower()
    if married_norm in partners_norm:
        return True
    items = [item.strip() for item in re.split(r"[\n,;]+", partners_norm) if item.strip()]
    for item in items:
        clean_item = re.sub(r"\s*-\s*\d{2,3}.*$", "", item).strip()
        if married_norm == clean_item or married_norm in clean_item or clean_item in married_norm:
            return True
        m_words = set(re.findall(r"\w+", married_norm))
        i_words = set(re.findall(r"\w+", clean_item))
        if m_words and m_words.issubset(i_words):
            return True
    return False


def format_answer(question: Mapping[str, object], value: object) -> str:
    """Format one stored value for the consolidated human-readable result."""

    question_type = question.get("type")
    if question_type == "boolean" and isinstance(value, bool):
        return "Sim" if value else "Não"
    if question_type == "choice":
        for option in _choice_options(question):
            if option.get("value") == value:
                return str(option.get("label", value))
    if question_type == "date" and isinstance(value, str):
        try:
            return date.fromisoformat(value).strftime("%d/%m/%Y")
        except ValueError:
            return value
    return str(value)


def format_answers(
    schema: Mapping[str, object],
    answers: Mapping[str, object],
) -> list[dict[str, object]]:
    """Build a stable, audit-friendly representation of active answers."""

    formatted: list[dict[str, object]] = []
    for section in get_active_sections(schema, answers):
        section_id = cast(str, section["id"])
        section_title = cast(str, section["title"])
        for question in cast(list[dict[str, object]], section["questions"]):
            question_id = cast(str, question["id"])
            if question_id not in answers:
                continue
            value = answers[question_id]
            formatted.append(
                {
                    "section_id": section_id,
                    "section_title": section_title,
                    "question_id": question_id,
                    "question_label": question["label"],
                    "question_type": question["type"],
                    "value": value,
                    "display_value": format_answer(question, value),
                }
            )
    return formatted


def build_frontend_config(
    schema: Mapping[str, object],
    answers: Mapping[str, object],
) -> dict[str, object]:
    """Expose every branch so the browser can reveal fields interactively."""

    validate_template_schema(schema)
    return {
        "sections": [
            {
                **dict(section),
                "questions": [dict(question) for question in _section_questions(section)],
            }
            for section in _schema_sections(schema)
        ],
        "answers": dict(answers),
    }


def _validate_question(question: Mapping[str, object], *, question_id: str) -> None:
    _validate_allowed_keys(question, _QUESTION_KEYS, f'A pergunta "{question_id}"')
    _require_non_empty_text(
        question.get("label"),
        f'A pergunta "{question_id}" deve informar um rótulo.',
    )
    question_type = question.get("type")
    if question_type not in QUESTION_TYPES:
        raise ValidationError(f'A pergunta "{question_id}" possui um tipo não permitido.')
    required = question.get("required", False)
    if not isinstance(required, bool):
        raise ValidationError(f'A obrigatoriedade da pergunta "{question_id}" deve ser booleana.')
    _validate_optional_text(
        question,
        "help_text",
        f'A ajuda da pergunta "{question_id}" deve ser textual.',
    )
    _validate_optional_text(
        question,
        "placeholder",
        f'O placeholder da pergunta "{question_id}" deve ser textual.',
    )

    if question_type == "choice":
        options = _require_list(
            question.get("options"),
            f'A pergunta "{question_id}" deve informar opções.',
        )
        if not options:
            raise ValidationError(f'A pergunta "{question_id}" deve informar ao menos uma opção.')
        values: set[str] = set()
        for raw_option in options:
            option = _require_mapping(
                raw_option,
                f'As opções da pergunta "{question_id}" devem ser objetos JSON.',
            )
            _validate_allowed_keys(
                option,
                frozenset({"value", "label"}),
                f'Uma opção da pergunta "{question_id}"',
            )
            raw_value = option.get("value")
            value = _require_non_empty_text(
                raw_value,
                f'Uma opção da pergunta "{question_id}" possui valor inválido.',
            )
            if value != raw_value:
                raise ValidationError(
                    f'Uma opção da pergunta "{question_id}" deve usar valor sem espaços externos.'
                )
            _require_non_empty_text(
                option.get("label"),
                f'Uma opção da pergunta "{question_id}" deve informar um rótulo.',
            )
            if value in values:
                raise ValidationError(
                    f'A opção "{value}" está duplicada na pergunta "{question_id}".'
                )
            values.add(value)
    elif "options" in question:
        raise ValidationError(f'A pergunta "{question_id}" só pode ter opções se for de escolha.')

    validation = question.get("validation")
    if validation is None:
        return
    validation_config = _require_mapping(
        validation,
        f'A validação da pergunta "{question_id}" deve ser um objeto JSON.',
    )
    unsupported = set(validation_config) - {"min_length", "max_length"}
    if unsupported:
        raise ValidationError(f'A pergunta "{question_id}" possui validação não permitida.')
    if question_type not in {"text", "textarea", "email"} and validation_config:
        raise ValidationError(
            f'O tipo da pergunta "{question_id}" não aceita validação de tamanho.'
        )
    minimum = _optional_non_negative_int(validation_config.get("min_length"), question_id)
    maximum = _optional_non_negative_int(validation_config.get("max_length"), question_id)
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValidationError(
            f'O tamanho mínimo da pergunta "{question_id}" não pode exceder o máximo.'
        )


def _validate_condition(
    condition: object,
    *,
    known_questions: Mapping[str, int],
    question_definitions: Mapping[str, Mapping[str, object]],
    before_position: int,
    context: str,
    depth: int = 0,
) -> None:
    if depth >= _MAX_CONDITION_DEPTH:
        raise ValidationError(f"A condição de {context} excede o limite de níveis.")
    node = _require_mapping(condition, f"A condição de {context} deve ser um objeto JSON.")
    operator = node.get("operator")
    if operator in COMPOSITE_OPERATORS:
        _validate_allowed_keys(
            node,
            frozenset({"operator", "conditions"}),
            f"A condição composta de {context}",
        )
        children = _require_list(
            node.get("conditions"),
            f"A condição composta de {context} deve conter condições filhas.",
        )
        if not children:
            raise ValidationError(f"A condição composta de {context} deve conter condições filhas.")
        for child in children:
            _validate_condition(
                child,
                known_questions=known_questions,
                question_definitions=question_definitions,
                before_position=before_position,
                context=context,
                depth=depth + 1,
            )
        return
    if operator not in LEAF_OPERATORS:
        raise ValidationError(f"A condição de {context} possui um operador não permitido.")
    _validate_allowed_keys(
        node,
        frozenset({"field", "operator", "value"}),
        f"A condição de {context}",
    )
    field = node.get("field")
    if not isinstance(field, str) or field not in known_questions:
        raise ValidationError(f"A condição de {context} referencia uma pergunta inexistente.")
    if known_questions[field] >= before_position:
        raise ValidationError(f"A condição de {context} deve referenciar uma pergunta anterior.")
    if "value" not in node:
        raise ValidationError(f"A condição de {context} deve informar um valor de comparação.")
    referenced_question = question_definitions[field]
    if operator == "in":
        values = _require_list(
            node["value"],
            f'O operador "in" da condição de {context} exige uma lista.',
        )
        if not values:
            raise ValidationError(
                f'O operador "in" da condição de {context} exige ao menos um valor.'
            )
        for value in values:
            _validate_condition_value(referenced_question, value, context=context)
        return
    _validate_condition_value(referenced_question, node["value"], context=context)


def _validate_condition_value(
    question: Mapping[str, object],
    value: object,
    *,
    context: str,
) -> None:
    question_type = question["type"]
    if question_type == "boolean":
        if not isinstance(value, bool):
            raise ValidationError(f"A condição de {context} deve comparar um valor booleano.")
        return
    if not isinstance(value, str):
        raise ValidationError(f"A condição de {context} deve comparar um valor textual.")
    try:
        normalized = _coerce_answer(question, value)
    except ValidationError as exc:
        raise ValidationError(
            f"A condição de {context} possui valor incompatível: {exc.messages[0]}"
        ) from exc
    if normalized != value:
        raise ValidationError(
            f"A condição de {context} deve usar um valor já normalizado, sem espaços externos."
        )


def _coerce_answer(question: Mapping[str, object], raw_value: object) -> object:
    question_type = question["type"]
    if question_type == "boolean":
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, int) and raw_value in {0, 1}:
            return bool(raw_value)
        if isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if normalized in _BOOLEAN_TRUE:
                return True
            if normalized in _BOOLEAN_FALSE:
                return False
        raise ValidationError("Informe Sim ou Não.")

    if not isinstance(raw_value, str):
        raise ValidationError("Informe um valor textual válido.")
    value = raw_value.strip()
    if question_type == "choice":
        allowed = {cast(str, option["value"]) for option in _choice_options(question)}
        if value not in allowed:
            raise ValidationError("Selecione uma das opções disponíveis.")
    elif question_type == "date":
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError("Informe uma data válida.") from exc
    elif question_type == "email":
        try:
            validate_email(value)
        except ValidationError as exc:
            raise ValidationError("Informe um e-mail válido.") from exc

    validation = question.get("validation")
    if isinstance(validation, Mapping):
        minimum = validation.get("min_length")
        maximum = validation.get("max_length")
        if isinstance(minimum, int) and len(value) < minimum:
            raise ValidationError(f"Informe ao menos {minimum} caracteres.")
        if isinstance(maximum, int) and len(value) > maximum:
            raise ValidationError(f"Informe no máximo {maximum} caracteres.")
    return value


def _schema_sections(schema: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [
        cast(Mapping[str, object], section) for section in cast(list[object], schema["sections"])
    ]


def _section_questions(section: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [
        cast(Mapping[str, object], question)
        for question in cast(list[object], section["questions"])
    ]


def _choice_options(question: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [
        cast(Mapping[str, object], option)
        for option in cast(list[object], question.get("options", []))
    ]


def _require_mapping(value: object, message: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationError(message)
    if not all(isinstance(key, str) for key in value):
        raise ValidationError(message)
    return cast(Mapping[str, object], value)


def _require_list(value: object, message: str) -> list[object]:
    if not isinstance(value, list):
        raise ValidationError(message)
    return value


def _require_non_empty_text(value: object, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(message)
    return value.strip()


def _validate_optional_text(
    value: Mapping[str, object],
    key: str,
    message: str,
) -> None:
    if key in value and not isinstance(value[key], str):
        raise ValidationError(message)


def _validate_allowed_keys(
    value: Mapping[str, object],
    allowed: frozenset[str],
    context: str,
) -> None:
    unsupported = sorted(set(value) - allowed)
    if unsupported:
        fields = ", ".join(unsupported)
        raise ValidationError(f"{context} possui campo(s) não permitido(s): {fields}.")


def _validate_identifier(value: object, message: str) -> str:
    identifier = _require_non_empty_text(value, message)
    if identifier != value or not _IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValidationError(message)
    return identifier


def _optional_non_negative_int(value: object, question_id: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(
            f'A validação de tamanho da pergunta "{question_id}" deve ser um inteiro positivo.'
        )
    return value


def _is_empty(value: object) -> bool:
    return value is None or value == "" or value == []
