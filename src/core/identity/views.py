from django.contrib.auth.views import LoginView, LogoutView

from core.identity.forms import PortalAuthenticationForm


class PortalLoginView(LoginView):
    authentication_form = PortalAuthenticationForm
    template_name = "identity/login.html"
    redirect_authenticated_user = True


class PortalLogoutView(LogoutView):
    http_method_names = ["post"]
    next_page = "/conta/entrar/"
