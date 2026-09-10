# Guia de Estudo e Roteiro de Demonstração — SheepContabil

Este documento foi elaborado para capacitar você a dominar completamente o sistema **SheepContabil**, entender a fundo cada uma das quatro automações e conduzir uma **demonstração de alto impacto** para os avaliadores e o criador do desafio.

O objetivo aqui é provar que a ferramenta não é apenas um protótipo visual ("mockup"), mas uma **plataforma empresarial robusta, resiliente, auditável e 100% funcional de ponta a ponta**.

---

## Sumário Executivo do Projeto

| Atributo | Descrição |
| :--- | :--- |
| **Arquitetura** | Monólito Modular em Python 3.12/3.13 e Django 5.2 LTS |
| **Frontend** | Django Templates + HTMX + Alpine.js + Tailwind CSS v4 (Zero SPA externa) |
| **Processamento Assíncrono** | Celery + Redis com isolamento de tarefas |
| **Persistência de Dados** | PostgreSQL como única fonte da verdade transacional |
| **Armazenamento de Arquivos** | Storage seguro compatível com AWS S3 / MinIO (Hashing SHA-256 e RBAC) |
| **RPA e Automação Web** | Playwright Chromium nativo com Page Objects e Saga Compensável |
| **Inteligência Artificial** | OpenAI Adapter com validação estrita via Pydantic e Structured Outputs JSON |
| **Acessibilidade** | WCAG 2.1 Nível AA com suporte total a tema escuro e teclado |
| **Suíte automatizada** | Testes de domínio, integração, segurança, views, RPA, infraestrutura e contratos PostgreSQL executados pelo CI |
| **Ambiente em Produção** | Publicado no Railway: `https://web-production-8f055.up.railway.app` |

---

## PARTE 1: Os Pilares de Engenharia (O que responder se perguntarem de Arquitetura)

Antes de entrar nas automações, é fundamental dominar os 4 pilares arquiteturais que tornam a SheepContabil superior a soluções amadoras:

### 1. "O núcleo é real, apenas a fronteira externa é sintetizada"
- Se um avaliador perguntar se a ferramenta funciona de verdade: **"Sim, o núcleo de domínio, as regras de negócio, a orquestração de fila, a máquina de estados, os cálculos e o banco de dados são 100% reais."**
- Como não temos acesso às credenciais bancárias e aos sistemas legados reais do escritório neste momento, criamos **Adapters de Fronteira** realistas (como os 3 portais HTML sintéticos do SC-05 operados pelo Playwright). Quando o escritório liberar as credenciais de produção, basta trocar o adapter — nenhuma linha de regra de negócio precisará ser reescrita.

### 2. Monólito Modular vs Microsserviços
- Não caímos na armadilha da complexidade prematura de microsserviços. Criamos um monólito modular dividido por bounded contexts (`core/identity`, `core/automations/sc04`, `sc05`, `sc06`, `sc20`).
- Cada módulo possui suas próprias tabelas, regras e limites. O código é limpo, a implantação é única e a consistência transacional do PostgreSQL garante que nada fique inconsistente.

### 3. Idempotência e Auditoria Centralizada (`AutomationRun`)
- Toda e qualquer automação (seja disparada manualmente pelo operador ou agendada pelo cron) instancia o mesmo caso de uso e cria um registro em `AutomationRun`.
- Sabemos exatamente **quem disparou, quando começou, quanto tempo durou, qual o payload de entrada, quais passos foram executados e qual foi o resultado**.
- Todas as operações são **idempotentes**: se o robô for executado duas vezes seguidas, ele não duplica tarefas, não envia e-mails repetidos e não reaplica bloqueios desnecessários.

### 4. Controle de Acesso Baseado em Papel e Área (RBAC)
- O sistema possui controle de acesso real:
  - **Administrador**: Visão global de todas as automações, configuração de templates e logs.
  - **Operadores Setoriais**: Acesso restrito apenas à sua respectiva área funcional:
    - Operador Fiscal -> Módulo SC-04
    - Operador Tecnologia -> Módulo SC-05
    - Operador Societário -> Módulo SC-06
    - Operador de Processos -> Módulo SC-20

