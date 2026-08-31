# Implantação e operação — Railway

## Topologia de produção

| Recurso | Exposição | Responsabilidade |
| --- | --- | --- |
| `web` | Domínio HTTPS público | Portal, autenticação, comandos e consulta |
| `worker` | Privado | Celery, IA, RPA e geração de artefatos |
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

Revise o plano antes de aplicar. `.railway/railway.ts` cria web, worker, scheduler, PostgreSQL, Redis e bucket; valores `preserve()` nunca revelam nem substituem um segredo já existente.

Depois de aplicar:

1. Cadastre `DJANGO_SECRET_KEY`, `DEMO_ADMIN_PASSWORD` e `DEMO_OPERATOR_PASSWORD` no serviço web. A última cria operadores sintéticos de Processos, Societário e Fiscal; use as variáveis específicas somente se precisar separar as senhas.
2. Mantenha `OPENAI_API_KEY` e `OPENAI_MODEL` somente no worker. O modelo deve ser escolhido explicitamente entre os disponíveis na conta e validado com a massa sintética; web e worker compartilham apenas os segredos de infraestrutura necessários.
3. Confirme que web e worker receberam a fonte `Guilherme-Justo/SheepContabil`, branch `main`, declarada na IaC.
4. Gere domínio Railway somente no web.
5. Mantenha o pre-deploy `sh scripts/predeploy.sh` no web.
6. Para a carga inicial, defina `SEED_DEMO_ON_DEPLOY=true`, publique uma vez e
   volte a variável para `false` sem novo deploy. O script executa as migrations
   em toda publicação e só executa o seed idempotente quando a flag está ativa.
7. Registre a URL e as credenciais de avaliação fora do repositório.

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

O healthcheck da Railway é de ativação de deploy, não monitoramento contínuo. Um monitor externo de uptime pode ser adicionado antes da entrega final.

## Scheduler do SC-04 e SC-20

A Railway executa `python src/manage.py dispatch_due_schedules` a cada 15 minutos em UTC. O comando converte as regras para `America/Sao_Paulo`: após 08:00 cria no máximo uma chave diária `sc04:scheduled:AAAA-MM-DD`; para o SC-20, só considera a competência vencida após o primeiro dia do mês às 08:00 e cria `sc20:scheduled:AAAA-MM`. A data-base mensal permanece ancorada no primeiro dia mesmo se o pulso atrasar.

O pulso termina sem executar a automação. Se a publicação no broker falhar antes de o worker iniciar, a execução registra a falha e o pulso seguinte pode republicar o mesmo UUID; qualquer execução já iniciada ou terminal continua protegida contra duplicidade. Para um ensaio operacional controlado fora do horário, use `python src/manage.py dispatch_due_schedules --force` apenas com dados sintéticos.

Referências oficiais: [IaC](https://docs.railway.com/infrastructure-as-code), [Django](https://docs.railway.com/guides/django), [pre-deploy](https://docs.railway.com/deployments/pre-deploy-command), [healthchecks](https://docs.railway.com/deployments/healthchecks), [cron](https://docs.railway.com/cron-jobs) e [buckets](https://docs.railway.com/storage-buckets).
