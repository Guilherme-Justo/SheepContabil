# ADR-0009 — Decisões arquiteturais explicitamente evitadas

- **Status:** Aceita
- **Data:** 2026-08-27
- **Decisores:** Engenharia/Arquitetura SheepContabil

## Contexto

O prazo curto cria dois riscos opostos: solução cenográfica demais ou infraestrutura sofisticada sem fluxos concluídos. Este ADR registra limites de complexidade para evitar que preferências tecnológicas silenciosamente ampliem o escopo.

## Decisão

As escolhas abaixo não serão introduzidas na entrega inicial sem revisão explícita deste ADR.

| Decisão evitada | Motivo | Gatilho legítimo de revisão |
| --- | --- | --- |
| Microsserviços por SC | Rede e consistência distribuída sem necessidade | Times, releases ou escala realmente independentes |
| SPA e API implantadas separadamente | Duplica contrato, autenticação, build e deploy | Clientes múltiplos ou equipe frontend independente |
| JWT no armazenamento do navegador | Complexidade e risco sem benefício same-origin | API pública ou cliente não-browser |
| GraphQL | Não há grafo/consumidores que justifiquem schema adicional | Diversos clientes com necessidades de consulta distintas |
| Kafka/event streaming | Volume e topologia não justificam operação | Throughput alto e múltiplos consumidores independentes |
| Kubernetes | Nenhum requisito de cluster | Escala/equipe/plataforma corporativa existentes |
| CQRS e event sourcing | Sobrecarga de modelo, projeções e operação | Auditoria temporal e escala que não caibam no modelo atual |
| Banco por módulo | Consistência distribuída e mais operação | Isolamento regulatório ou ciclo de vida independente |
| SQLite em produção | Divergência e limitação de concorrência | Nenhum para a arquitetura integrada atual |
| Redis como fonte de verdade | Broker efêmero não substitui histórico | Nenhum para dados auditáveis |
| Arquivos no disco do contêiner | Perda em redeploy e acoplamento de réplica | Somente temporários descartáveis |
| BLOBs no PostgreSQL | Crescimento de backup e tráfego | Artefatos mínimos e requisito transacional comprovado |
| Cron no processo web | Duplicidade e omissão em réplicas/redeploy | Nenhum enquanto houver scheduler dedicado |
| Execução longa na requisição | Timeout e ausência de recuperação | Operação comprovadamente curta e atômica |
| WebSockets | Polling atende o volume e reduz operação | Atualização subsegundo ou alta frequência comprovada |
| Chamadas OpenAI nas views/domain | Acoplamento e resposta não validada | Nenhum; sempre usar port/adapter |
| Fake de IA no ambiente demonstrável | Simula o miolo do SC-04 | Somente testes automatizados |
| Alteração direta no banco dos simuladores | Falsa execução de RPA | Somente preparação/reset controlado do seed |
| Falha aleatória nos mocks | Demonstração e testes não reproduzíveis | Testes de caos separados e controlados |
| Regras SC-06 hardcoded no frontend | Viola configurabilidade e backend pode divergir | Nenhum para regra de negócio |
| Arquivamento automático de baixa confiança | Risco de cliente/tipo incorreto | Somente após métricas e política formal |
| Segredos e credenciais demo no Git | Exposição do ambiente público | Nenhum |
| Terraform completo no Dia 1 | Retorno baixo no PaaS e prazo curto | Infraestrutura estável que precise reprodução entre contas |
| Stack própria de métricas/tracing | Operação desproporcional | SLA e volume que exijam telemetria dedicada |

## Consequências positivas

- foco em fluxos completos, falhas e experiência operacional;
- menor superfície de deploy e segurança;
- decisões de expansão se tornam explícitas e justificáveis;
- reduz risco de não concluir os quatro processos.

## Consequências negativas

- algumas demonstrações de infraestrutura avançada ficam de fora;
- futura evolução pode exigir extração gradual de componentes;
- a equipe deve aceitar soluções mais simples mesmo conhecendo alternativas sofisticadas.

## Regra de exceção

Uma decisão evitada só pode entrar se:

1. um requisito ou risco concreto for registrado;
2. a alternativa atual tiver sido medida como insuficiente;
3. impacto no prazo e nos quatro fluxos for aceito;
4. este ADR ou um ADR substituto for atualizado antes da implementação.
