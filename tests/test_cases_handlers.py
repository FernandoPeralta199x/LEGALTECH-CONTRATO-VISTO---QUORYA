"""Fase 2 — testes de integração dos handlers cases/case_results (PG18 + RLS).

Invoca os handlers com o `event` que o API Gateway entregaria (com o contexto do
JWT Authorizer), validando RLS, isolamento, gate de case visível e auth.
"""
import json
import uuid

import psycopg2
import pytest

from src.handlers import case_results as cr_h
from src.handlers import cases as cases_h
from src.handlers import payments as pay_h
from src.handlers import requests as req_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _admin_conn():
    return psycopg2.connect(
        host="localhost", port=5433, user="dbadmin",
        password="localdev_cv", dbname="contrato_visto", connect_timeout=5,
    )


def _reset_and_seed_client() -> str:
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.cases, public.clients RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE audit.audit_log RESTART IDENTITY")
        cur.execute("SELECT set_config('app.organization_id', %s, false)", (SYSTEM_ORG,))
        cur.execute(
            "INSERT INTO public.clients (organization_id, legal_name, document_number, document_type)"
            " VALUES (%s, 'Cliente Teste', %s, 'cnpj') RETURNING id",
            (SYSTEM_ORG, uuid.uuid4().hex[:14]),
        )
        cid = cur.fetchone()[0]
    conn.close()
    return str(cid)


def _event(user_id, role="analyst", body=None, path=None, query=None, org_id=SYSTEM_ORG):
    # Shape REAL do REST API: claims do authorizer ACHATADOS em `authorizer.<key>`.
    return {
        "requestContext": {
            "authorizer": {"user_id": user_id, "email": "u@t.c", "role": role, "organization_id": org_id}
        },
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": query or {},
    }


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"])["data"]


@pytest.fixture()
def client_id():
    return _reset_and_seed_client()


def _make_case(user_id, client_id, role="analyst"):
    resp = cases_h.create_case(
        _event(user_id, role, body={"client_id": client_id, "case_type": "contract_analysis"}),
        None,
    )
    return resp


def test_create_get_list_case(client_id):
    a = str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]

    assert cases_h.get_case(_event(a, path={"caseId": case_id}), None)["statusCode"] == 200
    listed = _data(cases_h.list_cases(_event(a), None))["items"]
    assert len(listed) == 1 and listed[0]["id"] == case_id


def _completed_at(case_id):
    conn = _admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT completed_at FROM public.cases WHERE id=%s", (case_id,))
        val = cur.fetchone()[0]
    conn.close()
    return val


def test_completed_at_preservado_ao_fechar(client_id):
    # Varredura-qualidade #3 (MEDIO): transição completed -> closed NÃO deve zerar completed_at.
    a = str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]
    cases_h.update_case(_event(a, path={"caseId": case_id}, body={"status": "completed"}), None)
    apos_completed = _completed_at(case_id)
    assert apos_completed is not None
    cases_h.update_case(_event(a, path={"caseId": case_id}, body={"status": "closed"}), None)
    assert _completed_at(case_id) == apos_completed  # preservado


def test_completed_at_limpo_ao_reabrir(client_id):
    # reabrir (status não-finalizado) deve limpar completed_at
    a = str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]
    cases_h.update_case(_event(a, path={"caseId": case_id}, body={"status": "completed"}), None)
    assert _completed_at(case_id) is not None
    cases_h.update_case(_event(a, path={"caseId": case_id}, body={"status": "in_progress"}), None)
    assert _completed_at(case_id) is None


def test_case_isolation_and_admin(client_id):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]

    # usuário de OUTRA organização não vê os casos (RLS por org)
    assert len(_data(cases_h.list_cases(_event(b, org_id=OTHER_ORG), None))["items"]) == 0
    assert cases_h.get_case(_event(b, path={"caseId": case_id}, org_id=OTHER_ORG), None)["statusCode"] == 404
    # admin da MESMA organização vê
    assert len(_data(cases_h.list_cases(_event(b, role="admin"), None))["items"]) == 1


def test_update_and_delete_case(client_id):
    a = str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]

    assert cases_h.update_case(
        _event(a, path={"caseId": case_id}, body={"status": "in_progress"}), None
    )["statusCode"] == 200
    assert cases_h.delete_case(_event(a, path={"caseId": case_id}), None)["statusCode"] == 200
    assert cases_h.get_case(_event(a, path={"caseId": case_id}), None)["statusCode"] == 404


def test_update_status_completed_sets_completed_at(client_id):
    a = str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]
    cases_h.update_case(_event(a, path={"caseId": case_id}, body={"status": "completed"}), None)
    got = _data(cases_h.get_case(_event(a, path={"caseId": case_id}), None))
    assert got["completed_at"] is not None  # completed_at preenchido
    # reabrir limpa o completed_at
    cases_h.update_case(_event(a, path={"caseId": case_id}, body={"status": "in_progress"}), None)
    got2 = _data(cases_h.get_case(_event(a, path={"caseId": case_id}), None))
    assert got2["completed_at"] is None


