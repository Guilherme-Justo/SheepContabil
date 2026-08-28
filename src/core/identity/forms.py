from typing import Any

from django.contrib.auth.forms import AuthenticationForm
from django.http import HttpRequest


class PortalAuthenticationForm(AuthenticationForm):
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