---

## PARTE 2: Estudo Profundo das 4 Automações

---

### MÓDULO 1: SC-04 — Triagem da Caixa de Arquivos
> **Natureza:** Agente de IA · **Frequência:** Diário · **Área:** Fiscal

#### 1. A Dor de Negócio no Escritório
Escritórios contábeis recebem diariamente centenas de documentos por e-mail e WhatsApp: extratos bancários (PDF/OFX), notas fiscais de serviço e produto (PDF/XML), comprovantes de pagamento e guias de recolhimento. Operadores perdem de 2 a 4 horas por dia abrindo anexo por anexo, renomeando arquivos e tentando descobrir a qual cliente aquele documento pertence.

#### 2. Como a SheepContabil Resolve
1. **Ingestão:** Os arquivos chegam pela caixa de entrada diária automatizada (`DocumentInbox`) ou via upload manual direto pelo operador no portal.
2. **Armazenamento e Hashing:** O arquivo é armazenado em Storage seguro S3 com hash SHA-256 para garantir integridade e detectar duplicidades.
3. **Extração e OCR:** O sistema extrai o texto do documento e executa correspondência exata de CNPJ/CPF contra a base cadastral de clientes.
4. **Classificação com IA (Adapter OpenAI):** A IA classifica o tipo documental, extrai valores, data de competência e determina um **Score de Confiança** (0.00 a 1.00).
5. **Regra de Ouro (Limiar de 0.85):**
   - **Confiança >= 85% e cliente identificado:** O documento é **arquivado e roteado automaticamente** para a pasta fiscal da empresa.
   - **Confiança < 85% ou cliente ambíguo:** O sistema **NÃO adivinha silenciosamente**! Ele envia o documento para a **Fila de Revisão Humana**.
6. **Fila de Revisão com Feedback Loop:** O operador revisa o documento com 1 clique, confirma ou altera a classificação, e o sistema registra esse feedback de forma versionada para auditoria.

#### 3. O que Destacar aos Avaliadores
- **Segurança da IA:** Saída estritamente tipada com Structured Outputs JSON (Pydantic). Se a IA devolver um formato inválido, o sistema não quebra: ele encaminha para revisão.
- **Transparência:** A interface mostra o percentual de confiança da IA, o tipo inferido e o cliente associado.
- **Privacidade:** Apenas dados sintéticos minimizados são enviados à API; os arquivos originais nunca ficam expostos publicamente.

---

### MÓDULO 2: SC-05 — Bloqueio e Desbloqueio de Inadimplentes
> **Natureza:** RPA (Robotic Process Automation) · **Frequência:** Sob demanda · **Área:** Tecnologia

#### 1. A Dor de Negócio no Escritório
Quando um cliente atrasa honorários por meses, o escritório precisa suspender o atendimento. Porém, o escritório utiliza múltiplos softwares legados (Portal de Documentos, Sistema Contábil e Sistema de Tarefas da equipe). Bloquear manualmente exige navegar em três portais diferentes. Se o operador esquecer de bloquear um sistema, o trabalho continua sendo feito de graça. Pior: se desativar o cliente no sistema de tarefas, o histórico de trabalho da equipe é destruído.

#### 2. Como a SheepContabil Resolve (Padrão Saga com Playwright)
A SheepContabil executa uma **Saga Orquestrada via RPA com Playwright Chromium real** sobre três portais:

1. **Ordem Estrita de Bloqueio:**
   - **Passo 1: Portal de Arquivos** -> Bloqueia o upload/download de documentos do cliente.
   - **Passo 2: Sistema Contábil** -> Bloqueia a conciliação e geração de relatórios.
   - **Passo 3: Sistema de Gestão de Tarefas** -> **REQUISITO CRÍTICO DO DESAFIO**: O cliente **NÃO é desativado**! O robô inspeciona as tarefas abertas, salva quem era o responsável original e substitui o responsável pelo marcador `BLOQUEADO_INADIMPLENCIA`. As tarefas fechadas e o histórico permanecem 100% intactos!
