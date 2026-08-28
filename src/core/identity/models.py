from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models


class UserRole(models.TextChoices):
    ADMINISTRATOR = "administrator", "Administrador"
    OPERATOR = "operator", "Operador"


class Area(models.Model):
    code = models.SlugField("código", max_length=40, unique=True)
    name = models.CharField("nome", max_length=80)
    description = models.CharField("descrição", max_length=240, blank=True)
    created_at = models.DateTimeField("criada em", auto_now_add=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "área"
        verbose_name_plural = "áreas"

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    email = models.EmailField("e-mail", unique=True)
    display_name = models.CharField("nome de exibição", max_length=120, blank=True)
    role = models.CharField(
        "perfil",
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.OPERATOR,
    )
    force_password_change = models.BooleanField("trocar senha no próximo acesso", default=False)

    REQUIRED_FIELDS = ["email"]

    class Meta:
        verbose_name = "usuário"
        verbose_name_plural = "usuários"

    @property
    def label(self) -> str:
        return self.display_name or self.get_full_name() or self.username

    @property
    def is_business_administrator(self) -> bool:
        return self.role == UserRole.ADMINISTRATOR or self.is_superuser

    def can_access_area(self, area: Area) -> bool:
        if self.is_business_administrator:
            return True
        return self.area_memberships.filter(area=area).exists()


class AreaMembership(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="area_memberships",
        verbose_name="usuário",
    )
    area = models.ForeignKey(
        Area,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="área",
    )
    created_at = models.DateTimeField("criada em", auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "area"), name="unique_user_area"),
        ]
        ordering = ("area__name",)
        verbose_name = "acesso à área"
        verbose_name_plural = "acessos às áreas"

    def __str__(self) -> str:
        return f"{self.user.label} · {self.area.name}"
