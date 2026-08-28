# ADR-0006 — Playwright para o RPA do SC-05

- **Status:** Aceita
- **Data:** 2026-08-27
- **Decisores:** Engenharia/Arquitetura SheepContabil

## Contexto

SC-05 foi selecionado como demonstração de RPA em múltiplos sistemas. Atualizar diretamente tabelas dos simuladores ou chamar um endpoint interno privilegiado provaria apenas integração, não automação da interface usada pelo operador.

O fluxo também pode terminar parcialmente aplicado e precisa de reversão ou retomada.

## Decisão

Usar Playwright/Chromium no Celery worker para operar portais HTML simulados. Os simuladores ficam em serviço privado e possuem dados e falhas determinísticas.

Para cada portal:

- um port de domínio;
- um adapter Playwright;
- Page Object próprio;
- seletores estáveis por papel ou `data-testid` no simulador;
- contexto de navegador isolado por execução;
- timeout explícito;
- screenshot em marcos e falhas;
- trace quando necessário ao diagnóstico;
- credenciais e URL por ambiente.

O caso de uso implementa saga:

1. registra o estado anterior;
2. executa passos idempotentes;
3. persiste cada resultado;
4. compensa passos já aplicados quando houver inversa segura;
5. marca falha parcial se execução ou compensação não terminar;
6. permite retomada sem repetir passos confirmados.

## Consequências positivas

- natureza RPA demonstrável e inspecionável;
- screenshots ajudam auditoria e apresentação;
- Page Objects isolam mudanças de tela;
- simuladores permitem demonstrar timeout e recuperação;
- caminho de substituição por portais reais fica explícito.

## Consequências negativas

- imagem do worker maior por conter navegador;
- automação de UI é mais lenta e frágil que API;
- seletores e sincronização exigem testes;
- concorrência deve ser limitada para controlar memória.

## Alternativas consideradas

### Alterar diretamente o banco do simulador

Rejeitada porque atravessa a fronteira e falseia a execução RPA.

### Usar apenas APIs HTTP dos simuladores

Rejeitada como implementação principal de SC-05 porque enfraquece a demonstração de RPA. Pode existir como futuro adapter se o sistema real oferecer API oficial.

### Selenium

Viável, mas não escolhido por ergonomia de espera, traces e isolamento oferecidos pelo Playwright.

### Browser farm

Rejeitada por volume e complexidade desnecessários.

## Critérios de revisão

Preferir adapter de API oficial quando existir contrato estável e autorizado. Separar um worker RPA especializado se consumo ou frequência exigirem isolamento próprio.

