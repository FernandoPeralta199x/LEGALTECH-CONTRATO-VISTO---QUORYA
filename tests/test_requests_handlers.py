"""Coração 3 — testes de integração do POST /requests (wizard orquestrador, PG18+RLS).

Invoca o handler com o `event` que o API Gateway entregaria (claims do JWT Authorizer
achatados) e valida, ponta a ponta: criação atômica de request + case (awaiting_triage)
+ partes + documento + plano de triagem + timeline + price_snapshot, RLS por org,
sequência do código, override de preço, idempotência e RBAC.
"""
from _dbadmin import admin_conn
import json
import uuid
from collections import Counter
from datetime import datetime, timezone

import psycopg2
import pytest

from src.handlers import requests as req_h
from src.services.pricing.estimate import normalize_selected_modules
from src.services.triage_plan import plan_for_selection

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"
# Triagem executa APENAS o que foi comprado (infra do produto + módulos técnicos
# dos comerciais selecionados) — mesma seleção do _payload() abaixo.
N_TRIAGE = len(plan_for_selection(
    "analise_contratual",
    normalize_selected_modules("analise_contratual",
                               ["ia_deepseek", "analise_contratual_ia"])))  # 5


def _admin_conn():
    return admin_conn()


def _reset():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE public.timeline_events, public.triage_modules, public.case_parties,"
            " public.documents, public.requests, public.cases, public.clients,"
            " public.request_code_sequences, public.pricing_configs RESTART IDENTITY CASCADE")
    conn.close()


def _seed_pricing_override(org, overrides):
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.organization_id', %s, false)", (org,))
        # app.user_id também: o trigger de auditoria de pricing_configs (migration 013) exige
        cur.execute("SELECT set_config('app.user_id', %s, false)", (str(uuid.uuid4()),))
        cur.execute("INSERT INTO public.pricing_configs (organization_id, module_overrides)"
                    " VALUES (%s, %s)", (org, json.dumps(overrides)))
    conn.close()


def _event(user_id, role="admin", body=None, path=None, org_id=SYSTEM_ORG):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": {},
    }


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"])["data"]


def _payload(product_type="analise_contratual", **over):
    base = {
        "product_type": product_type,
        "title": "Contrato de prestação de serviços",
        "parties": [
            {"name": "Empresa X LTDA", "role": "contratante", "person_type": "company",
             "document": "12345678000199", "document_type": "cnpj"},
            {"name": "João da Silva", "role": "contratado", "person_type": "individual"},
        ],
        "document": {"filename": "contrato.pdf", "mime_type": "application/pdf", "size_bytes": 2048},
        "selected_modules": ["ia_deepseek", "analise_contratual_ia"],
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


def test_create_request_orquestra_case_partes_doc_triagem_timeline():
    a = str(uuid.uuid4())
    data = _data(req_h.create_request(_event(a, body=_payload()), None))

    assert data["status"] == "awaiting_triage"
    assert data["parties_count"] == 2
    assert data["triage_modules_count"] == N_TRIAGE
    assert data["total_price_cents"] == 2900 + 7900  # ia_deepseek + analise_contratual_ia
    case_id = data["case_id"]

    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT status, product_type, title, code, source_mode FROM public.cases WHERE id=%s",
                    (case_id,))
        st, pt, title, code, sm = cur.fetchone()
        assert st == "awaiting_triage" and pt == "analise_contratual"
        assert title == "Contrato de prestação de serviços" and code.startswith("REQ-")
        assert sm == "local"

        cur.execute("SELECT count(*) FROM public.triage_modules WHERE case_id=%s", (case_id,))
        assert cur.fetchone()[0] == N_TRIAGE
        cur.execute("SELECT count(*) FROM public.case_parties WHERE case_id=%s", (case_id,))
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT count(*) FROM public.documents WHERE case_id=%s", (case_id,))
        assert cur.fetchone()[0] == 1
        # vínculo request<->case fechado nas duas pontas
        cur.execute("SELECT case_id FROM public.requests WHERE id=%s", (data["request_id"],))
        assert str(cur.fetchone()[0]) == case_id
        cur.execute("SELECT event_type FROM public.timeline_events WHERE case_id=%s", (case_id,))
        types = Counter(r[0] for r in cur.fetchall())
    conn.close()

    assert types == Counter({
        "request_created": 1, "case_created": 1, "party_added": 2,
        "document_attached": 1, "triage_plan_created": 1, "wizard_completed": 1,
    })