def test_update_nonexistent_case_returns_404(client_id):
    a = str(uuid.uuid4())
    resp = cases_h.update_case(
        _event(a, path={"caseId": str(uuid.uuid4())}, body={"status": "closed"}), None
    )
    assert resp["statusCode"] == 404


def test_unauthenticated_is_blocked(client_id):
    assert cases_h.list_cases({"requestContext": {}}, None)["statusCode"] == 401


def test_invalid_case_type_returns_400(client_id):
    a = str(uuid.uuid4())
    resp = cases_h.create_case(
        _event(a, body={"client_id": client_id, "case_type": "invalido"}), None
    )
    assert resp["statusCode"] == 400


def test_case_result_only_for_visible_case(client_id):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]

    ok = cr_h.create_case_result(
        _event(a, body={"case_id": case_id, "result_type": "due_diligence",
                        "findings": {"score": 1}, "risk_level": "low"}),
        None,
    )
    assert ok["statusCode"] == 201

    blocked = cr_h.create_case_result(
        _event(b, org_id=OTHER_ORG, body={"case_id": case_id, "result_type": "due_diligence",
                                          "findings": {}, "risk_level": "low"}),
        None,
    )
    assert blocked["statusCode"] == 404  # caso de OUTRA organização não é visível


def test_viewer_cannot_write(client_id):
    # `viewer` é somente leitura: create/update/delete devem ser bloqueados (403).
    v = str(uuid.uuid4())
    resp = cases_h.create_case(
        _event(v, role="viewer", body={"client_id": client_id,
                                       "case_type": "contract_analysis"}),
        None,
    )
    assert resp["statusCode"] == 403


def test_viewer_can_read(client_id):
    # `viewer` continua podendo listar (somente leitura).
    v = str(uuid.uuid4())
    assert cases_h.list_cases(_event(v, role="viewer"), None)["statusCode"] == 200


def test_nested_authorizer_shape_still_works(client_id):
    # Compat: shape aninhado (authorizer.context) — HTTP API / testes legados.
    a = str(uuid.uuid4())
    ev = {
        "requestContext": {"authorizer": {"context": {
            "user_id": a, "email": "u@t.c", "role": "analyst",
            "organization_id": "00000000-0000-0000-0000-000000000001"}}},
        "body": json.dumps({"client_id": client_id, "case_type": "contract_analysis"}),
        "pathParameters": {}, "queryStringParameters": {},
    }
    assert cases_h.create_case(ev, None)["statusCode"] == 201


def test_create_case_invalid_client_returns_400(client_id):
    a = str(uuid.uuid4())
    resp = cases_h.create_case(
        _event(a, body={"client_id": str(uuid.uuid4()),
                        "case_type": "contract_analysis"}),
        None,
    )
    assert resp["statusCode"] == 400  # client_id inexistente


def test_aggregate_expoe_payment_status_e_plano(client_id):
    # caso criado pelo wizard (com request vinculada) nasce pending e sem plano
    admin = str(uuid.uuid4())
    case_id = _data(req_h.create_request(_event(admin, role="admin", body={
        "product_type": "analise_contratual",
        "title": "Contrato p/ teste de pagamento",
        "parties": [{"name": "Empresa X LTDA", "role": "contratante", "person_type": "company"}],
        "selected_modules": ["ia_deepseek", "analise_contratual_ia"],
        "idempotency_key": str(uuid.uuid4()),
    }), None))["case_id"]

    data = _data(cases_h.get_case_aggregate(_event(admin, path={"caseId": case_id}), None))
    assert data["payment_status"] in ("pending", "simulated")
    assert "installment_plan" in data
    assert data["payment_status"] == "pending" and data["installment_plan"] is None
    assert data["request"]["payment_status"] == "pending"

    # aplica o pagamento simulado (1x cartão é sempre ofertado) e reconsulta
    pay = pay_h.create_case_payment(
        _event(admin, body={"parcelas": 1, "method": "cartao", "idempotency_key": "agg-1",
                            "card_token": "tok_mock_agg", "card_last4": "4242",
                            "card_brand": "visa"},
               path={"caseId": case_id}), None)
    assert pay["statusCode"] == 201, pay
    data2 = _data(cases_h.get_case_aggregate(_event(admin, path={"caseId": case_id}), None))
    assert data2["payment_status"] == "simulated"
    assert data2["installment_plan"]["parcelas"] == 1

    # detalhe (get_case/_case_detail) expõe as mesmas chaves top-level
    got = _data(cases_h.get_case(_event(admin, path={"caseId": case_id}), None))
    assert got["payment_status"] == "simulated"
    assert got["installment_plan"]["parcelas"] == 1


# ── case_results: get / list / update / delete + RLS ─────────────────────────
def _make_result(user_id, case_id, risk="low"):
    return cr_h.create_case_result(
        _event(user_id, body={"case_id": case_id, "result_type": "due_diligence",
                              "findings": {"score": 1}, "risk_level": risk}), None)


