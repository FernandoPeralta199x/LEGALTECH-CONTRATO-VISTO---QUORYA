# Plano de Trabalho — Migração FastAPI → Serverless (contrato_visto)

> **Goal:** Migrar o backend de FastAPI para AWS Lambda (Serverless Framework),
> por partes, alinhado ao banco real `contrato_visto` (PostgreSQL 18 + pgvector),
> respeitando RLS, triggers de auditoria e as regras do dono do repositório.
>
> **Arquitetura:** Lambdas Python 3.11 por rota (API Gateway), acesso ao
> PostgreSQL via psycopg com conexão reutilizada fora do handler + contexto RLS
> por transação (`SET LOCAL`). Auth pelo **JWT Authorizer** do dev (contexto em
> `requestContext.authorizer.context`).
>
> **Tech Stack:** Serverless Framework, AWS Lambda, API Gateway, RDS PostgreSQL
> 18 (pgvector 0.8.1 / HNSW), psycopg, Pydantic, SSM (secrets), S3, RDS Proxy.

---

## 0. Regras (do dev + de segurança)

Do dono do repositório (mensagens):
1. **PostgreSQL 18+** é obrigatório (dump gerado da 18.3).
2. Migração é **FastAPI → Serverless**, **por partes**.
3. **Login/autenticação é responsabilidade do dev** (JWT Authorizer). Eu pego
   outra parte.

Herdadas (segurança/LGPD — o banco já impõe):
4. Toda tabela sensível é escopada por dono via **RLS** (`cases`, `case_results`,
   `documents`, `audit.audit_log`) — o app **precisa** setar `app.user_id` e
   `app.user_role` por transação.
5. **Triggers de auditoria** (`audit.log_audit`) em `cases`/`documents` exigem
   `current_setting('app.user_id')` — sem ele, UPDATE/DELETE falham.
6. Nunca logar PII (CPF/CNPJ, e-mail, token) nem segredos. Segredos no **SSM**.
7. `organization_id`/`user_id`/`role` vêm do **JWT validado** (authorizer), nunca
   do payload do cliente.

---

## 1. Fonte de verdade e contrato de auth

- **O dump (`documentacao_banco.sql`) é a fonte de verdade do schema.** Os
  handlers atuais de `clients`/`cases` foram escritos contra um schema antigo e
  serão realinhados.
- **Contrato de auth (assumido):** o JWT Authorizer injeta
  `event.requestContext.authorizer.context = { user_id, email, role }`. Os
  handlers leem via `get_user_from_event` e **não** revalidam o token.
- **Decisões a confirmar com o dev** (não bloqueiam a Fase 0):
  1. `users` oficial: `public.users` (id `varchar(36)`) — confirmar; o
     `app.user_id` da RLS é `uuid`, então o `user_id` precisa ser um UUID válido.
  2. Quem corrige a **fundação compartilhada** (`services/database.py`,
     `utils/safety.py`) — proponho que eu corrija e o dev revise.
  3. O app vai conectar com um **role de aplicação NÃO-owner** (para a RLS valer)
     — hoje, se conectar como `dbadmin` (owner), a RLS é *bypassada*.

---

## 2. Decisões técnicas (com base em pesquisa)

### 2.1 Conexão ao banco em Lambda
- Conexão **global** (fora do handler) reutilizada entre invocações; **validar**
  antes de usar (`SELECT 1` / `conn.closed`) e reconectar se stale.
- **RDS Proxy** na frente do RDS (pooling) — Lambda escala mais rápido que o
  limite de conexões do Postgres.
- Padrão alvo do `database.py`:

```python
import os
import psycopg2
from psycopg2.extras import RealDictCursor

_conn = None

def _connect():
    return psycopg2.connect(
        host=os.environ["DB_HOST"], user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"], dbname=os.environ["DB_NAME"],
        port=int(os.getenv("DB_PORT", "5432")), connect_timeout=5,
    )

def get_connection():
    """Conexão reutilizada entre invocações, revalidada a cada uso."""
    global _conn
    if _conn is None or _conn.closed:
        _conn = _connect()
    else:
        try:
            with _conn.cursor() as c:
                c.execute("SELECT 1")
        except psycopg2.Error:
            _conn = _connect()
    return _conn
```

### 2.2 Contexto RLS por request (peça central)
Toda operação em tabela com RLS/trigger roda numa transação que primeiro fixa o
contexto do usuário autenticado (do authorizer), com `SET LOCAL` (escopo da
transação — seguro com pooling/RDS Proxy):