2. **Snapshots de Auditoria:** O robô tira um "snapshot" (foto do estado) antes de alterar qualquer registro.
3. **Resiliência a Falhas e Compensação Automática:**
   - Se o Passo 3 falhar, o robô não deixa o cliente "meio bloqueado". Ele executa a **compensação reversa** (desfaz o Passo 2 e o Passo 1) se o ambiente ainda estiver no estado anterior.
   - Se houver divergência externa, o sistema marca a execução como `PARTIALLY_FAILED` (Falha Parcial) e permite a **Retomada Explícita com 1 clique**.
4. **Desbloqueio Inteligente na Renegociação:**
   - Quando o cliente paga, o operador aciona o "Desbloquear". O robô executa a **ordem inversa** (Tarefas -> Contábil -> Arquivos).
   - No sistema de tarefas, ele **restaura exatamente os responsáveis originais** a partir do snapshot prévio!
5. **Evidências Visuais Privadas:** A cada portal acessado, o Playwright tira um screenshot recortado do cliente e salva no storage S3 protegido por RBAC.

#### 3. O que Destacar aos Avaliadores
- **Não é automação de fachada:** O Playwright abre uma sessão real de navegador com cookies, CSRF e manipulação de formulários DOM.
- **Restauração fiel:** O robô não atribui as tarefas a um "usuário genérico"; ele devolve a tarefa exatamente para quem estava trabalhando nela antes do bloqueio.
- **Simulação de Falhas para Demonstração:** No ambiente demonstrativo, o administrador pode selecionar cenários de falha controlada (ex.: timeout no portal de tarefas) para provar aos avaliadores que a compensação e a retomada funcionam na prática!

---

### MÓDULO 3: SC-06 — Briefing Societário com Perguntas Condicionais
> **Natureza:** Controle Sistematizado · **Frequência:** Sob demanda · **Área:** Societário

#### 1. A Dor de Negócio no Escritório
O processo de abertura ou alteração de empresa no departamento societário é caótico. Coletar dados dos sócios por WhatsApp ou formulários estáticos de Word/Excel gera erros recorrentes: se o sócio é casado, precisa de anuência do cônjuge e certidão de casamento; se a empresa é de outro estado, exige inscrição estadual na Junta de origem. Operadores esquecem de pedir esses dados e o processo entra em exigência na Junta Comercial, atrasando a abertura em semanas.

#### 2. Como a SheepContabil Resolve
1. **Motor de Regras Condicionais (DSL no Servidor):**
   - Regras declarativas com operadores `equals`, `not_equals`, `in`, `all`, `any`.
   - **Diferencial de Engenharia:** As regras não são meros scripts de `show/hide` em JavaScript. Elas são validadas **rigorosamente no backend Django**. Se alguém tentar forjar uma requisição sem preencher um campo condicionalmente obrigatório, o servidor recusa.
2. **Formulário Reativo em Tempo Real (Alpine.js + HTMX):**
   - O operador preenche os dados básicos.
   - Ao selecionar uma **UF diferente de São Paulo** (UF de referência do desafio), o formulário expande automaticamente os campos do **Bloco Interestadual** (órgão de registro de origem e número).
   - Ao declarar que um sócio é **Casado**, o formulário exige o nome do cônjuge e o **Regime de Bens** (Comunhão Parcial, Separação Total, etc.).
3. **Templates Versionados e Imutáveis:**
   - O administrador pode criar novos templates de briefing societário. Uma vez publicado, o template torna-se imutável (`v1`, `v2`). Briefings preenchidos há 2 anos continuam reproduzíveis exatamente sob as regras da época.
4. **Geração de PDF Consolidado:**
   - Ao concluir, o sistema consolida as respostas, registra quem iniciou e quem concluiu, e gera um **PDF para download** pronto para protocolo ou assinatura do cliente.

#### 3. O que Destacar aos Avaliadores
- **Eliminação de Exigências na Junta Comercial:** O formulário só permite conclusão se todos os campos condicionais exigidos forem válidos.
- **Acessibilidade completa:** Mensagens de validação claras em vermelho com alto contraste, atributos `aria-invalid`, foco automático no campo com erro e navegação fluida.

