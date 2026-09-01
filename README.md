# SheepContabil

Portal único para quatro automações contábeis do desafio Sheep Technology. O projeto parte de pouco contexto, registra as premissas adotadas e mantém a lógica real atrás de fronteiras externas simuladas.

> Estado do código: **versão 0.5.0 em validação final** — os quatro processos selecionados estão implementados. O Dia 5 acrescenta o SC-05 com RPA Playwright real sobre três portais HTML sintéticos, saga compensável, retomada explícita e evidência visual privada. O ambiente público continua comprovado na versão 0.4.0 até o merge, o deploy e os smoke tests da 0.5.0; este documento não antecipa essa evidência.

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
- Saga SC-05 com snapshots antes/desejado/depois, bloqueio na ordem Arquivos → Contábil → Tarefas, desbloqueio inverso e compensação segura.
- Storage privado compatível com S3 ativo no SC-04, com objetos endereçados por hash, checagem de integridade e nenhum arquivo persistido no disco do contêiner.
- O mesmo storage privado conserva screenshots PNG recortados ao cliente/erro do SC-05 por tentativa, com SHA-256 e tamanho verificados no download novamente autorizado.
- Docker Compose local para web, worker, simulador SC-05, scheduler efêmero, banco, Redis e MinIO; a topologia Railway da 0.5.0 declara também um simulador privado, cuja publicação ainda precisa ser confirmada.

Os motivos, consequências e alternativas rejeitadas estão em [`docs/architecture.md`](docs/architecture.md) e nos nove ADRs de [`docs/adr/`](docs/adr/).

## Executar com Docker

Pré-requisito: Docker Desktop com Compose v2.

1. Copie `.env.example` para `.env`.
2. Preencha `DEMO_ADMIN_PASSWORD`, `DEMO_OPERATOR_PASSWORD`, `S3_SECRET_ACCESS_KEY`, `SC05_SIMULATOR_DJANGO_SECRET_KEY` e `SC05_SIMULATOR_PASSWORD` com valores locais. Para reproduzir produção, use também senhas distintas em `DEMO_SOCIETARY_OPERATOR_PASSWORD`, `DEMO_FISCAL_OPERATOR_PASSWORD` e `DEMO_TECHNOLOGY_OPERATOR_PASSWORD`.
3. Para classificação real do SC-04, informe também `OPENAI_API_KEY` e um `OPENAI_MODEL` disponível e validado na sua conta. Sem ambos, o documento é encaminhado honestamente para revisão humana.
4. Suba o ambiente:

```powershell
docker compose up --build --detach
docker compose exec web python src/manage.py seed_demo
```

5. Abra `http://localhost:8000`.

PostgreSQL, Redis, MinIO e o simulador privado do SC-05 são inicializados pelo Compose. O simulador escuta somente em `127.0.0.1:8010`; o portal principal fica em `http://localhost:8000` e o MinIO Console em `http://localhost:9001`. `docker compose down` interrompe os serviços sem remover os volumes.

## Executar sem Docker

Pré-requisitos: Python 3.12 ou 3.13 e Node.js 24.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install uv
.\.venv\Scripts\uv.exe sync --all-groups
npm ci
npm run build
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe src\manage.py migrate
.\.venv\Scripts\python.exe src\manage.py seed_demo
.\.venv\Scripts\python.exe src\manage.py runserver
```

Com `DATABASE_URL` vazio, o desenvolvimento local usa SQLite em `var/dev.sqlite3`. As senhas precisam estar preenchidas em `.env` antes do seed. O comando é idempotente: pode ser repetido sem duplicar áreas, módulos, acessos ou execuções sintéticas.

Esse caminho sobe apenas o processo web. Para executar o SC-05 de ponta a ponta também são necessários Redis, worker Celery e o WSGI privado `config.simulator_wsgi` na porta configurada por `SC05_SIMULATOR_BASE_URL`. O Compose é o caminho suportado mais curto para iniciar esse conjunto sem divergência entre processos.

## Verificações

```powershell
npm run build
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
.\.venv\Scripts\pytest.exe --cov
.\.venv\Scripts\pytest.exe tests\test_sc05_playwright_contract.py -q
.\.venv\Scripts\python.exe src\manage.py makemigrations --check --dry-run
docker compose config --quiet
```

O pipeline em `.github/workflows/ci.yml` instala o Chromium headless e repete lint, tipagem, testes — inclusive contrato RPA com navegador real —, cobertura, conferência de migrations, build dos assets e build da imagem de produção.

No estado final local do Dia 5, a suíte completa aprovou `122` testes com `83,83%` de cobertura; os `37` testes focados do SC-05 também passaram. Ruff, verificação de formatação, Mypy, checks Django, conferência de migrations, build dos assets e validação do Compose estão verdes. O build local da imagem `0.5.0` não pôde ser executado porque o Docker Desktop estava desligado; esse gate permanece atribuído ao job `Container build` do CI antes do merge. Isso ainda não constitui evidência de publicação: o ambiente público comprovado continua na versão `0.4.0`.

## Estrutura

```text
src/
├── config/                  settings, URLs, healthchecks e Celery
├── core/
│   ├── identity/            usuário, perfis, áreas e acessos
│   ├── automations/         catálogo, execução comum e casos SC-04/SC-05/SC-06/SC-20
│   └── sc05_simulator/      três portais HTML privados usados pelo RPA
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
- O SC-05 não chama endpoint oculto nem altera tabelas do simulador: Playwright autentica, lê a página e aciona formulários visíveis com CSRF.
- No sistema de tarefas, o cliente permanece ativo. Somente tarefas abertas recebem `BLOQUEADO_INADIMPLENCIA`; o undo restaura os responsáveis exatos sem reverter fechamento posterior nem apagar tarefa nova.
- Screenshots de RPA são privados, limitados ao cliente/erro atual, têm hash e tamanho verificados e exigem novamente permissão para o módulo Tecnologia no download.

## Documentação

- [`docs/day-1.md`](docs/day-1.md): aceite e evidências do primeiro dia.
- [`docs/day-2.md`](docs/day-2.md): contrato, implementação e aceite do SC-20.
- [`docs/day-3.md`](docs/day-3.md): motor condicional, briefing e aceite do SC-06.
- [`docs/day-4.md`](docs/day-4.md): triagem documental, IA, OCR, revisão e aceite do SC-04.
- [`docs/day-5.md`](docs/day-5.md): RPA SC-05, saga, compensação, retomada e estado de publicação.
- [`docs/architecture.md`](docs/architecture.md): visão arquitetural completa.
- [`docs/assumptions.md`](docs/assumptions.md): premissas, dúvidas e riscos.
- [`docs/deployment.md`](docs/deployment.md): implantação e operação Railway.
- [`docs/adr/`](docs/adr/): decisões arquiteturais versionadas.

O repositório e o portal acima são os endereços canônicos do projeto. Detalhes de operação, smoke test e rollback estão em [`docs/deployment.md`](docs/deployment.md).
