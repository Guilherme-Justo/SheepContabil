# ADR-0002 — PostgreSQL como fonte de verdade

- **Status:** Aceita
- **Data:** 2026-08-27
- **Decisores:** Engenharia/Arquitetura SheepContabil

## Contexto

O portal precisa manter autenticação, permissões, execuções, etapas, auditoria, regras configuráveis, resultados e estado parcial. Redis e o result backend de Celery não oferecem o modelo relacional, as constraints e a durabilidade adequados a esse histórico.

## Decisão

Usar PostgreSQL gerenciado como única fonte de verdade para dados funcionais e operacionais.

Convenções:

- UUID para entidades expostas;
- timestamps persistidos em UTC;
- constraints e índices para invariantes e idempotência;
- migrations por módulo Django;
- transações nos limites dos casos de uso locais;
- JSONB somente para snapshots, metadados e estruturas genuinamente variáveis;
- trilha de auditoria append-only para eventos relevantes;
- arquivos binários fora do banco.

Redis será broker/cache efêmero. Estados oficiais de execução permanecem em `AutomationRun` e `AutomationRunStep`.

## Consequências positivas

- consistência transacional local;
- consultas e dashboards simples;
- constraints contra duplicidade;
- backup e restauração conhecidos;
- suporte natural pelo ORM Django;
- perda de Redis não apaga histórico.

## Consequências negativas

- módulos compartilham a mesma instância física;
- migrations precisam ser coordenadas com deploy;
- consultas inadequadas podem criar acoplamento entre módulos;
- JSONB precisa de disciplina para não substituir modelagem.

## Alternativas consideradas

### SQLite

Aceitável somente em ferramentas isoladas, não no ambiente integrado. Rejeitado em produção por concorrência, operação e divergência do ambiente real.

### Banco por módulo

Rejeitado por criar consistência distribuída e custo operacional sem benefício no prazo.

### Redis como histórico de jobs

Rejeitado porque fila e expiração não equivalem a auditoria durável.

### Armazenar arquivos como BLOB

Rejeitado por aumentar backup, tráfego e tamanho do banco sem necessidade.

## Critérios de revisão

Reavaliar separação física somente diante de isolamento regulatório, volume desproporcional ou ciclo de vida independente comprovado de um módulo.