def test_request_nasce_pending_sem_plano():
    # contrato de pagamento: request nasce pending, sem plano e sem versão de config
    a = str(uuid.uuid4())
    data = _data(req_h.create_request(_event(a, body=_payload()), None))
    conn = _admin_conn()  # dbadmin bypassa RLS: SELECT direto
    with conn.cursor() as cur:
        cur.execute("SELECT payment_status, installment_plan, pricing_config_version"
                    " FROM public.requests WHERE id = %s", (data["request_id"],))
        row = cur.fetchone()
    conn.close()
    assert row[0] == "pending" and row[1] is None and row[2] is None


def test_codigo_sequencial_por_org():
    a = str(uuid.uuid4())
    year = datetime.now(timezone.utc).year
    c1 = _data(req_h.create_request(_event(a, body=_payload()), None))["case_code"]
    c2 = _data(req_h.create_request(_event(a, body=_payload()), None))["case_code"]
    assert c1 == f"REQ-{year}-0001"
    assert c2 == f"REQ-{year}-0002"


def test_override_de_preco_da_org_aplicado_ao_total():
    _seed_pricing_override(SYSTEM_ORG, {"analise_contratual_ia": {"price_cents": 9900}})
    a = str(uuid.uuid4())
    data = _data(req_h.create_request(_event(a, body=_payload()), None))
    assert data["total_price_cents"] == 2900 + 9900  # ia_deepseek + override


def test_pedido_sem_documento_e_opcional():
    # documento é opcional (toggle "anexar contrato" off no wizard) -> cria sem doc
    a = str(uuid.uuid4())
    p = _payload()
    p["document"] = None
    data = _data(req_h.create_request(_event(a, body=p), None))
    assert data["status"] == "awaiting_triage" and data["documents_count"] == 0


def test_product_type_invalido_retorna_400():
    a = str(uuid.uuid4())
    assert req_h.create_request(
        _event(a, body=_payload(product_type="inexistente")), None)["statusCode"] == 400


def test_viewer_nao_cria_pedido():
    v = str(uuid.uuid4())
    assert req_h.create_request(
        _event(v, role="viewer", body=_payload()), None)["statusCode"] == 403


def test_nao_autenticado_bloqueado():
    assert req_h.create_request({"requestContext": {}, "body": "{}"}, None)["statusCode"] == 401


def test_idempotency_key_duplicada_retorna_409():
    a = str(uuid.uuid4())
    p = _payload()
    p["idempotency_key"] = "wizard-abc-123"
    assert req_h.create_request(_event(a, body=p), None)["statusCode"] == 201
    assert req_h.create_request(_event(a, body=p), None)["statusCode"] == 409


def test_get_request_retorna_contagens():
    a = str(uuid.uuid4())
    rid = _data(req_h.create_request(_event(a, body=_payload()), None))["request_id"]
    got = _data(req_h.get_request(_event(a, path={"requestId": rid}), None))
    assert got["status"] == "created"  # ciclo de vida do request
    assert got["case_status"] == "awaiting_triage"  # ciclo de vida do case
    assert got["product_type"] == "analise_contratual"
    assert got["parties_count"] == 2
    assert got["triage_modules_count"] == N_TRIAGE


def test_isolamento_outra_org_nao_ve_pedido():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    rid = _data(req_h.create_request(_event(a, body=_payload()), None))["request_id"]
    # outra organização não enxerga (RLS por org) → 404
    assert req_h.get_request(
        _event(b, path={"requestId": rid}, org_id=OTHER_ORG), None)["statusCode"] == 404


