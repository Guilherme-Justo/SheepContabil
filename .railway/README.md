# Railway — infraestrutura em código

Este diretório usa a API TypeScript de infraestrutura em código da Railway. Ela está em beta; por isso, toda alteração deve passar por `plan` antes de `apply`.

## Fluxo seguro

1. Instale/autentique a Railway CLI e vincule o projeto.
2. Confirme o acesso da Railway à fonte `Guilherme-Justo/SheepContabil`, branch `main`, já declarada no arquivo.
3. Cadastre as senhas de demonstração em `web` e execute `seed_demo` explicitamente.
4. Execute `railway config plan` e revise recursos, variáveis preservadas, região e comandos.
5. Só então execute `railway config apply`.
6. Gere um domínio público apenas para `web`; `worker`, bancos e bucket ficam privados.

O arquivo não inclui segredos. `preserve()` mantém valores já cadastrados na plataforma sem gravá-los no Git.

## Limite do Dia 1

O cron efêmero de 15 minutos será adicionado quando o comando de domínio `dispatch_due_schedules` existir. Publicar um cron que apenas simule a automação faria o deploy parecer mais completo do que está.

Referências: [Infrastructure as Code](https://docs.railway.com/infrastructure-as-code), [pre-deploy](https://docs.railway.com/deployments/pre-deploy-command), [healthchecks](https://docs.railway.com/deployments/healthchecks) e [cron jobs](https://docs.railway.com/cron-jobs).
