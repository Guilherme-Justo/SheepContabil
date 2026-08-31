# Premissas, incertezas e limites — SheepContabil

| Campo | Valor |
| --- | --- |
| Status | Registro vivo iniciado no Dia 1 |
| Data-base | 2026-08-27 |
| Escopo | SC-04, SC-05, SC-06 e SC-20 |

## 1. Como usar este documento

O desafio contém lacunas deliberadas. Este registro impede que decisões de implementação sejam confundidas com requisitos do documento original.

Status usados:

- **Confirmada**: decorre diretamente do desafio ou de decisão arquitetural já aceita.
- **Aceita para o desafio**: suposição necessária para avançar, reversível e documentada.
- **Validar**: pode alterar comportamento, dados ou apresentação; precisa ser confirmada antes do congelamento da entrega.
- **Fora do escopo**: não será implementada na semana, salvo mudança explícita de prioridade.

Se uma premissa mudar, o histórico desta tabela deve ser preservado no Git e a arquitetura/ADR afetado deve ser revisado.

## 2. Premissas transversais

| ID | Status | Premissa | Implicação |
| --- | --- | --- | --- |
| A-001 | Confirmada | O produto implementará exatamente SC-04, SC-05, SC-06 e SC-20. | Novos processos não entram antes da conclusão ponta a ponta destes quatro. |
| A-002 | Confirmada | Todos os dados da demonstração são sintéticos. | Seeds, documentos, clientes e credenciais de portais não podem conter dados pessoais reais. |
| A-003 | Confirmada | As integrações externas serão simuladas, mas o núcleo das automações será real. | Simuladores têm contrato realista; regras, decisões, fila, histórico e falhas não retornam respostas preparadas. |
| A-004 | Confirmada | O portal terá administrador e operador com acesso restrito. | Autorização será aplicada no backend, inclusive em arquivos e endpoints parciais. |
| A-005 | Confirmada | Toda automação terá disparo manual. | SC-04 e SC-20 compartilham o mesmo caso de uso entre execução manual e agendada. |
| A-006 | Confirmada | SC-04 é diário, SC-05 e SC-06 são sob demanda e SC-20 é mensal. | O pulso Railway Cron publica apenas SC-04 e SC-20 quando vencidos. |
| A-007 | Aceita para o desafio | Datas são persistidas em UTC e apresentadas em `America/Sao_Paulo`. | Agendamentos e cálculos temporais precisam de relógio injetável e testes de fuso. |
| A-008 | Aceita para o desafio | PostgreSQL é a única fonte de verdade de estado operacional. | Redis pode ser limpo sem apagar histórico; o sistema reconcilia trabalhos pendentes. |
| A-009 | Aceita para o desafio | O portal será same-origin e autenticado por sessão Django. | Não haverá CORS, tokens JWT ou frontend/API implantados separadamente. |
| A-010 | Aceita para o desafio | O ambiente público usará recursos Railway que não entram em suspensão. | Deve haver orçamento e validade da conta até o fim do processo seletivo. |
| A-011 | Aceita para o desafio | A URL gerada pela Railway atende ao requisito público. | Domínio próprio é opcional e não bloqueia a entrega. |
| A-012 | Aceita para o desafio | Arquivos originais e derivados ficam em storage compatível com S3. | O disco do contêiner não é usado como persistência. |
| A-013 | Aceita para o desafio | O limite inicial de upload será 10 MiB por arquivo. | Arquivos maiores são recusados com mensagem operacional; o limite fica configurável. |
| A-014 | Validar | PDFs, imagens JPEG/PNG e documentos textuais cobrem os cenários de SC-04. | Tipos adicionais só entram se o seed ou o documento do desafio exigir. |
| A-015 | Aceita para o desafio | O seed é idempotente e cria cenários de sucesso, revisão, duplicidade e falha. | A demonstração pode ser repetida sem edição manual do banco. |
| A-016 | Aceita para o desafio | Credenciais públicas não serão commitadas no repositório. | Usuários são criados por comando usando variáveis; credenciais são entregues separadamente. |
| A-017 | Fora do escopo | Alta disponibilidade multi-região e recuperação automática regional. | Backup e restauração bastam para o desafio; a limitação será informada. |
| A-018 | Fora do escopo | Dados reais, integrações produtivas e homologação jurídica/fiscal. | A interface deixa claro que regras e massas são demonstrativas. |

