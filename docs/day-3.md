# Dia 3 — SC-06 de ponta a ponta

| Campo | Valor |
| --- | --- |
| Data | 2026-08-31 |
| Versão | 0.3.0 |
| Processo | SC-06 — Briefing societário com perguntas condicionais |
| Natureza preservada | Controle sistematizado |
| Estado local | Concluído |
| Estado externo | Concluído no ambiente `production` da Railway |

## Resultado entregue

O SC-06 agora permite iniciar um atendimento societário, salvar e retomar um rascunho, revelar perguntas conforme as respostas, bloquear uma conclusão incompleta e consultar o resultado consolidado. Cada caso fica ligado ao histórico comum de execuções do portal e, depois de concluído, pode ser baixado como PDF com a identidade SheepContabil.

A interface reage imediatamente para orientar o operador, mas não decide a validade do caso. O servidor reprocessa o mesmo schema, descarta campos desconhecidos ou ocultos e só conclui quando todas as respostas obrigatórias do caminho ativo são válidas.

## Leitura do requisito e premissas explícitas

O documento do desafio exige dois desvios mínimos: cliente de outro estado abre um bloco adicional e sócio casado exige o regime de casamento. Ele não define a UF de referência, as perguntas exatas nem o formato do resultado. Para tornar a demonstração objetiva sem apresentar suposições como regras legais, foram adotadas estas premissas reversíveis:

| Decisão | Premissa desta versão |
| --- | --- |
| UF de referência | São Paulo; qualquer outra UF ativa o bloco interestadual |
| Processo inicial | Abertura de empresa ou alteração contratual |
| Bloco interestadual | Órgão de registro, número sintético na origem e observação opcional |
| Quadro societário | O operador declara se há sócio casado; em caso positivo, identifica o sócio sintético e o regime |
| Resultado | Visualização consolidada no portal e PDF gerado sob autorização |
| Natureza dos dados | Todos os nomes, documentos, e-mails, endereços e registros são sintéticos |

Esses campos organizam a demonstração; não constituem checklist jurídico ou homologação societária para uso real.

## Arquitetura implementada

```text
Template estável
    └─ versão publicada e imutável (schema JSON validado)
                     │
                     ▼
Portal + Alpine ──► formulário Django ──► serviço SC-06
      reação UX             │                   │
                            │                   ├─ reavalia condições
                            │                   ├─ descarta campos ocultos
                            │                   ├─ valida obrigatórios ativos
                            │                   └─ finaliza AutomationRun
                            ▼
                     PostgreSQL
                     ├─ briefing e respostas
                     ├─ versão exata do template
                     └─ execução, ator e duração
                            │
                            ▼
                     PDF gerado sob demanda
```

- `BriefingTemplate` é a identidade estável de uma configuração.
- `BriefingTemplateVersion` guarda o schema e não aceita alteração depois de publicada. Mudança funcional exige nova versão.
- `SocietaryBriefing` mantém cliente sintético, respostas sanitizadas, estado, autor da abertura, autor da conclusão, versão usada e execução correspondente.
- `AutomationRun` começa em execução quando o caso é aberto, permanece assim enquanto há rascunho e termina com sucesso apenas na conclusão válida.
- O PDF é derivado da evidência imutável; não cria uma segunda fonte de verdade.

## DSL condicional

A linguagem é pequena, declarativa e não executa código arbitrário:

| Recurso | Suporte |
| --- | --- |
| Operadores folha | `equals`, `not_equals`, `in` |
| Composição | `all`, `any`, com profundidade limitada |
| Tipos de pergunta | texto curto, texto longo, escolha, sim/não, data e e-mail |
| Validação textual | tamanho mínimo e máximo |
| Dependências | Uma condição só pode referenciar pergunta anterior; referências futuras e ciclos são recusados |

IDs duplicados, tipo desconhecido, opção repetida, operador indevido, condição vazia, valor condicional não canônico e referência inexistente invalidam o schema antes da publicação. Perguntas, opções, obrigatoriedade, ajuda, validações e visibilidade ficam no JSON; não há regra societária espalhada em view, template ou JavaScript.

## Ciclo de vida e concorrência

1. O operador autorizado cria um caso com nome e CPF/CNPJ sintéticos.
2. O sistema fixa a versão publicada mais recente e cria uma execução `running` na mesma transação.
3. “Salvar rascunho” normaliza somente respostas ativas e permite lacunas.
4. “Concluir briefing” bloqueia a linha no banco, recalcula o caminho e exige todos os campos obrigatórios ativos.
5. Sucesso grava respostas, horário, autor real da conclusão e execução `succeeded` de forma atômica, mesmo quando outro integrante autorizado iniciou o caso.
6. Uma segunda tentativa de edição ou conclusão é recusada; template e briefing concluído permanecem imutáveis e a evidência concluída não pode ser excluída, inclusive em lote.

O bloqueio transacional impede duas submissões concorrentes de concluírem o mesmo caso com estados divergentes.

## Interface e autorização

- O operador da área Societário e o administrador veem o módulo; o operador de Processos continua recebendo 404 inclusive em URLs diretas e downloads.
- O módulo mostra total, rascunhos, concluídos, versão ativa e lista de casos.
- Perguntas e seções aparecem e desaparecem sem recarregar a página; controles ocultos são desabilitados para não serem enviados pelo navegador.
- O progresso considera apenas perguntas visíveis.
- Erros retornam junto ao campo aplicável e não apagam o que foi informado.
- O resultado concluído fica somente para leitura, aparece no histórico de execução e oferece PDF.
- Todas as ações preservam sessão real, CSRF e a política de área do backend.

## PDF consolidado

