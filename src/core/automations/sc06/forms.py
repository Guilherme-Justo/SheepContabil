from __future__ import annotations

from datetime import date
from typing import Any

from django import forms

from core.automations.sc06.rules import get_active_questions


class BriefingAnswersForm(forms.Form):
    """Build a bounded Django form from one already-validated template version."""

    def __init__(
        self,
        *,
        schema: dict[str, Any],
        answers: dict[str, Any] | None = None,
        data: Any | None = None,
    ) -> None:
        initial = {
            key: ("true" if value is True else "false" if value is False else value)
            for key, value in (answers or {}).items()
        }
        super().__init__(data=data, initial=initial)
        self.schema = schema
        self._questions: list[dict[str, Any]] = []
        for section in schema.get("sections", []):
            for question in section.get("questions", []):
                self._questions.append(question)
                self.fields[question["id"]] = _question_field(question)

    @property
    def sections(self) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        for section in self.schema.get("sections", []):
            questions = []
            for question in section.get("questions", []):
                questions.append(
                    {
                        "id": question["id"],
                        "label": question["label"],
                        "help_text": question.get("help_text", ""),
                        "required": bool(question.get("required", False)),
                        "condition": question.get("visible_when"),
                        "bound_field": self[question["id"]],
                    }
                )
            sections.append(
                {
                    "id": section["id"],
                    "title": section["title"],
                    "description": section.get("description", ""),
                    "condition": section.get("visible_when"),
                    "questions": questions,
                }
            )
        return sections

    @property
    def answer_payload(self) -> dict[str, Any]:
        if not hasattr(self, "cleaned_data"):
            raise RuntimeError("Valide o formulário antes de obter as respostas.")
        return {
            question["id"]: _serialise_answer(self.cleaned_data.get(question["id"]))
            for question in self._questions
        }

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        if not self.is_bound:
            return cleaned_data
        active_ids = {question["id"] for question in get_active_questions(self.schema, self.data)}
        for question in self._questions:
            question_id = question["id"]
            if question_id in active_ids:
                continue
            self.errors.pop(question_id, None)
            cleaned_data.pop(question_id, None)
        return cleaned_data


def _question_field(question: dict[str, Any]) -> forms.Field:
    question_type = question["type"]
    validation = question.get("validation", {})
    common: dict[str, Any] = {
        "label": question["label"],
        "help_text": question.get("help_text", ""),
        # Conditional requiredness is authoritative in the domain service.
        "required": False,
    }
    attrs = {
        "data-sc06-answer": question["id"],
        "autocomplete": "off",
    }
    placeholder = question.get("placeholder")
    if placeholder:
        attrs["placeholder"] = placeholder

    if question_type == "textarea":
        return forms.CharField(
            **common,
            min_length=validation.get("min_length"),
            max_length=validation.get("max_length"),
            widget=forms.Textarea(attrs={**attrs, "rows": 4}),
        )
    if question_type == "email":
        return forms.EmailField(
            **common,
            min_length=validation.get("min_length"),
            max_length=validation.get("max_length"),
            widget=forms.EmailInput(attrs=attrs),
        )
    if question_type == "choice":
        choices = [("", "Selecione uma opção")]
        choices.extend((option["value"], option["label"]) for option in question["options"])
        return forms.ChoiceField(**common, choices=choices, widget=forms.Select(attrs=attrs))
    if question_type == "boolean":
        return forms.TypedChoiceField(
            **common,
            choices=(("", "Selecione uma opção"), ("true", "Sim"), ("false", "Não")),
            coerce=lambda value: value == "true",
            empty_value=None,
            widget=forms.Select(attrs=attrs),
        )
    if question_type == "date":
        return forms.DateField(
            **common,
            input_formats=("%Y-%m-%d",),
            widget=forms.DateInput(attrs={**attrs, "type": "date"}),
        )
    return forms.CharField(
        **common,
        min_length=validation.get("min_length"),
        max_length=validation.get("max_length"),
        widget=forms.TextInput(attrs=attrs),
    )


def _serialise_answer(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return value
