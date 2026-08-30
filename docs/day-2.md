# Dia 2 — SC-20 de ponta a ponta

| Campo | Valor |
| --- | --- |
| Data | 2026-08-30 |
| Versão | 0.2.0 |
| Processo | SC-20 — Vencimento de certificado digital |
| Natureza preservada | Controle sistematizado |
| Estado local | Concluído |
| Estado externo | A validar após CI e deploy da versão 0.2.0 |

## Resultado entregue

O SC-20 deixou de ser apenas um cartão no portal. A versão 0.2.0 mantém uma carteira sintética de certificados, seleciona apenas os ativos com vencimento entre a data-base e os 60 dias seguintes, registra um aviso simulado por e-mail ou WhatsApp e conserva todas as evidências de sucesso ou falha.

O disparo manual e o mensal usam exatamente o mesmo caso de uso. Reexecutar a verificação não envia novamente uma comunicação já registrada para a combinação de certificado, validade, canal e política. Uma falha de entrega permanece visível e pode ser tentada novamente sem apagar a tentativa original.

## Regras implementadas

| Regra | Decisão verificável |
| --- | --- |
| Janela | Inclusiva: da data-base até data-base + 60 dias |
| Elegibilidade | Apenas certificados no estado `Ativo` |
| Fora da janela | Vencidos e documentos com 61 dias ou mais não recebem aviso |
| Estado impeditivo | Revogados e substituídos permanecem no histórico, sem novos avisos |
| Canal | E-mail ou WhatsApp, selecionado no cadastro |
| Envio | Adapter determinístico; nenhuma mensagem real deixa o ambiente de demonstração |
| Deduplicação | Certificado + validade + canal + versão da política |
| Retentativa | Explícita, somente enquanto a comunicação atual continuar com falha |
| Obsolescência | Retentativa é dispensada se o certificado for inativado ou sua validade mudar |
| Periodicidade | Primeiro dia do mês, 08:00, em `America/Sao_Paulo` |
| Competência | Chave única `sc20:scheduled:AAAA-MM` e data-base no primeiro dia do mês |

## Arquitetura implementada

O domínio foi mantido dentro do monólito modular, sem acoplar a regra a views, Celery ou Railway:

```text
Portal / scheduler
        │ cria AutomationRun
        ▼
Celery task ──► caso de uso SC-20 ──► NotificationGateway
                       │                       │
                       ▼                       ▼
                 PostgreSQL             adapter simulado
                 ├─ certificado
                 ├─ comunicação lógica
                 └─ tentativas imutáveis
```

- `DigitalCertificate` representa o documento monitorado, seu contato, canal, validade e estado.
- `CertificateCommunication` é a comunicação lógica deduplicada e conserva a validade considerada, canal, política, destinatário e resultado atual.
- `CommunicationAttempt` é a evidência append-only de cada envio ou retentativa, vinculada à execução que a produziu.
- `AutomationRun` continua sendo o histórico comum do portal e recebe origem, transições, resumo, falha segura e métricas do SC-20.
- `NotificationGateway` isola o provedor. O adapter atual é deliberadamente sintético e produz IDs determinísticos; destinatários reservados falham na primeira tentativa e se recuperam na retentativa explícita, sem contato externo.

Uma exceção do adapter é convertida em tentativa auditável com falha. Erros estruturais do caso de uso marcam a execução como falha e preservam apenas o tipo técnico nos metadados; stack trace e segredos não são apresentados ao usuário.

## Fluxos da interface

1. O operador de Processos ou administrador abre o SC-20.
2. O painel mostra totais monitorados, próximos do vencimento, vencidos e avisos com falha.
3. Um formulário permite cadastrar somente dados sintéticos e valida CPF/CNPJ fictício, identificador único e contato obrigatório conforme o canal.
4. “Executar verificação” cria uma execução manual e a publica no worker.
5. O detalhe atualiza automaticamente enquanto estiver pendente, na fila ou em execução.
6. A página exibe a tentativa, destinatário, canal, horário, resultado e mensagem operacional.
7. Uma falha atual oferece “Tentar novamente”; depois do sucesso, a tentativa antiga aparece como superada e continua auditável.