O arquivo usa A4, logo e paleta SheepContabil, identificação sintética do caso, versão do template, autor e horário de conclusão, seções aplicáveis, respostas humanizadas, numeração de páginas e aviso de dados sintéticos. Opções exibem seus rótulos, booleanos exibem “Sim/Não” e datas usam `DD/MM/AAAA`.

A geração é recusada para rascunhos. O download revalida o acesso no momento da requisição, usa `application/pdf`, força anexo e aplica `nosniff`. A amostra de QA foi extraída e renderizada em duas páginas; não houve corte, sobreposição ou caracteres ausentes.

## Massa demonstrativa

O seed idempotente publica o template v1 e cria dois casos:

- um briefing concluído de abertura, cliente no Rio de Janeiro e sócia casada em comunhão parcial, cobrindo os dois desvios obrigatórios;
- um rascunho de alteração contratual, cliente em São Paulo e sem sócio casado, pronto para retomada.

Também é criado `operador.societario`, limitado à área Societário. Por padrão ele reutiliza a senha sintética configurada em `DEMO_OPERATOR_PASSWORD`; uma senha separada pode ser fornecida por `DEMO_SOCIETARY_OPERATOR_PASSWORD`, sempre fora do Git.

## Critérios de aceite

- [x] Migração versionada para template, versão e briefing, com constraints e índices.
- [x] Template publicado imutável e seleção da versão mais recente.
- [x] DSL validada, sem avaliação de código e sem condições societárias hardcoded.
- [x] Abertura e alteração contratual seguem caminhos distintos.
- [x] Cliente fora de SP recebe bloco adicional.
- [x] Sócio casado exige identificação e regime de casamento.
- [x] Campos desconhecidos e ocultos são descartados no servidor.
- [x] Rascunho pode ser salvo e retomado.
- [x] Conclusão incompleta é bloqueada pelo backend.
- [x] Conclusão e histórico de execução são atômicos e rastreáveis.
- [x] Autor da abertura e autor real da conclusão são preservados separadamente.
- [x] Evidência concluída bloqueia edição e exclusão individual ou em lote.
- [x] Resultado somente para leitura e PDF autorizado.
- [x] RBAC, sessão e CSRF preservados.
- [x] Seed sintético e idempotente com os dois desvios obrigatórios.
- [x] Testes de domínio, interface, PDF, regressão e seed.
- [x] CI verde e smoke test da versão 0.3.0 no portal público.

## Resultado da validação local

Em 30/08/2026, a suíte concluiu 41 testes com 90,47% de cobertura. Ruff, formatação, mypy estrito, Django checks e conferência de migrations passaram. O PDF completo foi extraído com `pypdf`, rasterizado com Poppler e inspecionado visualmente em todas as páginas.

O build dos assets, a validação do Compose, a imagem de produção e o ensaio autenticado no navegador passaram. O fluxo foi percorrido em desktop e mobile: criação, alternância dos desvios, salvamento, retomada, erro de obrigatoriedade, conclusão somente leitura e download do PDF, sem erro no console.

## Resultado da publicação

Em 31/08/2026, o commit `c7ccc2fca1b62b47ecbeff03219a2b40a1a8f010` entrou em `main` pelo [PR #1](https://github.com/Guilherme-Justo/SheepContabil/pull/1). A execução [GitHub Actions #33352697536](https://github.com/Guilherme-Justo/SheepContabil/actions/runs/33352697536) concluiu com sucesso os jobs de qualidade/testes e build da imagem de produção.

O release foi validado no projeto SheepContabil, ambiente `production` da Railway:

- o deployment web `76c2e32a-458e-4663-87b1-f00b47fb1a55` terminou com sucesso, aplicou as migrations `0004` e `0005`, executou o seed idempotente e iniciou o Gunicorn;
- o deployment worker `daa5f114-87c7-4a42-83ca-f03fda4e0dbb` terminou com sucesso, conectou-se ao Redis e ficou pronto para consumir a fila Celery;
- `GET /health/live` respondeu `200` com estado `ok`; `GET /health/ready` respondeu `200` com estado `ready` e PostgreSQL `ok`; a tela de login também respondeu `200` por HTTPS;
- o smoke test autenticado do operador Societário confirmou template v1, dois briefings sintéticos, sendo um rascunho e um concluído, evidência concluída somente para leitura e PDF de 14.011 bytes com `application/pdf`;
- o acesso anônimo ao módulo redirecionou para login, enquanto o operador de Processos não viu o SC-06 no painel e recebeu `404` na URL direta;
- `SEED_DEMO_ON_DEPLOY` foi ativada apenas para este release e voltou a `false` depois do seed, evitando alteração de senhas e massa demonstrativa nos próximos deploys.

O domínio público permanece [web-production-8f055.up.railway.app](https://web-production-8f055.up.railway.app). A associação automática da origem GitHub na Railway exibiu `GitHub Repo not found`; por isso este release foi enviado pela Railway CLI autenticada depois do CI verde. A aplicação e os serviços estão saudáveis, mas a autorização do GitHub App da Railway deve ser restabelecida antes de depender de auto-deploy no próximo release.

## Limites conscientes

- O template inicial é administrado como configuração estruturada; um editor visual genérico não faz parte do desafio.
- As perguntas demonstram o motor condicional, mas precisam de validação jurídica antes de uso real.
- O PDF é gerado sob demanda a partir do banco. Assinatura digital, protocolo e guarda legal não estão no escopo.
- Não há colaboração simultânea em tempo real na mesma tela; o bloqueio do servidor garante consistência na conclusão.
- Cancelamento e reabertura deliberada de casos podem ser adicionados depois de uma regra de negócio explícita.
