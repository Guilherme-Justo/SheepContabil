# SheepContabil

Portal único para quatro automações contábeis do desafio Sheep Technology. O projeto parte de pouco contexto, registra as premissas adotadas e mantém a lógica real atrás de fronteiras externas simuladas.

> Estado do código: **versão 0.5.0 incorporada à `main`, implantada e validada em produção** — os quatro processos selecionados estão implementados. O Dia 5 acrescenta o SC-05 com RPA Playwright real sobre três portais HTML sintéticos, saga compensável, retomada explícita e evidência visual privada. O arranjo co-localizado no worker passou por PR, dois CIs públicos, deploy automático condicionado ao CI e smoke test ponta a ponta.

## Ambientes publicados

| Entrega | Endereço |
| --- | --- |
| Portal HTTPS | [web-production-8f055.up.railway.app](https://web-production-8f055.up.railway.app) |
| Repositório público | [Guilherme-Justo/SheepContabil](https://github.com/Guilherme-Justo/SheepContabil) |
| Integração contínua | [GitHub Actions](https://github.com/Guilherme-Justo/SheepContabil/actions/workflows/ci.yml) |

Em 2026-08-31, o deploy automático GitHub → Railway foi recuperado e validado pelo [PR `#3`](https://github.com/Guilherme-Justo/SheepContabil/pull/3): o CI terminou verde e `web`, `worker` e `scheduler` aguardaram sua conclusão antes de publicar pela integração nativa.

Em 2026-09-01, o [PR `#5`](https://github.com/Guilherme-Justo/SheepContabil/pull/5) incorporou a versão 0.5.0 à `main` no commit [`d5b71b3`](https://github.com/Guilherme-Justo/SheepContabil/commit/d5b71b384340f4f3cd66e07f801309529790b39f). O CI do PR e o CI do push em `main` foram aprovados, e o deploy automático condicionado ao CI terminou com sucesso nos três serviços existentes. O limite de recursos do plano impediu criar um quarto serviço Railway para o simulador; o ajuste atual mantém a separação no Compose local e executa o WSGI sintético como processo auxiliar supervisionado dentro do serviço `worker` na Railway.

O [PR `#6`](https://github.com/Guilherme-Justo/SheepContabil/pull/6) incorporou esse ajuste no commit [`4ab7af3`](https://github.com/Guilherme-Justo/SheepContabil/commit/4ab7af38ccd0259d89c80a00b82679d3754d5ac3). O [CI do PR](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33556800150) e o [CI de `main`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33557162559) ficaram verdes antes da publicação. Os deployments automáticos de `web` (`4c35c556-9f52-4ee9-b0c7-a092a703f1b8`), `worker` (`8101f6cb-4401-489e-850e-02f62075e8e3`) e `scheduler` (`348d9edf-8bf4-45de-b8ed-e955f3ff3934`) foram promovidos; o seed controlado posterior gerou o deployment web final `cfe0a20c-e358-44ef-870f-5aec6271a24d` e voltou a ficar desativado. O smoke público confirmou bloqueio, desbloqueio, seis screenshots privadas, falha parcial, retomada da mesma execução, RBAC por área e restauração do cliente ao estado ativo.

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
- Resiliência em desenvolvimento local: fallback automático e transparente para SQLite (`var/dev.sqlite3`) e FileSystem Storage (`var/storage/`) em modo `DEBUG` quando executado fora do Docker.
- Docker Compose local para web, worker, simulador SC-05 separado, scheduler efêmero, banco, Redis e MinIO.
- Na Railway, a IaC preserva apenas `web`, `worker` e `scheduler`: o worker inicia o WSGI do simulador como processo auxiliar com ambiente sanitizado, sem herdar Redis, S3 ou OpenAI. O Playwright usa `127.0.0.1:8000`; a plataforma alcança a mesma porta apenas pela rede privada para healthcheck, sem domínio público.

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

### 1. Preparação do ambiente e dependências

**No Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install uv
.\.venv\Scripts\uv.exe sync --all-groups
npm ci
npm run build
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m playwright install chromium
```

**No Linux / macOS (Bash):**
```bash
python3 -m venv .venv
./.venv/bin/python -m pip install uv
./.venv/bin/uv sync --all-groups
npm ci
npm run build
cp .env.example .env
./.venv/bin/python -m playwright install chromium
```

### 2. Configuração de Variáveis de Ambiente (`.env`)

No arquivo `.env` recém-criado, defina as senhas dos usuários locais para o seed:
```ini
DEMO_ADMIN_PASSWORD=admin123
DEMO_OPERATOR_PASSWORD=operator123
```
*(Nota de Banco de Dados: com `DATABASE_URL` vazio, o projeto utiliza automaticamente SQLite local em `var/dev.sqlite3`, sem necessidade de instalar ou configurar PostgreSQL.)*
*(Nota de Storage: com `S3_ENDPOINT_URL` apontando para porta local inativa ou vazio em desenvolvimento, o projeto utiliza automaticamente o diretório isolado `var/storage/`, permitindo upload no SC-04 e capturas no SC-05 de forma 100% autônoma sem necessidade de Docker.)*

### 3. Banco de Dados e Inicialização do Servidor

**No Windows (PowerShell):**
```powershell
.\.venv\Scripts\python.exe src\manage.py migrate
.\.venv\Scripts\python.exe src\manage.py seed_demo
.\.venv\Scripts\python.exe src\manage.py runserver 127.0.0.1:8000
```

**No Linux / macOS (Bash):**
```bash
./.venv/bin/python src/manage.py migrate
./.venv/bin/python src/manage.py seed_demo
./.venv/bin/python src/manage.py runserver 127.0.0.1:8000
```

### 4. Acesso ao Portal

Abra no navegador `http://localhost:8000`:
- **Administrador**: usuário `admin` / senha definida em `DEMO_ADMIN_PASSWORD` (ex: `admin123`).
- **Operador**: usuário `operador.processos` (ou `operador.fiscal`, `operador.societario`, `operador.tecnologia`) / senha definida em `DEMO_OPERATOR_PASSWORD` (ex: `operator123`).

O comando `seed_demo` é idempotente: pode ser executado a qualquer momento para restaurar os dados de demonstração sem duplicar registros.

> **Nota para execução completa do SC-05**: O caminho acima sobe o portal web completo com todos os módulos (SC-04, SC-06 e SC-20 prontos para uso). Para executar a automação de RPA do SC-05 de ponta a ponta, também são necessários Redis, worker Celery e o WSGI privado do simulador (`config.simulator_wsgi`). Para subir toda essa infraestrutura em um único comando, recomenda-se o uso do **Docker Compose** descrito acima.

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

No estado final local do Dia 5, a suíte completa aprovou `124` testes com `83,85%` de cobertura; os `37` testes focados do SC-05 também passaram. Ruff, verificação de formatação, Mypy, sintaxe do supervisor POSIX, checks Django, conferência de migrations, build dos assets e validação do Compose ficaram verdes. O build da imagem `0.5.0`, indisponível localmente com o Docker Desktop desligado, foi aprovado pelos jobs `Container build` dos CIs do [PR `#5`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33538813847), de seu [push em `main`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33539137377), do [PR `#6`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33556800150) e do [push final em `main`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33557162559). A evidência operacional no ambiente público inclui os quatro UUIDs de execução registrados em [`docs/day-5.md`](docs/day-5.md), download autenticado de PNG privado com integridade e negação `404` para usuário de outra área.

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