## 3. SC-04 — Triagem inteligente

| ID | Status | Premissa | Implicação |
| --- | --- | --- | --- |
| A-04-01 | Aceita para o desafio | A entrada diária será uma caixa simulada com mensagens e anexos, além de upload manual para demonstração. | O port `DocumentInbox` suporta listagem incremental e o upload usa o mesmo pipeline após ingestão. |
| A-04-02 | Aceita para o desafio | CNPJ/CPF sintético, razão social e aliases são sinais possíveis de identificação de cliente. | Correspondência exata tem precedência sobre inferência do modelo. |
| A-04-03 | Aceita para o desafio | OpenAI será o primeiro adapter de classificação. | A chave e o modelo vêm de ambiente; o domínio não importa o SDK. |
| A-04-04 | Aceita para o desafio | A resposta do modelo será JSON estruturado e validado. | Texto livre, schema inválido ou tipo desconhecido não produz roteamento automático. |
| A-04-05 | Validar | O limiar inicial para arquivamento automático será 0,85 para tipo e cliente. | Abaixo do limiar o documento entra em revisão; o valor ficará configurável e será calibrado com o seed. |
| A-04-06 | Confirmada | Casos ambíguos não serão classificados silenciosamente. | A fila de revisão é parte do fluxo principal, não um cenário excepcional oculto. |
| A-04-07 | Aceita para o desafio | Correção humana não retreina o modelo automaticamente durante a semana. | A correção é registrada como feedback versionado para evolução posterior. |
| A-04-08 | Aceita para o desafio | Indisponibilidade da OpenAI não será escondida por um fake em produção. | A execução falha de forma compreensível ou encaminha para revisão conforme a etapa alcançada. |
| A-04-09 | Aceita para o desafio | Somente conteúdo sintético e minimizado será enviado ao provedor. | Logs não guardam corpo integral nem resposta sensível; guardam IDs, versão e métricas. |
| A-04-10 | Validar | OCR será necessário para pelo menos um documento de demonstração. | A imagem do worker deve conter a dependência escolhida e um teste de qualidade básico. |

## 4. SC-05 — Bloqueio e desbloqueio

Os nomes abaixo são aliases arquiteturais para facilitar implementação; não afirmam nomes oficiais dos sistemas reais.

| ID | Status | Premissa | Implicação |
| --- | --- | --- | --- |
| A-05-01 | Aceita para o desafio | Haverá um simulador privado representando os portais necessários, incluindo um sistema de tarefas. | A fronteira é HTTP/HTML; o domínio nunca consulta as tabelas internas do simulador. |
| A-05-02 | Aceita para o desafio | Playwright operará os portais pelo mesmo caminho visível a um usuário. | Alteração direta no banco ou endpoint oculto do simulador não conta como execução RPA. |
| A-05-03 | Aceita para o desafio | Cada portal terá Page Object e adapter independentes. | Seletores e credenciais podem mudar sem alterar o caso de uso. |
| A-05-04 | Confirmada | No sistema de tarefas, bloqueio é representado por troca de responsável/marcador, preservando histórico. | O estado anterior deve ser capturado para desbloqueio/compensação. |
| A-05-05 | Aceita para o desafio | Passos já concluídos são idempotentes e não são repetidos na retomada. | Cada passo recebe chave própria e conserva tentativa/resultado. |
| A-05-06 | Aceita para o desafio | Falha após sucesso parcial inicia compensação automática apenas quando a ação inversa é conhecida e segura. | Compensação também é auditada; falha de compensação produz `PARTIALLY_FAILED`. |
| A-05-07 | Aceita para o desafio | O administrador poderá selecionar um cenário determinístico de timeout ou falha para a próxima execução. | A apresentação demonstra resiliência sem erro aleatório. |
| A-05-08 | Validar | A ordem exata dos portais será definida a partir da reversibilidade e da criticidade. | Antes de codificar a saga, registrar ordem, pré-condições e compensação por etapa. |
| A-05-09 | Aceita para o desafio | Uma instância de navegador por execução e concorrência baixa bastam ao volume sintético. | Não haverá browser farm nem paralelismo agressivo. |

