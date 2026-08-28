# ADR-0005 — OpenAI atrás de um adapter de classificação

- **Status:** Aceita
- **Data:** 2026-08-27
- **Decisores:** Engenharia/Arquitetura SheepContabil

## Contexto

SC-04 precisa classificar documentos e associá-los ao cliente, registrar confiança e encaminhar ambiguidade para revisão. Uma integração direta do SDK nas views ou tarefas acoplaria o domínio ao provedor e tornaria difícil validar, testar e substituir a inferência.

A saída probabilística também não pode ser tratada como comando confiável.

## Decisão

Definir um port de domínio `DocumentClassifier` e implementar inicialmente `OpenAIClassificationAdapter`.

Contrato normalizado de saída:

- tipo documental dentre valores permitidos;
- identificador/candidatos de cliente;
- confiança por decisão;
- evidências curtas e verificáveis, sem solicitar ou armazenar raciocínio interno;
- indicação de ambiguidade;
- versão do modelo, prompt e schema.

O adapter:

- usa resposta estruturada e valida schema estritamente;
- minimiza o conteúdo enviado;
- aplica timeout e retentativa limitada a erros transitórios;
- recebe chave e nome do modelo por variável de ambiente;
- não registra documento integral nem segredo em logs;
- traduz erros técnicos para categorias internas estáveis.

A política de aplicação, fora do adapter, decide entre arquivamento e revisão. Regras determinísticas, como CNPJ exato, têm precedência. Nenhum SDK OpenAI aparece no domínio.

Testes usam fake determinístico do port. O ambiente demonstrável não pode usar esse fake como se fosse classificação real. Se o provedor estiver indisponível, o sistema registra falha/revisão de forma honesta; não fabrica sucesso.

## Consequências positivas

- implementação rápida de classificação com saída estruturada;
- provedor substituível;
- testes independentes de rede;
- governança de confiança e revisão permanece interna;
- versão de modelo/prompt é auditável.

## Consequências negativas

- dependência de rede, credencial, cota, custo e latência;
- comportamento pode variar entre versões do modelo;
- dados enviados ao provedor exigiriam revisão jurídica antes de uso real;
- confiança declarada pelo modelo não é probabilidade calibrada por si só.

## Alternativas consideradas

### Chamar OpenAI diretamente da view ou task

Rejeitada por acoplamento, ausência de contrato e dificuldade de teste.

### Classificador local treinado no seed

É alternativa válida e futuro adapter. Não é a primeira escolha porque o corpus sintético inicial pode ser pequeno e produzir uma demonstração artificialmente ajustada.

### Regras por nome de arquivo

Rejeitadas como núcleo porque não demonstram classificação do conteúdo e são frágeis.

### Fake de IA em produção

Rejeitado porque simularia o miolo da automação, contrariando o desafio.

## Critérios de revisão

Trocar ou complementar o adapter se houver restrição de dados, falta de conectividade, custo proibitivo, requisitos de residência ou corpus suficiente para um modelo local validado.

