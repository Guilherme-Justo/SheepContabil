# Implantação e operação — Railway

## Topologia de produção

| Recurso | Exposição | Responsabilidade |
| --- | --- | --- |
| `web` | Domínio HTTPS público | Portal, autenticação, comandos e consulta |
| `worker` | Privado | Celery, IA, RPA, geração de artefatos e WSGI sintético do SC-05; Playwright usa loopback e Railway usa a porta privada para healthcheck |
| `scheduler` | Privado e efêmero | Pulso de 15 minutos que publica SC-04 diário e SC-20 mensal |
| PostgreSQL | Privado | Fonte de verdade |
| Redis | Privado | Broker; não guarda histórico oficial |
| Bucket | Privado | Originais e resultados via S3 |

Web e worker ficam em Virgínia (`us-east4-eqdc4a`) e o bucket privado usado pelo
SC-04 fica na localidade equivalente (`iad`). Os
templates de PostgreSQL e Redis do ambiente `Trial` foram criados em Amsterdã
(`ams`). Essa topologia cruzada é aceitável apenas para a fundação demonstrativa:
antes da implementação dos fluxos reais, a região permanente deve ser decidida
e os volumes só podem ser movidos com backup e aprovação explícita.

Em 10/09/2026, `us-east4-eqdc4a` continuava sendo o identificador oficial da
região **US East Metal** e os três serviços de aplicação estavam ativos nessa
região. Um aviso isolado do painel não autoriza trocar a região na IaC: confira
primeiro se existe uma alteração staged obsoleta e descarte-a ou selecione
novamente US East Metal. A constante `APPLICATION_REGION` mantém web, worker e
scheduler alinhados. Migrar aplicação, banco, Redis ou bucket para outra região
continua exigindo plano revisado e, quando houver volume, janela e backup.

## Pré-requisitos

1. Repositório GitHub público com `main` verde no CI.
2. Railway CLI 5.42.1 ou superior autenticada.
3. Projeto Railway em plano que não suspenda o portal durante a avaliação.
4. Senhas sintéticas de demonstração e `DJANGO_SECRET_KEY` geradas fora do Git.
5. `OPENAI_API_KEY` e `OPENAI_MODEL` válidos no worker para a classificação real do SC-04.

## Provisionamento

```text
railway login
railway init --name SheepContabil
railway config plan
railway config apply
```

Revise o plano antes de aplicar. `.railway/railway.ts` cria web, worker, scheduler, PostgreSQL, Redis e bucket; valores `preserve()` nunca revelam nem substituem um segredo já existente. O plano Railway disponível não comporta um quarto serviço de aplicação, portanto não existe recurso `simulator` na IaC. Como evidência histórica da publicação `0.5.0` em 01/09/2026, a revisão daquele plano indicou `0` recursos novos, `10` ajustes e `0` remoções. Em 09/09/2026, a release `1.0.0` foi reavaliada com a CLI `5.49.6`, que informou `Your Railway configuration is already up to date`; nenhuma aplicação ou alteração de infraestrutura foi necessária.

Depois de aplicar:

1. Cadastre `DJANGO_SECRET_KEY`, `DEMO_ADMIN_PASSWORD`, `DEMO_OPERATOR_PASSWORD`, `DEMO_SOCIETARY_OPERATOR_PASSWORD`, `DEMO_FISCAL_OPERATOR_PASSWORD` e `DEMO_TECHNOLOGY_OPERATOR_PASSWORD` no serviço web. Use valores distintos e entregue-os aos avaliadores fora do Git.
2. Mantenha `OPENAI_API_KEY` e `OPENAI_MODEL` somente no worker. O modelo deve ser escolhido explicitamente entre os disponíveis na conta e validado com a massa sintética; web e worker compartilham apenas os segredos de infraestrutura necessários.
3. No `worker`, configure `SC05_SIMULATOR_DJANGO_SECRET_KEY`, `SC05_SIMULATOR_USERNAME` e `SC05_SIMULATOR_PASSWORD` com valores fortes e mantenha `SC05_SIMULATOR_BASE_URL=http://127.0.0.1:8000`. Não repita esses segredos em web ou scheduler.
4. Confirme que web, worker e scheduler receberam a fonte `Guilherme-Justo/SheepContabil`, branch `main`, declarada na IaC, com **Wait for CI** ativo.
5. Gere domínio Railway somente no web. O WSGI sintético não possui domínio nem porta pública; a rede privada alcança apenas a porta autenticada usada também pelo healthcheck da plataforma, enquanto o Playwright usa loopback.
6. Mantenha o pre-deploy `sh scripts/predeploy.sh` no web.
7. Para a carga inicial, defina `SEED_DEMO_ON_DEPLOY=true`, publique uma vez e
   volte a variável para `false` sem novo deploy. O script executa as migrations
   em toda publicação e só executa o seed idempotente quando a flag está ativa.
