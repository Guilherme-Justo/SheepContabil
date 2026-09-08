# ADR-0003 — Celery e Redis para execução assíncrona e agendamento

- **Status:** Aceita
- **Data:** 2026-08-27
- **Decisores:** Engenharia/Arquitetura SheepContabil

## Contexto

SC-04 processa arquivos, SC-05 executa navegador e SC-20 dispara comunicações. Essas operações podem exceder uma requisição web, falhar transitoriamente e exigir execução periódica. O portal também deve responder rapidamente e exibir progresso/histórico.

## Decisão

Usar Celery com Redis como broker e um Railway Cron como pulso efêmero para periodicidade.

Topologia:

- web cria a execução e publica após commit;
- worker consome e chama o caso de uso;
- um pulso de 15 minutos consulta no PostgreSQL e publica SC-04 e SC-20 quando vencidos;
- PostgreSQL guarda estado oficial, etapas e resultado;
- HTMX consulta o progresso por polling.

Políticas:

- chave de idempotência por comando/período;
- identificador UUID de entrega persistido antes da publicação e usado como fencing token no worker;
- `acks_late` e redelivery após perda do worker, sem retentativa automática da tarefa inteira;
- publicação fora da transação do banco e atualização condicional em falha do broker;
- início e confirmação da publicação persistidos separadamente, para que backlog confirmado não seja confundido com mensagem órfã;
- heartbeat persistido e tempos ordenados como limite brando < limite rígido < visibilidade do Redis < corte de órfão;
- reconciliação limitada a uma recuperação automática por padrão, com histórico preservado nos metadados;
- SC-04 pode retomar trabalho interrompido depois de fechar a tentativa anterior;
- SC-05 enfileirado pode recuperar uma publicação não confirmada, mas uma execução já iniciada fica parcial e exige reinspeção manual dos três portais;
- SC-20 já iniciado nunca é reenviado automaticamente, pois uma comunicação aceita e não confirmada no banco é ambígua;
- entregas antigas ou substituídas são no-op e conclusões usam comparação de estado e identificador;
- mesma entrada de aplicação para disparo manual e agendado;
- o pulso reconcilia um lote limitado antes de publicar novas competências;
- o processo do cron deve terminar após publicar os trabalhos vencidos;
- regras de horário ficam no domínio, em `America/Sao_Paulo`, e não no cron UTC.

## Consequências positivas

- requisições web curtas;
- retentativa e agendamento maduros;
- worker escalável separadamente do web;
- falhas e duração visíveis por execução;
- mesmo mecanismo para IA, RPA e notificações.

## Consequências negativas

- Redis e dois processos adicionais precisam ser operados;
- entrega de mensagem pode ocorrer mais de uma vez;
- código precisa ser explicitamente idempotente;
- indisponibilidade do broker exige reconciliação.
- uma interrupção do SC-20 após o início exige conferência humana até existir outbox transacional com confirmação do provedor.

## Alternativas consideradas

### Execução síncrona na requisição

Rejeitada por timeout, experiência ruim e impossibilidade de recuperação robusta.

### Cron dentro do web

Rejeitado porque réplicas e redeploys causam omissões ou duplicidades sem controle.

### Kafka

Rejeitado por complexidade desproporcional ao volume e prazo.

### Estado apenas no result backend Celery

Rejeitado porque não satisfaz histórico de negócio e auditoria.

## Critérios de revisão

Reavaliar o broker e a topologia se volume, prioridade, garantia de entrega ou isolamento de cargas ultrapassarem a capacidade operacional do Redis/Celery.
