# Dia 4 — SC-04 de ponta a ponta

| Campo | Valor |
| --- | --- |
| Data | 2026-08-31 |
| Versão | 0.4.0 |
| Processo | SC-04 — Triagem da caixa de arquivos |
| Natureza preservada | Agente de IA |
| Estado local | Implementado e validado para release |
| Estado externo | Aguardando credencial e modelo OpenAI no worker Railway |

## Resultado entregue

O SC-04 recebe anexos sintéticos por upload ou caixa demonstrativa, valida o arquivo antes da persistência, preserva o original em storage privado, extrai texto, solicita classificação estruturada à OpenAI e aplica uma política interna antes de decidir o destino. Conteúdo idêntico ou origem repetida permanece auditável sem classificação ou cópia duplicada.

O modelo não controla o roteamento. Uma decisão automática só é aceita quando tipo e cliente são permitidos, as duas confianças atingem `0,85`, há evidência curta e a resposta não é ambígua. Qualquer dúvida, resposta inválida ou indisponibilidade do classificador abre revisão humana; o portal nunca apresenta um fake como sucesso de IA.

## Arquitetura implementada

```text
Upload / caixa sintética
          │
          ▼
assinatura + estrutura + limite
          │
          ├── origem repetida ─────────────┐
          ├── hash repetido ───────────────┤ auditoria sem reprocessar
          ▼                               │
original S3 por SHA-256                   │
          │                               │
          ▼                               │
pypdf / TXT UTF-8 / OCR Tesseract         │
          │                               │
          ▼                               │
OpenAI Responses API + JSON Schema        │
          │                               │
          ▼                               │
política v1 ── baixa confiança ──► revisão humana
          │                               │
          ▼                               │
decisão imutável + cópia idempotente ◄────┘
          │
          ▼
PostgreSQL + histórico comum de execução
```

As fronteiras `DocumentInbox`, `ObjectStorage`, `TextExtractor` e `DocumentClassifier` isolam caixa, S3, OCR e OpenAI. A task Celery recebe somente o UUID da execução. PostgreSQL é a fonte de verdade; Redis transporta trabalho e nunca guarda arquivo ou resultado oficial.

## Domínio e rastreabilidade

- `FiscalDocument` representa o conteúdo canônico pelo SHA-256 e conserva somente chave opaca, metadados, projeção atual e trecho minimizado.
- `DocumentIntake` registra cada origem observada e seu nome higienizado.
- `DocumentRunItem` liga a ocorrência à execução com resultado novo, origem repetida ou hash repetido.
- `DocumentClassificationAttempt` é append-only e conserva provedor, modelo, versões de prompt/schema, hash/tamanho da entrada, saída validada e erro seguro; prompt, documento integral e resposta bruta não são persistidos.
- `DocumentReview` registra motivo, correção, justificativa, pessoa e horário.
- `DocumentDecision` separa a verdade final da predição e é imutável.
- `DocumentRouting` mantém destino determinístico, tentativas, falha e confirmação da cópia sem remover o original.

Constraints protegem faixas de confiança, sequência de tentativas, consistência temporal, resolução de revisão, origem da decisão, duplicidade e estado do roteamento. Atores de revisão/decisão usam `PROTECT`, preservando a evidência.

## Ingestão, extração e segurança de arquivo

Formatos aceitos: PDF, PNG, JPEG e TXT UTF-8, todos com até 10 MiB. O backend ignora MIME declarado e detecta o formato pela assinatura/conteúdo. Imagens passam por verificação estrutural e limite de pixels antes da descompressão; PDFs passam por parse, limite de páginas e rejeição segura de corrupção. PDFs sem texto pesquisável são informados como não suportados neste ciclo, em vez de produzir extração vazia.

TXT usa decodificação UTF-8; PDF textual usa `pypdf`; PNG/JPEG usam Tesseract em português dentro da imagem Docker. Texto extraído é limitado antes do envio ao classificador. Os nomes não entram em chaves de storage.

O adapter S3 usa operação condicional, metadata SHA-256, verificação de tamanho/hash, leitura limitada e fechamento garantido do stream. A cópia final é determinística e idempotente; o original não é movido ou apagado.

## Classificação por IA

O adapter usa a Responses API com JSON Schema estrito e `store=false`. O schema limita tipo documental e cliente aos candidatos ativos, exige confiança entre zero e um, pelo menos uma evidência curta e indicação explícita de ambiguidade. O texto do documento é tratado como entrada não confiável e não pode alterar as instruções do sistema.

Timeout, conexão, rate limit, HTTP 408/409 e falhas 5xx viram indisponibilidade transitória segura. Recusas permanentes e respostas incompletas/inválidas têm categorias distintas. A integração não registra conteúdo, segredo, prompt completo nem resposta bruta em logs.

