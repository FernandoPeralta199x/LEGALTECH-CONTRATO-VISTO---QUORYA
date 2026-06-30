# Fundação V2 — Parte 1A: Organizations + Auth Multi-tenant — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduzir `organizations` e tornar a autenticação multi-tenant (cada usuário pertence a uma organização; `organization_id` viaja no JWT → authorizer → contexto → `tenant_tx`; signup cria a própria organização e o usuário vira `admin` dela), **sem ainda alterar a RLS por-dono** das tabelas core (isso é a Parte 1B).

**Architecture:** Migração `005` cria `organizations` (RLS `id = app.organization_id` + FORCE RLS) e semeia uma organização de sistema. Migração `006` converte `users.id` (e `password_resets.user_id`) de `varchar`→`uuid`, adiciona `users.organization_id` (FK + backfill p/ a org de sistema), e o código passa a propagar/validar `organization_id` no `create_access_token`/`jwt_authorizer`/`context.py`/`tenant_tx`. O signup vira atômico (cria org + usuário admin). A RLS das tabelas `cases/case_results/documents/clients` continua **por-dono** nesta parte; `tenant_tx` apenas passa a setar também `app.organization_id` (inócuo enquanto as policies usam `app.user_id`).

**Tech Stack:** Python 3.11, psycopg2, PyJWT (HS256), pytest, PostgreSQL 18 + pgvector (Docker `cv-pg18`, porta 5433), migrations `.sql` aplicadas como owner `dbadmin`; app conecta como `cv_app` (não-owner).

**Ambiente de teste:** `.venv\Scripts\python.exe -m pytest tests/ -q` (com `.env` apontando para `localhost:5433`, role `cv_app`). Migrações aplicadas via `psql`/owner antes de rodar os testes.

**Constante compartilhada:** organização de sistema com id fixo `00000000-0000-0000-0000-000000000001` (usada no backfill e nas fixtures de teste). Referida abaixo como `SYSTEM_ORG_ID`.

---

## File Structure

- Create: `migrations/005_organizations.sql` — tabela `organizations` + RLS/FORCE + seed da org de sistema.
- Create: `migrations/006_users_multitenant_auth.sql` — `users.id`/`password_resets.user_id` → uuid; `users.organization_id` + backfill + índice.
- Modify: `src/services/database.py` — `tenant_tx(user_id, role, organization_id)` seta os 3 GUCs.
- Modify: `src/utils/context.py` — extrai e valida `organization_id`.
- Modify: `src/authorizers/jwt_authorizer.py` — exige/repassa `organization_id`.
- Modify: `src/handlers/users.py` — signup atômico (org+admin); login inclui org no token; `list/get/update/delete` filtram por org; anti-lockout por org.
- Modify: `src/handlers/cases.py`, `clients.py`, `documents.py`, `case_results.py`, `search.py` — call sites de `tenant_tx` passam `organization_id`.
- Create: `tests/test_organizations.py` — RLS de `organizations`.
- Modify: `tests/test_users_handlers.py`, `tests/test_context.py` — org no signup/login/contexto.
- Modify (fixtures): `tests/test_cases_handlers.py`, `tests/test_clients_handlers.py`, `tests/test_documents_handlers.py`, `tests/test_case... (case_results)`, `tests/test_search_handlers.py`, `tests/test_rls.py`, `tests/test_e2e.py`, `tests/test_edge_cases.py` — `_event(...)` e chamadas `tenant_tx(...)` passam a incluir `organization_id`.

---

## Task 1: Migração 005 — tabela `organizations`

**Files:**
- Create: `migrations/005_organizations.sql`
- Test: `tests/test_organizations.py`

- [ ] **Step 1: Escrever a migração**

