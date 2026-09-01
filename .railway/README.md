# Railway — infraestrutura em código

Este diretório usa a API TypeScript de infraestrutura em código da Railway. Ela está em beta; por isso, toda alteração deve passar por `plan` antes de `apply`.

## Fluxo seguro

1. Instale/autentique a Railway CLI e vincule o projeto.
2. Confirme o acesso da Railway à fonte `Guilherme-Justo/SheepContabil`, branch `main`, já declarada no arquivo.
3. Cadastre as senhas distintas dos operadores em `web`; no `simulator`, configure um `DJANGO_SECRET_KEY` próprio e as credenciais sintéticas repetidas somente no `worker`.
4. Execute `railway config plan` e revise recursos, variáveis preservadas, região e comandos.
5. Só então execute `railway config apply`.
6. Gere um domínio público apenas para `web`; `worker`, `scheduler`, `simulator`, bancos e bucket ficam privados.

O arquivo não inclui segredos. `preserve()` mantém valores já cadastrados na plataforma sem gravá-los no Git.

## Scheduler e simulador

O recurso `scheduler` executa `dispatch_due_schedules` a cada 15 minutos e publica no Redis o SC-04 diário e o SC-20 mensal elegíveis. Competência, janela horária e recuperação de falha anterior ao início são controladas no PostgreSQL; a função efêmera não contém a regra de negócio nem executa o processamento completo.

O `simulator` expõe somente os portais HTML do SC-05 na rede privada, com settings e WSGI próprios. Ele usa `PORT=8000`, não recebe Redis, S3 nem OpenAI e não deve possuir domínio público. O worker acessa `http://simulator.railway.internal:8000` com as mesmas credenciais sintéticas cadastradas nos dois serviços.

Referências: [Infrastructure as Code](https://docs.railway.com/infrastructure-as-code), [pre-deploy](https://docs.railway.com/deployments/pre-deploy-command), [healthchecks](https://docs.railway.com/deployments/healthchecks) e [cron jobs](https://docs.railway.com/cron-jobs).
