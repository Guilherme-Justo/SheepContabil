from django.urls import path

from core.identity import views

app_name = "identity"

urlpatterns = [
    path("entrar/", views.PortalLoginView.as_view(), name="login"),
    path("sair/", views.PortalLogoutView.as_view(), name="logout"),
]
