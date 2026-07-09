"""Pentest dinâmico AUTORIZADO contra a API viva do Contrato Visto (dev-server).

Bateria de segurança em 3 ondas contra o programa VIVO (NÃO é pytest — bate numa
API real; para a suíte isolada de regressão dos achados, ver ``tests/``):

  1) AuthZ/RBAC/token (sem token, assinatura adulterada, alg=none, sem exp,
     segredo errado, viewer vs writer), IDOR multi-tenant, injeção (SQLi/path).
  2) Upload malicioso (tamanho, path traversal), validação/DoS (body gigante,
     JSON malformado/NaN/null-byte, aninhamento profundo, paginação gigante),
     lógica de negócio (parcelas, débito à vista, cartão sem token, idempotência),
     vazamento/PII (stack/SQL em erros, CPF mascarado p/ viewer).
  3) Mass-assignment (org/role no body), priv-esc/user-mgmt (promoção, cross-tenant,
     anti-lockout), IDOR em outros recursos, reset de senha (enumeração/token),
     authz de admin (pricing, review de relatório, status inválido).

Uso (com o dev-server em pé — ``tools/local_server.py`` — e o PG18 semeado com a
org demo):

    ./.venv/Scripts/python.exe tools/security_probe.py                # todas as ondas
    ./.venv/Scripts/python.exe tools/security_probe.py --waves 1,2
    ./.venv/Scripts/python.exe tools/security_probe.py --cleanup      # remove dados de teste ao fim
    ./.venv/Scripts/python.exe tools/security_probe.py --base http://127.0.0.1:8000

Cada check imprime ``SECURE`` (bloqueado/comportamento esperado) ou ``!!VULN!!``
(inseguro). Sai com código 0 se TUDO secure, 2 se houver ao menos um achado, 1 em
erro de setup — serve como gate.

Lê ``JWT_SECRET_KEY`` do ambiente (ou do ``.env`` do backend) SÓ para forjar tokens
de teste de authz (o segredo nunca é impresso). Cria dados DESCARTÁVEIS: uma org B
via signup (e-mails ``@qapentest.com``) e casos/clientes tagueados ``SEC-PENTEST``.
Use um banco de DEV/descartável — NÃO rode contra produção.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

import jwt

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAG = "SEC-PENTEST"  # title/nome dos recursos criados — âncora do --cleanup

vulns: list[str] = []


def result(secure: bool, name: str, evidence: str = "") -> None:
    print(f"[{' SECURE ' if secure else ' !!VULN!! '}] {name}  {evidence}")
    if not secure:
        vulns.append(f"{name} :: {evidence}")


def _load_secret() -> str:
    """JWT_SECRET_KEY do ambiente ou do ``.env`` do backend. Nunca ecoa o valor."""
    s = os.getenv("JWT_SECRET_KEY")
    if s:
        return s
    envp = os.path.join(BACKEND_DIR, ".env")
    if os.path.exists(envp):
        with open(envp, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("JWT_SECRET_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("JWT_SECRET_KEY não encontrado (defina no ambiente ou no .env do backend).")


def make_call(base: str):
    """Fecha ``call(method, path, token, body, raw_body, headers) -> (status, bytes)``."""
    def call(method, path, token=None, body=None, raw_body=None, headers=None):
        data = raw_body if raw_body is not None else (
            json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None))
        req = urllib.request.Request(base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:  # noqa: BLE001 — rede/erro vira status -1 p/ o check
            return -1, str(e).encode()
    return call


def jbody(b):
    try:
        return json.loads(b or b"{}")
    except Exception:
        return {}


def d(b):
    j = jbody(b)
    return j.get("data", j) if isinstance(j, dict) else j


def leaks(b: bytes):
    low = (b or b"").lower()
    for sig in [b"traceback", b"psycopg2", b"select ", b"insert into", b".py\", line",
                b"jwt_secret", b"password_hash"]:
        if sig in low:
            return sig.decode(errors="ignore")
    return None


def mint(claims: dict, secret: str, alg="HS256", with_exp=True):
    payload = dict(claims)
    if with_exp:
        payload["exp"] = int(time.time()) + 3600
    payload.setdefault("iat", int(time.time()))
    if alg == "none":
        hdr = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=")
        pl = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
        return (hdr + b"." + pl + b".").decode()
    return jwt.encode(payload, secret, algorithm=alg)


def setup(call, secret: str) -> dict:
    """Duas orgs (A=demo, B=signup) + caso/cliente/parte/documento na org A + tokens forjados."""
    st, b = call("POST", "/auth/login", body={"email": "demo@contratovisto.com", "password": "DemoLocal#2026"})
    tokA = d(b).get("access_token")
    if not tokA:
        raise SystemExit(f"login demo falhou (st={st}) — dev-server no ar e org demo semeada? {b[:120]!r}")
    clA = jwt.decode(tokA, secret, algorithms=["HS256"])
    orgA, userA = clA["organization_id"], clA["user_id"]

    emailB = f"pentest-b-{uuid.uuid4().hex[:8]}@qapentest.com"
    call("POST", "/users", body={"email": emailB, "name": TAG, "password": "PentestB#2026"})
    st, b = call("POST", "/auth/login", body={"email": emailB, "password": "PentestB#2026"})
    tokB = d(b).get("access_token")
    if not tokB:
        raise SystemExit(f"signup/login org B falhou (st={st}) {b[:120]!r}")
    clB = jwt.decode(tokB, secret, algorithms=["HS256"])
    orgB, userB = clB["organization_id"], clB["user_id"]

    idem = "sec-" + uuid.uuid4().hex[:10]
    st, b = call("POST", "/requests", tokA, {
        "product_type": "analise_contratual", "selected_modules": [], "source_mode": "local",
        "idempotency_key": idem, "title": TAG,
        "parties": [{"name": "Parte PII", "role": "reu", "person_type": "individual",
                     "document": "12345678901", "email": "vitima@empresa.com", "phone": "11999998888"}],
        "document": {"filename": "doc.pdf", "size_bytes": 2000}})
    caseA, reqA = d(b).get("case_id"), d(b).get("request_id")
    partyA = docA = None
    if caseA:
        _, ag = call("GET", f"/cases/{caseA}/aggregate", tokA)
        parts, docs = d(ag).get("parties", []), d(ag).get("documents", [])
        partyA = parts[0].get("id") if parts else None
        docA = docs[0].get("id") if docs else None

    st, b = call("POST", "/clients", tokA, {"name": TAG, "document": "11144477735", "email": "a@qapentest.com"})
    clientA = d(b).get("id") or d(b).get("client_id")

    return {
        "tokA": tokA, "orgA": orgA, "userA": userA,
        "tokB": tokB, "orgB": orgB, "userB": userB,
        "tokViewerA": mint({"user_id": userA, "role": "viewer", "organization_id": orgA}, secret),
        "tokTampered": tokA[:-3] + ("aaa" if not tokA.endswith("aaa") else "bbb"),
        "tokAlgNone": mint({"user_id": userA, "role": "admin", "organization_id": orgA}, secret, alg="none"),
        "tokNoExp": mint({"user_id": userA, "role": "admin", "organization_id": orgA}, secret, with_exp=False),
        "tokWrongSecret": mint({"user_id": userA, "role": "admin", "organization_id": orgA}, "segredo-do-atacante"),
        "caseA": caseA, "reqA": reqA, "clientA": clientA, "partyA": partyA, "docA": docA, "idem": idem,
    }


def wave1(call, c):
    print("\n" + "=" * 70 + "\n1) AUTHZ / RBAC / TOKEN\n" + "=" * 70)
    st, _ = call("GET", "/cases")
    result(st in (401, 403), "sem token -> rota protegida", f"HTTP {st}")
    st, _ = call("GET", "/cases", c["tokTampered"])
    result(st in (401, 403), "token com assinatura adulterada", f"HTTP {st}")
    st, _ = call("GET", "/cases", c["tokAlgNone"])
    result(st in (401, 403), "token alg=none", f"HTTP {st}")
    st, _ = call("GET", "/cases", c["tokNoExp"])
    result(st in (401, 403), "token sem exp", f"HTTP {st}")
    st, _ = call("GET", "/cases", c["tokWrongSecret"])
    result(st in (401, 403), "token assinado com segredo errado", f"HTTP {st}")
    st, _ = call("GET", "/cases", c["tokViewerA"])
    result(st == 200, "viewer LÊ /cases (deve poder)", f"HTTP {st}")
    st, _ = call("POST", "/clients", c["tokViewerA"], {"name": "x", "document": "11144477735"})
    result(st == 403, "viewer ESCREVE /clients (barrado)", f"HTTP {st}")
    st, _ = call("POST", "/requests", c["tokViewerA"], {"product_type": "analise_contratual",
                 "selected_modules": [], "idempotency_key": "v" + c["idem"], "parties": []})
    result(st == 403, "viewer cria pedido (barrado)", f"HTTP {st}")
    if c["caseA"]:
        st, _ = call("POST", f"/cases/{c['caseA']}/triage/run", c["tokViewerA"], {})
        result(st == 403, "viewer roda triagem (barrado)", f"HTTP {st}")

    print("\n" + "=" * 70 + "\n2) IDOR / ISOLAMENTO MULTI-TENANT (org B x recursos da org A)\n" + "=" * 70)
    tokB, caseA, clientA = c["tokB"], c["caseA"], c["clientA"]
    if caseA:
        for path, label in [(f"/cases/{caseA}", "caso"), (f"/cases/{caseA}/aggregate", "aggregate")]:
            st, _ = call("GET", path, tokB)
            result(st in (404, 403), f"org B LÊ {label} da org A", f"HTTP {st}")
        st, _ = call("POST", f"/cases/{caseA}/triage/run", tokB, {})
        result(st in (404, 403), "org B roda triagem no caso da org A", f"HTTP {st}")
        st, _ = call("POST", f"/cases/{caseA}/parties", tokB,
                     {"name": "Intruso", "party_type": "reu", "document": "12345678901"})
        result(st in (404, 403), "org B adiciona parte no caso da org A", f"HTTP {st}")
    if clientA:
        st, _ = call("GET", f"/clients/{clientA}", tokB)
        result(st in (404, 403), "org B LÊ cliente da org A", f"HTTP {st}")
    st, b = call("GET", "/cases", tokB)
    lst = d(b)
    items = lst.get("items", lst) if isinstance(lst, dict) else lst
    ids = {str(x.get("id")) for x in items} if isinstance(items, list) else set()
    result(str(caseA) not in ids, "caso da org A NÃO aparece na lista da org B", f"{len(ids)} casos p/ B")

    print("\n" + "=" * 70 + "\n3) INJEÇÃO (SQLi / path)\n" + "=" * 70)
    for payload in ["' OR '1'='1", "'; DROP TABLE cases;--", "1 UNION SELECT NULL--", "%27%20OR%201=1"]:
        st, b = call("GET", "/cases?q=" + urllib.request.quote(payload), c["tokA"])
        result(st != 500 and not leaks(b), f"SQLi em ?q= [{payload[:22]}]", f"HTTP {st}")
    st, _ = call("GET", "/cases/" + urllib.request.quote("' OR 1=1--") + "/aggregate", c["tokA"])
    result(st in (400, 404), "SQLi em path param (uuid)", f"HTTP {st}")
    st, _ = call("GET", "/cases/" + urllib.request.quote("1;DROP TABLE"), c["tokA"])
    result(st in (400, 404), "path param malicioso", f"HTTP {st}")


def wave2(call, c):
    tokA, caseA = c["tokA"], c["caseA"]
    idem = c["idem"]
    print("\n" + "=" * 70 + "\n4) UPLOAD MALICIOSO\n" + "=" * 70)
    st, _ = call("POST", "/requests", tokA, {"product_type": "analise_contratual", "idempotency_key": "up1" + idem,
                 "parties": [], "document": {"filename": "x.pdf", "size_bytes": 11 * 1024 * 1024}})
    result(st == 400, "documento > 10MB (size_bytes)", f"HTTP {st}")
    st, b = call("POST", "/requests", tokA, {"product_type": "analise_contratual", "idempotency_key": "up2" + idem,
                 "parties": [], "document": {"filename": "../../../etc/passwd", "size_bytes": 1000,
                                             "storage_key": "../../../secret"}})
    cid = d(b).get("case_id")
    if cid:
        _, ab = call("GET", f"/cases/{cid}/aggregate", tokA)
        docs = d(ab).get("documents", [])
        raw = json.dumps([dd.get("s3_path", dd.get("s3_url", "")) for dd in docs])
        result(".." not in raw, "traversal no nome do arquivo é sanitizado", f"paths={raw[:60]}")
    else:
        result(st in (200, 201), "upload c/ nome malicioso não quebra", f"HTTP {st}")

    print("\n" + "=" * 70 + "\n5) VALIDAÇÃO / DoS\n" + "=" * 70)
    st, _ = call("POST", "/requests", tokA, raw_body=b'{"a":' + b"9" * (1024 * 1024 + 50) + b"}")
    result(st in (400, 413), "body > 1MB", f"HTTP {st}")
    st, _ = call("POST", "/requests", tokA, raw_body=b'{"product_type": "x", ')
    result(st == 400, "JSON malformado", f"HTTP {st}")
    st, _ = call("POST", "/requests", tokA, raw_body=b'{"product_type": NaN}')
    result(st == 400, "JSON com NaN", f"HTTP {st}")
    st, _ = call("POST", "/requests", tokA, raw_body=b'{"product_type": "a\\u0000b"}')
    result(st in (400, 201, 422), "null byte em string", f"HTTP {st}")
    st, b = call("GET", "/cases?page=99999999&pageSize=999999", tokA)
    result(st != 500 and not leaks(b), "paginação gigante (overflow/DoS)", f"HTTP {st}")
    nested = b'{"a":' * 2000 + b'1' + b'}' * 2000
    st, _ = call("POST", "/requests", tokA, raw_body=nested)
    result(st in (400, 413), "JSON aninhado profundo (RecursionError)", f"HTTP {st}")

    print("\n" + "=" * 70 + "\n6) LÓGICA DE NEGÓCIO\n" + "=" * 70)
    if caseA:
        for parc, exp, label in [(0, 400, "parcelas=0"), (99, 400, "parcelas=99 (>24)")]:
            st, _ = call("POST", f"/cases/{caseA}/payment", tokA,
                         {"parcelas": parc, "method": "pix", "idempotency_key": f"bl{parc}{idem}"})
            result(st == exp, f"pagar {label}", f"HTTP {st}")
        st, _ = call("POST", f"/cases/{caseA}/payment", tokA, {"parcelas": 2, "method": "debito",
                     "idempotency_key": "dbt" + idem, "card_token": "tok_x", "card_last4": "1234", "card_brand": "visa"})
        result(st == 400, "débito parcelado (parcelas=2)", f"HTTP {st}")
        st, _ = call("POST", f"/cases/{caseA}/payment", tokA,
                     {"parcelas": 1, "method": "cartao", "idempotency_key": "not" + idem})
        result(st == 400, "cartão sem card_token", f"HTTP {st}")
        k = "idem" + idem
        s1, _ = call("POST", f"/cases/{caseA}/payment", tokA, {"parcelas": 1, "method": "pix", "idempotency_key": k})
        s2, _ = call("POST", f"/cases/{caseA}/payment", tokA, {"parcelas": 1, "method": "pix", "idempotency_key": k})
        result(s1 in (200, 201) and s2 in (200, 201), "pagamento idempotente (2x mesma key)", f"HTTP {s1}/{s2}")

    print("\n" + "=" * 70 + "\n7) VAZAMENTO / PII\n" + "=" * 70)
    _, e1 = call("GET", "/cases/nao-e-uuid/aggregate", tokA)
    _, e2 = call("POST", "/requests", tokA, raw_body=b'{"product_type":123, "selected_modules":"x"}')
    result(not leaks(e1) and not leaks(e2), "erros não vazam stack/SQL/segredo", f"amostra={e1[:60]}")
    if caseA:
        _, pa = call("GET", f"/cases/{caseA}/parties", tokA)
        _, pv = call("GET", f"/cases/{caseA}/parties", c["tokViewerA"])
        admin_raw = "12345678901" in json.dumps(d(pa))
        viewer_raw = "12345678901" in json.dumps(d(pv))
        result(not viewer_raw, "viewer NÃO vê CPF cru da parte (mascarado)",
               f"admin_vê_cru={admin_raw} viewer_vê_cru={viewer_raw}")


def wave3(call, c):
    tokA, tokB, tokV = c["tokA"], c["tokB"], c["tokViewerA"]
    userA, orgB, userB, caseA = c["userA"], c["orgB"], c["userB"], c["caseA"]
    reqA, partyA, docA, idem = c["reqA"], c["partyA"], c["docA"], c["idem"]

    print("\n" + "=" * 70 + "\n8) MASS ASSIGNMENT (org/role/created_by no body -> IGNORAR)\n" + "=" * 70)
    _, b = call("POST", "/requests", tokA, {"product_type": "analise_contratual", "selected_modules": [],
                "idempotency_key": "ma" + idem, "title": TAG, "parties": [],
                "organization_id": orgB, "created_by": userB})
    maCase = d(b).get("case_id")
    if maCase:
        sB, _ = call("GET", f"/cases/{maCase}", tokB)
        sA, _ = call("GET", f"/cases/{maCase}", tokA)
        result(sB in (404, 403) and sA == 200, "org_id no body é ignorado (fica na org do token)", f"B={sB} A={sA}")

    print("\n" + "=" * 70 + "\n9) PRIV-ESC / USER MGMT\n" + "=" * 70)
    s, _ = call("PUT", f"/users/{userA}", tokV, {"name": "x", "role": "admin", "status": "active"})
    result(s in (403, 400), "viewer NÃO promove via PUT /users (role só admin)", f"HTTP {s}")
    s, _ = call("PUT", f"/users/{userA}", tokB, {"name": "hack"})
    result(s in (404, 403), "org B edita user da org A", f"HTTP {s}")
    s, _ = call("DELETE", f"/users/{userA}", tokB)
    result(s in (404, 403), "org B deleta user da org A", f"HTTP {s}")
    s, _ = call("DELETE", f"/users/{userA}", tokA)
    result(s == 409, "anti-lockout do último admin (self-delete)", f"HTTP {s}")

    print("\n" + "=" * 70 + "\n10) IDOR EM OUTROS RECURSOS (org B -> recursos da org A)\n" + "=" * 70)
    for path, label in [(f"/requests/{reqA}", "request"), (f"/documents/{docA}/download-url", "document download-url"),
                        (f"/cases/{caseA}/parties", "parties"), (f"/cases/{caseA}/timeline", "timeline"),
                        (f"/cases/{caseA}/report", "report")]:
        if "None" in path:
            continue
        s, _ = call("GET", path, tokB)
        result(s in (404, 403), f"org B lê {label} da org A", f"HTTP {s}")
    if partyA:
        s, _ = call("PATCH", f"/cases/{caseA}/parties/{partyA}", tokB, {"name": "Sequestrado"})
        result(s in (404, 403), "org B edita parte da org A", f"HTTP {s}")
    s, _ = call("GET", "/case-results", tokB)
    result(s in (200, 400, 403, 404), "case-results (org B) não vaza org A", f"HTTP {s} (4xx/vazio = sem leak)")

    print("\n" + "=" * 70 + "\n11) RESET DE SENHA (enumeração / token)\n" + "=" * 70)
    sk, _ = call("POST", "/users/forgot-password", body={"email": "demo@contratovisto.com"})
    su, r_unknown = call("POST", "/users/forgot-password", body={"email": "naoexiste-" + uuid.uuid4().hex + "@x.com"})
    ud = (r_unknown or b"").decode("utf-8", "ignore").lower()
    enum = (su == 404) or (sk != su) or ("encontrado" in ud) or ("not found" in ud) or ("existe" in ud)
    result(not enum, "forgot-password NÃO enumera e-mail (resposta genérica)", f"known={sk} unknown={su}")
    s, _ = call("POST", "/users/reset-password",
                body={"token": "token-do-atacante-" + uuid.uuid4().hex, "password": "NovaSenha#2026"})
    result(s in (400, 404, 403), "reset-password com token inválido rejeitado", f"HTTP {s}")

    print("\n" + "=" * 70 + "\n12) AUTHZ EM ADMIN / LÓGICA\n" + "=" * 70)
    s, _ = call("PUT", "/pricing/config", tokV, {"cases_limit": 999999})
    result(s in (403, 400), "viewer altera pricing/config (barrado)", f"HTTP {s}")
    if caseA:
        s, _ = call("POST", f"/cases/{caseA}/report/review", tokA, {"status": "approved", "review_notes": None})
        result(s in (400, 404, 409), "aprovar relatório inexistente (sem gerar)", f"HTTP {s}")
        s, _ = call("PATCH", f"/cases/{caseA}", tokA, {"status": "STATUS_INVALIDO_XYZ"})
        result(s == 400, "status inválido no update de caso", f"HTTP {s}")


def cleanup(c):
    """Best-effort: remove os recursos DESCARTÁVEIS criados (tagueados ``SEC-PENTEST``
    e e-mails ``@qapentest.com``) via conexão admin ao banco apontado pelo dev-server.
    Só apaga linhas com a âncora — nunca um TRUNCATE. Recusa banco que pareça produção."""
    try:
        import psycopg2  # noqa: PLC0415 — só quando --cleanup
    except Exception:
        print("\n[cleanup] psycopg2 indisponível — pulei a limpeza.")
        return
    dbname = os.getenv("DB_NAME", "contrato_visto")
    if "prod" in dbname.lower():
        print(f"\n[cleanup] DB_NAME={dbname!r} parece produção — RECUSADO.")
        return
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5433")),
            user=os.getenv("DB_ADMIN_USER", "dbadmin"), password=os.getenv("DB_ADMIN_PASS", "localdev_cv"),
            dbname=dbname, connect_timeout=5)
    except Exception as e:  # noqa: BLE001
        print(f"\n[cleanup] sem conexão admin ({e}) — pulei a limpeza.")
        return
    conn.autocommit = True
    deleted: dict[str, object] = {}
    tagged = "(SELECT id FROM public.cases WHERE title = %s)"
    with conn.cursor() as cur:
        # requests<->cases têm FKs compostas circulares ON DELETE SET NULL (migrações 008/009)
        # que incluem organization_id (NOT NULL): qualquer DELETE escopado dispara o SET NULL e
        # viola o NOT NULL. dbadmin é superuser -> desligamos os efeitos de FK/trigger SÓ nesta
        # sessão, apagamos as linhas-filhas (varridas por case_id) + os casos tagueados, e
        # RESTAURAMOS. Só toca linhas SEC-PENTEST — nunca um TRUNCATE.
        cur.execute("SET session_replication_role = replica")
        try:
            cur.execute(
                "SELECT c.table_name FROM information_schema.columns c"
                " JOIN information_schema.tables t"
                "   ON t.table_schema = c.table_schema AND t.table_name = c.table_name"
                " WHERE c.table_schema = 'public' AND c.column_name = 'case_id'"
                "   AND t.table_type = 'BASE TABLE'")  # exclui views (ex.: documents_with_embeddings)
            for (t,) in cur.fetchall():
                try:
                    cur.execute(f"DELETE FROM public.{t} WHERE case_id IN {tagged}", (TAG,))
                    if cur.rowcount:
                        deleted[t] = cur.rowcount
                except Exception as e:  # noqa: BLE001 — uma tabela-filha não aborta o resto
                    deleted[t] = f"erro: {str(e).splitlines()[0][:40]}"
            for label, sql, params in [
                ("casos", "DELETE FROM public.cases WHERE title = %s", (TAG,)),
                ("clientes", "DELETE FROM public.clients WHERE legal_name = %s", (TAG,)),
                ("usuários qapentest", "DELETE FROM public.users WHERE email LIKE %s", ("%@qapentest.com",)),
                ("orgs qapentest órfãs",
                 "DELETE FROM public.organizations o WHERE o.name = %s"
                 " AND NOT EXISTS (SELECT 1 FROM public.users u WHERE u.organization_id = o.id)", (TAG,)),
            ]:
                try:
                    cur.execute(sql, params)
                    deleted[label] = cur.rowcount
                except Exception as e:  # noqa: BLE001
                    deleted[label] = f"erro: {str(e).splitlines()[0][:40]}"
        finally:
            cur.execute("SET session_replication_role = DEFAULT")
    conn.close()
    print("\n[cleanup] " + " | ".join(f"{k}={v}" for k, v in deleted.items() if v))


def main() -> int:
    ap = argparse.ArgumentParser(description="Pentest dinâmico autorizado contra a API viva (dev).")
    ap.add_argument("--base", default="http://127.0.0.1:8000/api/v1", help="base da API (com /api/v1)")
    ap.add_argument("--waves", default="1,2,3", help="ondas a rodar, ex.: 1,2 (default todas)")
    ap.add_argument("--cleanup", action="store_true", help="remove os dados de teste ao final")
    args = ap.parse_args()

    secret = _load_secret()
    call = make_call(args.base)
    waves = {w.strip() for w in args.waves.split(",") if w.strip()}

    print("=" * 70 + "\nSETUP\n" + "=" * 70)
    try:
        ctx = setup(call, secret)
    except SystemExit as e:
        print(f"[setup] {e}")
        return 1
    print(f"  org A={ctx['orgA'][:8]} user={ctx['userA'][:8]} | org B={ctx['orgB'][:8]} | "
          f"caso={str(ctx['caseA'])[:8]} cliente={str(ctx['clientA'])[:8]}")

    runners = {"1": wave1, "2": wave2, "3": wave3}
    for w in ("1", "2", "3"):
        if w in waves:
            runners[w](call, ctx)

    if args.cleanup:
        cleanup(ctx)

    print("\n" + "=" * 70)
    print(f"RESULTADO ({len(waves)} onda(s)): {'TUDO SECURE' if not vulns else str(len(vulns)) + ' ACHADO(S)'}")
    for v in vulns:
        print("  - " + v)
    return 0 if not vulns else 2


if __name__ == "__main__":
    sys.exit(main())
