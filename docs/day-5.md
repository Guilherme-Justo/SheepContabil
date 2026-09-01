# Dia 5 — SC-05 de ponta a ponta

| Campo | Valor |
| --- | --- |
| Data | 2026-09-01 |
| Versão | 0.5.0 |
| Processo | SC-05 — Bloqueio e desbloqueio de clientes inadimplentes |
| Natureza preservada | RPA |
| Frequência | Sob demanda |
| Área | Tecnologia |
| Estado local | Implementado; 122 testes e gates locais disponíveis aprovados |
| Estado externo | Não publicado nem validado em produção nesta versão |

## Resultado entregue

Uma única ação no módulo Tecnologia cria uma execução auditável e envia ao worker somente seu UUID. O worker abre um navegador Chromium real, autentica em três portais HTML sintéticos, lê o estado visível, executa o bloqueio ou desbloqueio na ordem definida e confirma o resultado em cada sistema. A página da execução apresenta situação geral, duração, ator, etapa por portal, estado antes/desejado/depois, tentativas, erros operacionais e screenshots privados.

O núcleo não é simulado. Validação, idempotência, concorrência, orquestração, snapshots, compensação, retomada, persistência e autorização são regras reais. A única substituição é a fronteira externa: os sistemas sem acesso no desafio são representados por páginas HTML privadas e substituíveis, operadas exclusivamente pelo navegador.

## Requisitos do desafio preservados

- O fluxo é manual e sob demanda, sem agendamento artificial para o SC-05.
- Uma ação do portal dispara a sequência completa em múltiplos sistemas.
- O histórico mostra o que aconteceu em cada portal, com resultado compreensível e evidência visual.
- O mesmo módulo permite desbloquear quando houver renegociação.
- No sistema de tarefas, o cliente **não é desativado**.
- Somente tarefas abertas recebem um marcador de inadimplência; tarefas fechadas e histórico permanecem intactos.
- O desbloqueio recupera os responsáveis anteriores exatamente, sem escolher um usuário genérico.
- Falha parcial é explícita e pode ser retomada por ação humana autorizada.
- Sessão, CSRF e RBAC continuam aplicados no backend para operação, histórico e arquivos.

## Arquitetura implementada

```text
Operador Tecnologia / Administrador
                 │ POST + CSRF
                 ▼
        Portal Django SheepContabil
                 │ cria AutomationRun + saga
                 │ publica somente após commit
                 ▼
              Redis / Celery
                 │ UUID da execução
                 ▼
       Worker + Playwright Chromium
                 │ login e HTML visível
        ┌────────┼─────────┐
        ▼        ▼         ▼
    Arquivos  Contábil  Tarefas     serviço privado sintético
        │        │         │
        └────────┼─────────┘
                 │ snapshots + tentativas
        ┌────────┴────────┐
        ▼                 ▼
   PostgreSQL       Storage S3 privado
                    screenshots PNG
```

Web, worker e scheduler permanecem processos do mesmo monólito modular. O `simulator` possui URLconf e entrypoint WSGI próprios e representa apenas os três sistemas externos. Ele não contém a saga, não oferece atalho de integração ao worker e não precisa de URL pública.

## Ordem, pré-condições e compensações

### Bloqueio

| Ordem | Portal | Pré-condição | Mutação desejada | Estado preservado |
| ---: | --- | --- | --- | --- |
| 1 | Portal de arquivos | Cliente identificado de forma única | Conta bloqueada | Booleano observado antes da ação |
| 2 | Sistema contábil | Cliente identificado de forma única | Conta bloqueada | Booleano observado antes da ação |
| 3 | Sistema de tarefas | Cliente ativo e estrutura de tarefas válida | Responsável das tarefas abertas = `BLOQUEADO_INADIMPLENCIA` | Cliente ativo, referências, abertura/fechamento e responsáveis de todas as tarefas |

### Desbloqueio

| Ordem | Portal | Pré-condição | Mutação desejada | Proteção |
| ---: | --- | --- | --- | --- |
| 1 | Sistema de tarefas | Projeção bloqueada e snapshot anterior disponível | Restauração exata dos responsáveis | Ausência de snapshot recusa a operação |
| 2 | Sistema contábil | Snapshot do bloqueio anterior disponível | Restaura o booleano anterior | Não remove bloqueio que já existia antes do SC-05 |
| 3 | Portal de arquivos | Snapshot do bloqueio anterior disponível | Restaura o booleano anterior | Não remove bloqueio que já existia antes do SC-05 |

