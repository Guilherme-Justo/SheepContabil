# Implantação e operação — Railway

## Topologia de produção

| Recurso | Exposição | Responsabilidade |
| --- | --- | --- |
| `web` | Domínio HTTPS público | Portal, autenticação, comandos e consulta |
| `worker` | Privado | Celery, IA, RPA e geração de artefatos |
| `cron` | Privado e efêmero | Pulso de 15 minutos; entra após o dispatcher real |
| PostgreSQL | Privado | Fonte de verdade |
| Redis | Privado | Broker; não guarda histórico oficial |
| Bucket | Privado | Originais e resultados via S3 |

Todos os recursos ficam na região de Virgínia. A exceção é o identificador próprio do bucket (`iad`), que corresponde à mesma localidade.

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

Revise o plano antes de aplicar. `.railway/railway.ts` cria web, worker, PostgreSQL, Redis e bucket; valores `preserve()` nunca revelam nem substituem um segredo já existente.

Depois de aplicar:

1. Cadastre `DJANGO_SECRET_KEY`, `DEMO_ADMIN_PASSWORD` e `DEMO_OPERATOR_PASSWORD` no serviço web.
2. Mantenha `OPENAI_API_KEY` somente no worker; web e worker compartilham apenas os segredos de infraestrutura necessários.
3. Confirme que web e worker receberam a fonte `Guilherme-Justo/SheepContabil`, branch `main`, declarada na IaC.
4. Gere domínio Railway somente no web.
5. Mantenha o pre-deploy `python src/manage.py migrate --noinput` no web.
6. Execute `python src/manage.py seed_demo` uma vez em um shell do web.
7. Registre a URL e as credenciais de avaliação fora do repositório.

## Smoke test

- `/health/live` responde `200` e não consulta dependências.
- `/health/ready` responde `200` e confirma PostgreSQL.
- `/conta/entrar/` abre sem VPN.
- Administrador vê SC-04, SC-05, SC-06 e SC-20.
- Operador de Processos vê somente SC-20.
- Acesso direto do operador a outro módulo retorna 404.
- Assets, logo e fontes carregam em HTTPS.
- Worker conecta ao Redis sem possuir domínio público e inicia com concorrência 1 para limitar o consumo do Chromium no primeiro deploy.

## Deploy e rollback

O deploy de `main` só deve ocorrer depois do CI verde. Migrations precisam ser retrocompatíveis com a versão anterior durante a troca. Para falha de aplicação, redeploy da última versão saudável; para migração destrutiva, não avançar sem backup e procedimento reversível.

O healthcheck da Railway é de ativação de deploy, não monitoramento contínuo. Um monitor externo de uptime pode ser adicionado antes da entrega final.

## Scheduler futuro

A Railway executará `python src/manage.py dispatch_due_schedules` a cada 15 minutos em UTC. O comando converte regras para `America/Sao_Paulo`, identifica o que venceu no PostgreSQL, aplica chave idempotente e publica no Redis. Se uma rodada anterior ainda estiver ativa, a plataforma pode omitir a seguinte; por isso o pulso deve terminar rápido e nunca executar a automação inteira.

Referências oficiais: [IaC](https://docs.railway.com/infrastructure-as-code), [Django](https://docs.railway.com/guides/django), [pre-deploy](https://docs.railway.com/deployments/pre-deploy-command), [healthchecks](https://docs.railway.com/deployments/healthchecks), [cron](https://docs.railway.com/cron-jobs) e [buckets](https://docs.railway.com/storage-buckets).
