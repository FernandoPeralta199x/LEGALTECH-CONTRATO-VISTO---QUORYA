# PROGRESSO — Migração FastAPI → Serverless (contrato_visto)

> Documento de estado ("save point"). Atualizar a cada avanço. Última atualização:
> Fase 2 concluída, revisada e **endurecida** pelo Codex (25 testes). Auth/Users
> entrou no escopo (Fase A).

## Visão geral (dois projetos relacionados)

1. **LEGALTECH-CONTRATO-VISTO-main** (FastAPI, projeto anterior) — endurecimento
   de segurança feito em ondas; ver `docs/PLANO_CORRECAO_SEGURANCA.md` lá.
   Ondas 1–2 (SEC), 3 (rate limit Redis), 4a/4b (revisão humana), 5c (concorrência)
   concluídas e publicadas; 5a (RLS) / 5b (httpOnly) / 5d (AWS) pendentes.
2. **contrato_visto_backend-main** (Serverless, ESTE repo) — migração em andamento.
   Plano em `docs/PLANO_MIGRACAO_SERVERLESS.md`.

## Estado da migração serverless

| Fase | Escopo | Estado |
|------|--------|--------|
| 0 | Ambiente local (PG18 + role não-owner) | ✅ concluída |
| 1 | Fundação RLS-aware + policies + auditoria | ✅ concluída (**13 testes**), revisada pelo Codex |
| 2 | `cases` + `case_results` (RLS-aware + authorizer + **RBAC viewer** + **INSERT atômico**) | ✅ concluída (**25 testes**), revisada+endurecida (Codex) |
| **A** | **Auth/Users** (login + CRUD + forgot/reset + `jwt_authorizer`; signup→viewer; reset token hasheado/atômico; anti-timing; sem PII) | ✅ concluída+endurecida (**45 testes**) |
| 3 | `clients` (catálogo compartilhado, RBAC writer, sem RLS) | ✅ concluída+endurecida (**61 testes**) |
| 4 | `documents` (S3 presigned + RLS por `uploaded_by`) | ✅ concluída+endurecida (**69 testes**) |
| 5 | `search` / RAG (pgvector + embeddings, RLS na busca) | ✅ concluída+endurecida (**74 testes**) |
| 6 | services + remover código morto + requirements (boto3/openai; tirar FastAPI) | ✅ concluída (código morto **zerado**) |
| **E2E** | **GATE antes do deploy:** E2E da cadeia real ✅ (loop zerado) + varredura: migração 100% (28 fns), **sem código morto**, sem legado/`str(e)` — `health` migrado | ✅ **79 testes**; auditoria de qualidade (Codex) |
| 7 | hardening + **deploy AWS** (só após E2E verde + autorização) | roadmap |

### O que já foi feito (commits locais, branch `feat/migracao-fase-0-1`)
- `services/database.py`: `get_connection` (reuso + revalidação) + `tenant_tx`
  (SET LOCAL `app.user_id`/`app.user_role`) + timeouts; compat `Database`/`db`.
- `utils/safety.py`: corrigido (`os.getenv`, `ENVIRONMENT`).
- `utils/context.py`: helper compartilhado `get_user_from_event`/`require_user`.
- `migrations/001_rls_policies.sql`: policies RLS de escrita
  (cases/case_results/documents) + `audit.log_audit` SECURITY DEFINER.
- `tests/test_rls.py` (5) + `tests/test_safety.py` (4) + `tests/test_context.py` (4):
  **13 testes** — todos passam.
- Correções pós-revisão do Codex: `safety.py` bloqueia segredo ausente (None);
  `utils/auth.py` sem fallback inseguro; `get_connection` faz `rollback` antes do
  health check; `context.py` canoniza UUID.
- `docs/schema_referencia.sql` + `docs/dicionario_de_dados.md`: schema de referência.

**Fase 2 (`cases` + `case_results`) — revisada e endurecida pelo Codex:**
- `handlers/cases.py` + `handlers/case_results.py`: handlers nativos `@require_user`
  + `tenant_tx`; schema real; sem vazar `str(e)`; `rowcount`→404.