O desbloqueio usa a ordem inversa para liberar primeiro o trabalho operacional e depois as contas. Em qualquer falha, a compensação percorre em sentido inverso somente as etapas que chegaram a capturar estado e produziram uma mudança conhecida.

O snapshot do bloqueio anterior também protege o undo tardio: Arquivos e Contábil retornam ao booleano que possuíam antes da saga, e não obrigatoriamente a “ativo”. Em Tarefas, somente as referências efetivamente alteradas recebem o responsável original; uma tarefa que fechou depois do bloqueio continua fechada e uma tarefa nova com responsável normal é preservada. Referência ausente, novo marcador sem snapshot ou responsável modificado por terceiro produz conflito seguro.

Antes de restaurar, o robô inspeciona o portal novamente. Ele só aplica a compensação quando o estado atual ainda é exatamente o estado desejado produzido pela saga. Se outra pessoa ou integração tiver alterado o sistema, a divergência é preservada e o caso fica parcial para reconciliação; a automação não sobrescreve uma mudança externa silenciosamente.

## Estados e semântica de falha

| Resultado | Estado comum | Projeção do cliente | Interpretação |
| --- | --- | --- | --- |
| Todos os portais confirmados | `SUCCEEDED` | `blocked` ou `active` conforme a ação | Sequência completa e verificada |
| Falha seguida de restauração integral | `FAILED` | Estado anterior | A ação não concluiu, mas não deixou resíduo conhecido |
| Mutação ou compensação pendente | `PARTIALLY_FAILED` | `partial` | Pelo menos um portal requer retomada/reconciliação |
| Marcador de tarefa sem snapshot confiável | `FAILED` | `unknown` | Impede falso sucesso e exige reconciliação humana |

A retomada é deliberadamente explícita e permitida somente para `PARTIALLY_FAILED`. A falha demonstrativa original permanece no histórico, enquanto a nova tentativa executa o fluxo normal; o contador e o evento de retomada são persistidos e os portais são reinspecionados. Um portal que já estiver conforme é marcado sem novo clique. Se a compensação da nova tentativa for integral, a projeção do cliente volta ao estado anterior em vez de ficar presa em `partial`.

Redelivery do broker também é tratada: tentativas que ficaram `RUNNING` são encerradas como interrompidas e etapas presas são reconciliadas a partir do estado externo observado. Uma redelivery de caso já `PARTIALLY_FAILED` não contorna a retomada explícita. Falha ao publicar uma retomada preserva `PARTIALLY_FAILED`, permitindo tentar novamente; falha no primeiro despacho termina a execução sem deixá-la eternamente pendente.

## Domínio e evidências

- `SC05Client` mantém a projeção operacional do cliente e o snapshot usado na restauração das tarefas.
- `SC05Operation` liga exatamente uma saga ao histórico comum `AutomationRun`, com ação, cenário e número de retomadas.
- `SC05PortalStep` conserva posição, portal, estado atual, snapshots antes/desejado/depois, horários e erro seguro.
- `SC05StepAttempt` registra, em sequência, inspeção, aplicação ou compensação. Uma tentativa finalizada rejeita edição e exclusão pela instância; o admin também bloqueia criação, edição e exclusão dessas evidências.
- `SC05Artifact` liga uma captura PNG a uma tentativa, com chave privada, SHA-256, tipo e tamanho.

Constraints impedem portal ou posição duplicados na mesma operação, sequência duplicada na etapa e combinações inconsistentes entre estado, erro e horário da tentativa. A operação comum usa chave de idempotência derivada do token do formulário; o cliente é bloqueado no PostgreSQL durante a criação e não aceita duas sagas ativas simultâneas.

O storage limita cada screenshot a 5 MiB, usa escrita condicional e só aceita colisão quando tamanho e hash do objeto existente conferem. O download recalcula SHA-256 e tamanho, passa novamente pela política de visibilidade do módulo e responde como PNG com `private, no-store`, `nosniff` e mesma origem. Chave do bucket e credenciais não aparecem no HTML.

## RPA e simulador

