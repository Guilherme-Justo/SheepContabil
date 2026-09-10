# Changelog

As mudanças relevantes do SheepContabil são registradas neste arquivo. O projeto segue
[Versionamento Semântico](https://semver.org/lang/pt-BR/) a partir da release estável `1.0.0`.

## [Não publicado]

### Adicionado

- Comando `prepare_demo`, somente leitura por padrão, para conferir o estado operacional dos
  quatro processos, validar o PDF do SC-06 e localizar evidências funcionais de contingência.
- Ciclos sintéticos próprios no SC-04 para permitir novos ensaios sem apagar documentos ou
  confundir idempotência entre lotes.
- Massa oficial de certificado com e-mail e WhatsApp para demonstrar o duplo contato do SC-20.

### Alterado

- `seed_demo` preserva credenciais e datas já persistidas por padrão; rotações e
  reposicionamentos exigem opções explícitas.
- Guia de demonstração acompanha os controles, rótulos, rotas e estados realmente exibidos pelo
  portal e exige execuções funcionais com evidências filhas.
- Os três serviços de aplicação Railway compartilham uma única constante de região e continuam
  condicionados ao CI verde da branch `main`.

### Segurança

- Downloads PDF do SC-06 passam a declarar cache privado e sem armazenamento, preservando o RBAC,
  o download como anexo e a proteção contra inferência de conteúdo.
- O preparo operacional nunca apaga execuções, eventos, comunicações, briefings ou artefatos e
  recusa estados SC-05 ativos ou divergentes.

## [1.0.0] — 2026-09-09

Primeira release estável da entrega do desafio Sheep Technology. Consolida os quatro processos
selecionados — SC-04, SC-05, SC-06 e SC-20 — e as três naturezas exigidas: agente de IA, RPA e
controle sistematizado.

### Adicionado

- Trilha imutável de eventos de execução para os quatro processos.
- Correlação ponta a ponta por `run_id`, `request_id`, `task_id` e `pulse_id`.
- Linha do tempo operacional no portal com IDs técnicos restritos a administradores.
- Reconciliação limitada de execuções assíncronas órfãs e cercamento de entregas Celery antigas.
- Contrato de migration executado em PostgreSQL com dados preexistentes no CI.

### Alterado

- Promoção de `web`, `worker` e `scheduler` permanece condicionada ao CI da `main` na Railway.
- Scheduler mensal do SC-20 exige módulo habilitado, data-base correta e horário elegível.
- IaC preserva explicitamente a variável de horário diário do SC-04 existente na Railway.
- Logs estruturados incluem serviço, ambiente, release, ator, sequência e IDs de correlação.

### Corrigido

- Execuções interrompidas deixam de permanecer indefinidamente em estados assíncronos órfãos.
- Rascunhos vazios cancelados do SC-06 deixam de ser interpretados como histórico fabricado.
- Workers atrasados não podem finalizar uma execução cuja entrega persistida já foi substituída.
- Tentativas ambíguas de comunicação do SC-20 ficam em quarentena e não são reenviadas
  automaticamente.

### Segurança

- Metadados de eventos passam por sanitização e allowlist para impedir persistência de respostas,
  documentos, mensagens e credenciais sensíveis na trilha de execução.
- Falhas do gateway SMTP deixam de registrar destinatários ou exceções brutas do provedor em logs
  e mensagens operacionais.
- O RBAC da trilha segue o módulo de origem; acessos de outra área retornam `404`.
- O SC-20 persiste a tentativa antes de chamar o gateway, preservando incerteza de entrega sem
  duplicação automática.

### Compatibilidade e dados

- A migration `automations.0010_automation_run_event` é aditiva e não inventa eventos para
  execuções históricas.
- Não há breaking change intencional nas URLs públicas ou nos contratos operacionais da versão
  `0.19.1`.

[Não publicado]: https://github.com/Guilherme-Justo/SheepContabil/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Guilherme-Justo/SheepContabil/compare/v0.19.1...v1.0.0
