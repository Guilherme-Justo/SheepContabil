# Release v1.0.0

## Identificação

| Campo | Valor |
| --- | --- |
| Estado | Release Candidate validado; tag e GitHub Release pendentes |
| Versão | `1.0.0` |
| Data de preparação | 09/09/2026 |
| Base anterior | `e1e968a32cd56625a31083a938456599f4c92d39` |
| Commit funcional validado | `09fa855ee8039be2dce402050cd43acaa6756268` |
| PR de promoção | [#46](https://github.com/Guilherme-Justo/SheepContabil/pull/46) |
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
- [x] Migration `0010` validada sobre PostgreSQL com dados preexistentes.
- [x] Suíte completa e cobertura mínima de 75% aprovadas.
- [x] Assets do navegador recompilados sem diferença inesperada.
- [x] Configuração do Docker Compose validada.
- [x] Imagem de produção construída pelo CI.

Resultado local de 09/09/2026: `308` testes coletados, `307 passed`, `1 skipped` por exigir
PostgreSQL e cobertura de `85,86%`. Os oito testes Playwright passaram com Chromium real.
`uv lock --check`, `uv sync --locked --all-groups`, `npm ci --ignore-scripts`, `uv pip check`, build de assets,
Ruff, formatação, Mypy, checks Django, migrations, sintaxe POSIX e Compose também passaram. O npm
auditou 47 pacotes sem vulnerabilidades. O Docker Desktop estava desligado; por isso, migration em
PostgreSQL e imagem foram então aprovados nos CIs do PR e da `main`.

### Promoção externa

- [x] PR da release aprovado por todos os jobs obrigatórios.
- [x] Merge realizado na `main` sem bypass do CI.
- [x] Deploy automático de `web`, `worker` e `scheduler` em `SUCCESS`.
- [x] `/health/live` e `/health/ready` respondem HTTP 200 e preservam `X-Request-ID`.
- [x] Smoke autenticado usa somente dados sintéticos e confirma RBAC e trilha de execução.
- [ ] Tag anotada `v1.0.0` aponta para o commit exato validado em produção.
- [ ] GitHub Release publicado com evidências e limitações conhecidas.

## Evidência externa de 09/09/2026

O [PR #46](https://github.com/Guilherme-Justo/SheepContabil/pull/46) aprovou `Quality and tests` e
`Container build` na execução
[34364266517](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/34364266517). O merge sem
bypass gerou o commit
[`09fa855`](https://github.com/Guilherme-Justo/SheepContabil/commit/09fa855ee8039be2dce402050cd43acaa6756268),
e o CI de `main`
[34364726394](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/34364726394) repetiu os dois
jobs com sucesso antes da promoção automática.

Os três deployments do mesmo SHA terminaram em `SUCCESS`:

- `web`: `8304904e-931c-4e13-94d5-5845a599882d`;
- `worker`: `907198bb-ff7a-49a2-9639-70ec8e22e242`;
- `scheduler`: `da96abcb-8128-4051-bbab-873f92ca2a36`.

`/health/live` e `/health/ready` responderam HTTP 200 e preservaram, respectivamente, os UUIDs
`af36d302-7700-44ab-8976-9b3dbbf9e038` e `f161ab4d-89a7-4b8b-8d94-483c918d5604` enviados em
`X-Request-ID`. A rota canônica `/conta/entrar/` também respondeu 200 com o formulário esperado.

O smoke autenticado consultou os quatro módulos como administrador e criou o briefing exclusivamente
sintético `a584dfcd-037c-4c13-8582-68e91d9b9d8b`, imediatamente cancelado. Sua
[execução](https://web-production-8f055.up.railway.app/execucoes/d23bd1fb-2a69-4f21-ac7a-aeb46354c3ed/)
`d23bd1fb-2a69-4f21-ac7a-aeb46354c3ed` preserva os eventos contínuos `created` → `started` →
`cancelled`. O administrador recebeu HTTP 200 com a coluna técnica de correlação; o operador
societário recebeu HTTP 200 sem essa coluna; o operador fiscal recebeu HTTP 404. Nenhum documento,
mensagem ou contato real foi usado e nenhuma integração externa foi acionada.

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
