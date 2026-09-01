# ADR-0008 — Railway como plataforma de hospedagem

- **Status:** Aceita; emendada em 2026-09-01 por limite do plano
- **Data:** 2026-08-27
- **Decisores:** Engenharia/Arquitetura SheepContabil

## Contexto

A entrega precisa de URL pública estável em uma semana e deve manter web, worker, scheduler, PostgreSQL, Redis, storage e simuladores. Montar rede, IAM, balanceamento e observabilidade diretamente em um hyperscaler consumiria tempo sem aumentar a completude dos processos. O plano Railway disponível, contudo, não comporta um quarto serviço de aplicação dedicado ao simulador SC-05.

## Decisão

Hospedar a entrega em um projeto Railway, com:

- `web` público por HTTPS;
- `worker` privado, com Celery e o WSGI sintético do SC-05 co-localizados no mesmo contêiner;
- `cron` privado e efêmero, com pulso de 15 minutos;
- PostgreSQL gerenciado;
- Redis;
- storage/bucket compatível com S3.

Web, worker e cron são construídos do mesmo commit/imagem e usam comandos distintos. Migrations rodam em etapa de release/pre-deploy. Healthcheck de readiness condiciona a ativação do web. As três fontes GitHub usam **Wait for CI**.

No worker, um supervisor aguarda o schema, inicia `config.simulator_wsgi`, confirma liveness, inicia Celery e só então libera readiness para a Railway. O Playwright usa loopback; a plataforma alcança a porta apenas pela rede privada para healthcheck, sem domínio público. O subprocesso recebe uma allowlist mínima de ambiente sem herdar Redis, S3 nem OpenAI; segredo Django e credenciais SC-05 existem apenas nas variáveis do worker. A saída de qualquer processo encerra o outro e permite reinício conjunto pela plataforma. O Compose local preserva o simulador em contêiner separado.

A URL inicial será o domínio estável gerado pela Railway. Domínio próprio só será configurado se já existir domínio controlado; sua compra não é requisito.

O ambiente usará recursos sem suspensão automática e permanecerá ativo até o encerramento da avaliação. Segredos ficam nas variáveis da plataforma; banco, Redis e simulador não recebem domínio público. O Playwright usa loopback, enquanto a porta privada do simulador existe somente para o healthcheck autenticado e interno da plataforma.

## Consequências positivas

- menor tempo de infraestrutura;
- deploy de contêiner e serviços gerenciados em um único projeto;
- HTTPS e domínio público simples;
- rede privada entre componentes;
- caminho compatível com Docker, PostgreSQL, Redis e S3.
- preservação da fronteira HTTP/HTML do RPA sem consumir um quarto serviço do plano.

## Consequências negativas

- dependência de um PaaS e seus limites;
- custo precisa ser monitorado;
- alguns controles de rede/infra são menos granulares que em hyperscaler;
- disponibilidade depende da conta e dos recursos permanecerem ativos.
- worker e simulador compartilham escala, reinício, CPU e memória na Railway demonstrativa;
- a co-localização é uma concessão operacional do plano, não o alvo preferido para produção real.

## Alternativas consideradas

### AWS com ECS/Fargate, RDS, ElastiCache e S3

Arquitetura válida para produção, rejeitada para a semana por IAM, rede, múltiplos recursos e tempo operacional.

### Kubernetes

Rejeitado por não haver escala, equipe ou requisito que justifique um cluster.

### Vercel/serverless

Rejeitado como plataforma principal porque worker Celery, scheduler, Playwright e jobs longos não se encaixam naturalmente no ciclo serverless.

### Túnel ou execução local

Rejeitados por não satisfazer a URL pública permanente.

## Critérios de revisão

Separar o simulador em serviço próprio assim que o limite do plano deixar de existir ou quando escala, segurança ou isolamento de falha justificarem o recurso. Migrar quando SLA, compliance, residência de dados, rede corporativa ou escala exigirem controles não oferecidos pelo PaaS. A portabilidade é preservada por contêineres e protocolos padrão.
