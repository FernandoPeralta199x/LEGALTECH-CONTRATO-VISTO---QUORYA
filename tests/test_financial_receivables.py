"""Testes da aba Recebíveis — vendas PENDENTES (a receber) sobre requests.

Cobre: vazio, SÓ pendentes (exclui paid/simulated), projeção (cliente/venda/valor/
vencimento/atraso/status), vencimento = created_at + prazo, atraso computado, status
a-vencer vs em-atraso, PRAZO da config por org, KPIs do período, paginação, busca,
RBAC admin-only + revogação, RLS, e reconciliação com o 'total pendente' da Visão Geral.
"""
from _dbadmin import admin_conn
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest

from src.handlers import financial as ovw_h
from src.handlers import financial_receivables as rec_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def _reset():
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.requests, public.cases, public.clients,"
                    " public.pricing_configs RESTART IDENTITY CASCADE")
    conn.close()


def _seed_user(user_id, org=SYSTEM_ORG, role="admin", status="active"):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s,%s,'x','Test',%s,%s,%s)"
            " ON CONFLICT (id) DO UPDATE SET role=EXCLUDED.role, status=EXCLUDED.status",
            (user_id, f"u_{user_id}@t.c", role, status, org))
    conn.close()


def _admin(org=SYSTEM_ORG):
    uid = str(uuid.uuid4())
    _seed_user(uid, org=org, role="admin", status="active")
    return uid


def _seed_client(org, name):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("INSERT INTO public.clients (organization_id, legal_name, document_number)"
                    " VALUES (%s,%s,%s) RETURNING id", (org, name, uuid.uuid4().hex[:18]))
        cid = cur.fetchone()[0]
    conn.close()
    return cid


def _seed_case(org, client_id):
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("INSERT INTO public.cases (organization_id, client_id, case_type)"
                    " VALUES (%s,%s,'fin') RETURNING id", (org, client_id))
        caseid = cur.fetchone()[0]
    conn.close()
    return caseid


def _seed(org=SYSTEM_ORG, price=10000, pay="pending", days_ago=0, case_id=None, code=None):
    """Venda com created_at = now - days_ago. Recebível = payment_status pending/processing."""
    conn = admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.requests"
            " (organization_id, created_by, code, product_type, product_label, title,"
            "  status, source_mode, total_price_cents, payment_status, case_id, created_at)"
            " VALUES (%s, gen_random_uuid(), %s, 'analise_contratual', 'Análise', 'T',"
            "         'created', 'manual', %s, %s, %s, %s) RETURNING id",
            (org, code or ("R-" + uuid.uuid4().hex[:10]), price, pay, case_id, _days_ago(days_ago)))
        rid = cur.fetchone()[0]
    conn.close()
    return rid


def _seed_config(org, term):
    """Prazo de pagamento da org (pricing_configs dispara o trigger de auditoria 'duro')."""
    conn = admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(uuid.uuid4()),))
        cur.execute("SELECT set_config('app.organization_id', %s, true)", (org,))
        cur.execute(
            "INSERT INTO public.pricing_configs (organization_id, installment_config)"
            " VALUES (%s, %s::jsonb)",
            (org, json.dumps({"primeiro_vencimento_dias": term})))
    conn.commit()
    conn.close()


def _event(user_id, role="admin", org_id=SYSTEM_ORG, query=None):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "perfil": "administrador", "organization_id": org_id}},
        "body": None,
        "pathParameters": {},
        "queryStringParameters": query or {},
    }


def _data(resp, code=200):
    assert resp["statusCode"] == code, resp
    return json.loads(resp["body"]).get("data")


YEAR = {"period": "year"}


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


def test_receivables_vazio():
    a = _admin()
    d = _data(rec_h.list_receivables(_event(a), None))
    assert d["items"] == [] and d["total"] == 0
    assert d["summary"]["count"] == 0 and d["summary"]["total_cents"] == 0
    assert d["payment_term_days"] == 30    # default sem config


def test_receivables_so_pendentes():
    a = _admin()
    _seed(price=10000, pay="pending", days_ago=1)      # recebível
    _seed(price=20000, pay="processing", days_ago=1)   # recebível
    _seed(price=30000, pay="paid", days_ago=1)         # recebido → NÃO é recebível
    _seed(price=40000, pay="simulated", days_ago=1)    # mock → NÃO é recebível
    d = _data(rec_h.list_receivables(_event(a, query=YEAR), None))
    assert d["total"] == 2
    assert d["summary"]["total_cents"] == 30000        # 10000 + 20000 (só pendentes)


def test_receivables_projecao_e_vencimento():
    a = _admin()
    cid = _seed_client(SYSTEM_ORG, "Cliente A")
    case = _seed_case(SYSTEM_ORG, cid)
    _seed(price=15000, pay="pending", days_ago=5, case_id=case, code="R-REC")
    row = _data(rec_h.list_receivables(_event(a, query=YEAR), None))["items"][0]
    assert row["code"] == "R-REC"
    assert row["client_name"] == "Cliente A"
    assert row["amount_cents"] == 15000
    # vencimento = created_at(5 dias atrás) + 30 = 25 dias no futuro → a vencer
    assert row["days_overdue"] == 0
    assert row["status"] == "pending"
    esperado = (datetime.now(timezone.utc) - timedelta(days=5) + timedelta(days=30)).date().isoformat()
    assert row["due_date"] == esperado


