# ADR-0004 — Storage compatível com S3 para arquivos e artefatos

- **Status:** Aceita
- **Data:** 2026-08-27
- **Decisores:** Engenharia/Arquitetura SheepContabil

## Contexto

SC-04 trabalha com documentos originais e SC-05 pode produzir screenshots e traces. Relatórios e downloads também precisam sobreviver a reinícios e novos deploys. O filesystem de contêineres cloud é efêmero.

## Decisão

Usar object storage compatível com S3 em produção, acessado pelo port `ObjectStorage`. Ambiente local usa MinIO ou adapter compatível.

O banco armazena:

- chave opaca do objeto;
- nome original apenas como metadado higienizado;
- MIME type validado;
- tamanho;
- hash SHA-256;
- proprietário/cliente;
- execução e artefato relacionados;
- datas e política de retenção.

Políticas:

- objetos originais auditáveis são imutáveis;
- chaves não contêm entrada direta do usuário;
- upload tem limite e allowlist;
- download exige autorização do backend;
- URL assinada tem expiração curta;
- bucket não é público;
- temporários locais são removidos após uso.

## Consequências positivas

- arquivos sobrevivem a deploys;
- aplicação permanece stateless no filesystem;
- troca de fornecedor é possível pelo protocolo S3;
- banco permanece menor;
- autorização e auditoria podem ser centralizadas.

## Consequências negativas

- testes precisam cobrir banco e storage em conjunto;
- exclusão/retorno parcial exige reconciliação;
- URLs assinadas e políticas de bucket precisam ser configuradas corretamente;
- há custo e dependência adicional da plataforma.

## Alternativas consideradas

### Disco do contêiner ou volume local

Rejeitado por efemeridade, acoplamento de réplica e dificuldade de acesso pelo worker.

### BLOB no PostgreSQL

Rejeitado por custo de backup, crescimento do banco e tráfego.

### Bucket público

Rejeitado porque conhecer a URL não pode ignorar RBAC.

## Critérios de revisão

Reavaliar retenção, versionamento, antivírus e lifecycle quando arquivos reais, requisitos regulatórios ou volumes de produção forem conhecidos.