## 5. SC-06 — Briefing societário

| ID | Status | Premissa | Implicação |
| --- | --- | --- | --- |
| A-06-01 | Confirmada | Perguntas e regras condicionais devem ser configuráveis. | Regras não serão codificadas apenas em templates ou JavaScript. |
| A-06-02 | Aceita para o desafio | A DSL inicial suporta `equals`, `not_equals`, `in`, `all` e `any`. | Operadores novos exigem validação e versão; não haverá execução de código arbitrário. |
| A-06-03 | Confirmada | Cliente de outro estado e sócio casado são cenários obrigatórios no seed. | Testes unitários e E2E cobrem ambos. |
| A-06-04 | Aceita para o desafio | Um template publicado é imutável; alterações criam nova versão. | Respostas históricas continuam interpretáveis. |
| A-06-05 | Aceita para o desafio | O operador pode preencher briefings; apenas administrador autorizado altera templates. | RBAC distingue uso operacional de configuração. |
| A-06-06 | Aceita para o desafio | O resultado principal é uma visualização consolidada com download em PDF. | O PDF é derivado da versão imutável e exige autorização no momento do download. |
| A-06-07 | Fora do escopo | Editor visual genérico de arrastar e soltar. | A configuração inicial pode usar formulários administrativos estruturados. |
| A-06-08 | Aceita para o desafio | São Paulo é a UF de referência do template demonstrativo. | Qualquer outra UF ativa o bloco interestadual; a referência pode mudar em nova versão do template. |
| A-06-09 | Aceita para o desafio | O bloco interestadual pede órgão e número sintético de registro na origem. | Os campos demonstram a ramificação e não constituem checklist jurídico homologado. |
| A-06-10 | Aceita para o desafio | O operador declara se existe sócio casado e identifica qual sócio sintético está nesse estado civil. | A resposta positiva torna identificação e regime de casamento obrigatórios. |
| A-06-11 | Aceita para o desafio | Um integrante autorizado da área pode concluir um caso aberto por outro. | Abertura e conclusão registram atores distintos; a evidência concluída é imutável e não pode ser excluída. |

## 6. SC-20 — Certificados digitais

| ID | Status | Premissa | Implicação |
| --- | --- | --- | --- |
| A-20-01 | Confirmada | A janela principal é de 60 dias e a periodicidade é mensal. | Não alterar para varredura diária por conveniência técnica. |
| A-20-02 | Aceita para o desafio | A execução ocorre no primeiro dia do mês às 08:00 de São Paulo. | Cron e idempotência usam competência `AAAA-MM`; horário fica configurável. |
| A-20-03 | Aceita para o desafio | Uma comunicação é deduplicada por certificado, data de validade, canal e política. | Reexecução no mesmo período não produz spam. |
| A-20-04 | Validar | Um único aviso ao entrar na janela de 60 dias é suficiente para a primeira versão. | Faixas adicionais de 30/15/7 dias só serão adotadas como regra documentada. |
| A-20-05 | Aceita para o desafio | O canal de comunicação é simulado e registra destinatário, conteúdo resumido, horário e resultado. | “Enviado” só existe após sucesso retornado pelo adapter. |
| A-20-06 | Aceita para o desafio | Falha no canal não altera o fato de o certificado estar em risco. | Resultado separa seleção correta de falha de entrega e permite retentativa. |
| A-20-07 | Validar | Certificados substituídos ou revogados deixam de gerar comunicação. | O modelo precisa representar estado e manter o histórico anterior. |

## 7. Segurança, privacidade e operação