8. Registre a URL e as credenciais de avaliação fora do repositório.

O seed comum preserva senhas e validades já persistidas. Rotação de credenciais
exige `seed_demo --sync-credentials`; reposicionar datas relativas dos
certificados exige `seed_demo --refresh-certificate-dates`. Antes de uma
apresentação, use `python src/manage.py prepare_demo` em modo somente leitura e
exija `Resultado: READY`. Use `--apply` apenas para restaurar a projeção
allowlisted da Aurora no SC-05; o comando não apaga evidências e recusa operação
ativa, estado parcial, estado desconhecido ou backend SC-20 não simulado. O
preparo também recusa certificados controlados fora da janela ativa de 60 dias;
nesse caso, `seed_demo --refresh-certificate-dates` deve ser uma decisão
consciente, porque cria uma nova identidade funcional para deduplicação.

O comando do worker é `sh scripts/run_worker_with_simulator.sh`. O supervisor aguarda o schema aplicado pelo web, inicia `config.simulator_wsgi` com ambiente reconstruído por allowlist, confirma liveness e então inicia Celery com concorrência 1. Um marcador efêmero só libera `/health/ready` depois que Celery permanece vivo; a Railway usa esse endpoint para promover o deploy. O subprocesso WSGI recebe runtime Python, segredo Django próprio, fuso, PostgreSQL e a credencial sintética, mas não herda `REDIS_URL`, S3, `OPENAI_API_KEY` nem `OPENAI_MODEL`. No shutdown, Celery recebe `TERM` primeiro e conserva o simulador durante o encerramento gracioso de até 300 segundos.

## Smoke test

- `/health/live` responde `200` e não consulta dependências.
- `/health/ready` responde `200` e confirma PostgreSQL.
- `/conta/entrar/` abre sem VPN.
- Administrador vê SC-04, SC-05, SC-06 e SC-20.
- Operador de Processos vê somente SC-20.
- Operador Societário vê somente SC-06.
- Operador Fiscal vê somente SC-04.
- Acesso direto do operador a outro módulo retorna 404.
- Assets, logo e fontes carregam em HTTPS.
- Worker conecta ao Redis sem possuir domínio público e inicia com concorrência 1 para limitar o consumo do Chromium no primeiro deploy.
- Após Celery iniciar, o WSGI sintético responde `200` em `http://127.0.0.1:8000/health/ready`; o healthcheck da Railway confirma o mesmo estado pela rede privada, sem domínio público.
- O ambiente efetivo do subprocesso WSGI não contém Redis, S3 nem OpenAI e a autenticação aceita somente a credencial sintética cadastrada no worker.
- O SC-05 bloqueia Arquivos → Contábil → Tarefas, mantém o cliente ativo em Tarefas, registra screenshots privados e restaura exatamente os responsáveis no desbloqueio inverso.
- O cenário de falha combinada termina `PARTIALLY_FAILED`; a retomada conclui sem novo clique no portal já conforme. Download com RBAC válido confere hash/tamanho e acesso de outra área retorna `404`.
- O módulo SC-20 lista a massa sintética, executa a janela inclusiva de 60 dias e registra uma falha deliberada disponível para retentativa.
- Uma segunda execução do SC-20 não repete os avisos já registrados para a mesma validade, canal e política.
- O SC-06 lista um caso concluído e um rascunho, reage aos caminhos de abertura/alteração, exige o bloco de outra UF e o regime quando houver sócio casado.
- Um briefing incompleto não conclui; o caso completo aparece na execução e seu PDF baixa apenas para usuário autorizado.
- O SC-04 aceita PDF, PNG, JPEG e TXT sintéticos até 10 MiB, processa a caixa simulada e não repete conteúdo/origem.
- Uma predição acima da política é encaminhada; confiança baixa, ambiguidade ou indisponibilidade da IA abre revisão humana sem fabricar sucesso.
- Preview e download revalidam o acesso Fiscal, não exibem chaves S3 e o original mantém o mesmo SHA-256 depois do roteamento.
- O scheduler possui a expressão de 15 minutos, não expõe domínio e termina depois de publicar o trabalho no Redis.

## Deploy e rollback

O deploy de `main` só deve ocorrer depois do CI verde. Migrations precisam ser retrocompatíveis com a versão anterior durante a troca. Para falha de aplicação, redeploy da última versão saudável; para migração destrutiva, não avançar sem backup e procedimento reversível.

