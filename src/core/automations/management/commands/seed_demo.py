import os
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.automations.models import (
    AutomationComplexity,
    AutomationFrequency,
    AutomationModule,
    AutomationNature,
    AutomationRun,
    RunStatus,
    RunTrigger,
)
from core.identity.models import Area, AreaMembership, User, UserRole

AREAS = (
    ("fiscal", "Fiscal", "Documentos, tributos e obrigações fiscais."),
    ("tecnologia", "Tecnologia", "Operação e integração dos sistemas internos."),
    ("societario", "Societário", "Aberturas, alterações e atos societários."),
    ("processos", "Processos", "Obrigações e acessos junto aos órgãos."),
)

MODULES = (
    {
        "code": "SC-04",
        "slug": "triagem-caixa-arquivos",
        "name": "Triagem da caixa de arquivos",
        "short_description": (
            "Classifica cada anexo por documento e cliente, renomeia e encaminha "
            "casos duvidosos para revisão humana."
        ),
        "nature": AutomationNature.AI_AGENT,
        "complexity": AutomationComplexity.MEDIUM,
        "frequency": AutomationFrequency.DAILY,
        "area": "fiscal",
        "sort_order": 10,
    },
    {
        "code": "SC-05",
        "slug": "bloqueio-clientes-inadimplentes",
        "name": "Bloqueio e desbloqueio de clientes",
        "short_description": (
            "Executa a sequência de bloqueio nos sistemas, mostra cada etapa e "
            "preserva o caminho seguro de desfazer."
        ),
        "nature": AutomationNature.RPA,
        "complexity": AutomationComplexity.MEDIUM,
        "frequency": AutomationFrequency.ON_DEMAND,
        "area": "tecnologia",
        "sort_order": 20,
    },
    {
        "code": "SC-06",
        "slug": "briefing-societario",
        "name": "Briefing societário condicional",
        "short_description": (
            "Abre perguntas conforme as respostas e só conclui quando todos os "
            "dados obrigatórios daquele caso estão completos."
        ),
        "nature": AutomationNature.CONTROL,
        "complexity": AutomationComplexity.MEDIUM,
        "frequency": AutomationFrequency.ON_DEMAND,
        "area": "societario",
        "sort_order": 30,
    },
    {
        "code": "SC-20",
        "slug": "vencimento-certificado-digital",
        "name": "Vencimento de certificado digital",
        "short_description": (
            "Acompanha certificados nos próximos 60 dias e registra avisos sem "
            "repetir comunicações que já foram feitas."
        ),
        "nature": AutomationNature.CONTROL,
        "complexity": AutomationComplexity.LOW,
        "frequency": AutomationFrequency.MONTHLY,
        "area": "processos",
        "sort_order": 40,
    },
)


class Command(BaseCommand):
    help = "Cria massa sintética idempotente para a demonstração do portal."

    @transaction.atomic
    def handle(self, *args: object, **options: object) -> None:
        areas: dict[str, Area] = {}
        for code, name, description in AREAS:
            areas[code], _ = Area.objects.update_or_create(
                code=code,
                defaults={"name": name, "description": description},
            )

        modules: dict[str, AutomationModule] = {}
        for definition in MODULES:
            payload = dict(definition)
            code = str(payload.pop("code"))
            area_code = str(payload.pop("area"))
            payload["area"] = areas[area_code]
            modules[code], _ = AutomationModule.objects.update_or_create(
                code=code,
                defaults=payload,
            )

        admin = self._upsert_user(
            username=os.getenv("DEMO_ADMIN_USERNAME", "admin"),
            email=os.getenv("DEMO_ADMIN_EMAIL", "admin@sheepcontabil.local"),
            password=os.getenv("DEMO_ADMIN_PASSWORD", ""),
            display_name="Administrador SheepContabil",
            role=UserRole.ADMINISTRATOR,
        )
        operator = self._upsert_user(
            username=os.getenv("DEMO_OPERATOR_USERNAME", "operador.processos"),
            email=os.getenv(
                "DEMO_OPERATOR_EMAIL",
                "operador.processos@sheepcontabil.local",
            ),
            password=os.getenv("DEMO_OPERATOR_PASSWORD", ""),
            display_name="Operador de Processos",
            role=UserRole.OPERATOR,
        )
        if operator:
            AreaMembership.objects.get_or_create(user=operator, area=areas["processos"])
        if admin:
            self._seed_runs(modules, admin)

        self.stdout.write(self.style.SUCCESS("Áreas e quatro módulos sintéticos disponíveis."))
        if not admin or not operator:
            self.stdout.write(
                self.style.WARNING(
                    "Usuários sem senha não foram criados. Defina DEMO_ADMIN_PASSWORD e "
                    "DEMO_OPERATOR_PASSWORD e execute o comando novamente."
                )
            )

    def _upsert_user(
        self,
        *,
        username: str,
        email: str,
        password: str,
        display_name: str,
        role: str,
    ) -> User | None:
        if not password:
            return None
        user, _ = User.objects.get_or_create(username=username, defaults={"email": email})
        user.email = email
        user.display_name = display_name
        user.role = role
        user.is_active = True
        user.is_staff = role == UserRole.ADMINISTRATOR
        user.set_password(password)
        user.save()
        return user

    def _seed_runs(self, modules: dict[str, AutomationModule], admin: User) -> None:
        now = timezone.now()
        examples = (
            (
                "demo-sc04-review",
                "SC-04",
                RunStatus.AWAITING_REVIEW,
                "12 anexos processados; 2 aguardam revisão por baixa confiança.",
                "",
                timedelta(minutes=8),
            ),
            (
                "demo-sc05-success",
                "SC-05",
                RunStatus.SUCCEEDED,
                "Bloqueio aplicado nos três sistemas simulados.",
                "",
                timedelta(days=1, minutes=4),
            ),
            (
                "demo-sc06-success",
                "SC-06",
                RunStatus.SUCCEEDED,
                "Briefing validado com 18 respostas obrigatórias.",
                "",
                timedelta(days=2, seconds=22),
            ),
            (
                "demo-sc20-warning",
                "SC-20",
                RunStatus.SUCCEEDED_WITH_WARNINGS,
                "7 certificados na janela; 6 comunicações registradas.",
                "Falha de entrega simulada para um contato; nova tentativa disponível.",
                timedelta(days=3, minutes=1),
            ),
        )
        for key, code, status, summary, error, age in examples:
            created_at = now - age
            run, created = AutomationRun.objects.get_or_create(
                idempotency_key=key,
                defaults={
                    "module": modules[code],
                    "trigger": (
                        RunTrigger.SCHEDULED if code in {"SC-04", "SC-20"} else RunTrigger.MANUAL
                    ),
                    "status": status,
                    "triggered_by": admin if code not in {"SC-04", "SC-20"} else None,
                    "summary": summary,
                    "error_message": error,
                    "started_at": created_at,
                    "finished_at": created_at + timedelta(seconds=47),
                    "metadata": {"synthetic": True},
                },
            )
            if created:
                AutomationRun.objects.filter(pk=run.pk).update(created_at=created_at)
