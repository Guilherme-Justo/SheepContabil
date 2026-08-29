# Dia 1 — fundação do SheepContabil

| Campo | Valor |
| --- | --- |
| Data | 2026-08-27 |
| Publicação concluída | 2026-08-29 |
| Objetivo | Base reproduzível e demonstrável para implementar SC-04, SC-05, SC-06 e SC-20 |
| Estado local | Concluído |
| Estado externo | Concluído — [GitHub público](https://github.com/Guilherme-Justo/SheepContabil) e [portal Railway](https://web-production-8f055.up.railway.app) |

## Critérios de aceite

- [x] Repositório Git iniciado na branch `main`.
- [x] Stack e fronteiras arquiteturais decididas e documentadas.
- [x] Nove ADRs e registro vivo de premissas.
- [x] Modelo de usuário próprio antes da primeira migração.
- [x] Login real por sessão e logout somente por POST.
- [x] Perfis Administrador e Operador.
- [x] Acesso do operador restringido por área no backend.
- [x] Catálogo dos quatro módulos com código, área, natureza, complexidade e frequência.
- [x] Modelo comum de execução com UUID, origem, estado, duração, resumo, erro e idempotência.
- [x] Home, tela própria por módulo e detalhe de execução.
- [x] Identidade oficial: logo, sete cores e três famílias tipográficas do desafio.
- [x] Seed sintético, versionado e idempotente.
- [x] Migrações aplicáveis do zero.
- [x] Healthchecks de vida e prontidão.
- [x] Dockerfile multi-stage e Compose com web, worker, PostgreSQL, Redis e MinIO.
- [x] CI com build, lint, mypy, testes, cobertura, migrations e imagem Docker.
- [x] Especificação Railway sem segredos.
- [x] Repositório GitHub público na branch `main` com CI verde.
- [x] Primeiro deploy e URL pública HTTPS com healthcheck de prontidão.

## Evidências locais

| Verificação | Resultado esperado |
| --- | --- |
| `python src/manage.py check` | 0 problemas |
| `ruff check src tests` | sem ocorrências |
| `mypy src` | sem erros |
| `pytest --cov` | suíte verde e cobertura mínima de 75% |
| `makemigrations --check --dry-run` | nenhuma mudança |
| `npm run build` | CSS e JavaScript gerados |
| `docker compose config --quiet` | configuração válida |
| `GET /conta/entrar/` | HTTP 200 |
| [CI no GitHub](https://github.com/Guilherme-Justo/SheepContabil/actions/workflows/ci.yml) | quality gate e imagem verdes |
| `GET https://web-production-8f055.up.railway.app/health/ready` | HTTP 200 e PostgreSQL disponível |

## Estado da hospedagem

O ambiente está ativo no período `Trial` da Railway. Nenhuma compra ou alteração de billing foi feita automaticamente; antes do fim do período, o proprietário deve decidir a migração para um plano que não suspenda o portal durante a avaliação.

## O que deliberadamente não foi fingido

- O botão funcional de cada automação ainda não executa o processo específico.
- Rate limit de login, troca obrigatória de senha e auditoria append-only permanecem controles da entrega final, não alegações do Dia 1.
- O MinIO local e o bucket Railway estão especificados; o adapter S3 será conectado junto ao primeiro fluxo real de artefato.
- O cron não foi publicado antes de existir `dispatch_due_schedules` com idempotência real.
- A chave OpenAI não ganhou fallback silencioso que fabricaria classificação.
- A infraestrutura em código não inclui senha, token, hostname ou repositório inventado.

Esses limites preservam a diferença entre uma fundação pronta e uma automação efetivamente entregue.
