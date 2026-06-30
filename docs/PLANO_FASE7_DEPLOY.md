# Fase 7 — Deploy AWS (runbook)

> **Quem executa:** o **usuário** roda os comandos na AWS (a IA prepara tudo, mas
> NÃO toca na AWS nem faz deploy). Cada passo tem comando concreto.

**Goal:** subir o backend serverless (migrado e auditado, 141 testes) na AWS de forma
funcional e segura (MVP), com banco privado e segredos por stage.

**Arquitetura:** API Gateway (REST) → Lambda (Python 3.11, handlers nativos) → **RDS
PostgreSQL 18 privado** (pgvector, RLS) via **RDS Proxy**; Lambda **dentro da VPC**;
S3 privado p/ documentos; SES p/ e-mail; OpenAI p/ embeddings. Segredos no SSM por stage.

**Decisões tomadas (29/06):**
- **Região = `sa-east-1` (São Paulo)** — confirmada pelo console; o `serverless.yml` já está nela.
- Conta = **Contrato Visto** (`1126-1385-9096`), plano gratuito: **~US$109 de créditos / 152 dias**.
- Rede (alvo de produção) = **RDS privado + Lambda em VPC + RDS Proxy**.
- **Estratégia em 2 ETAPAS** (créditos limitados):
  - **7a — dev econômico (~US$15/mês):** RDS + Lambda em VPC **sem NAT, sem RDS Proxy**,
    backends **mock/local/log** (`ENVIRONMENT=dev` → `safety` permite). Valida auth/RLS/
    CRUD/paginação na nuvem barato. **Sem** S3/SES/OpenAI reais. Créditos rendem ~6 meses.
  - **7b — prod completo (depois):** + RDS Proxy + NAT + S3/SES/OpenAI reais + hardening.

---

## Parâmetros
```
REGION=sa-east-1
STAGE=dev                 # etapa 7a; depois 'prod' na 7b
ACCOUNT=112613859096      # conta "Contrato Visto"
```

## Etapa 7a — deploy dev econômico (FAZER PRIMEIRO)
Difere do runbook completo abaixo:
- **Pular** NAT Gateway, RDS Proxy, SES, OpenAI, bucket S3 (backends mock/local/log).
- Lambda **em VPC** acessa só o RDS (não precisa de internet em dev). `DB_HOST` (SSM dev)
  = endpoint **direto do RDS** (sem Proxy).
- SSM só **4 params**: `/contrato-visto/dev/{db_host,db_user,db_pass,jwt_secret}`.
- Após criar a VPC, adicionar ao `serverless.yml` o bloco com os IDs reais:
```yaml
  vpc:
    securityGroupIds:
      - sg-xxxxxxxx          # SG-LAMBDA
    subnetIds:
      - subnet-aaaa          # subnet privada AZ-a
      - subnet-bbbb          # subnet privada AZ-b
```
- `npx serverless deploy --stage dev --region sa-east-1` → smoke (`/health`, signup,
  login, criar client/case). Custo ~US$15/mês (só RDS).

## Pré-requisitos (na máquina que faz o deploy)
- AWS CLI v2 autenticado (`aws sts get-caller-identity`).
- Node + `npx serverless` (o repo já tem `serverless-python-requirements` em devDeps).
- Docker em execução (o plugin empacota deps Python com `dockerizePip: true`).

## GATES obrigatórios antes do 1º `sls deploy`
1. **RDS PG18 + pgvector** no ar (privado) e **role `cv_app` NÃO-owner** criada.
2. **migrations 001→004** aplicadas no RDS.
3. **SSM params** `/contrato-visto/${STAGE}/*` criados (db_host=endpoint do **Proxy**,
   db_user=cv_app, db_pass, jwt_secret forte, openai_key).
4. **Backends reais** por stage no ambiente (`EMAIL_BACKEND=ses`, `STORAGE_BACKEND=s3`,
   `EMBEDDINGS_BACKEND=openai`) — senão `enforce_production_safety` **bloqueia o boot**.
5. **Bucket S3** `contrato-visto-documents-${STAGE}` privado existente.
6. **SES** com identidade/domínio verificado (e fora do sandbox p/ produção).
7. **VPC**: subnets privadas + Security Groups (Lambda↔RDS) + RDS Proxy + acesso de
   saída a S3/SES/OpenAI (VPC endpoints p/ S3/SES; NAT p/ OpenAI/internet).

---

## Ordem de execução

### Tarefa 1 — Rede (VPC)
- [ ] Usar a VPC default ou criar uma. Garantir **2+ subnets privadas** (multi-AZ).
- [ ] SG-LAMBDA (egress liberado) e SG-RDS (ingress 5432 **apenas** do SG-LAMBDA).
- [ ] **VPC endpoints**: S3 (gateway) e SES/SSM/Secrets (interface) p/ evitar NAT onde
      possível; **NAT Gateway** se a Lambda precisar sair p/ a internet (OpenAI).