---

### MÓDULO 4: SC-20 — Vencimento de Certificados Digitais
> **Natureza:** Controle Sistematizado · **Frequência:** Mensal · **Área:** Processos

#### 1. A Dor de Negócio no Escritório
O certificado digital (e-CNPJ A1/A3) é vital para qualquer empresa. Se expirar, a emissão de notas fiscais (NF-e) trava imediatamente, as obrigações acessórias atrasam e a empresa toma multas pesadas. Controlar centenas de datas de expiração em planilhas manuais é inviável e gera perda de clientes por desorganização.

#### 2. Como a SheepContabil Resolve
1. **Janela de 60 Dias e Níveis de Urgência:**
   - Varredura mensal automatizada (primeiro dia do mês às 08:00) ou disparo sob demanda no painel.
   - Acompanha certificados na janela de 60 dias e categoriza visualmente:
     - **Vencido** (vermelho)
     - **Vencimento Crítico** (≤ 15 dias - amarelo/laranja)
     - **Atenção** (≤ 30 dias)
     - **Preventivo** (≤ 60 dias)
2. **Deduplicação Inteligente (Proteção Anti-Spam):**
   - Registra cada comunicação sob a chave `(certificado, validade considerada, canal, política)`. Enquanto esses quatro elementos permanecerem iguais, novas verificações reconhecem o aviso já registrado e **não criam outro envio**. Uma nova validade, outro canal ou uma nova política pode gerar uma nova comunicação; a competência mensal controla o agendamento, não a identidade da comunicação.
3. **Régua Multicanal de Alta Conveniência (E-mail e WhatsApp):**
   - **WhatsApp:**
     - Gera URL oficial com mensagem pré-formatada pronta.
     - Botão pill verde `[ WhatsApp ]` com ícone outline oficial vetorial em SVG.
     - Formatação canônica de telefones com DDI/DDD: `+55 (11) 98765-4321`.
   - **E-mail:**
     - Ícone de envelope outline vetorial.
     - Botão pill turquesa `[ E-mail ]` gerando link direto `mailto:` com **Assunto** e **Corpo** profissionais preenchidos (alertando prazo, situação e instruções de renovação preventiva junto à Autoridade Certificadora).
     - Integração opcional com envio direto via SMTP/Gmail.
   - **Exibição Estruturada de Contatos:**
     - Quando o cliente tem ambos os canais cadastrados (ex.: **Mariana Souza Demo**, incluída na massa oficial), exibe as duas opções na mesma célula. A estrela preenchida identifica o canal preferencial da rotina não assistida; a estrela vazia permite alterar essa preferência.
4. **Auditoria e Reenvio Manual no Histórico:**
   - Histórico completo de tentativas (com status `Enviada` ou `Falhou`, mensagem de erro e data/hora).
   - Botão para **retentar envio de falhas** com 1 clique.
   - Ações de e-mail e WhatsApp disponíveis diretamente na tabela de histórico.

#### 3. O que Destacar aos Avaliadores
- **Ergonomia Operacional:** O operador não precisa copiar e colar dados; com 1 clique ele abre a conversa no WhatsApp Web ou a redação no Outlook/Gmail com todo o texto técnico pronto.
- **Resiliência:** Falha de envio (ex.: e-mail inválido) não é mascarada; é registrada com clareza e fica disponível para retentativa.

---

## PARTE 3: Roteiro Prático de Demonstração (Script Passo a Passo para Apresentação)

> **Tempo sugerido:** 15 a 20 minutos  
> **Ambiente:** `https://web-production-8f055.up.railway.app`  
> **Usuário recomendado:** `admin` (visão completa de todos os módulos)

```text
Dica de Ouro: Abra o painel com 2 abas preparadas:
Aba 1: Portal SheepContabil logado como admin
Aba 2: Repositório GitHub com o CI e a documentação aberta
```

### Preparação obrigatória do ensaio

Faça esta preparação antes de compartilhar a tela, nunca durante a apresentação:

1. Execute `python src/manage.py seed_demo` para assegurar o catálogo e a massa sintética estrutural. Esse comando é idempotente por chaves conhecidas, mas **não é um reset operacional**, não apaga evidências, não reabre briefings e não limpa comunicações anteriores.
2. Execute `python src/manage.py prepare_demo`. Sem opção adicional, o comando é somente leitura: confere os quatro módulos, o modo simulado e a janela ativa de 60 dias do SC-20, a Aurora do SC-05, o PDF consolidado e localiza execuções funcionais de contingência. Se os certificados controlados tiverem envelhecido para fora da janela, decida conscientemente se deve reposicioná-los com `python src/manage.py seed_demo --refresh-certificate-dates`; isso altera a validade que participa da identidade de deduplicação e, por isso, nunca é feito implicitamente.
3. Se o relatório indicar apenas projeções conhecidas da Aurora a restaurar, execute `python src/manage.py prepare_demo --apply` e rode a conferência novamente. O comando recusa operação ativa, estado parcial ou estado desconhecido; nesses casos, investigue ou retome a saga em vez de esconder a divergência.
4. Confirme que o relatório termina em `Resultado: READY`. Mantenha abertos os caminhos de execução que ele imprimir.
5. Confirme no Railway que `web`, `worker`, `scheduler`, PostgreSQL e Redis estão operacionais e que `SC20_NOTIFICATION_BACKEND` permanece em `simulated`.
6. Execute as automações em série. O worker demonstrativo usa concorrência 1; espere cada execução chegar a um estado terminal antes de iniciar a seguinte.

> **Regra de evidência:** não use os cabeçalhos legados `demo-sc04-review`,
> `demo-sc05-success` ou `demo-sc20-warning` como prova funcional. Abra execuções
> produzidas pelo fluxo real e confirme que possuem linha do tempo e evidências
> filhas. Mantenha como contingência uma execução funcional do SC-04, o par
> bloqueio/desbloqueio do SC-05, o briefing concluído do SC-06 e o par primeira
> execução/reexecução deduplicada do SC-20.

As evidências validadas em produção em 09/09/2026 continuam disponíveis como contingência datada:

- SC-04: `63a2e3b3-286d-4aad-8ac7-5cdc3dcbe547`;
- SC-05, bloqueio: `beac504e-8578-40fb-b28a-356aaa162dd6`;
- SC-05, desbloqueio: `e450a7aa-ea67-4b6c-b2c8-80246733a927`;
- SC-06, briefing concluído: `e4657aa1-2e40-4e1c-8035-1f5ee9abf462`;
- SC-20, comunicação com falha controlada: `9f88c44c-dc21-4132-8221-bd66e22aac08`;
- SC-20, reexecução deduplicada: `36a413cd-5508-40ea-8508-31402a84488d`.

Esses identificadores pertencem ao ambiente atual; o procedimento durável é usar os candidatos mais recentes informados por `prepare_demo`.

Rotas canônicas, úteis para preparar as abas sem depender da posição dos cards:

- SC-04: `/modulos/triagem-caixa-arquivos/`;
- SC-05: `/modulos/bloqueio-clientes-inadimplentes/`;
- SC-06: `/modulos/briefing-societario/`;
- SC-20: `/modulos/vencimento-certificado-digital/`.

---

### ETAPA 1: Abertura e Visão Geral da Plataforma (2 minutos)

1. **Apresentação Inicial (O que falar):**
   > *"Olá! Hoje vou apresentar o portal SheepContabil, desenvolvido para atender ao desafio de automação contábil. Nossa premissa fundamental foi construir uma solução que não fosse apenas uma maquete conceitual, mas uma plataforma modular completa, com persistência real em PostgreSQL, mensageria com Celery/Redis, conformidade estrita de acessibilidade WCAG 2.1 AA e uma suíte automatizada executada integralmente pelo CI."*
2. **Mostrar o Dashboard Principal:**
   - Aponte para os 4 cards de automação:
     - **SC-04** (Fiscal · IA)
     - **SC-05** (Tecnologia · RPA)
     - **SC-06** (Societário · Formulários Condicionais)
     - **SC-20** (Processos · Gestão de Certificados)
   - Destaque o menu lateral com suporte ao controle de acesso por perfil e área.