`migrations/005_organizations.sql`:
```sql
-- Migration 005 — tabela organizations (raiz do tenant) + RLS por organização.
-- A app conecta como cv_app (não-owner); FORCE RLS garante isolamento também
-- contra o owner/funções/views. O contexto app.organization_id é setado por
-- tenant_tx (ver migração 006 / database.py).

CREATE TABLE IF NOT EXISTS public.organizations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        varchar(255) NOT NULL,
    document    varchar(32),
    status      varchar(30) NOT NULL DEFAULT 'active',
    metadata    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz
);

ALTER TABLE public.organizations OWNER TO dbadmin;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.organizations TO cv_app;

ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organizations FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS org_self_select ON public.organizations;
CREATE POLICY org_self_select ON public.organizations FOR SELECT
  USING (id = current_setting('app.organization_id')::uuid);
DROP POLICY IF EXISTS org_self_insert ON public.organizations;
CREATE POLICY org_self_insert ON public.organizations FOR INSERT
  WITH CHECK (id = current_setting('app.organization_id')::uuid);
DROP POLICY IF EXISTS org_self_update ON public.organizations;
CREATE POLICY org_self_update ON public.organizations FOR UPDATE
  USING (id = current_setting('app.organization_id')::uuid)
  WITH CHECK (id = current_setting('app.organization_id')::uuid);

-- Org de sistema (id fixo) para backfill de dados legados/dev. Inserida pelo owner
-- (dbadmin) — o owner com FORCE RLS também sofre policy, então setamos o GUC.
SELECT set_config('app.organization_id', '00000000-0000-0000-0000-000000000001', false);
INSERT INTO public.organizations (id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'Organização de Sistema')
ON CONFLICT (id) DO NOTHING;
RESET app.organization_id;
```

- [ ] **Step 2: Aplicar a migração no PG18**

Run:
```bash
psql "host=localhost port=5433 user=dbadmin password=localdev_cv dbname=contrato_visto" -f migrations/005_organizations.sql
```
Expected: `CREATE TABLE`, `ALTER TABLE`, `CREATE POLICY` ×3, `INSERT 0 1` (ou `INSERT 0 0` se reaplicado).

- [ ] **Step 3: Escrever o teste de RLS de organizations**

`tests/test_organizations.py`:
```python
"""Fundação V2 — RLS da tabela organizations (PG18 + role não-owner cv_app)."""
import uuid

import psycopg2
import pytest

from src.services.database import get_connection

SYSTEM_ORG_ID = "00000000-0000-0000-0000-000000000001"


def _admin_conn():
    return psycopg2.connect(
        host="localhost", port=5433, user="dbadmin",
        password="localdev_cv", dbname="contrato_visto", connect_timeout=5,
    )


def _set_org(cur, org_id):
    cur.execute("SELECT set_config('app.organization_id', %s, true)", (str(org_id),))


def test_org_visible_only_within_its_context():
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    admin = _admin_conn()
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute("SELECT set_config('app.organization_id', %s, false)", (org_a,))
        c.execute("INSERT INTO public.organizations (id, name) VALUES (%s, 'A')", (org_a,))
        c.execute("SELECT set_config('app.organization_id', %s, false)", (org_b,))
        c.execute("INSERT INTO public.organizations (id, name) VALUES (%s, 'B')", (org_b,))
    admin.close()

    conn = get_connection()
    cur = conn.cursor()
    try:
        _set_org(cur, org_a)
        cur.execute("SELECT count(*) FROM public.organizations")
        assert cur.fetchone()[0] == 1  # só a própria org
        cur.execute("SELECT id FROM public.organizations")
        assert str(cur.fetchone()[0]) == org_a
        conn.rollback()
    finally:
        cur.close()


def test_org_without_context_is_blocked():
    conn = get_connection()
    cur = conn.cursor()
    try:
        with pytest.raises(psycopg2.Error):
            cur.execute("SELECT count(*) FROM public.organizations")
        conn.rollback()
    finally:
        cur.close()
```