Todas as ações POST mantêm CSRF e passam pela mesma autorização de área aplicada à leitura. O operador de Processos não ganha acesso aos outros módulos e um acesso direto fora de sua área continua retornando 404.

## Agendamento e recuperação

O recurso efêmero `scheduler` da Railway pulsa a cada 15 minutos. O comando termina rapidamente: consulta a competência no PostgreSQL e publica o UUID da execução no Redis. Ele não executa a varredura dentro do cron.

Se o broker falhar antes de o worker iniciar, a execução fica marcada com falha de publicação. O pulso seguinte pode recolocar somente esse mesmo UUID na fila. Execuções em andamento, concluídas ou com falha após o início não são reabertas automaticamente. O lock transacional e a chave mensal evitam duas publicações lógicas concorrentes.

## Massa de demonstração

O `seed_demo` permanece idempotente e agora cria sete certificados relativos ao dia da execução:

- dois ativos dentro da janela com entrega bem-sucedida, incluindo os dois canais;
- um ativo exatamente no limite de 60 dias com falha transitória deliberada;
- um ativo com 61 dias, fora da janela;
- um ativo já vencido;
- um revogado e um substituído dentro da janela temporal, ambos inelegíveis.

Não há CPF, CNPJ, telefone, e-mail ou certificado real na carga de demonstração.

## Critérios de aceite

- [x] Migração versionada para os três modelos do SC-20, constraints e índices.
- [x] Consulta de elegibilidade com limites inclusivos testados.
- [x] Adapter simulado com sucesso e falha determinísticos.
- [x] Idempotência de comunicação e de competência mensal.
- [x] Execução manual assíncrona pelo mesmo caso de uso do scheduler.
- [x] Cadastro funcional com validação por canal.
- [x] Histórico de execução e tentativa visível no portal.
- [x] Retentativa explícita e preservação da evidência anterior.
- [x] Proteção contra retentativa obsoleta.
- [x] RBAC e CSRF preservados.
- [x] Seed sintético e idempotente.
- [x] Scheduler Railway especificado sem domínio público ou segredo no Git.
- [x] Testes de domínio, interface, comando e regressão.
- [ ] CI verde para a versão 0.2.0.
- [ ] Migração, seed e smoke test confirmados no portal público.

## Limites conscientes

- O envio permanece simulado; integrar e-mail ou WhatsApp real exige provedor, consentimento, política de dados e gestão de credenciais que não fazem parte desta etapa.
- A primeira versão envia um único aviso ao entrar na janela. Faixas adicionais de 30, 15 ou 7 dias continuam dependentes de confirmação da regra de negócio.
- O portal cadastra e consulta certificados, mas ainda não oferece edição em massa ou importação; essas capacidades não são necessárias para demonstrar o processo.
- O scheduler implementa somente o SC-20 nesta versão. O mesmo pulso receberá o SC-04 quando o processo diário estiver funcional, sem mover sua regra para a infraestrutura.

## Evidências locais exigidas antes da publicação

```powershell
npm run build
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\ruff.exe format --check src tests
.\.venv\Scripts\mypy.exe src
.\.venv\Scripts\pytest.exe --cov --cov-report=term-missing
.\.venv\Scripts\python.exe src\manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe src\manage.py check
docker compose config --quiet
```

Após o deploy, este documento deve registrar a execução do CI, a aplicação da migração, a carga sintética e o smoke test manual do fluxo completo na URL pública.

## Resultado da validação local

Em 30/08/2026, a suíte concluiu 27 testes com 90,41% de cobertura. Ruff, formatação, mypy estrito, Django checks, conferência de migrações, build de assets, validação do Compose e build da imagem `sheepcontabil:0.2.0` passaram.

O ensaio autenticado em navegador, com servidor, Redis e worker reais, confirmou:

- primeira execução: três certificados selecionados, dois avisos enviados e uma falha transitória auditada;
- retentativa: tentativa `#2` enviada com sucesso, preservando a evidência `#1`;
- reexecução: três certificados encontrados, zero aviso reenviado e três comunicações deduplicadas;
- layout validado nos breakpoints móvel e desktop, com e-mail e WhatsApp apresentados corretamente.
