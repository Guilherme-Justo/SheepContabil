from __future__ import annotations

import re
from typing import Any

from django import forms

from core.automations.models import CommunicationChannel, DigitalCertificate


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
