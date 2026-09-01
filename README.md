# SheepContabil

Portal único para quatro automações contábeis do desafio Sheep Technology. O projeto parte de pouco contexto, registra as premissas adotadas e mantém a lógica real atrás de fronteiras externas simuladas.

> Estado: **versão 0.4.0 publicada e validada em produção** — SC-04 está ativo com storage privado, extração/OCR, classificação OpenAI estruturada, revisão humana, deduplicação e roteamento auditável. Os caminhos de indisponibilidade segura e de encaminhamento automático foram confirmados no ambiente público.

## Ambientes publicados

| Entrega | Endereço |
| --- | --- |
| Portal HTTPS | [web-production-8f055.up.railway.app](https://web-production-8f055.up.railway.app) |
| Repositório público | [Guilherme-Justo/SheepContabil](https://github.com/Guilherme-Justo/SheepContabil) |
| Integração contínua | [GitHub Actions](https://github.com/Guilherme-Justo/SheepContabil/actions/workflows/ci.yml) |

Em 2026-08-31, o deploy automático GitHub → Railway foi recuperado e validado pelo [PR `#3`](https://github.com/Guilherme-Justo/SheepContabil/pull/3): o CI terminou verde e `web`, `worker` e `scheduler` aguardaram sua conclusão antes de publicar pela integração nativa.

As credenciais do ambiente publicado são sintéticas e devem ser entregues aos avaliadores fora do repositório. O projeto Railway está no período `Trial`; a mudança para um plano pago depende da decisão de billing do proprietário antes do fim do período de avaliação.

## Processos selecionados

| Código | Módulo | Natureza | Frequência | Área |
| --- | --- | --- | --- | --- |
| SC-04 | Triagem da caixa de arquivos | Agente de IA | Diário | Fiscal |
| SC-05 | Bloqueio e desbloqueio de clientes inadimplentes | RPA | Sob demanda | Tecnologia |
| SC-06 | Briefing societário com perguntas condicionais | Controle sistematizado | Sob demanda | Societário |
| SC-20 | Vencimento de certificado digital | Controle sistematizado | Mensal | Processos |

A seleção cobre as três naturezas do catálogo e combina dois processos de complexidade média com um controle de menor complexidade, preservando espaço para completude ponta a ponta.

## Decisões principais

- Monólito modular em Python 3.13 e Django 5.2 LTS.
- Django Templates, HTMX, Alpine.js e Tailwind CSS; não há SPA separada.
- Sessão Django com CSRF, Argon2 e RBAC no servidor por perfil e área.
- PostgreSQL como fonte de verdade; Celery e Redis para trabalho assíncrono.
- Playwright Chromium no adapter RPA e OpenAI atrás de um adapter de classificação.
- Storage privado compatível com S3 ativo no SC-04, com objetos endereçados por hash, checagem de integridade e nenhum arquivo persistido no disco do contêiner.
- Docker Compose local e Railway para web, worker, scheduler efêmero, banco, Redis e bucket.

Os motivos, consequências e alternativas rejeitadas estão em [`docs/architecture.md`](docs/architecture.md) e nos nove ADRs de [`docs/adr/`](docs/adr/).

## Executar com Docker

Pré-requisito: Docker Desktop com Compose v2.

1. Copie `.env.example` para `.env`.
2. Preencha `DEMO_ADMIN_PASSWORD`, `DEMO_OPERATOR_PASSWORD` e `S3_SECRET_ACCESS_KEY` com valores locais. A senha de operador cria acessos sintéticos separados para Processos, Societário e Fiscal.
3. Para classificação real do SC-04, informe também `OPENAI_API_KEY` e um `OPENAI_MODEL` disponível e validado na sua conta. Sem ambos, o documento é encaminhado honestamente para revisão humana.
4. Suba o ambiente:

```powershell
docker compose up --build --detach
docker compose exec web python src/manage.py seed_demo
```

5. Abra `http://localhost:8000`.

PostgreSQL, Redis e MinIO são inicializados pelo Compose. O MinIO Console fica em `http://localhost:9001`. `docker compose down` interrompe os serviços sem remover os volumes.

## Executar sem Docker

Pré-requisitos: Python 3.12 ou 3.13 e Node.js 24.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install uv
.\.venv\Scripts\uv.exe sync --all-groups
npm ci
npm run build
Copy-Item .env.example .env
.\.venv\Scripts\python.exe src\manage.py migrate
.\.venv\Scripts\python.exe src\manage.py seed_demo
.\.venv\Scripts\python.exe src\manage.py runserver
```

Com `DATABASE_URL` vazio, o desenvolvimento local usa SQLite em `var/dev.sqlite3`. As senhas precisam estar preenchidas em `.env` antes do seed. O comando é idempotente: pode ser repetido sem duplicar áreas, módulos, acessos ou execuções sintéticas.

## Verificações

```powershell
npm run build
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
.\.venv\Scripts\pytest.exe --cov
.\.venv\Scripts\python.exe src\manage.py makemigrations --check --dry-run
docker compose config --quiet
```

O pipeline em `.github/workflows/ci.yml` repete lint, tipagem, testes, cobertura, conferência de migrations, build dos assets e build da imagem de produção.

## Estrutura

```text
src/
├── config/                  settings, URLs, healthchecks e Celery
├── core/
│   ├── identity/            usuário, perfis, áreas e acessos
│   └── automations/         catálogo, execução comum e casos SC-04/SC-06/SC-20
├── templates/               portal renderizado no servidor
├── static_src/              fontes CSS e JavaScript
└── static/brand/            assinaturas oficiais e cartão social
docs/                        arquitetura, premissas, ADRs e operação
.railway/                    infraestrutura Railway em TypeScript
tests/                       autenticação, autorização e saúde
```

## Segurança e dados

- Todos os clientes, documentos, execuções e credenciais de demonstração são sintéticos.
- Senhas, chave OpenAI e credenciais cloud não são versionadas.
- O operador acessa apenas módulos de áreas concedidas; o administrador de negócio acessa os quatro.
- Ocultar um link não é controle de acesso: cada view consulta novamente a política no backend.
- Falhas são registradas com mensagem operacional; stack trace não é apresentado no portal.

## Documentação

- [`docs/day-1.md`](docs/day-1.md): aceite e evidências do primeiro dia.
- [`docs/day-2.md`](docs/day-2.md): contrato, implementação e aceite do SC-20.
- [`docs/day-3.md`](docs/day-3.md): motor condicional, briefing e aceite do SC-06.
- [`docs/day-4.md`](docs/day-4.md): triagem documental, IA, OCR, revisão e aceite do SC-04.
- [`docs/architecture.md`](docs/architecture.md): visão arquitetural completa.
- [`docs/assumptions.md`](docs/assumptions.md): premissas, dúvidas e riscos.
- [`docs/deployment.md`](docs/deployment.md): implantação e operação Railway.
- [`docs/adr/`](docs/adr/): decisões arquiteturais versionadas.

O repositório e o portal acima são os endereços canônicos do projeto. Detalhes de operação, smoke test e rollback estão em [`docs/deployment.md`](docs/deployment.md).