- `serverless.yml`: JWT Authorizer (token) nas 10 rotas + `JWT_SECRET` via SSM.
- Endurecimento (laudo Codex Fase 2): `context.py` lê o shape REAL do authorizer
  (achatado em `authorizer.<key>`, REST API) com fallback aninhado; `require_writer`
  (viewer só-leitura, 403); `case_results.create` atômico (INSERT…SELECT WHERE
  EXISTS, sem TOCTOU); `cases.create` valida `client_id` (→400); logs sem `str(e)`
  bruto (`type(e).__name__` + `pgcode`).
- `tests/test_cases_handlers.py` + `test_context.py`: **25 testes** no total, todos
  passam no PG18 (inclui viewer bloqueado/leitura, shape achatado/aninhado, client
  inexistente).

**Fase A (Auth/Users) — concluída (Codex revisando):**
- `handlers/users.py`: reescrito (handlers nativos). `public.users`/`password_resets`
  NÃO têm RLS → `simple_tx`. Signup PÚBLICO cria sempre `viewer` (corrige escalada de
  privilégio); `login` via `create_access_token` (JWT HS256 + exp, sem segredo padrão);
  RBAC: get (dono/admin), list/delete (admin), update (dono→name; role/status só admin);
  forgot/reset com `NOW()+INTERVAL`. Nunca loga PII (email/token) nem `str(e)`.
- `services/database.py`: `simple_tx` (transação sem RLS p/ tabelas globais).
- `services/email.py` (novo): backend SES (boto3) ou `log` (dev, sem expor token).
- `utils/auth.py`: `create_access_token`; `utils/context.py`: `require_role`.
- `schemas/user_schemas.py`: Pydantic 2; role corrigido (era `manager`, faltava
  `viewer`); signup sem `role` (`extra=forbid`); `UserUpdateSchema`; senha ≥8.
- `authorizers/jwt_authorizer.py`: não loga mais `email` (PII); erros sanitizados.
- `utils/safety.py`: bloqueia `EMAIL_BACKEND=log/mock` em produção.
- `serverless.yml`: authorizer nas rotas protegidas de `users`; públicas: signup/login/
  forgot/reset. Removido `handlers/users_new.py` (código morto).
- `tests/test_users_handlers.py` (16) + ajuste `test_safety` → **42 testes** no PG18.

### Achados validados na prática (PG18)
- Policies RLS de **escrita** não existiam → criadas.
- `audit.audit_log` tinha RLS sem policy → resolvido com SECURITY DEFINER no trigger.

## Como RETOMAR o ambiente (próxima sessão)

```bash
# 1) Subir o PostgreSQL 18 (container já existe; se parado, start)
docker start cv-pg18    # ou: docker run -d --name cv-pg18 -e POSTGRES_DB=contrato_visto \
                        #     -e POSTGRES_USER=dbadmin -e POSTGRES_PASSWORD=localdev_cv \
                        #     -p 5433:5432 pgvector/pgvector:pg18
# 2) (se recriar) restaurar schema + role + policies
docker exec -i cv-pg18 psql -U dbadmin -d contrato_visto < docs/schema_referencia.sql
#   criar role cv_app (LOGIN, NÃO-owner) + grants; aplicar migrations/001..004 em ordem
#   (001_rls_policies, 002_fix_audit_delete, 003_perf_indexes, 004_integrity_indexes)
# 3) venv + deps
cd apps/api ... (este repo)  python -m venv .venv
.venv/Scripts/python -m pip install psycopg2-binary python-dotenv pydantic PyJWT bcrypt email-validator pytest
# 4) rodar testes
.venv/Scripts/python -m pytest tests/ -v
```

- `.env` local (ignorado pelo git): PG18 em `localhost:5433`, user `cv_app`,
  `DB_PASS=localdev_app`, `DB_NAME=contrato_visto`, `ENVIRONMENT=local`.
- Role `cv_app` é NÃO-owner (sem BYPASSRLS) — exerce a RLS de verdade. `dbadmin`
  (owner) é só para setup/restore.