def test_wizard_tolera_campos_extras_do_frontend():
    # o frontend envia notes (raiz) + document com campos extras + parte com campo
    # extra; o backend deve ignorá-los (extra="ignore"), não retornar 400.
    a = str(uuid.uuid4())
    p = _payload(product_type="reuniao_equipe", selected_modules=["ia_deepseek"])
    p["notes"] = "Criado pelo Wizard operacional"
    p["document"] = {"filename": "c.docx", "mime_type": "x", "size_bytes": 10,
                     "original_filename": "c.docx", "storage_provider": "local",
                     "status": "uploaded", "preview_available": False,
                     "download_available": False}
    p["parties"][0]["wizard_party_id"] = "abc-123"  # campo extra na parte
    resp = req_h.create_request(_event(a, body=p), None)
    assert resp["statusCode"] == 201, resp


def test_produto_dados_partes_gera_plano_proprio():
    a = str(uuid.uuid4())
    p = _payload(product_type="dados_partes", selected_modules=["escavador", "targetdata", "ia_deepseek"])
    data = _data(req_h.create_request(_event(a, body=p), None))
    # sem serasa_procon comprado, serasa/procon ficam FORA do plano executável:
    # parties_validation + escavador + reputation_summary + ai_summary
    assert data["triage_modules_count"] == len(plan_for_selection(
        "dados_partes",
        normalize_selected_modules("dados_partes",
                                   ["escavador", "targetdata", "ia_deepseek"])))  # 4
    assert data["total_price_cents"] == 6000 + 3900 + 2900  # 12800


def test_triagem_nao_inclui_conectores_nao_comprados():
    """BUG 2026-07-08: seleção mínima não pode gerar serasa/procon/escavador
    (módulos comerciais não comprados) no plano de triagem."""
    a = str(uuid.uuid4())
    data = _data(req_h.create_request(_event(a, body=_payload()), None))
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT module_key FROM public.triage_modules WHERE case_id=%s",
                    (data["case_id"],))
        keys = {r[0] for r in cur.fetchall()}
    conn.close()
    assert keys == {"document_parser", "ocr", "contract_risk_analysis",
                    "obligations_mapping", "ai_report"}
    assert not {"serasa", "procon", "escavador"} & keys


def test_serasa_procon_comprado_entra_na_triagem():
    a = str(uuid.uuid4())
    p = _payload(selected_modules=["ia_deepseek", "analise_contratual_ia", "serasa_procon"])
    data = _data(req_h.create_request(_event(a, body=p), None))
    assert data["total_price_cents"] == 2900 + 7900 + 5900
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT module_key FROM public.triage_modules WHERE case_id=%s",
                    (data["case_id"],))
        keys = {r[0] for r in cur.fetchall()}
    conn.close()
    assert {"serasa", "procon"} <= keys
    assert "escavador" not in keys  # continua não comprado


def _seed_client(org, legal_name="Cliente Vinculado LTDA"):
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.organization_id', %s, false)", (org,))
        cur.execute("SELECT set_config('app.user_id', %s, false)", (str(uuid.uuid4()),))
        cur.execute(
            "INSERT INTO public.clients"
            " (organization_id, legal_name, document_type, document_number)"
            " VALUES (%s,%s,'cnpj','12345678000100') RETURNING id", (org, legal_name))
        cid = str(cur.fetchone()[0])
    conn.close()
    return cid


def test_request_vincula_cliente_existente():
    a = str(uuid.uuid4())
    client_id = _seed_client(SYSTEM_ORG)
    data = _data(req_h.create_request(_event(a, body=_payload(client_id=client_id)), None))
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT client_id FROM public.cases WHERE id=%s", (data["case_id"],))
        assert str(cur.fetchone()[0]) == client_id
    conn.close()


def test_request_client_id_inexistente_retorna_400():
    a = str(uuid.uuid4())
    resp = req_h.create_request(_event(a, body=_payload(client_id=str(uuid.uuid4()))), None)
    assert resp["statusCode"] == 400


def test_request_client_id_malformado_retorna_400():
    a = str(uuid.uuid4())
    resp = req_h.create_request(_event(a, body=_payload(client_id="nao-e-uuid")), None)
    assert resp["statusCode"] == 400
