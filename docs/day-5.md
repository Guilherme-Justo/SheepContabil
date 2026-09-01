# Dia 5 — SC-05 de ponta a ponta

| Campo | Valor |
| --- | --- |
| Data | 2026-09-01 |
| Versão | 0.5.0 |
| Processo | SC-05 — Bloqueio e desbloqueio de clientes inadimplentes |
| Natureza preservada | RPA |
| Frequência | Sob demanda |
| Área | Tecnologia |
| Estado local | Implementado; 124 testes e gates locais disponíveis aprovados |
| Estado externo | 0.5.0 implantada e SC-05 validado ponta a ponta no ambiente público |

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
                 │ HTTP loopback na Railway
                 ▼
          WSGI sintético privado
                 │ HTML visível
        ┌────────┼─────────┐
        ▼        ▼         ▼
    Arquivos  Contábil  Tarefas
        │        │         │
        └────────┼─────────┘
                 │ snapshots + tentativas
        ┌────────┴────────┐
        ▼                 ▼
   PostgreSQL       Storage S3 privado
                    screenshots PNG
```

Web, worker e scheduler permanecem processos do mesmo monólito modular. O simulador possui settings, URLconf e entrypoint WSGI próprios e representa apenas os três sistemas externos. Ele não contém a saga, não oferece atalho de integração ao worker e não precisa de URL pública.

O Compose mantém esse WSGI em contêiner separado. O limite do plano Railway impediu criar um quarto serviço de aplicação; por isso, a implantação-alvo inicia o simulador como subprocesso auxiliar do mesmo contêiner do `worker`. O Playwright o acessa em `127.0.0.1:8000`; a Railway alcança `/health/ready` apenas pela rede privada, sem domínio público. A co-localização é apenas física: o subprocesso recebe ambiente reconstruído por allowlist sem herdar Redis, S3 ou OpenAI, e as credenciais SC-05 existem somente no serviço `worker`.

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

No estado final local, a suíte completa aprovou `124` testes com cobertura total de `83,85%`, acima do piso obrigatório de `75%`. Os `37` testes focados descritos acima também passaram. Ruff, verificação de formatação, Mypy, sintaxe POSIX do supervisor, checks Django, ausência de migrations pendentes, build de assets e validação da configuração Compose estão verdes.

O único gate de build que não pôde ser reproduzido localmente foi a imagem `0.5.0`, porque o Docker Desktop estava desligado e o daemon Linux não estava disponível. Esse gate foi posteriormente aprovado pelos jobs `Container build` dos CIs do PR `#5`, de seu push em `main`, do PR `#6` e do push final em `main`.

O plano Railway original foi validado sem aplicação: `1` recurso a criar, `10` ajustes e `0` remoções. A criação correspondia ao quarto serviço `simulator`, mas o limite do plano não permitiu materializá-lo. A decisão operacional seguinte preservou o simulador separado no Compose e o co-localizou no worker somente na Railway. A IaC revisada declara apenas as três fontes GitHub já existentes — web, worker e scheduler —, todas com `checkSuites: true` para preservar **Wait for CI**. O plano final foi revisado com `0` recursos novos, `10` ajustes e `0` remoções e então aplicado com sucesso.

## Publicação e validação de produção