A política determinística tenta primeiro CPF/CNPJ sintético completo e alias com limites de tokens. Um match exato tem precedência sobre o cliente sugerido pelo modelo, mas não ignora ambiguidade geral nem baixa confiança no tipo.

## Revisão humana e interface

O operador Fiscal e o administrador podem:

- processar a caixa sintética ou enviar um único arquivo;
- acompanhar métricas, filtros, duplicidades e polling da fila;
- abrir preview/download protegido do original;
- consultar extração, predição, confiança, evidências e timeline real;
- confirmar ou corrigir tipo/cliente com justificativa quando a sugestão mudar;
- repetir somente um encaminhamento que falhou, sem recriar a decisão.

Usuários de outras áreas recebem `404` em página, fragmentos HTMX, revisão, preview e download. Arquivos usam `private, no-store`, `nosniff` e política same-origin; nenhuma chave S3 aparece no HTML. Polling cessa em estados terminais ou de revisão.

## Concorrência, idempotência e agendamento

Há duas identidades independentes:

- `(source, source_reference)` impede reapresentação da mesma origem;
- `sha256` impede nova persistência/classificação do mesmo conteúdo.

As constraints resolvem a corrida final entre workers. Claims de documento ocorrem sob lock e somente `queued` pode entrar no pipeline. Redelivery marcado pelo broker encerra a tentativa interrompida, recoloca apenas documentos recuperáveis na fila e cria uma nova sequência; decisão ou revisão já registrada não é repetida.

O pulso Railway continua a cada 15 minutos. A partir das 08:00 em `America/Sao_Paulo`, ele cria no máximo `sc04:scheduled:AAAA-MM-DD` e publica a mesma task usada pela ação manual da caixa. O SC-20 mensal permanece no mesmo comando e preserva sua competência.

## Massa demonstrativa

O seed idempotente cria quatro clientes fiscais sintéticos e `operador.fiscal`, limitado à área Fiscal. A caixa contém:

- nota fiscal textual da Aurora;
- guia sintética da Horizonte;
- cópia byte a byte da nota, demonstrando deduplicação por hash;
- documento sem cliente confiável, demonstrando revisão;
- imagem de extrato da Lume, demonstrando OCR.

Todos os nomes, documentos e conteúdos são fictícios.

## Evidências de validação local

- `85` testes aprovados em conjunto, incluindo regressão de SC-06, SC-20, autenticação, RBAC, seed e saúde.
- Cobertura total de `85,72%`, acima do piso obrigatório de `75%`.
- Ruff sem violações e formatação consistente.
- Mypy estrito sem erros em `52` arquivos-fonte.
- Django sem problemas de sistema e sem migrações pendentes.
- Build dos assets concluído e configuração Docker Compose válida.
- Imagem Linux `sheepcontabil:0.4.0` construída integralmente com Python 3.13, Tesseract e idioma português.

## Critérios de aceite

- [x] Migração versionada com domínio relacional, constraints e índices.
- [x] Upload/caixa com validação por conteúdo e limite antes da persistência.
- [x] Storage S3 privado, endereçado por hash e verificado por integridade.
- [x] Extração TXT/PDF e OCR de PNG/JPEG no contêiner.
- [x] OpenAI atrás de port, saída estruturada estrita e `store=false`.
- [x] Política explícita de confiança, evidência, allowlist e ambiguidade.
- [x] Match determinístico de cliente com precedência documentada.
- [x] Revisão humana auditável sem sobrescrever a predição.
- [x] Decisão final e roteamento separados e idempotentes.
- [x] Idempotência por origem, hash, execução diária e destino.
- [x] Recuperação de redelivery sem duplicar decisão.
- [x] Portal Fiscal responsivo, acessível, com polling progressivo e falhas compreensíveis.
- [x] RBAC, sessão, CSRF, preview e download revalidados no servidor.
- [x] Seed sintético e operador Fiscal.
- [x] Testes de adapters, serviços, views, scheduler, regressão e seed.
- [x] Imagem de produção construída com OCR em português e assets compilados.
- [ ] Credencial/modelo OpenAI configurados e smoke real concluído na Railway.
- [ ] Release 0.4.0 publicado em web, worker e scheduler.

## Limites conscientes

- PDF escaneado multipágina não recebe OCR nesta versão; o usuário deve fornecer imagem ou PDF pesquisável.
- Confiança declarada pelo modelo não é probabilidade calibrada. O limiar de 0,85 é política demonstrativa e deve ser reavaliado com corpus rotulado.
- A caixa é um adapter sintético determinístico; Gmail/Outlook não entram no escopo do desafio.
- Não há antivírus, DLP, lifecycle ou retenção legal para arquivos reais. Antes de produção, esses controles e a base jurídica para envio à IA precisam de validação.
- Banco/Redis estão em região distinta de web/worker no ambiente Trial; a topologia deve ser alinhada antes de carga real.
