# Railway — infraestrutura em código

Este diretório usa a API TypeScript de infraestrutura em código da Railway. Ela está em beta; por isso, toda alteração deve passar por `plan` antes de `apply`.

## Fluxo seguro

1. Instale/autentique a Railway CLI e vincule o projeto.
2. Confirme o acesso da Railway à fonte `Guilherme-Justo/SheepContabil`, branch `main`, já declarada no arquivo.
3. Cadastre as senhas distintas dos operadores em `web` e mantenha `SC05_SIMULATOR_USERNAME`/`SC05_SIMULATOR_PASSWORD` somente no `worker`.
4. Execute `railway config plan` e revise recursos, variáveis preservadas, região e comandos.
5. Só então execute `railway config apply`.
6. Gere um domínio público apenas para `web`; `worker`, `scheduler`, bancos e bucket ficam privados.

O arquivo não inclui segredos. `preserve()` mantém valores já cadastrados na plataforma sem gravá-los no Git.

## Scheduler e simulador SC-05

O recurso `scheduler` executa `dispatch_due_schedules` a cada 15 minutos e publica no Redis o SC-04 diário e o SC-20 mensal elegíveis. Competência, janela horária e recuperação de falha anterior ao início são controladas no PostgreSQL; a função efêmera não contém a regra de negócio nem executa o processamento completo.

O plano Railway disponível não comporta um quarto serviço de aplicação. Por isso, a IaC declara somente `web`, `worker` e `scheduler`; ela não cria um recurso `simulator`. O Compose local continua com o simulador em contêiner separado para conservar a fronteira de processo durante desenvolvimento e testes.

Na Railway, `scripts/run_worker_with_simulator.sh` inicia dois processos no mesmo contêiner do `worker`: o WSGI sintético na porta `8000` e, somente depois da liveness local, o Celery worker. O Playwright usa `http://127.0.0.1:8000`; a Railway usa `/health/ready` pela rede privada para promover o deploy após a conferência do schema, a liveness do simulador, a consulta ao banco e a sobrevivência inicial do processo Celery. A capacidade real de consumir a fila é validada separadamente pelo smoke funcional. O WSGI mantém settings, URLconf e entrypoint próprios e não possui domínio nem porta pública.

O subprocesso do simulador nasce com ambiente explicitamente sanitizado. Ele recebe apenas runtime Python, settings próprios, segredo Django, fuso, PostgreSQL e as credenciais sintéticas necessárias; `REDIS_URL`, credenciais S3 e `OPENAI_API_KEY`/`OPENAI_MODEL` não são propagados. Se Celery ou WSGI encerrar, o supervisor termina o outro processo e deixa a Railway reiniciar o serviço inteiro, evitando um worker aparentemente saudável sem sua fronteira RPA.

As fontes GitHub de `web`, `worker` e `scheduler` mantêm `checkSuites: true`; portanto, **Wait for CI** continua obrigatório nos três. A versão 0.5.0 passou pelo PR `#5`; a co-localização passou pelo PR `#6`, pelos CIs do PR e de `main`, por deploy automático nos três serviços e por smoke test ponta a ponta. O SC-05 está operacional no ambiente demonstrativo, com Playwright em loopback e sem domínio público para o WSGI sintético.

Referências: [Infrastructure as Code](https://docs.railway.com/infrastructure-as-code), [pre-deploy](https://docs.railway.com/deployments/pre-deploy-command), [healthchecks](https://docs.railway.com/deployments/healthchecks) e [cron jobs](https://docs.railway.com/cron-jobs).