- [ ] **Step 4: Rodar os testes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_organizations.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add migrations/005_organizations.sql tests/test_organizations.py
git commit -m "feat(fundacao-v2): migracao 005 organizations + RLS por org (FORCE)"
```

---

## Task 2: `tenant_tx` propaga `app.organization_id`

**Files:**
- Modify: `src/services/database.py:88-110` (a função `tenant_tx`)

- [ ] **Step 1: Atualizar `tenant_tx`**

Substituir a assinatura e o corpo de `tenant_tx` em `src/services/database.py` por:
```python
@contextmanager
def tenant_tx(user_id, role, organization_id):
    """Transação com o contexto RLS do usuário autenticado e da sua organização.

    ``set_config(..., true)`` aplica o valor SÓ nesta transação (seguro com
    pooling/RDS Proxy; não vaza entre invocações). Seta app.user_id,
    app.user_role e app.organization_id.
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT set_config('app.user_id', %s, true),"
            "       set_config('app.user_role', %s, true),"
            "       set_config('app.organization_id', %s, true)",
            (str(user_id), str(role), str(organization_id)),
        )
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
```

- [ ] **Step 2: Atualizar os testes de RLS existentes para a nova assinatura**

Em `tests/test_rls.py`, trocar todas as chamadas `tenant_tx(user, "role")` por `tenant_tx(user, "role", SYSTEM_ORG_ID)` e definir no topo `SYSTEM_ORG_ID = "00000000-0000-0000-0000-000000000001"`. Em `_seed_case`, a chamada vira `tenant_tx(user_id, "analyst", SYSTEM_ORG_ID)`. (A RLS de `cases` ainda é por-dono; o `organization_id` setado é inócuo aqui, mas a assinatura passa a exigir o 3º argumento.)

- [ ] **Step 3: Rodar os testes de RLS**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rls.py -v`
Expected: PASS (5 passed) — comportamento por-dono inalterado.

- [ ] **Step 4: Commit**

```bash
git add src/services/database.py tests/test_rls.py
git commit -m "feat(fundacao-v2): tenant_tx seta app.organization_id (3o argumento)"
```

---

## Task 3: Contexto e authorizer propagam/validam `organization_id`

**Files:**
- Modify: `src/utils/context.py:28-41` (`get_user_from_event`)
- Modify: `src/authorizers/jwt_authorizer.py:60-88` (sucesso) e logs
- Test: `tests/test_context.py`

- [ ] **Step 1: Escrever o teste do contexto**

Adicionar em `tests/test_context.py`:
```python
def test_context_requires_organization_id():
    from src.utils.context import get_user_from_event
    import uuid
    uid, oid = str(uuid.uuid4()), str(uuid.uuid4())
    ev_ok = {"requestContext": {"authorizer": {
        "user_id": uid, "role": "analyst", "organization_id": oid}}}
    u = get_user_from_event(ev_ok)
    assert u is not None and u["organization_id"] == oid

    ev_no_org = {"requestContext": {"authorizer": {"user_id": uid, "role": "analyst"}}}
    assert get_user_from_event(ev_no_org) is None  # sem org → inválido
```

- [ ] **Step 2: Rodar o teste (deve falhar)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_context.py::test_context_requires_organization_id -v`
Expected: FAIL (KeyError/None: `organization_id` ainda não extraído).

- [ ] **Step 3: Atualizar `get_user_from_event`**

Em `src/utils/context.py`, substituir o corpo de `get_user_from_event` por:
```python
def get_user_from_event(event):
    """Extrai e valida {user_id, organization_id (UUID), email, role}."""
    try:
        ctx = _authorizer_claims(event)
        user_id = str(uuid.UUID(str(ctx["user_id"])))
        organization_id = str(uuid.UUID(str(ctx["organization_id"])))
        role = ctx["role"]
        if role not in VALID_ROLES:
            return None
        return {"user_id": user_id, "organization_id": organization_id,
                "email": ctx.get("email", ""), "role": role}
    except (KeyError, ValueError, TypeError):
        return None
```

- [ ] **Step 4: Atualizar o authorizer**

Em `src/authorizers/jwt_authorizer.py`, dentro do `try` de sucesso, exigir e repassar `organization_id`. Substituir o bloco do `return` de sucesso por:
```python
        organization_id = payload["organization_id"]  # KeyError → token inválido

        logger.info(json.dumps({
            "event": "AUTH_TOKEN_VALID",
            "user_id": payload['user_id'],
            "role": payload['role'],
            "methodArn": event.get('methodArn')
        }))

        return {
            'principalId': payload['user_id'],
            'policyDocument': {
                'Version': '2012-10-17',
                'Statement': [{
                    'Action': 'execute-api:Invoke',
                    'Effect': 'Allow',
                    'Resource': event['methodArn']
                }]
            },
            'context': {
                'user_id': payload['user_id'],
                'role': payload['role'],
                'organization_id': organization_id,
            }
        }
```
(O `KeyError` de `payload["organization_id"]` cai no `except Exception` → `deny_access`.)

- [ ] **Step 5: Rodar os testes de contexto**

Run: `.venv\Scripts\python.exe -m pytest tests/test_context.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/utils/context.py src/authorizers/jwt_authorizer.py tests/test_context.py
git commit -m "feat(fundacao-v2): authorizer e contexto exigem/propagam organization_id"
```

---

## Task 4: Migração 006 — `users.id`→uuid + `users.organization_id`

**Files:**
- Create: `migrations/006_users_multitenant_auth.sql`

- [ ] **Step 1: Precheck — todos os `users.id` são UUID válidos**

Run:
```bash
psql "host=localhost port=5433 user=dbadmin password=localdev_cv dbname=contrato_visto" -c "SELECT count(*) FROM public.users WHERE id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';"
```
Expected: `0` (se >0, parar e corrigir os ids antes de converter).

- [ ] **Step 2: Escrever a migração**

`migrations/006_users_multitenant_auth.sql`:
```sql
-- Migration 006 — users multi-tenant.
-- 1) Converte users.id e password_resets.user_id de varchar -> uuid (FKs corretas).
-- 2) Adiciona users.organization_id (FK organizations) + backfill p/ org de sistema.

BEGIN;

-- desfaz a FK varchar antes de mudar os tipos
ALTER TABLE public.password_resets DROP CONSTRAINT IF EXISTS password_resets_user_id_fkey;

ALTER TABLE public.users
  ALTER COLUMN id TYPE uuid USING id::uuid;
ALTER TABLE public.password_resets
  ALTER COLUMN user_id TYPE uuid USING user_id::uuid;

ALTER TABLE public.password_resets
  ADD CONSTRAINT password_resets_user_id_fkey
  FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

-- organization_id em users (nullable p/ backfill; NOT NULL ao final)
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS organization_id uuid;
UPDATE public.users SET organization_id = '00000000-0000-0000-0000-000000000001'
 WHERE organization_id IS NULL;
ALTER TABLE public.users ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE public.users
  ADD CONSTRAINT users_organization_id_fkey
  FOREIGN KEY (organization_id) REFERENCES public.organizations(id);
CREATE INDEX IF NOT EXISTS idx_users_organization_id ON public.users(organization_id);

COMMIT;
```

- [ ] **Step 3: Aplicar a migração**

Run:
```bash
psql "host=localhost port=5433 user=dbadmin password=localdev_cv dbname=contrato_visto" -f migrations/006_users_multitenant_auth.sql
```
Expected: `BEGIN … COMMIT` sem erro.

- [ ] **Step 4: Verificar tipos**

Run:
```bash
psql "host=localhost port=5433 user=dbadmin password=localdev_cv dbname=contrato_visto" -c "\d public.users" | grep -E "id|organization_id"
```
Expected: `id | uuid | not null`, `organization_id | uuid | not null`.

- [ ] **Step 5: Commit**

```bash
git add migrations/006_users_multitenant_auth.sql
git commit -m "feat(fundacao-v2): migracao 006 users.id->uuid + organization_id"
```

---

## Task 5: Signup atômico (org + admin) + login com org no token

**Files:**
- Modify: `src/services/database.py` (novo helper `signup_tx`)
- Modify: `src/handlers/users.py:77-148` (`create_user`, `login`)
- Test: `tests/test_users_handlers.py`

- [ ] **Step 1: Escrever os testes (signup cria org+admin; login emite token com org)**

Adicionar em `tests/test_users_handlers.py` (ajuste imports conforme o arquivo):
```python
import json, uuid, jwt, os
from src.handlers import users as uh

def _signup(email):
    return uh.create_user({"body": json.dumps(
        {"email": email, "password": "Sderf!2025xZ", "name": "Org Admin"})}, None)

def test_signup_creates_org_and_admin():
    email = f"a{uuid.uuid4().hex[:8]}@t.co"
    resp = _signup(email)
    assert resp["statusCode"] == 201, resp
    data = json.loads(resp["body"])["data"]
    assert data["role"] == "admin"
    assert uuid.UUID(data["organization_id"])  # org criada

def test_login_token_carries_organization_id():
    email = f"b{uuid.uuid4().hex[:8]}@t.co"
    _signup(email)
    resp = uh.login({"body": json.dumps({"email": email, "password": "Sderf!2025xZ"})}, None)
    assert resp["statusCode"] == 200, resp
    token = json.loads(resp["body"])["data"]["token"]
    claims = jwt.decode(token, os.environ["JWT_SECRET_KEY"], algorithms=["HS256"])
    assert "organization_id" in claims and uuid.UUID(claims["organization_id"])
```

- [ ] **Step 2: Rodar (deve falhar)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_users_handlers.py::test_signup_creates_org_and_admin tests/test_users_handlers.py::test_login_token_carries_organization_id -v`
Expected: FAIL (role ainda 'viewer'; sem organization_id no retorno/token).

- [ ] **Step 3: Adicionar `signup_tx` em `database.py`**

Em `src/services/database.py`, adicionar:
```python
@contextmanager
def signup_tx(organization_id, role="admin"):
    """Transação de onboarding: seta app.organization_id ANTES dos INSERTs para
    satisfazer a RLS de organizations (id = app.organization_id). users não tem
    RLS, mas a mesma transação cria org + usuário admin atomicamente."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT set_config('app.organization_id', %s, true),"
            "       set_config('app.user_role', %s, true)",
            (str(organization_id), str(role)),
        )
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
```

- [ ] **Step 4: Reescrever `create_user` (signup atômico)**

Substituir o corpo de `create_user` em `src/handlers/users.py` por:
```python
def create_user(event, context):
    """Signup PÚBLICO: cria uma organização nova e o usuário como admin dela."""
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = UserSignupSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    user_id = generate_uuid()
    org_id = generate_uuid()
    try:
        with signup_tx(org_id) as cur:
            cur.execute("SELECT 1 FROM public.users WHERE email = %s", (data.email,))
            if cur.fetchone():
                return error_response(409, "Email já cadastrado")
            cur.execute(
                "INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                (org_id, f"Org de {data.name}"),
            )
            cur.execute(
                "INSERT INTO public.users (id, email, password_hash, name, role, status, organization_id)"
                " VALUES (%s, %s, %s, %s, 'admin', 'active', %s)",
                (user_id, data.email, hash_password(data.password), data.name, org_id),
            )
    except psycopg2.errors.UniqueViolation:
        return error_response(409, "Email já cadastrado")
    except Exception as e:
        logger.error(json.dumps({"event": "USER_CREATE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao criar usuário")

    logger.info(json.dumps({"event": "USER_CREATED", "user_id": user_id, "role": "admin"}))
    return success_response(201, "Usuário criado com sucesso",
                            {"user_id": user_id, "role": "admin",
                             "organization_id": org_id})
```
E adicionar `signup_tx` ao import: `from src.services.database import signup_tx, simple_tx`.

- [ ] **Step 5: Incluir `organization_id` no login**

Em `login`, mudar o SELECT e o token:
```python
            cur.execute(
                "SELECT id, email, password_hash, role, organization_id FROM public.users"
                " WHERE email = %s AND status = 'active'",
                (data.email,),
            )
```
e
```python
    token = create_access_token({
        "user_id": str(user["id"]), "role": user["role"],
        "organization_id": str(user["organization_id"]),
    })
    logger.info(json.dumps({"event": "AUTH_LOGIN_SUCCESS", "user_id": str(user["id"])}))
    return success_response(200, "Login bem-sucedido", {
        "token": token, "user_id": str(user["id"]), "role": user["role"],
        "organization_id": str(user["organization_id"]),
    })
```

- [ ] **Step 6: Rodar os testes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_users_handlers.py -v`
Expected: PASS (incl. os 2 novos; ajustar testes antigos que esperavam role 'viewer' no signup — ver Task 6/7).

- [ ] **Step 7: Commit**

```bash
git add src/services/database.py src/handlers/users.py tests/test_users_handlers.py
git commit -m "feat(fundacao-v2): signup atomico cria org+admin; login emite token com organization_id"
```

---

## Task 6: `users` isolado por organização nos handlers

**Files:**
- Modify: `src/handlers/users.py` (`get_user`, `list_users`, `update_user`, `delete_user`, `_is_last_active_admin`)
- Test: `tests/test_users_handlers.py`

- [ ] **Step 1: Escrever o teste de isolamento de users por org**

Adicionar em `tests/test_users_handlers.py`:
```python
def _event_auth(user_id, org_id, role="admin", path=None, query=None, body=None):
    return {"requestContext": {"authorizer": {
                "user_id": user_id, "role": role, "organization_id": org_id}},
            "pathParameters": path or {}, "queryStringParameters": query or {},
            "body": json.dumps(body) if body is not None else None}

def test_list_users_only_same_org():
    e1 = f"o1{uuid.uuid4().hex[:8]}@t.co"; e2 = f"o2{uuid.uuid4().hex[:8]}@t.co"
    d1 = json.loads(_signup(e1)["body"])["data"]
    d2 = json.loads(_signup(e2)["body"])["data"]
    resp = uh.list_users(_event_auth(d1["user_id"], d1["organization_id"]), None)
    ids = [u["id"] for u in json.loads(resp["body"])["data"]]
    assert d1["user_id"] in ids and d2["user_id"] not in ids
```

- [ ] **Step 2: Rodar (deve falhar)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_users_handlers.py::test_list_users_only_same_org -v`
Expected: FAIL (list_users hoje retorna global).

- [ ] **Step 3: Filtrar por org nos handlers de users**

Em `src/handlers/users.py`:
- `list_users`: adicionar `WHERE organization_id = %s` e passar `event["user"]["organization_id"]`:
```python
            cur.execute(
                "SELECT id, email, name, role, status, created_at FROM public.users"
                " WHERE organization_id = %s"
                " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (event["user"]["organization_id"], page_size, offset),
            )
```
- `get_user`: após buscar `row`, garantir `row` da mesma org — trocar o SELECT para incluir `AND organization_id = %s` com `event["user"]["organization_id"]`.
- `update_user` e `delete_user`: nos `UPDATE ... WHERE id = %s`, adicionar `AND organization_id = %s` com a org do solicitante (impede gerir usuário de outra org).
- `_is_last_active_admin(cur, target_id, organization_id)`: filtrar por org:
```python
def _is_last_active_admin(cur, target_id, organization_id) -> bool:
    cur.execute("SELECT role, status FROM public.users WHERE id = %s AND organization_id = %s",
                (target_id, organization_id))
    t = cur.fetchone()
    if not t or t["role"] != "admin" or t["status"] != "active":
        return False
    cur.execute("SELECT count(*) AS n FROM public.users"
                " WHERE role='admin' AND status='active' AND organization_id = %s",
                (organization_id,))
    return cur.fetchone()["n"] <= 1
```
Atualizar as 2 chamadas de `_is_last_active_admin` para passar `event["user"]["organization_id"]`.

- [ ] **Step 4: Rodar a suíte de users**

Run: `.venv\Scripts\python.exe -m pytest tests/test_users_handlers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/handlers/users.py tests/test_users_handlers.py
git commit -m "feat(fundacao-v2): users isolados por organizacao (list/get/update/delete + anti-lockout por org)"
```

---

## Task 7: Adaptar call sites de `tenant_tx` (handlers core) e fixtures dos testes

**Files:**
- Modify: `src/handlers/cases.py`, `clients.py`, `documents.py`, `case_results.py`, `search.py`
- Modify: `tests/test_cases_handlers.py`, `tests/test_clients_handlers.py`, `tests/test_documents_handlers.py`, `tests/test_search_handlers.py`, `tests/test_e2e.py`, `tests/test_edge_cases.py`

- [ ] **Step 1: Atualizar os handlers core**

Em cada handler que chama `tenant_tx(user["user_id"], user["role"])`, passar o 3º argumento:
`tenant_tx(user["user_id"], user["role"], user["organization_id"])`.
Localizar todas as ocorrências:
```bash
grep -rn "tenant_tx(" src/handlers/
```
Aplicar o 3º argumento em todas. (Nesta parte a RLS ainda é por-dono; o argumento é exigido pela nova assinatura de `tenant_tx`.)

- [ ] **Step 2: Atualizar o helper `_event` dos testes**

Em cada arquivo de teste de handler, o helper `_event(...)` deve injetar `organization_id`. Padrão (usar uma org fixa de teste por usuário; aqui basta uma org compartilhada porque a RLS core ainda é por-dono):
```python
SYSTEM_ORG_ID = "00000000-0000-0000-0000-000000000001"

def _event(user_id, role="analyst", body=None, path=None, query=None, org_id=SYSTEM_ORG_ID):
    return {
        "requestContext": {"authorizer": {
            "user_id": user_id, "email": "u@t.c", "role": role,
            "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": query or {},
    }
```
Aplicar o mesmo acréscimo (`"organization_id": org_id`) ao shape aninhado em `test_nested_authorizer_shape_still_works`.

- [ ] **Step 3: Atualizar chamadas diretas de `tenant_tx` nos testes**

`grep -rn "tenant_tx(" tests/` e adicionar `SYSTEM_ORG_ID` como 3º argumento em cada chamada (ex.: `test_rls.py` já feito na Task 2; repetir em quaisquer outros).

- [ ] **Step 4: Rodar a suíte completa**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS — toda a suíte verde (os 141 + novos), comportamento por-dono inalterado, agora com `organization_id` propagado.

- [ ] **Step 5: Commit**

```bash
git add src/handlers tests
git commit -m "feat(fundacao-v2): handlers e fixtures propagam organization_id ao tenant_tx"
```

---

## Self-Review (preencher ao executar)
- Cobertura do spec (§3.2 auth, §3.3 onboarding, §4.1 users.id→uuid/org, §5 users por org): Tasks 1–7. RLS por-org das tabelas core e tabelas novas ficam para Parte 1B/Parte 2 (fora deste plano, por design).
- Sem placeholders: DDL e código completos em cada step.
- Consistência de tipos: `tenant_tx(user_id, role, organization_id)` usado igual em handlers e testes; `organization_id` sempre string UUID; `SYSTEM_ORG_ID` constante única.

## Próximos planos
- **Parte 1B:** migração 007 — `organization_id` + RLS **por organização** (substituindo por-dono) + `UNIQUE(org,id)` + FKs compostas + views `security_invoker` em `clients/cases/case_results/documents`; reescrever os testes de isolamento para semântica por-org.
- **Parte 2:** migrações 008–011 — chunks/embeddings+audit com org; `requests`/`case_parties`; pipeline (`triage_modules`/`provider_results`/`agent_executions`/`external_queries_cache`); `pricing_configs`.
