# Release v1.0.0

## Identificação

| Campo | Valor |
| --- | --- |
| Estado | Release Candidate em validação |
| Versão | `1.0.0` |
| Data de preparação | 09/09/2026 |
| Base funcional | `e1e968a32cd56625a31083a938456599f4c92d39` |
| Branch de preparação | `codex/release-v1.0.0` |
| URL pública | [web-production-8f055.up.railway.app](https://web-production-8f055.up.railway.app) |

## Objetivo

A `1.0.0` congela a primeira entrega estável do desafio. Ela não introduz um quinto processo nem
amplia silenciosamente regras contábeis: consolida os quatro fluxos selecionados, a resiliência
assíncrona, a rastreabilidade e a documentação necessária para avaliação e operação demonstrativa.

## Escopo congelado

| Processo | Natureza | Capacidade entregue |
| --- | --- | --- |
| SC-04 | Agente de IA | Ingestão segura, extração, classificação, confiança, revisão e roteamento |
| SC-05 | RPA | Bloqueio/desbloqueio em três portais, snapshots, compensação e retomada |
| SC-06 | Controle sistematizado | Briefing versionado, regras condicionais, auditoria e PDF autorizado |
| SC-20 | Controle sistematizado | Monitoramento mensal, canais assistidos, idempotência e falha ambígua segura |

A topologia permanece um monólito modular Django com processos `web`, `worker` e `scheduler`,
PostgreSQL como fonte de verdade e Redis como broker. Na Railway demonstrativa, o simulador SC-05
continua co-localizado fisicamente no worker, com ambiente reduzido e acesso somente por loopback.
Essa limitação está aceita apenas para o desafio e não transforma o simulador em código de domínio.

## Gates do Release Candidate

### Integridade do artefato

- [x] A branch parte da `main` limpa e sincronizada.
- [x] `pyproject.toml`, `uv.lock`, `package.json` e `package-lock.json` declaram `1.0.0`.
- [x] O changelog cobre as mudanças desde `v0.19.1`.
- [x] README, arquitetura e guia de demonstração refletem o baseline final.
- [x] Nenhum segredo ou dado pessoal foi acrescentado ao repositório.
- [x] `railway config plan`, com CLI `5.49.6`, confirmou a configuração de produção atualizada sem
  mudança a aplicar em 09/09/2026.

### Verificação local

- [x] Lockfiles consistentes e instalações congeladas reproduzíveis.
- [x] Ruff e verificação de formatação aprovados.
- [x] Mypy aprovado.
- [x] Django, settings de produção e simulador aprovados.
- [x] Ausência de migrations não versionadas.
- [ ] Migration `0010` validada sobre PostgreSQL com dados preexistentes.
- [x] Suíte completa e cobertura mínima de 75% aprovadas.
- [x] Assets do navegador recompilados sem diferença inesperada.
- [x] Configuração do Docker Compose validada.
- [ ] Imagem de produção construída pelo CI.

Resultado local de 09/09/2026: `308` testes coletados, `307 passed`, `1 skipped` por exigir
PostgreSQL e cobertura de `85,86%`. Os oito testes Playwright passaram com Chromium real.
`uv lock --check`, `uv sync --locked --all-groups`, `npm ci --ignore-scripts`, `uv pip check`, build de assets,
Ruff, formatação, Mypy, checks Django, migrations, sintaxe POSIX e Compose também passaram. O npm
auditou 47 pacotes sem vulnerabilidades. O Docker Desktop estava desligado; por isso, migration em
PostgreSQL e imagem continuam como gates do CI do PR.

### Promoção externa

- [ ] PR da release aprovado por todos os jobs obrigatórios.
- [ ] Merge realizado na `main` sem bypass do CI.
- [ ] Deploy automático de `web`, `worker` e `scheduler` em `SUCCESS`.
- [ ] `/health/live` e `/health/ready` respondem HTTP 200 e preservam `X-Request-ID`.
- [ ] Smoke autenticado usa somente dados sintéticos e confirma RBAC e trilha de execução.
- [ ] Tag anotada `v1.0.0` aponta para o commit exato validado em produção.
- [ ] GitHub Release publicado com evidências e limitações conhecidas.

## Regra de publicação

A tag `v1.0.0` não pode ser criada a partir do worktree ou antes do deploy. A ordem obrigatória é:

1. validar e revisar o Release Candidate;
2. incorporar o PR na `main` depois do CI verde;
3. confirmar os três deployments e os health checks;
4. executar o smoke autenticado e registrar as evidências sem segredos;
5. incorporar a evidência final, se o documento ainda estiver pendente;
6. confirmar novamente o commit publicado e criar a tag anotada nesse SHA;
7. publicar o GitHub Release a partir da mesma tag.

Qualquer falha antes da tag interrompe a publicação. Uma falha de aplicação após merge usa redeploy
da última revisão saudável; migrations destrutivas ou rollback de schema não fazem parte desta
release.

## Smoke de produção permitido

O ensaio utiliza exclusivamente contas e clientes sintéticos. É permitido consultar os quatro
módulos, validar acessos por área, criar e cancelar um briefing vazio e executar um ciclo reversível
do simulador SC-05. Não é permitido disparar comunicação para contato real, persistir documento real
ou registrar credenciais nos logs e nas evidências.

## Limitações conhecidas

- O simulador SC-05 compartilha contêiner, UID e ciclo de disponibilidade com o Celery na Railway.
- O healthcheck da plataforma valida promoção; não substitui monitoramento externo contínuo.
- Rate limit, troca obrigatória de senha e auditoria específica de login não estão implementados no
  ambiente sintético e são necessários antes de identidades reais.
- Backup diário e restauração do PostgreSQL não foram ensaiados neste ambiente demonstrativo.
- Faixas adicionais de aviso do SC-20 dependem de confirmação da regra de negócio.
- Retenção, antivírus, DLP e segregação regulatória devem ser revistos antes de dados reais.
- PDFs de imagem sem texto pesquisável continuam fora do ciclo atual do SC-04.

As justificativas arquiteturais permanecem em [`architecture.md`](architecture.md), as premissas em
[`assumptions.md`](assumptions.md), a operação em [`deployment.md`](deployment.md) e o contrato de
rastreabilidade em [`traceability.md`](traceability.md).
