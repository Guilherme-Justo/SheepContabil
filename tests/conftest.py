import pytest

from core.automations.models import (
    AutomationComplexity,
    AutomationFrequency,
    AutomationModule,
    AutomationNature,
)
from core.identity.models import Area, AreaMembership, User, UserRole


@pytest.fixture
def areas(db) -> dict[str, Area]:
    return {
        code: Area.objects.create(code=code, name=name)
        for code, name in (
            ("fiscal", "Fiscal"),
            ("tecnologia", "Tecnologia"),
            ("societario", "Societário"),
            ("processos", "Processos"),
        )
    }


@pytest.fixture
def modules(areas: dict[str, Area]) -> dict[str, AutomationModule]:
    definitions = (
        ("SC-04", "triagem", "Triagem da caixa de arquivos", "fiscal", AutomationNature.AI_AGENT),
        ("SC-05", "bloqueio", "Bloqueio e desbloqueio", "tecnologia", AutomationNature.RPA),
        ("SC-06", "briefing", "Briefing societário", "societario", AutomationNature.CONTROL),
        (
            "SC-20",
            "certificados",
            "Vencimento de certificado",
            "processos",
            AutomationNature.CONTROL,
        ),
    )
    return {
        code: AutomationModule.objects.create(
            code=code,
            slug=slug,
            name=name,
            short_description=f"Descrição sintética de {code}",
            nature=nature,
            complexity=AutomationComplexity.MEDIUM,
            frequency=AutomationFrequency.ON_DEMAND,
            area=areas[area_code],
            sort_order=index,
        )
        for index, (code, slug, name, area_code, nature) in enumerate(definitions, start=1)
    }


@pytest.fixture
def administrator(db) -> User:
    return User.objects.create_user(
        username="admin",
        email="admin@example.test",
        password="safe-test-password",
        display_name="Admin Teste",
        role=UserRole.ADMINISTRATOR,
    )


@pytest.fixture
def processes_operator(db, areas: dict[str, Area]) -> User:
    user = User.objects.create_user(
        username="operador",
        email="operador@example.test",
        password="safe-test-password",
        display_name="Operador Teste",
        role=UserRole.OPERATOR,
    )
    AreaMembership.objects.create(user=user, area=areas["processos"])
    return user


@pytest.fixture
def societary_operator(db, areas: dict[str, Area]) -> User:
    user = User.objects.create_user(
        username="operador.societario",
        email="operador.societario@example.test",
        password="safe-test-password",
        display_name="Operador Societário",
        role=UserRole.OPERATOR,
    )
    AreaMembership.objects.create(user=user, area=areas["societario"])
    return user


@pytest.fixture
def fiscal_operator(db, areas: dict[str, Area]) -> User:
    user = User.objects.create_user(
        username="operador.fiscal",
        email="operador.fiscal@example.test",
        password="safe-test-password",
        display_name="Operador Fiscal",
        role=UserRole.OPERATOR,
    )
    AreaMembership.objects.create(user=user, area=areas["fiscal"])
    return user