Uma sessão `PlaywrightPortalSession` mantém um navegador, um contexto e uma página durante toda a saga. Como o adapter usa a API síncrona do Playwright, suas chamadas são serializadas em uma thread dedicada; o acesso ORM continua no contexto normal do worker. Essa separação resolveu a incompatibilidade entre o event loop interno do navegador e as proteções assíncronas do Django sem criar um navegador por portal.

Cada gateway:

1. navega para a página do portal;
2. localiza o cliente por `data-testid` estável;
3. lê o estado apresentado no DOM;
4. aciona o formulário visível de bloquear/desbloquear;
5. aguarda o carregamento;
6. lê novamente o estado;
7. captura somente o cartão do cliente operado; em falha, captura o alerta de erro.

Autenticação inválida, timeout, resposta 5xx, seletor ausente, cliente não único e estado inesperado viram categorias seguras do SC-05. Nenhuma delas expõe stack trace ou segredo ao usuário.

O simulador implementa:

- conta ativa/bloqueada para Arquivos e Contábil;
- estado ativo/inativo explícito e visível do cliente no portal de Tarefas, nunca alterado pelas ações;
- `previous_assignee` independente para restauração;
- bloqueio e desbloqueio idempotentes;
- validação completa de todos os backups antes de iniciar um desbloqueio de tarefas;
- falha antes da mutação em Tarefas;
- timeout antes da mutação no Contábil;
- falha combinada em Tarefas e na compensação de Arquivos.

Os cenários de falha são determinísticos e exclusivos do administrador de negócio. Eles existem para demonstrar resiliência, não para mascarar falta de automação.

## Portal, identidade e RBAC

O módulo segue o shell, tipografia, cores, componentes responsivos e mensagens da identidade SheepContabil. A página principal apresenta totais de clientes ativos, bloqueados e parciais, formulário de ação, regra especial de Tarefas e histórico recente. A página comum da execução foi estendida com timeline por portal, snapshots legíveis, tentativas e links de evidência.

O operador da área Tecnologia vê e executa o SC-05 normal. Operadores de outras áreas recebem `404` tanto no módulo quanto em execução, retomada e screenshot. O administrador acessa os quatro módulos e pode escolher os cenários controlados de falha. Toda mutação usa `POST` e CSRF; conhecer um UUID ou uma URL de artefato não contorna a autorização.

## Massa demonstrativa

O seed idempotente acrescenta `operador.tecnologia`, três clientes SC-05, duas contas de serviço por cliente e seis tarefas sintéticas. Há tarefas abertas e fechadas, com responsáveis distintos, para tornar visível que o robô muda somente as abertas e depois restaura os valores exatos.

Reexecutar o seed não redefine bloqueios nem responsáveis já alterados pelo fluxo. Assim, uma demonstração em andamento não é revertida silenciosamente por preparação de dados.

## Evidências locais registradas

Até este documento, `37` testes focados do SC-05 foram aprovados:

- `12` testes de domínio para ordem, idempotência, bloqueio, desbloqueio tardio, snapshots anteriores, compensação completa, falha parcial, retomada explícita e divergência externa;
- `11` testes do simulador para autenticação limitada, Unicode, constraints, idempotência, atomicidade, cliente ativo, falhas e contrato DOM;
- `9` testes de view para RBAC, despacho idempotente, cenários administrativos, retomada, integridade do artefato e falha inicial ou durante retomada do broker;
- `4` testes de contrato que iniciam servidor real e usam Chromium nos três portais, cobrindo bloqueio, desbloqueio, evidência da página de erro e falha parcial seguida de retomada sem novo clique no portal já conforme;
- `1` teste isolado do WSGI privado, URLconf e política HTTP interna.

No estado final local, a suíte completa aprovou `122` testes com cobertura total de `83,83%`, acima do piso obrigatório de `75%`. Os `37` testes focados descritos acima também passaram. Ruff, verificação de formatação, Mypy, checks Django, ausência de migrations pendentes, build de assets e validação da configuração Compose estão verdes.

O único gate de build que não pôde ser reproduzido localmente foi a imagem `0.5.0`, porque o Docker Desktop estava desligado e o daemon Linux não estava disponível. Isso não representa falha conhecida da imagem: o job `Container build` do CI deve construí-la sobre o mesmo commit e permanece obrigatório antes do merge.