O healthcheck da Railway é de ativação de deploy, não monitoramento contínuo. A release `1.0.0`
mantém a ausência de monitor externo como limitação explícita do ambiente demonstrativo; antes de uma
operação real, disponibilidade contínua e alertas devem ser configurados fora da própria plataforma.

### Recuperação do deploy automático após merge

Recuperação concluída em 2026-08-31. A integração GitHub da Railway foi reautorizada somente para `Guilherme-Justo/SheepContabil`; a fonte `main` foi reassociada a `web`, `worker` e `scheduler`, o deploy automático foi habilitado e `Wait for CI` ficou ativo nos três serviços.

A validação controlada usou o [PR `#3`](https://github.com/Guilherme-Justo/SheepContabil/pull/3), incorporado no commit [`b5e86cd44cb53fd7083a40881111a7d7f7f3e999`](https://github.com/Guilherme-Justo/SheepContabil/commit/b5e86cd44cb53fd7083a40881111a7d7f7f3e999). A execução [GitHub Actions `33458151242`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33458151242) concluiu `Quality and tests` e `Container build` com sucesso. Somente depois do CI verde, a Railway iniciou pela origem GitHub os deployments:

- `web`: `58e133bd-b9d3-45cd-a5f0-a4d0dceb79aa`;
- `worker`: `07732a81-7cc9-4625-9798-4c2a5fb2a45e`;
- `scheduler`: `858094d8-f740-4630-8aea-99706622503a`.

O portal respondeu `200` em `/health/ready`, encerrando o teste de recuperação. A associação com `main`, o deploy automático e `Wait for CI` devem permanecer ativos nesses três serviços já comprovados. Não existe quarta fonte para o simulador: na topologia ajustada ele acompanha exatamente o commit e o deploy do worker. Se `GitHub Repo not found` reaparecer, reautorize a integração e reassocie apenas a fonte afetada; um workflow com token Railway continua sendo fallback de último recurso.

### Publicação da 0.5.0 e ajuste do SC-05

O [PR `#5`](https://github.com/Guilherme-Justo/SheepContabil/pull/5) foi incorporado no commit [`d5b71b384340f4f3cd66e07f801309529790b39f`](https://github.com/Guilherme-Justo/SheepContabil/commit/d5b71b384340f4f3cd66e07f801309529790b39f). O [CI do PR `33538813847`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33538813847) e o [CI do push em `main` `33539137377`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33539137377) ficaram verdes, inclusive no build da imagem. A Railway esperou o CI e concluiu os deployments 0.5.0:

- `web`: `e07e22d8-1d4c-4901-994c-7d41bbb78c7c`;
- `worker`: `5cba6372-cbdc-4ead-a8f0-a5a0f87c56f2`;
- `scheduler`: `ef6c395d-e250-49f3-9eed-c5d0d2b62865`.

Essa evidência confirma merge, CI, imagem e autodeploy da base 0.5.0 nos três serviços existentes. A topologia daquele commit ainda dependia de um quarto recurso que o plano não permitiu criar; por isso, a comprovação ponta a ponta foi concluída no ajuste seguinte.

O [PR `#6`](https://github.com/Guilherme-Justo/SheepContabil/pull/6), merge [`4ab7af38ccd0259d89c80a00b82679d3754d5ac3`](https://github.com/Guilherme-Justo/SheepContabil/commit/4ab7af38ccd0259d89c80a00b82679d3754d5ac3), passou pelo [CI do PR `33556800150`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33556800150) e pelo [CI de `main` `33557162559`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33557162559). Ambos aprovaram qualidade, 124 testes, cobertura e imagem antes do autodeploy:

- `web`: `4c35c556-9f52-4ee9-b0c7-a092a703f1b8`;
- `worker`: `8101f6cb-4401-489e-850e-02f62075e8e3`;
- `scheduler`: `348d9edf-8bf4-45de-b8ed-e955f3ff3934`.

Uma fila duplicada do worker (`4c39a415-f7a0-473b-81dc-f14007fcf87c`) foi cancelada depois que o deployment `8101f6cb-4401-489e-850e-02f62075e8e3` já estava saudável; ela não substitui nem invalida a promoção ativa. O WSGI respondeu ao healthcheck privado, Celery chegou a `ready`, o portal público respondeu `200` em `/health/ready` e o Playwright permaneceu em loopback. O seed controlado posterior terminou no web `cfe0a20c-e358-44ef-870f-5aec6271a24d`; `SEED_DEMO_ON_DEPLOY` foi novamente definido como `false` sem disparar outro deploy.

O smoke autenticado bloqueou e desbloqueou Aurora, validou três portais, seis PNGs privados e negação `404` para outra área, provocou `PARTIALLY_FAILED`, retomou a mesma execução e restaurou o cliente a `Ativo`. Os UUIDs e a evidência detalhada estão em [Dia 5](day-5.md#publicação-e-validação-de-produção). Esse resultado encerra a pendência operacional do SC-05 na entrega demonstrativa.

## Scheduler do SC-04 e SC-20

A Railway executa `python src/manage.py dispatch_due_schedules` a cada 15 minutos em UTC. O comando primeiro reconcilia um lote limitado de execuções órfãs e depois converte as regras para `APP_TIME_ZONE`: `SC04_DAILY_HOUR` define o horário diário do SC-04 e `SC20_MONTHLY_HOUR` define o horário do primeiro dia do mês para o SC-20, ambos com padrão `8`. Cada processo só é publicado quando seu módulo está habilitado e mantém a frequência esperada. As chaves idempotentes são `sc04:scheduled:AAAA-MM-DD` e `sc20:scheduled:AAAA-MM`; a data-base mensal permanece ancorada no primeiro dia mesmo se o pulso atrasar. Como o cron pulsa a cada 15 minutos, a publicação pode ocorrer até 14 minutos e 59 segundos depois do horário configurado.

O pulso termina sem executar a automação. Cada publicação usa um identificador persistido antes do envio ao Redis e registra separadamente o início da tentativa e a confirmação do broker; uma entrega substituída não pode iniciar nem finalizar a execução. Uma fila com confirmação do broker não é republicada apenas por estar antiga, pois pode representar backlog legítimo. Se a publicação inicial falhar, a execução recebe falha segura. Se uma republicação de recuperação falhar, a execução permanece enfileirada e auditada, sem exceder o limite automático. Para um ensaio operacional controlado fora do horário, use `python src/manage.py dispatch_due_schedules --force` apenas com dados sintéticos.

As variáveis operacionais têm estes valores iniciais em web, worker e scheduler:

| Variável | Valor | Regra |
| --- | ---: | --- |
| `CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS` | `1200` | maior que o limite rígido Celery de 900 s |
| `AUTOMATION_QUEUED_STALE_AFTER_SECONDS` | `1800` | tempo até considerar uma publicação não consumida |
| `AUTOMATION_RUNNING_STALE_AFTER_SECONDS` | `1800` | maior que a visibilidade do Redis |
| `AUTOMATION_RECONCILIATION_MAX_ATTEMPTS` | `1` | uma única recuperação automática |

O comando `python src/manage.py reconcile_automation_runs --dry-run` apenas informa o que faria. Sem `--dry-run`, recupera o lote padrão; `--batch-size N` limita explicitamente a inspeção. SC-04 fecha a tentativa interrompida antes de retomar. SC-05 e SC-20 já iniciados são colocados em `PARTIALLY_FAILED`, sem repetir ações externas; no SC-05 é necessário conferir os três portais e, no SC-20, o histórico do provedor antes de qualquer retomada manual.

## Rastreabilidade e migration 0010

A migration `0010_automation_run_event` cria somente a tabela, constraints e índices da trilha;
ela não altera execuções existentes nem inventa eventos retroativos. O pre-deploy do serviço web
deve aplicá-la antes de web, worker e scheduler operarem com o novo commit. Os três serviços
precisam convergir para a mesma revisão.

Não há segredo novo obrigatório. Railway fornece automaticamente nome do serviço, ambiente e SHA
quando disponíveis; `APP_SERVICE_NAME`, `APP_ENVIRONMENT` e `APP_RELEASE` são substituições
opcionais para outros provedores. O cabeçalho `X-Request-ID` pode ser enviado por um proxy, mas só
UUIDs válidos são preservados.

Após o deploy:

1. confirme `/health/live` e `/health/ready` com HTTP 200;
2. inicie uma execução sintética e confirme sequências contínuas na linha do tempo;
3. como administrador, confira `request_id`/`task_id`; como operador, confirme que eles não aparecem;
4. filtre os logs de web e worker pelo mesmo `run_id`;
5. execute o pulso agendado e confirme um `pulse_id` único nos logs;
6. verifique que uma execução histórica sem eventos mostra o aviso de ausência de backfill;
7. no SC-20, trate tentativa `PENDING`/`integration_unknown` como conferência manual, nunca como
   autorização de reenvio.

O rollback do código deve preservar a tabela de eventos. Reverter a migration apaga evidência e
não é um rollback operacional aceitável depois que produção registrar o primeiro evento.

Referências oficiais: [IaC](https://docs.railway.com/infrastructure-as-code), [Django](https://docs.railway.com/guides/django), [pre-deploy](https://docs.railway.com/deployments/pre-deploy-command), [healthchecks](https://docs.railway.com/deployments/healthchecks), [cron](https://docs.railway.com/cron-jobs) e [buckets](https://docs.railway.com/storage-buckets).