### Tarefa 2 — RDS PostgreSQL 18 + pgvector
- [ ] Criar RDS PG **18** (instância p/ MVP: `db.t4g.small`), **sem acesso público**,
      nas subnets privadas, SG-RDS.
- [ ] Conectar como master e habilitar a extensão:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```
- [ ] Restaurar/criar o schema (de `docs/schema_referencia.sql`).

### Tarefa 3 — Role de app NÃO-owner + migrations
```sql
-- role de aplicação (RLS aplica de verdade; sem BYPASSRLS, não-owner)
CREATE ROLE cv_app LOGIN PASSWORD '<senha-forte>';
GRANT USAGE ON SCHEMA public, audit TO cv_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO cv_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO cv_app;
-- aplicar as migrations (como owner/master):
\i migrations/001_rls_policies.sql
\i migrations/002_fix_audit_delete.sql
\i migrations/003_perf_indexes.sql
\i migrations/004_integrity_indexes.sql
```

### Tarefa 4 — RDS Proxy
- [ ] Criar **RDS Proxy** apontando para o RDS (pooling; evita esgotar conexões com
      muitas Lambdas). Secret do master no Secrets Manager.
- [ ] `DB_HOST` (SSM) = **endpoint do Proxy** (não o do RDS).

### Tarefa 5 — Segredos (SSM, por stage)
```bash
aws ssm put-parameter --region $REGION --name "/contrato-visto/$STAGE/db_host"   --type String       --value "<proxy-endpoint>"
aws ssm put-parameter --region $REGION --name "/contrato-visto/$STAGE/db_user"   --type String       --value "cv_app"
aws ssm put-parameter --region $REGION --name "/contrato-visto/$STAGE/db_pass"   --type SecureString  --value "<senha-forte>"
aws ssm put-parameter --region $REGION --name "/contrato-visto/$STAGE/jwt_secret" --type SecureString --value "<segredo >=32 bytes>"
aws ssm put-parameter --region $REGION --name "/contrato-visto/$STAGE/openai_key" --type SecureString --value "<openai-key>"
```

### Tarefa 6 — S3 + SES
```bash
aws s3api create-bucket --bucket contrato-visto-documents-$STAGE --region $REGION
aws s3api put-public-access-block --bucket contrato-visto-documents-$STAGE \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket contrato-visto-documents-$STAGE \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws ses verify-email-identity --region $REGION --email-address no-reply@<seu-dominio>
```

### Tarefa 7 — Ajustes no serverless.yml (a IA prepara quando a região for definida)
- [ ] `provider.region: ${REGION}`; `OPENAI_API_KEY: ${ssm:/contrato-visto/${stage}/openai_key}`.
- [ ] `EMAIL_BACKEND/STORAGE_BACKEND/EMBEDDINGS_BACKEND` reais por stage.
- [ ] Bloco `vpc:` (securityGroupIds=[SG-LAMBDA], subnetIds=[subnets privadas]).
- [ ] IAM: já tem S3 (bucket), SES, SSM por stage, logs. Conferir região nos ARNs.

### Tarefa 8 — Deploy + smoke test
```bash
npx serverless deploy --stage $STAGE --region $REGION
# smoke:
curl https://<api-id>.execute-api.$REGION.amazonaws.com/$STAGE/health         # 200 {status: ok}
# signup -> login -> (token) -> POST /clients -> POST /cases ...
```

---

## Hardening pós-MVP (depois do deploy funcionar) — diferidos das auditorias
- **Revogação de sessão** (token_version/`session_version` + revalidação no authorizer).
- **Rate limiting**: API Gateway **usage plans/throttling** + (opcional) **WAF**;
  reserved concurrency nas Lambdas.
- **Upload confiável**: confirmação pós-PUT (`HeadObject`/checksum/limite) e
  `s3:DeleteObject` + cleanup no `delete_case`.
- **FORCE ROW LEVEL SECURITY** (com ajuste de seeds/admin) + check de boot recusando
  role owner/superuser/BYPASSRLS.
- **CORS por ambiente** (origens específicas, não `*`).
- **Auditoria de acesso a PII** (`audit.data_access_log`).
- **Pipeline de ingestão de embeddings** (chunking/embedding assíncrono).
- **CloudWatch**: retention + alarmes (erros 5xx, throttles, latência).

## Estado do código (pronto para a Fase 7)
141 testes no PG18; migrations 001→004; `serverless.yml` com 28 functions, authorizer,
SSM por stage, package excludes, plugin. Diferidos acima são melhorias, não bloqueiam
um MVP funcional em ambiente controlado.
