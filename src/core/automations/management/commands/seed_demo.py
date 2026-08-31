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
    BriefingTemplate,
    BriefingTemplateVersion,
    BriefingVersionStatus,
    CertificateStatus,
    CommunicationChannel,
    DigitalCertificate,
    RunStatus,
    RunTrigger,
    SocietaryBriefing,
    SocietaryBriefingStatus,
)
from core.automations.sc06.rules import sanitize_answers
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

STATE_OPTIONS = [
    {"value": code, "label": label}
    for code, label in (
        ("AC", "Acre"),
        ("AL", "Alagoas"),
        ("AP", "Amapá"),
        ("AM", "Amazonas"),
        ("BA", "Bahia"),
        ("CE", "Ceará"),
        ("DF", "Distrito Federal"),
        ("ES", "Espírito Santo"),
        ("GO", "Goiás"),
        ("MA", "Maranhão"),
        ("MT", "Mato Grosso"),
        ("MS", "Mato Grosso do Sul"),
        ("MG", "Minas Gerais"),
        ("PA", "Pará"),
        ("PB", "Paraíba"),
        ("PR", "Paraná"),
        ("PE", "Pernambuco"),
        ("PI", "Piauí"),
        ("RJ", "Rio de Janeiro"),
        ("RN", "Rio Grande do Norte"),
        ("RS", "Rio Grande do Sul"),
        ("RO", "Rondônia"),
        ("RR", "Roraima"),
        ("SC", "Santa Catarina"),
        ("SP", "São Paulo"),
        ("SE", "Sergipe"),
        ("TO", "Tocantins"),
    )
]
OTHER_STATE_CODES = [option["value"] for option in STATE_OPTIONS if option["value"] != "SP"]

