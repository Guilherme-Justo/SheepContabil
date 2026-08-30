# Railway — infraestrutura em código

Este diretório usa a API TypeScript de infraestrutura em código da Railway. Ela está em beta; por isso, toda alteração deve passar por `plan` antes de `apply`.

## Fluxo seguro

1. Instale/autentique a Railway CLI e vincule o projeto.
2. Confirme o acesso da Railway à fonte `Guilherme-Justo/SheepContabil`, branch `main`, já declarada no arquivo.
3. Cadastre as senhas de demonstração em `web` e execute `seed_demo` explicitamente.
4. Execute `railway config plan` e revise recursos, variáveis preservadas, região e comandos.
5. Só então execute `railway config apply`.
6. Gere um domínio público apenas para `web`; `worker`, `scheduler`, bancos e bucket ficam privados.

O arquivo não inclui segredos. `preserve()` mantém valores já cadastrados na plataforma sem gravá-los no Git.

## Scheduler do Dia 2

O recurso `scheduler` executa `dispatch_due_schedules` a cada 15 minutos e apenas publica o SC-20 elegível no Redis. A competência mensal e a recuperação de falha anterior ao início são controladas no PostgreSQL; a função efêmera não contém a regra de negócio nem executa o processamento completo.

Referências: [Infrastructure as Code](https://docs.railway.com/infrastructure-as-code), [pre-deploy](https://docs.railway.com/deployments/pre-deploy-command), [healthchecks](https://docs.railway.com/deployments/healthchecks) e [cron jobs](https://docs.railway.com/cron-jobs).