O plano Railway foi validado sem aplicação: `1` recurso a criar, `10` ajustes e `0` remoções. A criação corresponde ao `simulator`; os ajustes acrescentam apenas variáveis declaradas ao web/worker e as referências do SC-05. `checkSuites: true` ficou explícito nas quatro fontes GitHub para preservar o bloqueio **Wait for CI**. Os smoke tests públicos continuam pendentes.

## Publicação ainda pendente

Não há evidência de produção da versão 0.5.0 neste documento. Antes de declarar o Dia 5 publicado, ainda é necessário:

1. obter CI verde da branch, incluindo o build da imagem de produção que ficou indisponível localmente;
2. criar ou reconciliar o serviço privado `simulator` na Railway;
3. configurar no simulador e no worker as mesmas credenciais sintéticas;
4. confirmar a URL privada `http://simulator.railway.internal:8000` a partir do worker;
5. aplicar a migration do SC-05 e executar o seed idempotente uma vez;
6. confirmar deploy automático de `main` condicionado ao CI para `web`, `worker`, `scheduler` e `simulator`;
7. executar smoke tests de bloqueio, desbloqueio, compensação parcial, retomada e download autorizado de screenshot;
8. registrar IDs de PR, CI, deploy e execuções somente depois que existirem.

A URL pública já comprovada continua sendo a do portal 0.4.0. O simulador não deve receber domínio público.

## Critérios de aceite do Dia 5

- [x] Ação manual única cria a sequência completa com histórico comum.
- [x] Natureza RPA preservada com Chromium e formulários HTML visíveis.
- [x] Três portais isolados por gateways substituíveis.
- [x] Ordem de bloqueio e desbloqueio registrada e testada.
- [x] Cliente de Tarefas permanece ativo.
- [x] Somente tarefas abertas recebem `BLOQUEADO_INADIMPLENCIA`.
- [x] Responsáveis anteriores são restaurados exatamente.
- [x] Snapshots antes/desejado/depois e tentativas auditáveis persistidos.
- [x] Passos idempotentes e concorrência por cliente protegida.
- [x] Compensação inversa recusa sobrescrever estado externo divergente.
- [x] `FAILED` e `PARTIALLY_FAILED` possuem semânticas distintas.
- [x] Retomada explícita não reaplica portal já conforme.
- [x] Screenshots privados com integridade e download autorizado.
- [x] Cenários determinísticos de falha restritos ao administrador.
- [x] Operador Tecnologia e isolamento entre áreas cobertos.
- [x] Seed sintético e idempotente sem reset operacional.
- [x] Teste de contrato usa navegador real, não fake do RPA.
- [x] Suíte completa com `122` testes e cobertura de `83,83%` aprovada sobre o estado final.
- [x] Ruff, formatação, Mypy, checks Django, migrations, assets e Compose aprovados.
- [x] Plano Railway revisado, sem remoções e com **Wait for CI** preservado.
- [ ] Imagem da 0.5.0 aprovada pelo CI; build local indisponível com Docker Desktop desligado.
- [ ] PR e CI públicos aprovados.
- [ ] Serviço privado do simulador publicado e acessível pelo worker.
- [ ] Smoke tests de produção registrados.
- [ ] Release e tag `v0.5.0` publicados.

## Limites conscientes

- Os três sistemas são portais sintéticos; seletores, autenticação, rate limits e regras dos fornecedores reais exigirão homologação antes de uso com clientes.
- Uma única sessão de navegador e concorrência baixa atendem a massa do desafio, não uma operação de alto volume.
- O snapshot de tarefas usa JSON por representar estado externo heterogêneo. Mudanças de schema do sistema real exigirão versionamento e migração do contrato.
- A compensação é uma tentativa segura de restauração, não uma transação distribuída. Estado divergente permanece parcial por escolha deliberada.
- Screenshots são recortados ao cliente ou erro atual, mas ainda podem conter dados visíveis; apenas dados sintéticos são permitidos nesta entrega e políticas reais de retenção/DLP continuam fora do escopo.
- O serviço privado compartilha imagem e banco para reduzir risco de entrega, mas não recebe Redis, S3 nem OpenAI. Em produção real, um usuário ou banco PostgreSQL dedicado e TLS serviço-a-serviço devem ser avaliados.
