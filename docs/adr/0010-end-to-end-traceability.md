# ADR-0010 — Rastreabilidade ponta a ponta por eventos imutáveis

- Estado: aceito
- Data: 2026-09-08
- Escopo: SC-04, SC-05, SC-06, SC-20, portal, worker, scheduler e reconciliador

## Contexto

`AutomationRun` conserva a projeção atual de uma execução e os quatro processos já possuem
evidências próprias. Isso, isoladamente, não permite reconstruir com precisão o caminho entre
uma ação no portal, a publicação no broker, a entrega ao worker, uma integração externa e a
reconciliação. Os logs também não tinham um contexto propagado de forma uniforme.

A rastreabilidade precisa sobreviver a novas tentativas e falhas parciais, respeitar o acesso por
área e não transformar dados pessoais, mensagens, documentos ou segredos em telemetria.

## Decisão

Adotamos `run_id` como correlação canônica de negócio e acrescentamos:

- `request_id`, um UUID validado ou gerado por requisição HTTP;
- `task_id`, a identidade persistida da entrega Celery;
- `pulse_id`, um UUID por pulso do scheduler;
- `AutomationRunEvent`, uma trilha sequencial, explícita e append-only por execução;
- contexto estruturado isolado por `ContextVar` para logs JSON;
- linha do tempo no detalhe da execução, sob a autorização já aplicada ao módulo.

`AutomationRun` continua sendo a projeção consultada para o estado atual. Os eventos são evidência
operacional e não constituem event sourcing. A versão não fabrica eventos para execuções antigas:
uma execução sem eventos é apresentada como anterior à trilha unificada.

Cada evento possui sequência por execução, tipo, origem, ator opcional, transição de estado,
identificadores opacos, etapa/tentativa, resultado, código de erro controlado, duração opcional,
mensagem operacional, detalhes minimizados e horário. As constraints impedem sequência ou chave
de deduplicação repetida na mesma execução. O gravador bloqueia a linha da execução para alocar a
próxima sequência e trata a repetição do mesmo conteúdo como idempotente; a mesma chave com
conteúdo divergente é um conflito explícito.

Atualização e exclusão de eventos são bloqueadas no modelo, queryset e administração Django.
Exclusão da execução ou do ator referenciado é protegida. Escritas em lote que contornariam a
validação também são rejeitadas. Mudanças de estado e o evento correspondente compartilham a
mesma transação sempre que a fronteira técnica permite.

## Minimização e autorização

Eventos não aceitam chaves ou valores reconhecidos como credenciais, tokens, URLs com senha,
e-mail, telefone, CPF/CNPJ, corpo, conteúdo extraído, respostas, payload bruto ou chave de
storage. O JSON é limitado em tamanho, profundidade, quantidade e tipos. Mensagens e códigos são
controlados pela aplicação; exceções brutas não são persistidas na trilha.

Operadores autorizados veem a sequência, mensagem, estado, origem, ator e horário. Somente o
administrador de negócio vê `request_id`, `task_id` e `pulse_id`. Conhecer o UUID de uma execução
continua insuficiente para atravessar o isolamento por área.

## Fronteira SC-20

Uma comunicação é dividida em três fases:

1. uma transação curta cria a comunicação e a tentativa `PENDING`;
2. o gateway externo é chamado fora da transação;
3. outra transação, ainda cercada pelo `task_id`, confirma `SENT` ou `FAILED`.

Se o processo for interrompido após o provedor aceitar e antes da confirmação local, a tentativa
permanece `PENDING`, um resultado desconhecido pode ser registrado e nenhum reenvio automático é
feito. A execução é isolada para conferência. Essa escolha evita duplicidade em SMTP, que não
oferece confirmação idempotente transacional com o PostgreSQL.

## Consequências

### Positivas

- caminho auditável e correlacionado entre todos os processos de execução;
- eventos idempotentes e ordenados mesmo quando uma entrega é repetida;
- diagnóstico técnico por logs sem expor identificadores internos a todos os operadores;
- evidência durável da janela ambígua de comunicação do SC-20;
- evolução incremental sem reescrever os modelos funcionais específicos.

### Custos e limites

- cada marco relevante acrescenta uma escrita curta no PostgreSQL;
- imutabilidade é aplicada pela aplicação e pelas relações/constraints, não por trigger SQL;
- não há spans distribuídos, OpenTelemetry ou plataforma externa de observabilidade nesta etapa;
- uma morte abrupta entre um efeito externo e qualquer instrução seguinte continua sem solução
  universal; para eliminá-la será necessário um provedor idempotente ou outbox/inbox compatível.

## Alternativas rejeitadas

- reconstruir toda a execução somente a partir de logs efêmeros;
- usar signals Django, que não capturam corretamente ator, origem e atualizações por queryset;
- transformar o domínio em event sourcing durante uma correção incremental;
- inventar backfill de eventos a partir do estado final de execuções antigas;
- reenviar automaticamente uma comunicação `PENDING` do SC-20.