SC06_SCHEMA_V1 = {
    "title": "Briefing societário condicional",
    "sections": [
        {
            "id": "request",
            "title": "Enquadramento do atendimento",
            "description": "Dados que determinam o caminho aplicável ao caso.",
            "questions": [
                {
                    "id": "process_type",
                    "label": "Qual processo societário será iniciado?",
                    "type": "choice",
                    "required": True,
                    "options": [
                        {"value": "opening", "label": "Abertura de empresa"},
                        {"value": "contract_change", "label": "Alteração contratual"},
                    ],
                },
                {
                    "id": "client_state",
                    "label": "UF atual do cliente",
                    "type": "choice",
                    "required": True,
                    "help_text": "Uma UF diferente de SP abre o bloco interestadual.",
                    "options": STATE_OPTIONS,
                },
                {
                    "id": "contact_email",
                    "label": "E-mail sintético para retorno",
                    "type": "email",
                    "required": True,
                    "placeholder": "contato@empresa.example.test",
                    "validation": {"max_length": 254},
                },
            ],
        },
        {
            "id": "opening",
            "title": "Dados para abertura",
            "description": "Perguntas exclusivas de constituição de uma nova empresa.",
            "visible_when": {
                "field": "process_type",
                "operator": "equals",
                "value": "opening",
            },
            "questions": [
                {
                    "id": "desired_company_name",
                    "label": "Nome empresarial pretendido",
                    "type": "text",
                    "required": True,
                    "validation": {"min_length": 3, "max_length": 180},
                },
                {
                    "id": "main_activity",
                    "label": "Atividade principal pretendida",
                    "type": "textarea",
                    "required": True,
                    "validation": {"min_length": 10, "max_length": 1200},
                },
                {
                    "id": "proposed_address",
                    "label": "Endereço sintético da futura sede",
                    "type": "text",
                    "required": True,
                    "validation": {"min_length": 8, "max_length": 240},
                },
            ],
        },
        {
            "id": "contract_change",
            "title": "Dados para alteração contratual",
            "description": "Perguntas exclusivas de uma empresa já constituída.",
            "visible_when": {
                "field": "process_type",
                "operator": "equals",
                "value": "contract_change",
            },
            "questions": [
                {
                    "id": "current_cnpj",
                    "label": "CNPJ sintético atual",
                    "type": "text",
                    "required": True,
                    "validation": {"min_length": 14, "max_length": 18},
                },
                {
                    "id": "alteration_summary",
                    "label": "O que precisa ser alterado?",
                    "type": "textarea",
                    "required": True,
                    "validation": {"min_length": 10, "max_length": 1600},
                },
            ],
        },
        {
            "id": "out_of_state",
            "title": "Informações interestaduais",
            "description": "Bloco adicional para cliente cuja UF atual não seja São Paulo.",
            "visible_when": {
                "field": "client_state",
                "operator": "in",
                "value": OTHER_STATE_CODES,
            },
            "questions": [
                {
                    "id": "origin_registry",
                    "label": "Órgão de registro na UF de origem",
                    "type": "text",
                    "required": True,
                    "placeholder": "Ex.: JUCERJA",
                    "validation": {"min_length": 3, "max_length": 120},
                },
                {
                    "id": "origin_registration",
                    "label": "Número de registro sintético na origem",
                    "type": "text",
                    "required": True,
                    "validation": {"min_length": 3, "max_length": 80},
                },
                {
                    "id": "out_of_state_notes",
                    "label": "Observações sobre a transferência de UF",
                    "type": "textarea",
                    "required": False,
                    "validation": {"max_length": 800},
                },
            ],
        },
        {
            "id": "partners",
            "title": "Quadro societário",
            "description": "A resposta sobre estado civil controla os campos de casamento.",
            "questions": [
                {
                    "id": "partner_names",
                    "label": "Sócios envolvidos (nomes sintéticos)",
                    "type": "textarea",
                    "required": True,
                    "validation": {"min_length": 3, "max_length": 1000},
                },
                {
                    "id": "has_married_partner",
                    "label": "Há sócio casado?",
                    "type": "boolean",
                    "required": True,
                },
                {
                    "id": "married_partner_name",
                    "label": "Qual sócio sintético é casado?",
                    "type": "text",
                    "required": True,
                    "visible_when": {
                        "field": "has_married_partner",
                        "operator": "equals",
                        "value": True,
                    },
                    "validation": {"min_length": 3, "max_length": 180},
                },
                {
                    "id": "marriage_regime",
                    "label": "Regime de casamento",
                    "type": "choice",
                    "required": True,
                    "visible_when": {
                        "field": "has_married_partner",
                        "operator": "equals",
                        "value": True,
                    },
                    "options": [
                        {"value": "partial_community", "label": "Comunhão parcial de bens"},
                        {"value": "universal_community", "label": "Comunhão universal de bens"},
                        {"value": "total_separation", "label": "Separação total de bens"},
                        {
                            "value": "final_participation",
                            "label": "Participação final nos aquestos",
                        },
                    ],
                },
            ],
        },
        {
            "id": "planning",
            "title": "Planejamento e observações",
            "questions": [
                {
                    "id": "desired_deadline",
                    "label": "Data pretendida para início do processo",
                    "type": "date",
                    "required": True,
                },
                {
                    "id": "additional_notes",
                    "label": "Observações adicionais",
                    "type": "textarea",
                    "required": False,
                    "validation": {"max_length": 1200},
                },
            ],
        },
    ],
}


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
        societary_operator = self._upsert_user(
            username=os.getenv("DEMO_SOCIETARY_OPERATOR_USERNAME", "operador.societario"),
            email=os.getenv(
                "DEMO_SOCIETARY_OPERATOR_EMAIL",
                "operador.societario@sheepcontabil.local",
            ),
            password=os.getenv(
                "DEMO_SOCIETARY_OPERATOR_PASSWORD",
                os.getenv("DEMO_OPERATOR_PASSWORD", ""),
            ),
            display_name="Operador Societário",
            role=UserRole.OPERATOR,
        )
        if operator:
            AreaMembership.objects.get_or_create(user=operator, area=areas["processos"])
        if societary_operator:
            AreaMembership.objects.get_or_create(
                user=societary_operator,
                area=areas["societario"],
            )
        template_version = self._seed_sc06_template(admin)
        if admin:
            self._seed_runs(modules, admin)
            self._seed_sc06_briefings(modules, admin, template_version)
        self._seed_certificates()

        self.stdout.write(self.style.SUCCESS("Áreas e quatro módulos sintéticos disponíveis."))
        if not admin or not operator or not societary_operator:
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

    def _seed_sc06_template(self, admin: User | None) -> BriefingTemplateVersion:
        template, _ = BriefingTemplate.objects.update_or_create(
            code="societary-briefing",
            defaults={
                "name": "Briefing societário padrão",
                "description": (
                    "Perguntas condicionais para abertura de empresa e alteração contratual."
                ),
                "is_active": True,
            },
        )
        version, created = BriefingTemplateVersion.objects.get_or_create(
            template=template,
            version=1,
            defaults={
                "schema": SC06_SCHEMA_V1,
                "status": BriefingVersionStatus.PUBLISHED,
                "published_at": timezone.now(),
                "created_by": admin,
            },
        )
        if not created and version.status == BriefingVersionStatus.DRAFT:
            version.schema = SC06_SCHEMA_V1
            version.status = BriefingVersionStatus.PUBLISHED
            version.published_at = timezone.now()
            version.created_by = admin
            version.save()
        return version

    def _seed_sc06_briefings(
        self,
        modules: dict[str, AutomationModule],
        admin: User,
        template_version: BriefingTemplateVersion,
    ) -> None:
        completed_run = AutomationRun.objects.get(idempotency_key="demo-sc06-success")
        completed_answers = sanitize_answers(
            template_version.schema,
            {
                "process_type": "opening",
                "client_state": "RJ",
                "contact_email": "societario@aurora.example.test",
                "desired_company_name": "Aurora Participações Demo Ltda.",
                "main_activity": "Participação sintética em outras sociedades não financeiras.",
                "proposed_address": "Rua Demonstração, 120, Centro, Rio de Janeiro/RJ",
                "origin_registry": "JUCERJA",
                "origin_registration": "DEMO-RJ-2026-0042",
                "out_of_state_notes": (
                    "Transferência simulada para abertura de filial em São Paulo."
                ),
                "partner_names": "Marina Exemplo e Rafael Demonstração",
                "has_married_partner": True,
                "married_partner_name": "Marina Exemplo",
                "marriage_regime": "partial_community",
                "desired_deadline": (timezone.localdate() + timedelta(days=20)).isoformat(),
                "additional_notes": "Caso sintético completo para demonstrar os dois desvios.",
            },
            require_complete=True,
        )
        completed_at = completed_run.finished_at or timezone.now()
        completed_briefing, _ = SocietaryBriefing.objects.get_or_create(
            run=completed_run,
            defaults={
                "template_version": template_version,
                "client_name": "Aurora Participações Demo",
                "client_document": "12345678000190",
                "answers": completed_answers,
                "status": SocietaryBriefingStatus.COMPLETED,
                "created_by": admin,
                "completed_by": admin,
                "completed_at": completed_at,
            },
        )
        AutomationRun.objects.filter(pk=completed_run.pk).update(
            status=RunStatus.SUCCEEDED,
            summary="Briefing concluído com 15 respostas aplicáveis e validadas.",
            error_message="",
            metadata={
                "answers_count": len(completed_answers),
                "template_code": template_version.template_id,
                "template_version": template_version.version,
                "briefing_id": str(completed_briefing.id),
                "completed_by_id": admin.pk,
                "synthetic": True,
            },
        )

        draft_started_at = timezone.now() - timedelta(minutes=24)
        draft_run, draft_created = AutomationRun.objects.get_or_create(
            idempotency_key="demo-sc06-draft",
            defaults={
                "module": modules["SC-06"],
                "trigger": RunTrigger.MANUAL,
                "status": RunStatus.RUNNING,
                "triggered_by": admin,
                "summary": "Rascunho salvo com o caminho de alteração contratual.",
                "started_at": draft_started_at,
                "metadata": {
                    "answers_count": 6,
                    "template_code": template_version.template_id,
                    "template_version": template_version.version,
                    "synthetic": True,
                },
            },
        )
        if draft_created:
            AutomationRun.objects.filter(pk=draft_run.pk).update(created_at=draft_started_at)
        draft_answers = sanitize_answers(
            template_version.schema,
            {
                "process_type": "contract_change",
                "client_state": "SP",
                "contact_email": "contato@cedro.example.test",
                "current_cnpj": "98765432000110",
                "partner_names": "Lucas Teste",
                "has_married_partner": False,
            },
        )
        SocietaryBriefing.objects.get_or_create(
            run=draft_run,
            defaults={
                "template_version": template_version,
                "client_name": "Cedro Serviços Fictícios",
                "client_document": "98765432000110",
                "answers": draft_answers,
                "status": SocietaryBriefingStatus.DRAFT,
                "created_by": admin,
            },
        )

    def _seed_certificates(self) -> None:
        today = timezone.localdate()
        examples = (
            {
                "serial_number": "DEMO-CERT-001",
                "client_name": "Horizonte Comércio Sintético",
                "client_document": "12345678000190",
                "responsible_name": "Ana Demonstração",
                "contact_email": "financeiro@horizonte.example.test",
                "contact_phone": "",
                "preferred_channel": CommunicationChannel.EMAIL,
                "valid_until": today + timedelta(days=15),
                "status": CertificateStatus.ACTIVE,
            },
            {
                "serial_number": "DEMO-CERT-002",
                "client_name": "Aurora Serviços Fictícios",
                "client_document": "98765432000110",
                "responsible_name": "Bruno Operação",
                "contact_email": "",
                "contact_phone": "+55 11 99999-2002",
                "preferred_channel": CommunicationChannel.WHATSAPP,
                "valid_until": today + timedelta(days=30),
                "status": CertificateStatus.ACTIVE,
            },
            {
                "serial_number": "DEMO-CERT-003",
                "client_name": "Cedro Participações Demo",
                "client_document": "11222333000181",
                "responsible_name": "Carla Revisão",
                "contact_email": "falha@avisos.invalid",
                "contact_phone": "",
                "preferred_channel": CommunicationChannel.EMAIL,
                "valid_until": today + timedelta(days=60),
                "status": CertificateStatus.ACTIVE,
            },
            {
                "serial_number": "DEMO-CERT-004",
                "client_name": "Vértice Consultoria Teste",
                "client_document": "44555666000172",
                "responsible_name": "Diego Planejamento",
                "contact_email": "contato@vertice.example.test",
                "contact_phone": "",
                "preferred_channel": CommunicationChannel.EMAIL,
                "valid_until": today + timedelta(days=61),
                "status": CertificateStatus.ACTIVE,
            },
            {
                "serial_number": "DEMO-CERT-005",
                "client_name": "Ponte Contábil Simulada",
                "client_document": "77888999000163",
                "responsible_name": "Elisa Pendência",
                "contact_email": "contato@ponte.example.test",
                "contact_phone": "",
                "preferred_channel": CommunicationChannel.EMAIL,
                "valid_until": today - timedelta(days=5),
                "status": CertificateStatus.ACTIVE,
            },
            {
                "serial_number": "DEMO-CERT-006",
                "client_name": "Nuvem Arquivos Fictícios",
                "client_document": "10111213000154",
                "responsible_name": "Fabio Histórico",
                "contact_email": "contato@nuvem.example.test",
                "contact_phone": "",
                "preferred_channel": CommunicationChannel.EMAIL,
                "valid_until": today + timedelta(days=20),
                "status": CertificateStatus.REVOKED,
            },
            {
                "serial_number": "DEMO-CERT-007",
                "client_name": "Raiz Tecnologia Demo",
                "client_document": "14151617000145",
                "responsible_name": "Giovana Certificados",
                "contact_email": "contato@raiz.example.test",
                "contact_phone": "",
                "preferred_channel": CommunicationChannel.EMAIL,
                "valid_until": today + timedelta(days=25),
                "status": CertificateStatus.REPLACED,
            },
        )
        for definition in examples:
            payload = dict(definition)
            serial_number = str(payload.pop("serial_number"))
            DigitalCertificate.objects.update_or_create(
                serial_number=serial_number,
                defaults=payload,
            )
