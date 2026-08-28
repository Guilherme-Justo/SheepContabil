# ADR-0008 — Railway como plataforma de hospedagem

- **Status:** Aceita
- **Data:** 2026-08-27
- **Decisores:** Engenharia/Arquitetura SheepContabil

## Contexto

A entrega precisa de URL pública estável em uma semana e deve manter web, worker, scheduler, PostgreSQL, Redis, storage e simuladores. Montar rede, IAM, balanceamento e observabilidade diretamente em um hyperscaler consumiria tempo sem aumentar a completude dos processos.

## Decisão

Hospedar a entrega em um projeto Railway, com:

- `web` público por HTTPS;
- `worker` privado;
- `cron` privado e efêmero, com pulso de 15 minutos;
- `simulator` privado;
- PostgreSQL gerenciado;
- Redis;
- storage/bucket compatível com S3.

Web, worker e cron são construídos do mesmo commit/imagem e usam comandos distintos. Migrations rodam em etapa de release/pre-deploy. Healthcheck de readiness condiciona a ativação do web.

A URL inicial será o domínio estável gerado pela Railway. Domínio próprio só será configurado se já existir domínio controlado; sua compra não é requisito.

O ambiente usará recursos sem suspensão automática e permanecerá ativo até o encerramento da avaliação. Segredos ficam nas variáveis da plataforma; banco, Redis e simulador não recebem domínio público.

## Consequências positivas

- menor tempo de infraestrutura;
- deploy de contêiner e serviços gerenciados em um único projeto;
- HTTPS e domínio público simples;
- rede privada entre componentes;
- caminho compatível com Docker, PostgreSQL, Redis e S3.

## Consequências negativas

- dependência de um PaaS e seus limites;
- custo precisa ser monitorado;
- alguns controles de rede/infra são menos granulares que em hyperscaler;
- disponibilidade depende da conta e dos recursos permanecerem ativos.

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

Migrar quando SLA, compliance, residência de dados, rede corporativa ou escala exigirem controles não oferecidos pelo PaaS. A portabilidade é preservada por contêineres e protocolos padrão.
