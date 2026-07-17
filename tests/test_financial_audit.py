"""Testes da aba Auditoria (Opção A) — leitura read-only de audit.audit_log.

Cobre: escopo financeiro (exclui recursos não-financeiros), diff por evento
(UPDATE mostra campos alterados sem ruído; INSERT mostra salientes), join do ator,
isolamento por org (RLS policy 020), filtros de ação/tipo, admin-only e período.
A escrita é semeada direto na trilha (o trigger real já é exercido noutros testes).
"""
from _dbadmin import admin_conn
import json
import uuid

import pytest

from src.handlers import financial_audit as aud_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _reset():
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE audit.audit_log RESTART IDENTITY")
    conn.close()


def _seed_user(user_id, org=SYSTEM_ORG, role="admin", status="active", email=None):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s,%s,'x','Ator Teste',%s,%s,%s)"
            " ON CONFLICT (id) DO UPDATE SET role=EXCLUDED.role, status=EXCLUDED.status",
            (user_id, email or f"u_{user_id}@t.c", role, status, org))
    conn.close()


def _admin(org=SYSTEM_ORG, email=None):
    uid = str(uuid.uuid4())
    _seed_user(uid, org=org, email=email)
    return uid


def _seed_audit(org, user_id, resource_type, action, before=None, after=None, created_at=None):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit.audit_log"
            " (user_id, organization_id, action, resource_type, resource_id,"
            "  change_before, change_after, created_at)"
            " VALUES (%s,%s,%s,%s, gen_random_uuid(), %s::jsonb, %s::jsonb, COALESCE(%s, now()))",
            (user_id, org, action, resource_type,
             json.dumps(before) if before is not None else None,
             json.dumps(after) if after is not None else None, created_at))
    conn.close()


def _event(user_id, role="admin", org_id=SYSTEM_ORG, query=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "organization_id": org_id}},
        "body": None,
        "pathParameters": {},
        "queryStringParameters": query or {},
    }


def _data(resp, code=200):
    assert resp["statusCode"] == code, resp
    return json.loads(resp["body"]).get("data")


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


def test_audit_vazio():
    a = _admin()
    d = _data(aud_h.list_audit(_event(a), None))
    assert d["summary"]["total"] == 0 and d["items"] == [] and d["total"] == 0
    assert "pricing_configs" in d["resources"]


def test_audit_escopo_financeiro_e_actor():
    email = f"quem_{uuid.uuid4().hex[:10]}@org.com"  # único por execução (users não é truncado)
    a = _admin(email=email)
    _seed_audit(SYSTEM_ORG, a, "pricing_configs", "INSERT", after={"base_price_cents": 12000, "status": "active"})
    _seed_audit(SYSTEM_ORG, a, "cases", "UPDATE", before={"status": "open"}, after={"status": "closed"})  # NÃO financeiro

    d = _data(aud_h.list_audit(_event(a), None))
    assert d["summary"]["total"] == 1  # 'cases' excluído do escopo financeiro
    ev = d["items"][0]
    assert ev["resource_type"] == "pricing_configs" and ev["action"] == "INSERT"
    assert ev["actor_email"] == email  # join escopado por org com public.users
    # INSERT: campos salientes viram changes (before None)
    fields = {c["field"]: c for c in ev["changes"]}
    assert "base_price_cents" in fields and fields["base_price_cents"]["after"] == 12000


def test_audit_diff_update_ignora_ruido():
    a = _admin()
    _seed_audit(SYSTEM_ORG, a, "tax_provisions", "UPDATE",
                before={"estimated_amount_cents": 5000, "updated_at": "2026-01-01"},
                after={"estimated_amount_cents": 8000, "updated_at": "2026-02-02"})
    d = _data(aud_h.list_audit(_event(a), None))
    changes = d["items"][0]["changes"]
    # só o campo que mudou de fato; updated_at (ruído) fora
    assert len(changes) == 1
    assert changes[0] == {"field": "estimated_amount_cents", "before": 5000, "after": 8000}


def test_audit_isolado_por_org():
    a = _admin()
    _seed_audit(SYSTEM_ORG, a, "fiscal_documents", "INSERT", after={"amount_cents": 9000})
    b = _admin(org=OTHER_ORG)  # admin ativo de OUTRA org (passa no assert_active_admin)
    d = _data(aud_h.list_audit(_event(b, org_id=OTHER_ORG), None))
    assert d["summary"]["total"] == 0 and d["items"] == []  # RLS: outra org não vê