## Decisão arquitetural: Mangum vs handlers Lambda nativos (pesquisa)

A migração "FastAPI → serverless" tem **dois caminhos**:
- **A) Mangum (1 Lambda com o app FastAPI inteiro):** reusa rotas/middleware/deps
  do FastAPI; migração de **mínimo esforço** (envolve `app` com `Mangum(app)`);
  acesso ao contexto do authorizer via `request.scope["aws.event"]`. Custo: cold
  start +100–200ms (deps FastAPI/uvicorn). Bom p/ API complexa / reuso.
- **B) Handlers Lambda nativos por rota (o que ESTE repo já adota):** menos cold
  start, mas muitas Lambdas e mais config; é o que o dev começou.

**Status:** seguindo **B** (alinhado ao repo/dev existente). **A (Mangum)** fica
registrado como alternativa — relevante se quisermos reusar diretamente o código
FastAPI do LEGALTECH em vez de reescrever cada handler. **Decisão a confirmar com
o usuário.**

## Infra / AWS
- **Conta AWS disponível** (usuário) — console em `us-east-1`. O `serverless.yml`
  usa `sa-east-1`; alinhar a região no deploy (Fase 7).
- **Não fazer deploy nem criar recursos AWS sem autorização explícita** (custo +
  outward-facing). Planejar a Fase 7 (RDS/RDS Proxy/SSM/API Gateway) com cuidado.

## Próximos passos
1. ~~Fase 1 + laudo Codex~~ ✅. ~~Fase 2 + laudo Codex~~ ✅ (25 testes). Decisão
   Mangum vs nativo: **nativo** (definido pelo usuário).
2. ~~Fase A — Auth/Users~~ ✅ (42 testes; Codex revisando). **Ponto a confirmar com
   o usuário:** signup é público criando `viewer` — se o onboarding for admin-only,
   restringir `create_user` ao authorizer + admin.
3. **Fase 3 — `clients`** (⏳ próxima, encadeamento automático): realinhar
   `handlers/clients.py` + `client_schemas.py` ao schema real (`legal_name`,
   `document_number`, `document_type`); `clients.py` importa `validate_tenant_access`
   inexistente (corrigir). Sem RLS → `simple_tx`.
4. Fases 4, 5, 6 (documents+S3, RAG, services). **Fase E2E** (teste ponta-a-ponta de
   TODO o programa) é **GATE obrigatório antes da Fase 7** (pedido do usuário).
5. Fase 7 (hardening + deploy AWS) só após E2E verde + autorização (conta em
   `us-east-1`; o yml usa `sa-east-1` — alinhar).

> **Diretrizes do usuário (29/06):** usar **skills** sempre. **Fase E2E (gate antes
> do deploy), 3 passos:** (1) E2E de todo o programa; (2) havendo erros, plano de
> correção → corrigir → E2E, **em loop até zerar**; (3) varredura final de migração
> completa, código morto e qualidade do código.

## Decisões de produto (resolvidas com o usuário em 29/06)

1. **Signup** → **mantido público criando `viewer`** (decisão do usuário). Sem mudança.
2. **PII p/ viewer** → **MASCARAR `document_number`** para `viewer` (analyst/admin veem
   completo). **A IMPLEMENTAR** em `clients` (get/list). [LGPD]
3. **`get` de inativos** → **mantido visível** (não ocultar). Sem mudança.
4. **Checksum CPF/CNPJ** → **VALIDAR dígito verificador** no schema. **A IMPLEMENTAR**
   em `client_schemas`.
5. **Modelo de visibilidade** → **opção 1: por dono + admin (atual)** — sem mudança de
   RLS. *Obs.: o usuário pediu revisar o relatório pendente do Codex (auditoria de
   qualidade) antes de fechar este item.*
6. ~~Services não-integrados~~ ✅ removidos na Fase 6 (código morto).

**A implementar agora (decisão A):** mascaramento de PII p/ viewer + checksum CPF/CNPJ.
Depois: melhoria DRY (C). Antes, revisar o laudo de qualidade do Codex.