---

### ETAPA 2: Demonstração do SC-04 — Triagem Fiscal com IA (4 minutos)

1. **Acessar o módulo SC-04:**
   - Clique em **SC-04 - Triagem da caixa de arquivos**.
2. **Produzir um ciclo funcional novo:**
   - Clique em **Processar caixa agora**. Cada disparo manual recebe um identificador sintético de ciclo novo, preservando os ciclos anteriores e mantendo, dentro do próprio lote, a cópia usada para demonstrar deduplicação por conteúdo.
   - Aguarde a execução chegar a um estado terminal e abra seu detalhe para mostrar a linha do tempo do portal, broker e worker.
3. **Mostrar a Tabela de Documentos:**
   - Em **Arquivos recebidos**, mostre os documentos triados, a confiança da IA, o tipo documental identificado e o cliente vinculado.
4. **Explicar o Limiar de Segurança (Threshold):**
   > *"Observem que a automação não classifica no escuro. Se a confiança for maior ou igual a 85%, o documento é roteado automaticamente. Mas se a IA tiver incerteza ou o documento for ambíguo, ele vai para a Fila de Revisão Humana."*
5. **Demonstrar a revisão humana:**
   - Na seção **Arquivos recebidos**, selecione **Aguardando revisão** no filtro **Estado** e clique em **Filtrar**.
   - Abra **Detalhes** de um documento pendente.
   - Mostre o motivo, a sugestão, as confianças e as evidências curtas; confirme ou corrija tipo e cliente.
   - Clique em **Confirmar e encaminhar** e destaque que a decisão final fica atribuída ao usuário autenticado.

---

### ETAPA 3: Demonstração do SC-05 — Bloqueio e Desbloqueio com RPA (5 minutos)
> *Este costuma ser o módulo mais impressionante para os avaliadores técnicos!*

1. **Acessar o módulo SC-05:**
   - Clique em **SC-05 - Bloqueio e desbloqueio de clientes**.
2. **Explicar o Desafio do Negócio:**
   > *"Aqui resolvemos a inadimplência sem gerar passivo. O escritório precisa bloquear o cliente em três sistemas. Nosso robô Playwright Chromium opera, pela interface visível, três portais HTML sintéticos realmente executados no ambiente demonstrativo. A regra, a navegação, os formulários, o estado, a saga e as evidências são reais; apenas a fronteira dos sistemas legados foi sintetizada."*
3. **Selecionar o caso demonstrativo e disparar o bloqueio:**
   - No painel **Operar os três sistemas**, escolha **Aurora Demonstração Ltda.** (`aurora-demo`).
   - Selecione a ação **Bloquear** e o cenário **Fluxo normal**.
   - Clique em **Executar sequência**.
   - Aguarde a execução chegar a um estado terminal antes de iniciar qualquer outra automação.
4. **Abrir a Página da Execução (`run_detail.html`):**
   - Mostre as três etapas concluídas com sucesso:
     1. Portal de Arquivos -> Bloqueado
     2. Sistema Contábil -> Bloqueado
     3. Sistema de Tarefas -> **Destaque:** As tarefas abertas receberam o responsável `BLOQUEADO_INADIMPLENCIA`, enquanto as tarefas fechadas foram preservadas!
   - Clique nas **Screenshots de Evidência**: mostre as capturas de tela tiradas pelo robô durante a navegação.
5. **Demonstrar o Desbloqueio (Ordem Inversa e Restauração dos Responsáveis):**
   - Mostre que o cliente agora está com status `Bloqueado`.
   - Clique em **Desbloquear**.
   - Aguarde a execução terminar antes de prosseguir.
   - Acompanhe a execução e mostre que as tarefas foram restauradas exatamente para os colaboradores originais a partir do snapshot!
6. **Mencionar a Tolerância a Falhas:**
   - Explique que se houver timeout ou erro em um dos portais, a saga executa a compensação reversa automática ou sinaliza `PARTIALLY_FAILED`, permitindo a retomada explícita sem deixar o cliente em estado inconsistente.

