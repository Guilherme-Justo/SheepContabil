# Implantação e operação — Railway

## Topologia de produção

| Recurso | Exposição | Responsabilidade |
| --- | --- | --- |
| `web` | Domínio HTTPS público | Portal, autenticação, comandos e consulta |
| `worker` | Privado | Celery, IA, RPA e geração de artefatos |
| `scheduler` | Privado e efêmero | Pulso de 15 minutos que publica o SC-20 quando a competência vence |
| PostgreSQL | Privado | Fonte de verdade |
| Redis | Privado | Broker; não guarda histórico oficial |
| Bucket | Privado | Originais e resultados via S3 |

Web e worker ficam em Virgínia (`us-east4-eqdc4a`) e o bucket, ainda vazio e
sem adapter conectado no Dia 1, usa a localidade equivalente (`iad`). Os
templates de PostgreSQL e Redis do ambiente `Trial` foram criados em Amsterdã
(`ams`). Essa topologia cruzada é aceitável apenas para a fundação demonstrativa:
antes da implementação dos fluxos reais, a região permanente deve ser decidida
e os volumes só podem ser movidos com backup e aprovação explícita.

## Pré-requisitos

1. Repositório GitHub público com `main` verde no CI.
2. Railway CLI 5.42.1 ou superior autenticada.
3. Projeto Railway em plano que não suspenda o portal durante a avaliação.
4. Senhas sintéticas de demonstração e `DJANGO_SECRET_KEY` geradas fora do Git.
5. `OPENAI_API_KEY` somente quando o adapter SC-04 entrar em uso.

## Provisionamento

```text
railway login
railway init --name SheepContabil
railway config plan
railway config apply
```

Revise o plano antes de aplicar. `.railway/railway.ts` cria web, worker, scheduler, PostgreSQL, Redis e bucket; valores `preserve()` nunca revelam nem substituem um segredo já existente.

Depois de aplicar:

1. Cadastre `DJANGO_SECRET_KEY`, `DEMO_ADMIN_PASSWORD` e `DEMO_OPERATOR_PASSWORD` no serviço web. A última cria os operadores sintéticos de Processos e Societário; use `DEMO_SOCIETARY_OPERATOR_PASSWORD` somente se precisar separá-las.
2. Mantenha `OPENAI_API_KEY` somente no worker; web e worker compartilham apenas os segredos de infraestrutura necessários.
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
- Acesso direto do operador a outro módulo retorna 404.
- Assets, logo e fontes carregam em HTTPS.
- Worker conecta ao Redis sem possuir domínio público e inicia com concorrência 1 para limitar o consumo do Chromium no primeiro deploy.
- O módulo SC-20 lista a massa sintética, executa a janela inclusiva de 60 dias e registra uma falha deliberada disponível para retentativa.
- Uma segunda execução do SC-20 não repete os avisos já registrados para a mesma validade, canal e política.
- O SC-06 lista um caso concluído e um rascunho, reage aos caminhos de abertura/alteração, exige o bloco de outra UF e o regime quando houver sócio casado.
- Um briefing incompleto não conclui; o caso completo aparece na execução e seu PDF baixa apenas para usuário autorizado.
- O scheduler possui a expressão de 15 minutos, não expõe domínio e termina depois de publicar o trabalho no Redis.

## Deploy e rollback

O deploy de `main` só deve ocorrer depois do CI verde. Migrations precisam ser retrocompatíveis com a versão anterior durante a troca. Para falha de aplicação, redeploy da última versão saudável; para migração destrutiva, não avançar sem backup e procedimento reversível.

O healthcheck da Railway é de ativação de deploy, não monitoramento contínuo. Um monitor externo de uptime pode ser adicionado antes da entrega final.

## Scheduler do SC-20

A Railway executa `python src/manage.py dispatch_due_schedules` a cada 15 minutos em UTC. O comando converte a regra para `America/Sao_Paulo`, só considera a competência vencida após o primeiro dia do mês às 08:00, cria a chave `sc20:scheduled:AAAA-MM` e publica no Redis. A data-base permanece ancorada no primeiro dia mesmo se o pulso atrasar.

O pulso termina sem executar a automação. Se a publicação no broker falhar antes de o worker iniciar, a execução registra a falha e o pulso seguinte pode republicar o mesmo UUID; qualquer execução já iniciada ou terminal continua protegida contra duplicidade. Para um ensaio operacional controlado fora do horário, use `python src/manage.py dispatch_due_schedules --force` apenas com dados sintéticos.

Referências oficiais: [IaC](https://docs.railway.com/infrastructure-as-code), [Django](https://docs.railway.com/guides/django), [pre-deploy](https://docs.railway.com/deployments/pre-deploy-command), [healthchecks](https://docs.railway.com/deployments/healthchecks), [cron](https://docs.railway.com/cron-jobs) e [buckets](https://docs.railway.com/storage-buckets).