def test_receivables_em_atraso():
    a = _admin()
    _seed(price=10000, pay="pending", days_ago=60, code="R-ATRASO")  # venc = 30 dias atrás
    _seed(price=20000, pay="pending", days_ago=5, code="R-AVENCER")  # venc = 25 dias frente
    items = {r["code"]: r for r in _data(rec_h.list_receivables(_event(a, query=YEAR), None))["items"]}
    assert items["R-ATRASO"]["status"] == "overdue" and items["R-ATRASO"]["days_overdue"] == 30
    assert items["R-AVENCER"]["status"] == "pending" and items["R-AVENCER"]["days_overdue"] == 0


def test_receivables_prazo_da_config_muda_atraso():
    a = _admin()
    _seed_config(SYSTEM_ORG, term=10)                     # prazo curto: 10 dias
    _seed(price=10000, pay="pending", days_ago=20, code="R-X")  # venc = 20-10 = 10 dias atrás
    d = _data(rec_h.list_receivables(_event(a, query=YEAR), None))
    assert d["payment_term_days"] == 10
    row = d["items"][0]
    assert row["status"] == "overdue" and row["days_overdue"] == 10  # com prazo 30 seria 'a vencer'


def test_receivables_summary_atraso_e_a_vencer():
    a = _admin()
    _seed(price=10000, pay="pending", days_ago=60)   # em atraso
    _seed(price=25000, pay="pending", days_ago=2)    # a vencer
    s = _data(rec_h.list_receivables(_event(a, query=YEAR), None))["summary"]
    assert s["count"] == 2 and s["total_cents"] == 35000
    assert s["overdue_cents"] == 10000 and s["due_cents"] == 25000


def test_receivables_cliente_nao_atribuido():
    a = _admin()
    _seed(price=8000, pay="pending", days_ago=1, case_id=None)
    row = _data(rec_h.list_receivables(_event(a, query=YEAR), None))["items"][0]
    assert row["client_name"] is None and row["client_id"] is None


def test_receivables_paginacao():
    a = _admin()
    for i in range(5):
        _seed(price=1000 * (i + 1), pay="pending", days_ago=i + 1, code=f"R-{i}")
    d = _data(rec_h.list_receivables(_event(a, query={"period": "year", "page": "1", "page_size": "2"}), None))
    assert len(d["items"]) == 2 and d["total"] == 5 and d["total_pages"] == 3
    assert d["summary"]["count"] == 5 and d["summary"]["total_cents"] == 15000


def test_receivables_busca_nao_altera_kpis():
    a = _admin()
    cid = _seed_client(SYSTEM_ORG, "Acme Corp")
    case = _seed_case(SYSTEM_ORG, cid)
    _seed(price=10000, pay="pending", days_ago=1, case_id=case, code="R-ACME")
    _seed(price=20000, pay="pending", days_ago=1, code="R-XYZ")
    d = _data(rec_h.list_receivables(_event(a, query={"period": "year", "q": "acme"}), None))
    assert d["total"] == 1 and d["items"][0]["client_name"] == "Acme Corp"
    assert d["summary"]["count"] == 2 and d["summary"]["total_cents"] == 30000   # KPIs do período


def test_receivables_admin_only_e_revogacao():
    a = _admin()
    _seed(price=10000, pay="pending", days_ago=1)
    assert rec_h.list_receivables(_event(a, role="viewer", query=YEAR), None)["statusCode"] == 403
    inactive = str(uuid.uuid4())
    _seed_user(inactive, role="admin", status="inactive")
    assert rec_h.list_receivables(_event(inactive, role="admin", query=YEAR), None)["statusCode"] == 403


def test_receivables_rls_isola_org():
    a = _admin()
    _seed(org=SYSTEM_ORG, price=10000, pay="pending", days_ago=1)
    _seed(org=OTHER_ORG, price=99999, pay="pending", days_ago=1)
    d = _data(rec_h.list_receivables(_event(a, query=YEAR), None))
    assert d["total"] == 1 and d["items"][0]["amount_cents"] == 10000


def test_receivables_reconcilia_com_overview_pending():
    """O total a receber = pending_cents da Visão Geral (mesma fonte: requests pendentes)."""
    a = _admin()
    _seed(price=30000, pay="pending", days_ago=60)
    _seed(price=8000, pay="processing", days_ago=2)
    _seed(price=5000, pay="paid", days_ago=1)        # não conta (recebido)
    rec = _data(rec_h.list_receivables(_event(a, query=YEAR), None))["summary"]
    kpis = _data(ovw_h.get_overview(_event(a, query=YEAR), None))["kpis"]
    assert rec["total_cents"] == kpis["pending_cents"] == 38000