### Decisões de regra de negócio pendentes (varredura #4 — 29/06)
Achados confirmados em runtime (são REGRA, não bug de runtime — decisão do usuário):
7. **`create_case` aceita client com `status=inactive`** (retorna 201). Deveria
   rejeitar caso para cliente desativado? (validar `status='active'`).
8. **Lockout de admin**: `update_user`/`delete_user` permitem rebaixar/desativar o
   **último admin** → sistema sem administrador. Proteger (recusar se restaria 0 admin)?
   *(Risco operacional; alguns sistemas permitem e recuperam via SQL — por isso é decisão.)*
9. **Case finalizado** (`completed`/`closed`) ainda aceita novos `case_result`/`document`
   e troca de status livre. Definir **matriz de transição** + bloquear escrita em case
   finalizado? (B4 do laudo). Decisão de produto.

✅ **B1/B3/B4 IMPLEMENTADOS** (29/06, commit `27a7757`): B3 → 409 cliente inativo;
B4 → 409 escrita em case finalizado; B1 → 409 anti-lockout do último admin. **141 testes.**

### Corrigidos na varredura #4
- **B5** `completed_at` agora setado/limpo conforme status (era sempre NULL).
- **B6** token de reset único por usuário (migration 004 UNIQUE + upsert atômico).
- **Perf** (migrations 003/004): índices p/ RLS (created_by/uploaded_by), list_clients
  (status,created_at), list_case_results (case_id,created_at), list_users (created_at);
  removido índice duplicado. Validado via EXPLAIN (list_clients: Seq Scan→Index Scan).
- **Diferidos Fase 7:** B2 revogação de sessão, B7 `delete_case` cleanup S3, M2 keyset
  pagination, M3 constraint status×is_active, atualizar `schema_referencia.sql` com índices.

## Auditoria de qualidade (E2E — passo 3)

**Migração completa ✅:** 28 funções do `serverless.yml` com handler; **zero** resíduo
FastAPI/SQLAlchemy/Mangum; `health` migrado; schema real respeitado; pgvector cosine.
**Código morto ✅ zerado:** removidos `users_new`, `decorators`, `audit`, `cache`,
`analytics`, `rate_limit`, `webhooks`, `exceptions`, `validators`, `Database`/`db`
legado, `verify_token` e helpers não-usados. **Sem `str(e)` ao cliente; sem PII em log.**
**Consistência (forte):** todos os handlers seguem o mesmo padrão (`enforce_production_safety`
no import; `@require_user`+RBAC; `tenant_tx` p/ tabelas com RLS e `simple_tx` p/ as sem;
Pydantic 2 `extra="forbid"`; `_valid_uuid`; erro sanitizado `type(e).__name__`+`pgcode`).
**81 testes** no PG18 (unit por fase + **E2E da cadeia real** login→authorizer→handlers).

### Auditoria de qualidade do Codex (29/06) — concluída
Nota geral: *"bem programado no núcleo"*. Achados **incorporados**: Alta-01 (package
exclui `.env`/`.venv`/`docs` — não vaza secrets), Alta-03 (mascarar PII p/ viewer),
Média-04 (`search` checa case antes do embedding), Média-07 (paginação em
`case_results`/`list_users`), Baixa-08 (checksum CPF/CNPJ), Baixa-09/C (DRY: helpers
extraídos p/ `utils/lambda_io.py`). **Diferidos p/ Fase 7** (prontidão de produção):
**Alta-02** revogação/versionamento de sessão; **Média-05** confirmação pós-upload
(`HeadObject`/checksum/limite); **Média-06** backends reais por stage via SSM.

## Varredura cirúrgica final (29/06) — Codex + verificação em runtime

**Evidências:** 86 testes no PG18; 23 módulos importam sem erro; RLS consistente
(cases/case_results/documents=tenant_tx; users/clients=simple_tx); 28 funções do yml
com handler; sem resíduo FastAPI ativo; 8/8 tabelas de domínio usadas.