| ID | Status | Premissa | Implicação |
| --- | --- | --- | --- |
| A-SEC-01 | Aceita para o desafio | Sessão expira após período de inatividade configurável. | Requisições HTMX também redirecionam corretamente para login. |
| A-SEC-02 | Aceita para o desafio | O papel de administrador do negócio não implica acesso ao shell ou à Railway. | Acesso à infraestrutura permanece separado. |
| A-SEC-03 | Aceita para o desafio | Downloads exigem autorização no momento da solicitação. | Conhecer uma chave S3 não concede acesso permanente. |
| A-SEC-04 | Aceita para o desafio | Extensão, MIME, tamanho e nome de upload serão validados. | Conteúdo ativo não será renderizado diretamente no navegador. |
| A-SEC-05 | Fora do escopo | Antivírus completo no upload. | Dados são sintéticos; a limitação e o ponto de extensão serão documentados. |
| A-OPS-01 | Aceita para o desafio | Logs estruturados da Railway e histórico interno bastam para operação inicial. | Sentry é opcional; stack própria de métricas não entra na semana. |
| A-OPS-02 | Aceita para o desafio | Um backup diário de PostgreSQL e um ensaio de restauração são suficientes. | RPO/RTO formais ficam fora do desafio. |
| A-OPS-03 | Aceita para o desafio | Cada pulso do scheduler é curto e termina após publicar os vencidos. | Sobreposição é evitada pela plataforma e duplicidade adicional pelo banco. |

## 8. Riscos derivados

| Risco | Probabilidade | Impacto | Mitigação inicial | Evidência esperada |
| --- | --- | --- | --- | --- |
| OpenAI indisponível, lenta ou sem saldo | Média | Alto em SC-04 | Timeout, retentativa limitada, revisão e falha honesta; não usar fake silencioso | Teste de timeout e mensagem no histórico |
| Saída de IA com schema ou confiança inválida | Média | Alto | Validação estrita e limiar configurável | Documento enviado à revisão |
| Seletores do RPA frágeis | Média | Alto | Page Objects, seletores estáveis, screenshots e testes de contrato | Falha identifica portal e etapa |
| Estado parcial em SC-05 | Média | Alto | Saga, estado anterior, compensação e retomada | Cenário `PARTIALLY_FAILED` demonstrável |
| Duplo disparo do scheduler | Baixa | Alto | Railway Cron curto e constraint de idempotência | Segunda publicação não duplica execução |
| Perda de arquivo no redeploy | Média sem mitigação | Alto | S3 e metadados no PostgreSQL | Arquivo permanece após nova versão |
| Regra de SC-06 diverge entre UI e backend | Média | Médio | Mesmo schema/regra avaliado no servidor; testes | Envio inválido é recusado no backend |
| Alertas repetidos no SC-20 | Média | Médio | Chave de deduplicação e histórico de comunicação | Reexecução não reenvia |
| Credenciais de demonstração vazarem | Baixa | Alto | Segredos fora do Git, entrega separada e rotação | Scanner do repositório sem segredos |
| Ambiente público suspenso | Média em plano gratuito | Alto | Recurso sem suspensão e monitor de uptime | URL permanece acessível |
| Escopo consumir o prazo | Alta | Alto | Congelamento dos quatro fluxos, sem tecnologias evitadas | Cada módulo possui caminho ponta a ponta antes de extras |

## 9. Itens que bloqueiam congelamento funcional, não o início técnico

1. Confirmar tipos documentais e clientes sintéticos do SC-04.
2. Calibrar o limiar de revisão com a massa sintética.
3. Registrar ordem, pré-condição e compensação de cada portal do SC-05.
4. Confirmar se SC-20 terá apenas o aviso de 60 dias ou faixas adicionais.
5. Confirmar o canal simulado e o conteúdo mínimo das comunicações.
6. Confirmar a conta Railway, orçamento e período de permanência pública.
7. Confirmar disponibilidade de credencial OpenAI para o ambiente demonstrável.

Nenhum desses itens justifica criar microsserviços ou adiar autenticação, histórico, fila, storage e estrutura modular.