---

### ETAPA 4: Demonstração do SC-06 — Briefing Societário Condicional (4 minutos)

1. **Acessar o módulo SC-06:**
   - Clique em **SC-06 - Briefing societário**.
2. **Iniciar um Novo Briefing:**
   - Preencha o nome e um CPF ou CNPJ estritamente sintético e clique em **Iniciar Briefing**.
3. **Demonstrar a Reatividade Condicional ao Vivo:**
   - No campo de Estado (UF), selecione **Rio de Janeiro (RJ)** ou qualquer estado diferente de SP:
     - **Efeito visual:** O formulário expande instantaneamente o **Bloco de Regularidade Interestadual**, solicitando Junta Comercial de origem e registro!
   - Na seção de Sócios, marque a opção **Sócio Casado: Sim**:
     - **Efeito visual:** O formulário revela os campos obrigatórios de identificação do cônjuge e seleção do **Regime de Bens**.
4. **Destacar a Validação de Backend:**
   > *"Essa reatividade visual é proporcionada por Alpine.js e HTMX, mas toda a integridade lógica é garantida por uma DSL no backend Django. Nenhum dado inconsistente é gravado no banco."*
5. **Demonstrar obrigatoriedade e resultado final:**
   - Tente concluir com um campo condicional obrigatório vazio e mostre a recusa tanto na interface quanto no contrato do backend.
   - Cancele esse atendimento transitório para não deixar um rascunho inesperado.
   - Abra o briefing concluído da **Aurora Participações Demo** e baixe seu **PDF consolidado**, mostrando o documento pronto para arquivamento ou protocolo.

---

### ETAPA 5: Demonstração do SC-20 — Gestão de Certificados Digitais (4 minutos)

1. **Acessar o módulo SC-20:**
   - Clique em **SC-20 - Vencimento de certificado digital**.
2. **Apresentar o Painel de Controle:**
   - Mostre os contadores executivos no topo: *Monitorados, Próximos do vencimento (janela de 60 dias), Vencidos e Avisos com falha*.
3. **Destacar a Tabela de Certificados e a Exibição de Contatos:**
   - Mostre a linha de **Mariana Souza Demo**, criada pela massa oficial com e-mail e WhatsApp.
   - Explique que a estrela preenchida identifica o canal preferencial da automação; a estrela vazia permite mudar a preferência.
   - O e-mail e o número são os próprios links assistidos na carteira. Os botões textuais **E-mail** e **WhatsApp** também aparecem nas ações do histórico.
4. **Demonstrar a Ação Rápida de Envio:**
   - Mostre que o link de WhatsApp contém texto corporativo pré-preenchido, dados da empresa, vencimento e aviso preventivo. Não conclua nenhum envio externo durante a demonstração.
   - Mostre que o link `mailto:` contém assunto e corpo completos. O envio assistido continua dependendo da ação explícita do operador no cliente de e-mail.
5. **Demonstrar a Proteção Anti-Spam e o Histórico:**
   - Confirme primeiro que o ambiente utiliza o backend de entrega **simulado**.
   - Clique em **Verificar vencimentos agora** e aguarde a execução terminar.
   - Execute novamente e mostre no resumo que os avisos elegíveis aparecem como **já registrados**, sem novas tentativas.
   - Explique que a deduplicação considera certificado, validade, canal e política — e não simplesmente o mês da execução.
   - No **Histórico de avisos**, mostre canal, estado, horário, falha controlada e retentativa.

---

### ETAPA 6: Encerramento e Conclusão Técnica (1 minuto)

1. **Resumo das Entregas:**
   > *"Para resumir: cobrimos todas as naturezas do desafio (IA, RPA e Controles Sistematizados) em um monólito limpo, sustentável e pronto para evoluir. O repositório conta com CI/CD no GitHub Actions, tipagem rigorosa no Mypy, auditoria de código no Ruff e testes automatizados de domínio, integração e contratos operacionais."*
2. **Abrir para Perguntas dos Avaliadores.**