```python
from contextlib import contextmanager

@contextmanager
def tenant_tx(user_id: str, role: str):
    """Abre transação com o contexto RLS do usuário (app.user_id/app.user_role)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT set_config('app.user_id', %s, true),"
                "       set_config('app.user_role', %s, true)",
                (user_id, role),
            )
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
```

- `set_config(..., true)` = `SET LOCAL` (reseta no fim da transação).
- A policy `case_owner_access` e o trigger `audit.log_audit` passam a funcionar.
- Para leitura fora de RLS, um `tx()` sem contexto pode ser usado, mas o padrão
  preferido é sempre setar o contexto.
- **Requisitos do `tenant_tx`** (revisão Codex): conexão `autocommit=False`;
  `commit`/`rollback` sempre; **todas** as queries do request na **mesma
  transação** (senão `current_setting` não enxerga o valor). `set_config(...,true)`
  é transaction-scoped → não vaza entre invocações (cobrir com teste de reset).
- **RDS Proxy / pinning:** `SET`/variáveis de sessão podem causar *session
  pinning* (perda de multiplexing). `set_config(...,true)` é por-transação e tende
  a evitar pin, mas **monitorar `DatabaseConnectionsCurrentlySessionPinned`**.
  Definir `statement_timeout`/`idle_in_transaction_session_timeout`/`lock_timeout`.

---

## 3. Mapa de fases

| Fase | Escopo | Dependência | Dono |
|------|--------|-------------|------|
| **0** | Ambiente local de validação (git, PG18, schema, venv) | — | eu |
| **1** | **Fundação + GATE de segurança**: `database.py`/`tenant_tx`, `safety.py` (`ENVIRONMENT`), **role de app não-owner**, **authorizer plugado nas rotas migradas**, `JWT_SECRET` via SSM, helper compartilhado `require_user`, sanitização de erros/logs, timeouts de sessão, teste de RLS | confirmar com dev | eu (dev revisa) |
| **2** | **`cases` + `case_results`** alinhados ao schema + RLS + **RBAC (viewer só-leitura)** + **INSERT atômico** (só após o gate) | Fase 1 | eu |
| **A** | **Auth/Users** — handlers `users` (login/CRUD/forgot/reset) + `jwt_authorizer`; alinhar ao schema real; remover código morto (`users_new`) e defaults inseguros; **não logar PII** | Fase 1 | **eu** (entrou no escopo em 29/06; antes era do dev) |
| **3** | `clients` (sem RLS) realinhado ao schema | Fase 1 | eu |
| **4** | `documents` (S3 presigned + metadados) | Fase 1 | eu |
| **5** | `search` / RAG (pgvector + embeddings OpenAI) | Fase 4 | eu |
| **6** | Services alinhados (`audit`, `webhooks`, `cache`) + remover código morto (`users_new`, auth duplicado) | — | eu |
| **E2E** | **Teste de ponta a ponta de TODO o programa (GATE obrigatório antes do deploy)**: fluxos completos signup→login→authorizer→clients/cases/case_results/documents/search; RLS + RBAC + isolamento entre usuários. **Pedido explícito do usuário (29/06).** | Fases A–6 | eu |
| **7** | Hardening pós-MVP + **deploy AWS**: RDS Proxy sizing + monitorar *pinning*, reserved concurrency, CORS/IAM por ambiente, remover `fastapi/uvicorn`. **Só após E2E verde e com autorização explícita.** | E2E | eu (infra exige conta AWS) |

> **Ajuste pós-revisão do Codex:** itens de segurança que estavam na Fase 7
> (role não-owner, authorizer nas rotas, SSM, sanitização de erros/logs) foram
> **promovidos a gate da Fase 1** — sem eles, migrar `cases` deixaria as rotas
> expostas e a RLS *bypassada*.

---

## 4. Fase 0 — Ambiente local de validação (minha máquina)

**Objetivo:** poder rodar e validar a migração de verdade (como no projeto anterior).

- [ ] **0.1** `git init` no repositório (rede de segurança local; não altera o
      remote do dev) + commit-snapshot do estado atual.
- [ ] **0.2** Subir **PostgreSQL 18 + pgvector** local:
      `docker run -d --name cv-pg18 -e POSTGRES_PASSWORD=... -p 5433:5432 pgvector/pgvector:pg18`
      (porta 5433 para não colidir com o pg16 do outro projeto).
