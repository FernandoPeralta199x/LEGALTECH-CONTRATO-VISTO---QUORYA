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
| 5 | `search` / RAG (pgvector + embeddings, RLS na busca) | ✅ concluída (**74 testes**), Codex revisando |
| 6 | services + remover código morto + requirements (boto3/openai; tirar FastAPI) | ⏳ próxima |
| **E2E** | **GATE antes do deploy:** E2E completo → loop (corrigir→E2E) **até zerar erros** → varredura final (migração completa, sem código morto, qualidade) | roadmap |
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
#   criar role cv_app (LOGIN, NÃO-owner) + grants; aplicar migrations/001_rls_policies.sql
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

## Decisões de produto pendentes (resolver no gate E2E / antes do deploy)

Aplicado o default mais seguro; confirmar com o usuário:
1. **Signup público** cria `viewer` (Fase A). Se o onboarding for admin-only,
   restringir `create_user` ao authorizer + admin.
2. **`viewer` vê PII completa** de clients (`document_number`/email/phone/endereço)
   em get/list (Fase 3). Se proibido por LGPD/negócio, mascarar p/ viewer.
3. **`get_client` retorna inativos** (soft-deleted) a qualquer autenticado se souber
   o UUID. Confirmar se é intencional.
4. **Sem checksum de CPF/CNPJ** (só tamanho/tipo). Avaliar validar dígito verificador
   (LegalTech). Auditoria de escrita de `clients` (sem trigger) → avaliar na Fase 6.
5. **Modelo de visibilidade (cases/case_results/documents)** — **decisão importante:**
   hoje é por **dono** (`created_by`/`uploaded_by`) + admin. Um colega que vê o case
   **não** vê documentos de outro; `assigned_to` do case **não** concede acesso. Se o
   produto for colaborativo (equipe/organização compartilha o case), rever as RLS
   policies (ex.: visível se o case for visível / por organização).