**Corrigido nesta varredura:**
- **Migration 002** — `audit.log_audit()` gravava `resource_id=NULL` em DELETE
  (`NEW` é NULL); agora `COALESCE(NEW.id, OLD.id)` (trilha de DELETE correta).
  *(O Codex previu que o delete "falharia"; o runtime provou que retorna 200 — o
  defeito real era a auditoria, não o delete. Iron Law.)*
- **PII**: `viewer` não vê mais email/phone/endereço de clients (além do documento mascarado).
- `jwt_authorizer`: comentário corrigido (Deny→403). Plugin `serverless-python-requirements`
  declarado; testes de `case_results`; removidos órfãos `serverless.yml_nok1`/`test_connection.py`.

**Lacunas/diferidos confirmados (Fase 7 / decisão):**
- `webhooks`: tabela existe, sem funcionalidade (service removido). Migrar se for requisito.
- `audit.data_access_log`/`compliance_events`: não populadas (log explícito de acesso a PII — LGPD).
- `delete_case` é **hard delete** com cascata; **não remove objetos S3** e o IAM não tem
  `s3:DeleteObject` → órfãos + perda de trilha. **Decidir:** soft delete? + cleanup.
- Dados legados com `created_by`/`uploaded_by` NULL ficam invisíveis a não-admin (backfill).
- `assigned_to`: não concede acesso (modelo dono) e sem FK p/ `users`.
- RAG: ingestão sem endpoint; revogação de sessão; VPC/RDS Proxy; backends por
  stage; validação pós-upload; validação de dimensão do embedding.

## Varredura profunda #2 (29/06) — Codex + runtime + TDD

Foco em ângulos finos. **Validados OK** (sem bug): reuso da conexão global, não-vazamento
de contexto RLS entre invocações (`SET LOCAL`), recuperação após tx abortada, JWT alg
confusion, SQL injection, unicode. **Bugs reais corrigidos (viravam 500):**
- **Body JSON não-objeto** (array/escalar/null) → `Schema(**body)` dava `TypeError` →
  `parse_json_body` valida objeto → 400 (todos os handlers).
- **NaN/Infinity** (jsonb) e **null byte** (text) → `parse_json_body` rejeita → 400.
- **`page` gigante** → OFFSET estourava bigint → `parse_pagination` com teto (4 handlers).
- **`create_user` em corrida** → `UniqueViolation` virava 500 → agora 409.
- **`forgot_password`** ignorava falha de envio → agora loga (mantém 200); **IAM
  `ses:SendEmail`** adicionado.

**129 testes** no PG18 (novo `tests/test_edge_cases.py`). **Diferidos p/ Fase 7:**
A1 revogação de sessão, A2 `delete_case` hard+S3, M2 forgot concorrente, M3 timing,
M4 upload metadados, M7 `audit.data_access_log` (LGPD).

## Varredura robusta #3 (29/06) — segurança (OWASP) + stress + Codex + web

**Stress (aguenta):** 2000 cases list paginado 17ms (+RLS); findings 340KB 65ms;
12 inserts concorrentes do mesmo `document_number` → 1 ok/11 dup (constraint); busca
vetorial sobre 300 embeddings 55ms. **Bugs corrigidos:**
- **bcrypt > 72 bytes** virava 500 → 400 (validação de bytes no schema).
- **`requirements.txt`** desalinhado das versões testadas → pinado ao `pip freeze`.
- **safety fail-safe**: stage desconhecido (`prd`/`qa`/`homolog`) agora = produtivo.
- **authorizer** exige `exp`; **body > 1MB → 413** + `RecursionError` tratado; **teto
  de page 10k**; **CORS `no-store`**; **SSM por stage**; removido `logger` morto.

**133 testes.** **Diferidos p/ Fase 7** (decisão/infra, não-bugs de runtime): FORCE RLS
+ check de role não-owner no boot, revogação de sessão (iss/aud/jti), rate limiting/WAF,
upload checksum/HeadObject + `s3:DeleteObject`, keyset pagination, auditoria de acesso a
PII, CORS por ambiente. CVE-2025-45768 (PyJWT) mitigado (HS256 fixo, v2.13.0).