def test_case_result_get_list(client_id):
    a = str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]
    rid = _data(_make_result(a, case_id))["id"]

    assert cr_h.get_case_result(_event(a, path={"resultId": rid}), None)["statusCode"] == 200
    listed = _data(cr_h.list_case_results(_event(a, query={"caseId": case_id}), None))
    assert len(listed) == 1 and listed[0]["id"] == rid


def test_case_result_update_and_delete(client_id):
    a = str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]
    rid = _data(_make_result(a, case_id))["id"]

    assert cr_h.update_case_result(
        _event(a, path={"resultId": rid}, body={"risk_level": "high"}), None
    )["statusCode"] == 200
    assert cr_h.delete_case_result(_event(a, path={"resultId": rid}), None)["statusCode"] == 200
    assert cr_h.get_case_result(_event(a, path={"resultId": rid}), None)["statusCode"] == 404


def test_case_result_isolation_and_rbac(client_id):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]
    rid = _data(_make_result(a, case_id))["id"]

    # usuário de OUTRA organização não vê o resultado (RLS por org) → 404
    assert cr_h.get_case_result(_event(b, path={"resultId": rid}, org_id=OTHER_ORG), None)["statusCode"] == 404
    # admin da MESMA organização vê
    assert cr_h.get_case_result(_event(b, role="admin", path={"resultId": rid}),
                                None)["statusCode"] == 200
    # viewer não escreve
    assert cr_h.update_case_result(
        _event(a, role="viewer", path={"resultId": rid}, body={"risk_level": "high"}),
        None)["statusCode"] == 403


def test_case_result_get_nonexistent_404(client_id):
    a = str(uuid.uuid4())
    assert cr_h.get_case_result(_event(a, path={"resultId": str(uuid.uuid4())}),
                                None)["statusCode"] == 404


def test_create_case_inactive_client_409(client_id):
    # B3: cliente inativo não aceita novos casos
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("UPDATE public.clients SET status='inactive' WHERE id=%s", (client_id,))
    conn.close()
    a = str(uuid.uuid4())
    resp = cases_h.create_case(
        _event(a, body={"client_id": client_id, "case_type": "contract_analysis"}), None)
    assert resp["statusCode"] == 409


def test_case_result_blocked_on_finalized_case(client_id):
    # B4: case completed/closed não aceita novos resultados
    a = str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]
    cases_h.update_case(_event(a, path={"caseId": case_id}, body={"status": "completed"}), None)
    resp = cr_h.create_case_result(
        _event(a, body={"case_id": case_id, "result_type": "dd",
                        "findings": {}, "risk_level": "low"}), None)
    assert resp["statusCode"] == 409


def test_delete_case_audit_records_resource_id(client_id):
    # #26: delete agora é soft (UPDATE deleted_at) -> auditado como UPDATE com o resource_id.
    a = str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]
    assert cases_h.delete_case(_event(a, path={"caseId": case_id}), None)["statusCode"] == 200
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT resource_id, change_after FROM audit.audit_log WHERE action='UPDATE'"
                    " AND resource_type='cases' ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
    conn.close()
    assert row is not None and str(row[0]) == case_id
    assert row[1] and row[1].get("deleted_at") is not None  # soft-delete registrado


def test_soft_delete_preserva_linha_e_some_da_listagem(client_id):
    # #26: a UI promete arquivar -> deleted_at marcado, linha preservada, fora da listagem
    a = str(uuid.uuid4())
    case_id = _data(_make_case(a, client_id))["id"]
    assert cases_h.delete_case(_event(a, path={"caseId": case_id}), None)["statusCode"] == 200
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT deleted_at FROM public.cases WHERE id=%s", (case_id,))
        row = cur.fetchone()
    conn.close()
    assert row is not None and row[0] is not None  # linha preservada, soft
    listed = _data(cases_h.list_cases(_event(a), None))
    assert case_id not in {c["id"] for c in listed["items"]}
    # 404 ao tentar deletar de novo (já arquivado)
    assert cases_h.delete_case(_event(a, path={"caseId": case_id}), None)["statusCode"] == 404


def test_triggers_de_auditoria_existem_nas_migracoes():
    # FE4-26-B2: migrations 013/014 garantem os triggers de auditoria em deploy limpo
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
            " WHERE NOT t.tgisinternal AND t.tgname LIKE 'audit_%'"
            " AND c.relname IN ('cases','documents','pricing_configs')")
        found = {(r[0], r[1]) for r in cur.fetchall()}
    conn.close()
    assert ("cases", "audit_cases_update") in found
    assert ("cases", "audit_cases_delete") in found
    assert ("documents", "audit_documents_delete") in found
    assert ("pricing_configs", "audit_pricing_configs_insert") in found
    assert ("pricing_configs", "audit_pricing_configs_update") in found