O [PR `#5`](https://github.com/Guilherme-Justo/SheepContabil/pull/5) incorporou a 0.5.0 à `main` no commit [`d5b71b384340f4f3cd66e07f801309529790b39f`](https://github.com/Guilherme-Justo/SheepContabil/commit/d5b71b384340f4f3cd66e07f801309529790b39f). O [CI do PR](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33538813847) e o [CI do push em `main`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33539137377) concluíram os jobs de qualidade, testes e imagem com sucesso. Depois do CI verde, a integração nativa da Railway concluiu os deployments iniciais:

- `web`: `e07e22d8-1d4c-4901-994c-7d41bbb78c7c`;
- `worker`: `5cba6372-cbdc-4ead-a8f0-a5a0f87c56f2`;
- `scheduler`: `ef6c395d-e250-49f3-9eed-c5d0d2b62865`.

O ajuste operacional foi incorporado pelo [PR `#6`](https://github.com/Guilherme-Justo/SheepContabil/pull/6), merge [`4ab7af38ccd0259d89c80a00b82679d3754d5ac3`](https://github.com/Guilherme-Justo/SheepContabil/commit/4ab7af38ccd0259d89c80a00b82679d3754d5ac3). O [CI do PR `33556800150`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33556800150) e o [CI de `main` `33557162559`](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33557162559) aprovaram `Quality and tests` e `Container build`. Somente após o CI verde, a integração GitHub da Railway promoveu:

- `web`: `4c35c556-9f52-4ee9-b0c7-a092a703f1b8`;
- `worker`: `8101f6cb-4401-489e-850e-02f62075e8e3`;
- `scheduler`: `348d9edf-8bf4-45de-b8ed-e955f3ff3934`.

O Gunicorn escuta em `0.0.0.0:8000` sem domínio público, permitindo o healthcheck pela rede privada; o RPA usa exclusivamente `http://127.0.0.1:8000`. O marcador de readiness só é criado depois da conferência do schema, da liveness do WSGI e de o processo Celery permanecer vivo durante a janela inicial de três segundos; o endpoint também consulta o banco. O smoke funcional abaixo é a evidência separada de que fila e worker processaram as tarefas. As credenciais e o segredo Django distintos do simulador ficaram somente no worker, e a allowlist do processo filho exclui Redis, S3 e OpenAI. O portal público respondeu `200` em `/health/ready`.

O seed idempotente foi habilitado por um único redeploy web (`cfe0a20c-e358-44ef-870f-5aec6271a24d`) e imediatamente voltou a `false`, sem novo deploy. Em seguida, o smoke autenticado produziu as seguintes evidências:

- bloqueio em fluxo normal: execução `4ee4eb62-c22b-40e1-a534-981969212c45`, três portais e seis screenshots PNG privadas;
- desbloqueio em fluxo normal: execução `c9cbbefa-d1a4-4caf-97bf-0f91d621f123`;
- falha injetada em Tarefas e na compensação de Arquivos: execução `d410661f-62a5-42f8-adf2-c5bf32e96c59`, encerrada primeiro como `PARTIALLY_FAILED` e depois retomada explicitamente com o mesmo UUID e uma retomada registrada;
- restauração final: execução `3896a0e5-1091-4e94-8183-dba05d38ce66`, deixando Aurora novamente `Ativo`;
- uma captura de `10.996` bytes foi baixada como `image/png`, com assinatura válida e `Cache-Control: private, no-store`; módulo e artefato retornaram `404` ao operador de outra área.

Assim, PR, dois CIs, **Wait for CI**, deploy automático, topologia co-localizada, readiness, compensação parcial, retomada, storage privado e RBAC possuem evidência operacional no ambiente público. Nenhum valor secreto foi registrado neste documento.

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
- [x] Suíte completa com `124` testes e cobertura de `83,85%` aprovada sobre o estado final.
- [x] Ruff, formatação, Mypy, checks Django, migrations, assets e Compose aprovados.
- [x] Imagem da 0.5.0 aprovada pelos CIs públicos; build local indisponível com Docker Desktop desligado.
- [x] PR `#5`, merge em `main` e deploy automático de web/worker/scheduler aprovados.
- [x] Limite do plano Railway registrado e topologia-alvo reduzida aos três serviços existentes.
- [x] Ajuste co-localizado aprovado no PR `#6` e em dois CIs, com **Wait for CI** preservado nos três serviços.
- [x] WSGI privado publicado junto ao worker, Playwright em loopback e allowlist de ambiente verificada.
- [x] Smoke tests de produção registrados.
- [ ] Release e tag `v0.5.0` publicados.

## Limites conscientes

- Os três sistemas são portais sintéticos; seletores, autenticação, rate limits e regras dos fornecedores reais exigirão homologação antes de uso com clientes.
- Uma única sessão de navegador e concorrência baixa atendem a massa do desafio, não uma operação de alto volume.
- O snapshot de tarefas usa JSON por representar estado externo heterogêneo. Mudanças de schema do sistema real exigirão versionamento e migração do contrato.
- A compensação é uma tentativa segura de restauração, não uma transação distribuída. Estado divergente permanece parcial por escolha deliberada.
- Screenshots são recortados ao cliente ou erro atual, mas ainda podem conter dados visíveis; apenas dados sintéticos são permitidos nesta entrega e políticas reais de retenção/DLP continuam fora do escopo.
- Na Railway demonstrativa, Celery e WSGI compartilham contêiner, UID e ciclo de disponibilidade para caber no plano; isso reduz isolamento de segurança, falha e escala. A allowlist impede herança acidental de Redis, S3 e OpenAI, mas não equivale a uma fronteira de processo forte. Em produção real, o simulador deve voltar a um serviço separado, com usuário/banco dedicado e TLS serviço-a-serviço quando aplicável.
