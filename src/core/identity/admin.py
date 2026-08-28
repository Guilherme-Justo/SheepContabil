from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from core.identity.models import Area, AreaMembership, User


class AreaMembershipInline(admin.TabularInline):  # type: ignore[type-arg]
    model = AreaMembership
    extra = 0


@admin.register(User)
class PortalUserAdmin(UserAdmin):  # type: ignore[type-arg]
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Dados pessoais", {"fields": ("first_name", "last_name", "email")}),
        (
            "Acesso SheepContabil",
            {"fields": ("display_name", "role", "force_password_change")},
        ),
        (
            "Permissões Django",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Datas importantes", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "password1", "password2"),
            },
        ),
        (
            "Acesso SheepContabil",
            {"fields": ("email", "display_name", "role")},
        ),
    )
    list_display = ("username", "email", "display_name", "role", "is_active")
    list_filter = ("role", "is_active", "is_staff")
    inlines = (AreaMembershipInline,)


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "code", "description")
    search_fields = ("name", "code")


@admin.register(AreaMembership)
class AreaMembershipAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("user", "area", "created_at")
    list_filter = ("area",)
    autocomplete_fields = ("user", "area")
