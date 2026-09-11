# Release v1.1.0

## Identificação

| Campo | Valor |
| --- | --- |
| Estado | Release Candidate em validação |
| Versão | `1.1.0` |
| Data de preparação | 11/09/2026 |
| Tag anterior | [`v1.0.0`](https://github.com/Guilherme-Justo/SheepContabil/releases/tag/v1.0.0) |
| Commit anterior à preparação | [`7c44bf1`](https://github.com/Guilherme-Justo/SheepContabil/commit/7c44bf103d5428030bc1f1ed1af7feb794ce75e6) |
| Branch de preparação | `codex/release-v1.1.0` |
| Tag candidata | `v1.1.0` — pendente de validação externa |
| URL pública | [web-production-8f055.up.railway.app](https://web-production-8f055.up.railway.app) |

## Objetivo

A `1.1.0` promove o hardening de prontidão demonstrativa desenvolvido depois da primeira release
estável. Ela não cria um quinto processo, não amplia silenciosamente regras contábeis e não muda a
arquitetura: torna a preparação do ambiente conservadora e repetível, atualiza a massa estritamente
sintética e consolida evidências operacionais dos quatro fluxos.

O incremento é minor porque adiciona capacidades compatíveis de operação e demonstração. Não é
patch, pois inclui um comando operacional novo e novos comportamentos explícitos do seed e do ciclo
sintético do SC-04; não é major, pois URLs, RBAC, schema persistido e contratos dos módulos continuam
compatíveis com a `1.0.0`.

## Delta desde v1.0.0

| Área | Capacidade promovida |
| --- | --- |
| Preparação | `prepare_demo` verifica os quatro módulos, o modo simulado, o estado reversível do SC-05, o PDF do SC-06 e seis evidências funcionais; sem `--apply`, é somente leitura. |
| Massa sintética | `seed_demo` preserva credenciais e validades persistidas; mudanças exigem `--sync-credentials` ou `--refresh-certificate-dates`. |
| SC-04 | Cada ciclo manual recebe identidade sintética própria, permitindo novo ensaio sem apagar histórico; a duplicidade interna do lote continua demonstrável. |
| SC-06 | Downloads do PDF passam a declarar `Cache-Control: private, no-store`, mantendo autenticação, RBAC e download como anexo. |
| SC-20 | A massa oficial inclui um certificado com e-mail e WhatsApp para demonstrar os dois canais sem contato real. |
| Railway | A região dos três serviços de aplicação é centralizada em uma constante, sem alterar a topologia existente ou o requisito `Wait for CI`. |
| Evidência | O roteiro integral de produção preserva UUIDs atuais de SC-04, bloqueio/desbloqueio do SC-05, briefing/PDF do SC-06 e repetição deduplicada do SC-20. |

## Compatibilidade e migração

- Não existe migration nova entre `v1.0.0` e esta candidata.
- Nenhuma URL canônica, papel, área ou regra de autorização foi removida.
- PostgreSQL continua como fonte de verdade e Redis como broker; `web`, `worker` e `scheduler`
  mantêm a mesma topologia Railway.
- O simulador do SC-05 continua co-localizado no worker apenas no ambiente demonstrativo, com
  ambiente reduzido e acesso por loopback.
- Histórico, eventos, comunicações, briefings e artefatos existentes não são apagados ou
  reescritos pelo preparo.
- Scripts que dependiam da rotação implícita de senhas ou do reposicionamento implícito de
  validades devem passar as novas opções deliberadamente; a execução padrão agora é conservadora.

## Gates do Release Candidate

### Integridade do artefato

- [x] A branch parte da `main` limpa e sincronizada.
- [x] `pyproject.toml`, `uv.lock`, `package.json` e `package-lock.json` declaram `1.1.0`.
- [x] O changelog congela o delta completo desde `v1.0.0`.
- [x] README identifica a candidata sem declarar antecipadamente a tag como publicada.
- [x] O documento histórico da `v1.0.0` permanece inalterado.
- [x] Nenhum segredo ou dado pessoal foi acrescentado ao repositório.
- [x] `railway config plan`, com CLI `5.49.6`, confirma ausência de mudança na infraestrutura.

### Verificação local

- [x] Lockfiles consistentes e instalações congeladas reproduzíveis.
- [x] Ruff e verificação de formatação aprovados.
- [x] Mypy aprovado.
- [x] Django, settings de produção e simulador aprovados.
- [x] Ausência de migrations não versionadas.
- [x] Suíte completa e cobertura mínima de 75% aprovadas.
- [x] Contratos Playwright aprovados com Chromium real.
- [x] Assets do navegador recompilados sem diferença inesperada.
- [x] Configuração do Docker Compose validada.
- [ ] Imagem de produção construída pelo CI.

Resultado local de 11/09/2026: `320` testes coletados, `319 passed`, `1 skipped` por exigir o
PostgreSQL efêmero do CI e cobertura de `86,04%`, acima do piso de `75%`. Os oito contratos
Playwright passaram com Chromium real. `uv 0.12.6` aprovou o lock, sincronizou `sheepcontabil==1.1.0`
em modo frozen e confirmou `84` pacotes compatíveis. O npm reinstalou e auditou `47` pacotes sem
vulnerabilidades. Build dos assets, Ruff, formatação de `126` arquivos, Mypy em `75` fontes, checks
Django, ausência de migrations, sintaxe POSIX, Compose e `git diff --check` também passaram. A
migration em PostgreSQL com dados preexistentes e a construção da imagem permanecem gates
obrigatórios do CI.

### Promoção externa

- [ ] PR da release aprovado por todos os jobs obrigatórios.
- [ ] Merge realizado na `main` sem bypass do CI.
- [ ] Deploy automático de `web`, `worker` e `scheduler` em `SUCCESS` no mesmo SHA.
- [ ] `/health/live`, `/health/ready` e a tela de login respondem HTTP 200.
- [ ] Preflight autenticado confirma os quatro módulos sem erro de console ou página.
- [ ] Evidência final de CI, deploy e smoke incorporada ao documento.
- [ ] Tag anotada `v1.1.0` aponta para o commit exato validado em produção.
- [ ] GitHub Release publicada a partir da mesma tag, sem ser draft ou prerelease.

## Evidência operacional anterior à promoção

O baseline funcional `7c44bf1` já foi exercitado integralmente em produção em 10/09/2026. O roteiro
terminou em `PASSED`, sem erro de console ou página, e está registrado em
[`deployment.md`](deployment.md#ensaio-integral-cronometrado-dos-quatro-fluxos). O SC-05 foi
restaurado a `Ativo`, o atendimento inválido do SC-06 não foi persistido, a segunda execução do
SC-20 não criou tentativa adicional e nenhum link externo foi aberto.

Essa evidência reduz o risco do candidato, mas não substitui a validação do commit final. Depois do
merge do PR da release, os três deployments, os health checks e o preflight autenticado devem ser
confirmados novamente antes da tag.

## Regra de publicação

A tag `v1.1.0` não pode ser criada a partir da branch de trabalho nem antes do deploy. A ordem é:

1. validar e revisar este Release Candidate;
2. incorporar o PR na `main` somente depois do CI verde;
3. confirmar os três deployments automáticos no SHA do merge;
4. repetir health checks e preflight autenticado com dados sintéticos;
5. incorporar a evidência externa final, se ainda estiver pendente;
6. confirmar o commit publicado e criar a tag anotada exatamente nesse SHA;
7. publicar a GitHub Release a partir da mesma tag;
8. atualizar README e este documento para o estado publicado sem reescrever a `v1.0.0`.

Qualquer falha antes da tag interrompe a publicação. Depois que produção registrar eventos no novo
commit, rollback de código não autoriza rollback destrutivo de dados ou de schema.

## Smoke permitido

O smoke usa somente usuários, clientes, documentos, certificados e contatos sintéticos. É
permitido consultar os quatro módulos, validar RBAC, abrir as evidências autenticadas existentes e
executar um ciclo reversível quando necessário. É proibido persistir documento real, abrir ou
concluir comunicação externa e registrar credenciais em logs, URLs, relatórios ou capturas.

## Limitações conhecidas

- O plano Railway consultado durante a preparação ainda é `Trial`; disponibilidade durante toda a
  avaliação depende de capacidade e billing mantidos pelo proprietário.
- O simulador SC-05 compartilha contêiner, UID e ciclo de disponibilidade com o Celery na Railway.
- O healthcheck da plataforma valida promoção, mas não substitui monitoramento externo contínuo.
- Rate limit, troca obrigatória de senha e auditoria específica de login são necessários antes de
  identidades reais.
- Backup diário e restauração do PostgreSQL não foram ensaiados neste ambiente demonstrativo.
- Faixas adicionais de aviso do SC-20 dependem de confirmação formal da regra de negócio.
- Retenção, antivírus, DLP e segregação regulatória devem ser revistos antes de dados reais.
- PDFs de imagem sem texto pesquisável continuam fora do ciclo atual do SC-04.

As decisões arquiteturais permanecem em [`architecture.md`](architecture.md), as premissas em
[`assumptions.md`](assumptions.md), a operação em [`deployment.md`](deployment.md) e o histórico da
primeira release em [`release-v1.0.0.md`](release-v1.0.0.md).