- [ ] **0.3** Restaurar o **schema** do dump (`documentacao_banco.sql`) no banco
      `contrato_visto` + criar um **role de app não-owner** (ex.: `cv_app`) para
      validar a RLS.
- [ ] **0.4** Criar `.venv`, instalar `requirements.txt` (sem `fastapi/uvicorn`
      no runtime), rodar `test_connection.py` apontando para o PG18 local.
- [ ] **0.5** Seed mínimo fictício (1 user, 1 client, 1 case) para os testes de
      integração — **sem dados reais**.

## 5. Fase 1 — Fundação + GATE de segurança

> Nada de domínio (`cases`/`clients`/…) é migrado antes deste gate passar.

**Files:**
- Modify: `src/services/database.py`, `src/utils/safety.py`, `src/utils/auth.py`,
  `serverless.yml`
- Create: `src/utils/context.py` (helper compartilhado `get_user_from_event`/`require_user`)
- Test: `tests/test_database_context.py`, `tests/test_rls.py`

- [ ] **1.1** `safety.py`: corrigir `os.getenv`; usar **`ENVIRONMENT`** (nome real
      do `serverless.yml`, não `APP_ENV`); comparar com o default real da chave.
- [ ] **1.2** `database.py`: `get_connection` (reuso + revalidação) + `tenant_tx`
      com **`autocommit=False`** e `commit`/`rollback` obrigatórios; aplicar
      `statement_timeout`/`idle_in_transaction_session_timeout`/`lock_timeout`.
- [ ] **1.3** Helper **compartilhado** `get_user_from_event`/`require_user` em
      `src/utils/context.py` (hoje duplicado), com **validação de UUID e role** do
      `authorizer.context`. Erros **nunca** retornam `str(e)`; logs sem PII.
- [ ] **1.4** **Role de app não-owner** (sem `BYPASSRLS`) — a RLS só vale se o app
      não for owner. Documentar criação do role + grants mínimos.
- [ ] **1.4.1** **Completar as policies RLS (DDL — alinhar com o dev).**
      *Validado na Fase 0:* hoje só existe `case_owner_access` **FOR SELECT** em
      `cases`; não há policy de INSERT/UPDATE/DELETE em `cases`, e
      `case_results`/`documents` têm RLS habilitada **sem nenhuma policy** → com
      role não-owner, escrita é **negada** (`new row violates row-level security
      policy`). Criar policies `FOR INSERT/UPDATE/DELETE` (ex.: `WITH CHECK
      (created_by = current_setting('app.user_id')::uuid OR
      current_setting('app.user_role')='admin')`) para `cases`, e policies de
      SELECT + escrita para `case_results`/`documents`. Versionar como migration.
- [ ] **1.5** `serverless.yml`: associar o **JWT Authorizer** às rotas a migrar;
      `JWT_SECRET_KEY` via **SSM** (não hardcoded).
- [ ] **1.6** **Teste de RLS** (role não-owner): A não vê `case` de B; admin vê;
      trigger `audit.log_audit` grava com `app.user_id`. **Teste de reset de
      contexto** entre dois usuários na mesma conexão reutilizada (o `SET LOCAL`
      não pode vazar).
- [ ] **1.7** Validar no PG18 local; rodar a suíte; revisão do Codex.

## 6. Fase 2 — `cases` + `case_results` (minha parte)

**Files:**
- Modify: `src/handlers/cases.py`, `src/handlers/case_results.py`
- Modify: `src/schemas/case_schemas.py`
- Test: `tests/test_cases.py`, `tests/test_case_results.py`

Correções já mapeadas (vs. schema real):
- `cases` **não tem `updated_at`** → remover de get/update; usar
  `completed_at`/`status`/`priority`/`assigned_to`/`metadata`.
- Proteger com `@require_user` (hoje `cases.py` está aberto).
- `create_case` deve gravar `created_by = user_id` (necessário para a RLS
  `case_owner_access`).
- Toda query passa por `tenant_tx(user_id, role)`.
- `case_results` (tem RLS habilitada): incluir `created_by`; alinhar colunas
  (`result_title`, `confidence_score`, `detailed_findings`); criar resultado
  **apenas para `case_id` visível ao usuário, na mesma `tenant_tx`**.
- `schemas/case_schemas.py`: trocar `Field(regex=...)` por `pattern=` (Pydantic 2).
- Validar UUID/role do `authorizer.context`; checar `rowcount`/`RETURNING` em
  update/delete; **nunca** retornar `str(e)` ao cliente.