def test_audit_filtros_acao_e_tipo():
    a = _admin()
    _seed_audit(SYSTEM_ORG, a, "pricing_configs", "INSERT", after={"base_price_cents": 100})
    _seed_audit(SYSTEM_ORG, a, "pricing_configs", "UPDATE", before={"base_price_cents": 100}, after={"base_price_cents": 200})
    _seed_audit(SYSTEM_ORG, a, "tax_provisions", "INSERT", after={"estimated_amount_cents": 50})
    # summary reflete o período (todos os 3), não os filtros
    d = _data(aud_h.list_audit(_event(a), None))
    assert d["summary"]["total"] == 3 and d["summary"]["updates"] == 1
    # filtro por ação: a LISTA filtra (1), mas o SUMMARY continua o do período (3)
    d2 = _data(aud_h.list_audit(_event(a, query={"action": "UPDATE"}), None))
    assert d2["total"] == 1 and d2["summary"]["total"] == 3 and d2["summary"]["updates"] == 1
    # filtro por tipo: idem — lista 1, summary do período inteiro
    d3 = _data(aud_h.list_audit(_event(a, query={"resource_type": "tax_provisions"}), None))
    assert d3["total"] == 1 and d3["summary"]["total"] == 3
    # filtro inválido → 400 ('cases' é auditado mas NÃO está no escopo financeiro)
    assert aud_h.list_audit(_event(a, query={"action": "PATCH"}), None)["statusCode"] == 400
    assert aud_h.list_audit(_event(a, query={"resource_type": "cases"}), None)["statusCode"] == 400


def test_audit_admin_only():
    a = _admin(org=SYSTEM_ORG)
    # viewer (não-admin) → 403 mesmo com token válido
    assert aud_h.list_audit(_event(a, role="viewer"), None)["statusCode"] == 403


def test_audit_periodo():
    a = _admin()
    _seed_audit(SYSTEM_ORG, a, "pricing_configs", "INSERT", after={"base_price_cents": 100})  # now
    assert _data(aud_h.list_audit(_event(a, query={"period": "lastMonth"}), None))["summary"]["total"] == 0
    assert _data(aud_h.list_audit(_event(a, query={"period": "today"}), None))["summary"]["total"] == 1


def test_audit_clients_pii_mascarado():
    # Minimização LGPD: no diff de cliente, valores de PII de contato são mascarados
    # (mostra QUE mudou, não o dado do titular); a razão social permanece.
    a = _admin()
    _seed_audit(SYSTEM_ORG, a, "clients", "UPDATE",
                before={"legal_name": "Acme", "email": "old@x.com", "phone": "111", "address_city": "SP"},
                after={"legal_name": "Acme Nova", "email": "new@y.com", "phone": "222", "address_city": "RJ"})
    d = _data(aud_h.list_audit(_event(a, query={"resource_type": "clients"}), None))
    changes = {c["field"]: c for c in d["items"][0]["changes"]}
    assert changes["legal_name"]["after"] == "Acme Nova"        # nome NÃO mascarado
    assert changes["email"]["before"] == "•••" and changes["email"]["after"] == "•••"
    assert changes["phone"]["after"] == "•••"                   # telefone mascarado
    assert changes["address_city"]["after"] == "•••"           # endereço mascarado


def _write_request_audited(org, user_id):
    """Insere uma venda COM contexto de usuário (set_config) → dispara o trigger
    log_audit_org (Opção B) com ator real e organization_id vindo da própria linha."""
    conn = admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
        cur.execute("SELECT set_config('app.organization_id', %s, true)", (org,))
        cur.execute(
            "INSERT INTO public.requests"
            " (organization_id, created_by, code, product_type, product_label, title, status, source_mode)"
            " VALUES (%s,%s,%s,'analise_contratual','Análise','T','created','manual')",
            (org, user_id, "R-" + uuid.uuid4().hex[:10]))
    conn.commit()
    conn.close()


def test_audit_trigger_requests_captura_venda():
    # Opção B: uma venda (requests) escrita COM contexto vira evento auditado, com
    # ator identificado e visível à própria org (org veio da linha).
    a = _admin()
    _write_request_audited(SYSTEM_ORG, a)
    d = _data(aud_h.list_audit(_event(a, query={"resource_type": "requests"}), None))
    assert d["total"] == 1
    ev = d["items"][0]
    assert ev["resource_type"] == "requests" and ev["action"] == "INSERT"
    assert ev["actor_email"] is not None  # ator identificado via app.user_id
    assert "requests" in d["resources"]  # escopo financeiro agora inclui vendas