---

## PARTE 4: Como Responder às Perguntas Difíceis dos Avaliadores (FAQ)

### P1: "Por que escolheram monólito modular em vez de microsserviços?"
> **Resposta:**  
> *"Para o escopo de um portal contábil com quatro automações interconectadas, microsserviços adicionariam latência de rede, necessidade de orquestradores complexos (Kubernetes), transações distribuídas frágeis e custo de infraestrutura desproporcional. O monólito modular Django nos dá separação rigorosa de domínios em código, compartilhamento de autenticação e transações ACID no PostgreSQL, mantendo a simplicidade de implantação e manutenção."*

### P2: "E se a IA do SC-04 alucinar ou classificar errado um documento fiscal?"
> **Resposta:**  
> *"Nós mitigamos isso com três camadas de proteção: primeiro, usamos Structured Outputs via JSON Schema com Pydantic, impedindo saídas com formatos desconhecidos; segundo, estabelecemos um limiar rigoroso de confiança em 85% — se a IA tiver menos de 85% de certeza, o documento é forçado para a Fila de Revisão Humana; terceiro, o operador pode corrigir o dado com 1 clique e esse feedback é registrado para calibragem contínua."*

### P3: "Por que o robô do SC-05 não desativa o cliente no sistema de tarefas?"
> **Resposta:**  
> *"Essa foi uma decisão de negócio crítica explicitada no desafio. Se desativássemos o cliente no sistema de tarefas, o histórico de horas e trabalhos já entregues ficaria inacessível ou corrompido para o faturamento e para a equipe contábil. Por isso, implementamos a substituição seletiva: apenas tarefas em aberto recebem o marcador `BLOQUEADO_INADIMPLENCIA`, e o snapshot original permite devolver cada tarefa exatamente para o seu colaborador original quando houver o pagamento."*

### P4: "Como garantem que o SC-20 não envia avisos repetidos?"
> **Resposta:**  
> *"Cada comunicação possui uma identidade lógica composta por certificado, validade considerada, canal e política. Há uma restrição de unicidade correspondente no PostgreSQL e o serviço consulta essa identidade antes da entrega. Por isso, reexecutar a rotina não cria outro aviso enquanto esses elementos permanecerem iguais. A competência mensal controla o agendamento; ela não é a chave de deduplicação."*

### P5: "Como foi tratada a acessibilidade do portal?"
> **Resposta:**  
> *"O portal segue as diretrizes WCAG 2.1 Nível AA. Toda a navegação pode ser feita 100% via teclado com focus rings visíveis, formulários possuem rótulos semânticos e tags `aria-*`, alertas e erros possuem `role='alert'`, ícones decorativos possuem `aria-hidden='true'` e a paleta de cores do Design System foi validada para garantir contraste suficiente tanto no tema claro quanto no escuro."*

---

## PARTE 5: Checklist Rápido Antes de Apresentar

- [ ] Verificar se a conexão de internet está estável.
- [ ] Acessar `https://web-production-8f055.up.railway.app` e confirmar que a página de login carrega instantaneamente.
- [ ] Efetuar login com usuário `admin` e verificar se a sessão está ativa.
- [ ] Garantir que o zoom do navegador está em 100% (resolução recomendada: 1440x900 ou superior).
- [ ] Executar `python src/manage.py prepare_demo` e confirmar `Resultado: READY`.
- [ ] Confirmar que a Aurora está ativa, não existem execuções SC-05 parciais/ativas e o SC-20 usa entrega simulada.
- [ ] Ter a execução mais recente do CI verde e o arquivo `docs/architecture.md` abertos para consulta técnica se solicitado.
- [ ] Confirmar a tag `v1.0.0`, os health checks e a linha do tempo de uma execução antes de iniciar.
- [ ] Lembrar que somente o administrador vê IDs técnicos; operadores veem a mesma trilha sem esses campos.
- [ ] Baixar o PDF concluído da Aurora em um navegador convencional e confirmar que o arquivo abre.
- [ ] Manter as seis execuções funcionais de contingência indicadas por `prepare_demo` em abas preparadas.
