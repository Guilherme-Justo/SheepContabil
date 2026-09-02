from typing import Any

from django.contrib.auth.forms import AuthenticationForm
from django.http import HttpRequest


class A11yFormMixin:
    """Injeta propriedades de acessibilidade ARIA nos widgets (WCAG AA)."""

    def get_context(self) -> dict[str, Any]:
        context = super().get_context()  # type: ignore[misc]
        for bound_field in context["form"]:
            attrs = bound_field.field.widget.attrs
            described_by = []
            if bound_field.help_text:
                described_by.append(f"{bound_field.id_for_label}_helptext")
            if bound_field.errors:
                attrs["aria-invalid"] = "true"
                described_by.append(f"{bound_field.id_for_label}_errors")
            if described_by:
                existing = attrs.get("aria-describedby", "")
                attrs["aria-describedby"] = (existing + " " + " ".join(described_by)).strip()
        return context


class PortalAuthenticationForm(A11yFormMixin, AuthenticationForm):
    error_messages = {
        "invalid_login": "Usuário ou senha inválidos. Confira os dados e tente novamente.",
        "inactive": "Este acesso está inativo. Procure um administrador.",
    }

    def __init__(
        self,
        request: HttpRequest | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(request, *args, **kwargs)
        self.fields["username"].label = "Usuário"
        self.fields["username"].widget.attrs.update(
            {
                "autocomplete": "username",
                "autofocus": True,
                "placeholder": "Digite seu usuário",
                "class": "form-input",
            }
        )
        self.fields["password"].label = "Senha"
        self.fields["password"].widget.attrs.update(
            {
                "autocomplete": "current-password",
                "placeholder": "Digite sua senha",
                "class": "form-input",
            }
        )
