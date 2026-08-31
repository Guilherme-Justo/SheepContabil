from __future__ import annotations

import re
from typing import Any, cast

from django import forms
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from core.automations.models import (
    CommunicationChannel,
    DigitalCertificate,
    DocumentClassificationAttempt,
    DocumentRunOutcome,
    DocumentSource,
    DocumentStatus,
    DocumentType,
    FiscalClient,
)
from core.automations.sc04.contracts import InvalidDocument, ValidatedDocument
from core.automations.sc04.validation import validate_document


class SC04UploadForm(forms.Form):
    attachment = forms.FileField(
        label="Documento sintético",
        help_text="PDF, PNG, JPEG ou TXT UTF-8 com até 10 MiB.",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": (
                    ".pdf,.png,.jpg,.jpeg,.txt,application/pdf,image/png,image/jpeg,text/plain"
                )
            }
        ),
    )
    confirm_synthetic = forms.BooleanField(
        label="Confirmo que o arquivo contém somente dados sintéticos",
        required=True,
    )

    validated_document: ValidatedDocument | None = None

    def clean_attachment(self) -> UploadedFile:
        attachment = self.cleaned_data["attachment"]
        if not isinstance(attachment, UploadedFile):
            raise forms.ValidationError("Selecione um arquivo válido.")
        if (attachment.size or 0) > int(settings.SC04_MAX_UPLOAD_BYTES):
            max_mib = int(settings.SC04_MAX_UPLOAD_BYTES) // (1024 * 1024)
            raise forms.ValidationError(f"O arquivo ultrapassa o limite de {max_mib} MiB.")
        content = attachment.read()
        attachment.seek(0)
        try:
            self.validated_document = validate_document(
                filename=attachment.name or "documento",
                declared_content_type=attachment.content_type or "",
                content=content,
            )
        except InvalidDocument as exc:
            raise forms.ValidationError(str(exc)) from exc
        return attachment


class SC04ReviewForm(forms.Form):
    document_type = forms.ChoiceField(
        label="Tipo documental confirmado",
        choices=[
            ("", "Selecione o tipo documental"),
            *[choice for choice in DocumentType.choices if choice[0] != DocumentType.UNKNOWN],
        ],
    )
    client = forms.ModelChoiceField(
        label="Cliente confirmado",
        queryset=FiscalClient.objects.none(),
    )
    notes = forms.CharField(
        label="Justificativa da correção",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Obrigatória quando a decisão for diferente da sugestão da IA.",
    )

    def __init__(
        self,
        *args: Any,
        attempt: DocumentClassificationAttempt,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.attempt = attempt
        self.reference_client_id = attempt.document.matched_client_id or attempt.predicted_client_id
        client_field = cast("forms.ModelChoiceField[FiscalClient]", self.fields["client"])
        client_field.queryset = FiscalClient.objects.filter(is_active=True).order_by("name")
        if not self.is_bound:
            self.initial.update(
                {
                    "document_type": attempt.predicted_document_type,
                    "client": self.reference_client_id,
                }
            )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        selected_type = cleaned.get("document_type")
        selected_client = cleaned.get("client")
        changed = (
            selected_type != self.attempt.predicted_document_type
            or getattr(selected_client, "pk", None) != self.reference_client_id
        )
        if changed and not str(cleaned.get("notes") or "").strip():
            self.add_error("notes", "Explique a correção feita sobre a sugestão da IA.")
        return cleaned


class SC04QueueFilterForm(forms.Form):
    status = forms.ChoiceField(
        label="Estado",
        required=False,
        choices=[("", "Todos os estados"), *DocumentStatus.choices],
    )
    source = forms.ChoiceField(
        label="Origem",
        required=False,
        choices=[("", "Todas as origens"), *DocumentSource.choices],
    )
    outcome = forms.ChoiceField(
        label="Resultado",
        required=False,
        choices=[("", "Todos os resultados"), *DocumentRunOutcome.choices],
    )
    q = forms.CharField(
        label="Buscar",
        required=False,
        max_length=120,
        widget=forms.TextInput(attrs={"placeholder": "Arquivo ou cliente"}),
    )


class DigitalCertificateForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = DigitalCertificate
        fields = (
            "serial_number",
            "client_name",
            "client_document",
            "responsible_name",
            "contact_email",
            "contact_phone",
            "preferred_channel",
            "valid_until",
            "status",
        )
        labels = {
            "serial_number": "Identificador do certificado",
            "client_name": "Cliente",
            "client_document": "CPF ou CNPJ",
            "responsible_name": "Responsável",
            "contact_email": "E-mail",
            "contact_phone": "Telefone",
            "preferred_channel": "Canal do aviso",
            "valid_until": "Validade",
            "status": "Estado",
        }
        help_texts = {
            "serial_number": "Use um identificador sintético único.",
            "client_document": "Informe 11 ou 14 dígitos fictícios.",
            "contact_phone": "Obrigatório quando o canal escolhido for WhatsApp.",
        }
        widgets = {
            "valid_until": forms.DateInput(attrs={"type": "date"}),
            "contact_email": forms.EmailInput(attrs={"placeholder": "contato@exemplo.test"}),
            "contact_phone": forms.TextInput(attrs={"placeholder": "+55 11 99999-0000"}),
        }

    def clean_serial_number(self) -> str:
        return str(self.cleaned_data["serial_number"]).strip().upper()

    def clean_client_document(self) -> str:
        document = re.sub(r"\D", "", str(self.cleaned_data["client_document"]))
        if len(document) not in {11, 14}:
            raise forms.ValidationError("Informe um CPF ou CNPJ sintético com 11 ou 14 dígitos.")
        return document

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        channel = cleaned_data.get("preferred_channel")
        email = str(cleaned_data.get("contact_email") or "").strip()
        phone = str(cleaned_data.get("contact_phone") or "").strip()
        if channel == CommunicationChannel.EMAIL and not email:
            self.add_error("contact_email", "Informe o e-mail usado no aviso simulado.")
        if channel == CommunicationChannel.WHATSAPP and not phone:
            self.add_error("contact_phone", "Informe o telefone usado no aviso simulado.")
        return cleaned_data


class BriefingStartForm(forms.Form):
    client_name = forms.CharField(
        label="Cliente sintético",
        max_length=180,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Ex.: Horizonte Participações Demo",
            }
        ),
    )
    client_document = forms.CharField(
        label="CPF ou CNPJ sintético",
        max_length=18,
        help_text="Informe 11 ou 14 dígitos fictícios.",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "inputmode": "numeric",
                "placeholder": "00.000.000/0000-00",
            }
        ),
    )

    def clean_client_name(self) -> str:
        return str(self.cleaned_data["client_name"]).strip()

    def clean_client_document(self) -> str:
        document = re.sub(r"\D", "", str(self.cleaned_data["client_document"]))
        if len(document) not in {11, 14}:
            raise forms.ValidationError("Informe um CPF ou CNPJ sintético com 11 ou 14 dígitos.")
        return document
