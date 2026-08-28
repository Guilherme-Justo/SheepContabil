# ADR-0001 — Monólito modular com Django e HTMX

- **Status:** Aceita
- **Data:** 2026-08-27
- **Decisores:** Engenharia/Arquitetura SheepContabil

## Contexto

O desafio exige quatro módulos, autenticação, RBAC, execução assíncrona, histórico, arquivos, agendamento e uma URL pública em uma semana. Microsserviços ou frontend/backend independentes aumentariam contratos, autenticação distribuída, observabilidade e operações sem atender requisito adicional.

Ao mesmo tempo, um único conjunto de arquivos sem limites modulares dificultaria testes, evolução e substituição dos mocks.

## Decisão

Adotar um **monólito modular Django 5.2 LTS**, com páginas server-rendered, HTMX para interações parciais, Alpine.js somente para estado visual local e Tailwind CSS para o design system.

O produto é uma única unidade de versão e implantação. Web, Celery worker e o comando efêmero de cron são processos do mesmo sistema e compartilham código e banco.

Limites internos:

- `core` contém capacidades transversais estáveis;
- cada SC é um módulo Django separado;
- módulos não importam internals uns dos outros;
- views chamam serviços/casos de uso;
- integrações são acessadas por ports e adapters;
- regras de domínio não ficam em templates, tarefas ou JavaScript.

## Consequências positivas

- autenticação, sessão, forms, ORM, migrations e proteção CSRF já integrados;
- uma URL e uma política same-origin;
- menor tempo até um fluxo ponta a ponta;
- transações locais simples;
- módulos testáveis e substituíveis sem operação distribuída;
- frontend moderno sem duplicar estado de domínio.

## Consequências negativas

- escala e deploy ocorrem inicialmente para o conjunto da aplicação;
- disciplina de dependência é necessária para evitar um monólito acoplado;
- HTMX exige convenções claras para fragments, redirects e erros de sessão;
- equipes especializadas em SPA podem ter menos isolamento de trabalho.

## Alternativas consideradas

### Next.js/React + API FastAPI ou Django REST

Rejeitada para a semana por duplicar build, contratos, validação, autenticação e deploy. Pode ser revista se existir uma equipe frontend independente ou clientes externos consumindo uma API pública.

### Microsserviços por processo SC

Rejeitada porque introduz rede, consistência distribuída, múltiplos pipelines e observabilidade sem necessidade de escala independente demonstrada.

### Aplicação sem módulos explícitos

Rejeitada porque dificultaria demonstrar separação, testes e substituição das integrações.

## Critérios de revisão

Reavaliar se houver pelo menos um destes fatores:

- equipes com ciclos de release realmente independentes;
- requisitos de escala ou isolamento incompatíveis entre módulos;
- API pública consumida por múltiplos clientes;
- fronteira regulatória ou de segurança que exija isolamento de processo/dados.