Tarefas (TDD, por endpoint): criar teste de integração que falha → implementar →
validar no PG18 → commit. Repetir para create/get/list/update/delete de cada um.

## 7. Fases 3-7 — roadmap (detalhar quando chegar)

- **3 `clients`:** reescrever para `legal_name`, `document_number`,
  `document_type`, `address_*`, `is_active`; remover `created_by`/`name`/
  `cpf_cnpj`/`type` (não existem). Validar CPF/CNPJ com `utils/validators.py`.
- **4 `documents`:** gravar metadados + `s3_url`/`s3_path`; upload via **presigned
  URL** (não trafegar binário pela Lambda); RLS + trigger de delete.
- **5 `search`/RAG:** alinhar `document_embeddings` (`segment_text`, `embedding`,
  `chunk_id`); busca por `vector_cosine_ops` (HNSW); embeddings OpenAI 1536.
- **6 services:** `audit.py` (colunas reais: `change_before/after`, sem `status`);
  `webhooks`; `cache` (api_cache no schema `integration`); remover `users_new.py`
  e os 3 padrões de auth duplicados.
- **7 hardening:** plugar o **authorizer** nas rotas no `serverless.yml`;
  `JWT_SECRET_KEY` via SSM; **RDS Proxy**; role de app não-owner; remover
  `fastapi/uvicorn` do runtime; revisão de IAM (least privilege).

---

## 8. Bugs bloqueantes mapeados (corrigir / alinhar com o dev)

| Arquivo | Bug | Fase |
|---------|-----|------|
| `services/database.py` | falta `import os`; usa `DB_PASSWORD`/`DB_PORT` (real: `DB_PASS`) | 1 |
| `utils/safety.py:21` | `os-getenv` (hífen) → quebra em staging/prod; default da chave errado | 1 |
| `handlers/cases.py` | usa `updated_at` inexistente; sem `@require_user`; sem `created_by` | 2 |
| `handlers/clients.py` + schema | colunas inexistentes (`name`, `cpf_cnpj`, `type`, `created_by`) | 3 |
| `services/rag.py` | INSERT em `content`/`ON CONFLICT(document_id)` inexistentes | 5 |
| `services/audit.py` | coluna `status` inexistente em `audit.audit_log`/`data_access_log` | 6 |
| `handlers/users.py` | importa `src.services.email` inexistente; defaults de segredo inseguros | **A** |
| `handlers/users_new.py` | código morto / duplicado de auth → remover | **A** |
| `authorizers/jwt_authorizer.py` | shape do context (corrigido na Fase 2 no consumidor); **ainda loga `email` (PII)** → sanitizar | **A** |
| `serverless.yml` | authorizer não plugado; `JWT_SECRET` hardcoded | 1/dev |
| `utils/safety.py` | usa `APP_ENV`, mas o yml define `ENVIRONMENT` → trava nunca ativa | 1 |
| `schemas/*` | Pydantic 2 com `Field(..., regex=...)` (deve ser `pattern=`) → schemas quebram | 2/3 |
| múltiplos handlers | retornam `str(e)` ao cliente (vaza detalhe interno) | 1-2 |
| `authorizers/jwt_authorizer.py` | loga `email` (PII) | 1 |
| `utils/auth.py` | fallback de segredo `'sua-chave-secreta-aqui'` | 1 |
| transversal | falta `statement_timeout` / idempotência de POST / limite de concorrência | 1-2 |
| **banco (RLS)** | só `case_owner_access` FOR SELECT em `cases`; sem policy de INSERT/UPDATE/DELETE; `case_results`/`documents` com RLS **sem policy** → escrita negada p/ role não-owner (**validado na Fase 0**) | 1 |
| `test_connection.py` | prints com emoji quebram no console cp1252 (Windows) → usar `PYTHONIOENCODING=utf-8`/ASCII | baixa |

---

## 9. Gates de qualidade (por fase)
- `python -m compileall src`
- `.venv/Scripts/python -m pytest -q` (ou unittest) contra o **PG18 local**
- Revisão independente pelo **Codex** (read-only) a cada parte concluída
- Sem segredos/PII no diff; `git diff --check`
- **Publicação só pelo dono do repo** (não faço push ao remote do dev)

## 10. Coordenação
- A Fase 1 toca arquivos compartilhados com o dev (`database.py`, `safety.py`) —
  alinhar antes de mesclar.
- Entregar este plano + o mapa de bugs ao dev para divisão clara das partes.
