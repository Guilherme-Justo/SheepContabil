# ADR-0007 — Autenticação por sessão Django e RBAC por área

- **Status:** Aceita
- **Data:** 2026-08-27
- **Decisores:** Engenharia/Arquitetura SheepContabil

## Contexto

O desafio exige autenticação real, sessão, administrador com acesso total e operador restrito aos módulos de sua área. O portal e as ações HTMX são same-origin, sem cliente móvel ou API pública.

## Decisão

Usar autenticação e sessão do Django com modelo de usuário próprio criado na primeira migração.

Autorização combina:

- papel `ADMINISTRATOR`;
- papel `OPERATOR`;
- associação de usuário a áreas;
- política que associa cada módulo a uma ou mais áreas;
- permissões adicionais para configuração, revisão e execução quando necessário.

Toda autorização é verificada no servidor em páginas, comandos, fragments HTMX, downloads e ações administrativas. A navegação reflete permissões, mas não é o mecanismo de controle.

Controles:

- cookie de sessão `HttpOnly`, `Secure` e `SameSite=Lax`;
- CSRF;
- hash de senha forte;
- expiração e logout;
- limitação de tentativas de login;
- rotação de credenciais;
- criação de contas demo por comando idempotente e segredo de ambiente;
- eventos de login e ações críticas auditáveis.

O papel administrativo do produto usa a interface SheepContabil. Django Admin, caso habilitado, fica restrito à manutenção técnica.

### Estado de implementação

No Dia 1 estão implementados o modelo próprio, sessão, CSRF, hash Argon2, expiração, logout por POST e RBAC por área no servidor. Limitação de tentativas, rotação/troca obrigatória e trilha append-only continuam como controles obrigatórios da entrega final; o campo `force_password_change` apenas reserva o contrato de dados e não deve ser apresentado como enforcement enquanto o fluxo não existir.

## Consequências positivas

- implementação madura e same-origin;
- menos risco que autenticação própria ou JWT no navegador;
- grupos, permissões e sessões já integrados ao framework;
- revogação de acesso imediata no servidor;
- testes de política simples.

## Consequências negativas

- clientes externos futuros exigirão estratégia adicional de API;
- associação papel/área precisa de modelagem explícita;
- fragments HTMX devem tratar sessão expirada corretamente;
- cuidado operacional é necessário ao entregar credenciais de avaliação.

## Alternativas consideradas

### JWT em `localStorage`

Rejeitado por ampliar exposição a XSS, exigir renovação/revogação e não trazer benefício a uma aplicação same-origin.

### OAuth/OIDC externo

Rejeitado para a semana por depender de outro provedor e configuração sem requisito correspondente. Pode ser adapter futuro.

### Apenas esconder módulos no frontend

Rejeitado porque não protege URLs, comandos ou downloads.

### Usar Django Admin como portal

Rejeitado porque não atende experiência, identidade visual e fluxo operacional esperados.

## Critérios de revisão

Adicionar OIDC/SSO e tokens de API quando houver identidade corporativa real, clientes não-browser ou integrações máquina a máquina.
