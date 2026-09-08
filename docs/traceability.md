# Contrato operacional de rastreabilidade

## Objetivo

Esta página orienta desenvolvimento, suporte e demonstração da trilha unificada. A fonte do estado
atual é `AutomationRun`; `AutomationRunEvent` responde como a execução chegou até ele.

## Cadeia de correlação

| Identificador | Criado em | Alcance | Exposição no portal |
| --- | --- | --- | --- |
| `run_id` | serviço de aplicação | toda a execução | todos os usuários autorizados ao módulo |
| `request_id` | middleware HTTP | uma requisição | somente administrador |
| `task_id` | antes da publicação no broker | uma entrega Celery | somente administrador |
| `pulse_id` | início do comando agendado | um pulso de scheduler e reconciliação | somente administrador |

Um `X-Request-ID` de entrada só é aceito se for UUID; qualquer valor ausente, inválido ou longo é
substituído. O UUID efetivo é devolvido no mesmo cabeçalho. O contexto é restaurado ao final da
requisição/tarefa/pulso para não vazar correlação entre trabalhos reutilizados pelo processo.

## Marcos esperados

| Camada | Marcos principais |
| --- | --- |
| Portal/serviço | criação, fila, retomada humana, cancelamento e conclusão síncrona |
| Publicação | início, confirmação do broker ou falha controlada |
| Worker | entrega recebida, início, entrega tratada ou falha |
| SC-04 | revisão necessária e estado consolidado do pipeline |
| SC-05 | etapa e tentativa por portal, compensação e estado terminal |
| SC-06 | criação/início, conclusão, cancelamento ou descarte preservado |
| SC-20 | tentativa pendente antes do gateway, confirmação ou resultado desconhecido |
| Reconciliador | republicação, quarentena, esgotamento ou falha de inspeção/publicação |

Heartbeats atualizam apenas a projeção `heartbeat_at`; eles não geram eventos periódicos e não
poluem a trilha. O enum reserva esse tipo apenas para uma eventual evidência excepcional com
deduplicação estável.

## Regras de escrita

- usar somente `record_run_event`;
- fornecer uma chave de deduplicação determinística por marco;
- gravar a transição na mesma transação da mudança de estado quando possível;
- repetir chave e conteúdo é seguro; repetir a chave com conteúdo diferente é erro;
- persistir apenas IDs opacos, contagens, enums e mensagens controladas;
- nunca persistir nome de cliente, CPF/CNPJ, e-mail, telefone, destinatário, respostas, texto de
  documento, corpo de mensagem, payload bruto, URL, chave de storage, segredo ou exceção bruta;
- não criar evento retroativo com base apenas no estado atual.

## Leitura e diagnóstico

1. Abra o detalhe da execução pelo portal.
2. Confirme se as sequências são contínuas e compare a última transição com o estado exibido.
3. Como administrador, use os IDs de correlação para filtrar os logs JSON do serviço correto.
4. Em SC-20, uma tentativa `PENDING` acompanhada de `integration_unknown` exige consulta ao
   provedor; não execute nova tentativa antes da conferência.
5. Em SC-05, `quarantined` ou `partially_failed` exige reconciliar os três portais antes da
   retomada explícita.

## Checklist de aceite

- [x] Modelo e migration aditivos, sem backfill inventado.
- [x] Sequência e deduplicação protegidas por constraints.
- [x] Update/delete/bulk bypass bloqueados pela aplicação.
- [x] Middleware HTTP, worker e scheduler com contexto isolado.
- [x] Logs JSON com correlação e metadados de implantação quando disponíveis.
- [x] Eventos ligados aos quatro processos, publicação e reconciliação.
- [x] Tentativa SC-20 durável antes do efeito externo.
- [x] Linha do tempo protegida pelo RBAC do módulo.
- [x] IDs técnicos restritos ao administrador.
- [x] Testes de segurança, idempotência, falha ambígua e ausência de histórico fabricado.
- [x] Migration validada no PostgreSQL do CI do
  [PR #44](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/34284759591).
- [ ] Deploy automático e smoke público validados após merge.
