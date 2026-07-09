"""Coração 5 — testes de leitura do detalhe do caso (PG18 + RLS).

Cria um pedido via wizard (que popula partes/timeline/triagem) e valida as abas:
partes (com PII MASCARADA), timeline, triagem, e o GET /cases/{id} enriquecido
(campos de produto + contagens + pricing). Cobre isolamento por org e 404.
"""
from _dbadmin import admin_conn
import json
import uuid

import psycopg2
import pytest

from src.handlers import case_parties as cp_h
from src.handlers import cases as cases_h
from src.handlers import reports as rep_h
from src.handlers import requests as req_h
from src.handlers import timeline as tl_h
from src.handlers import triage as tr_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


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


def _event(user_id, role="admin", path=None, org_id=SYSTEM_ORG, body=None):
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


def _wizard_payload():
    return {
        "product_type": "analise_contratual",
        "title": "Contrato de prestação",
        "parties": [
            {"name": "Empresa X LTDA", "role": "contratante", "person_type": "company",
             "document": "12345678000199", "document_type": "cnpj",
             "email": "contato@empresax.com", "phone": "11987654321"},
            {"name": "João Silva", "role": "contratado", "person_type": "individual",
             "document": "12345678909"},
        ],
        "document": {"filename": "contrato.pdf", "size_bytes": 1024},
        "selected_modules": ["ia_deepseek", "analise_contratual_ia"],
    }


@pytest.fixture()
def case_id():
    _reset()
    a = str(uuid.uuid4())
    data = _data(req_h.create_request(_event(a, body=_wizard_payload()), None))
    return data["case_id"]


def test_parties_com_pii_mascarada(case_id):
    a = str(uuid.uuid4())
    parties = _data(cp_h.list_case_parties(_event(a, path={"caseId": case_id}), None))
    assert len(parties) == 2
    empresa = next(p for p in parties if p["party_type"] == "contratante")
    # PII nunca crua; só mascarada
    assert empresa["document"] is None
    assert empresa["document_masked"] == "**.***.***/****-99"  # CNPJ
    assert empresa["email"] is None
    assert empresa["email_masked"] == "c******@empresax.com"
    assert empresa["phone_masked"] == "(11) ****-**21"
    # metadata não vaza PII
    assert "email" not in empresa["metadata"]
    assert "phone" not in empresa["metadata"]
    assert empresa["metadata"].get("person_type") == "company"
    pessoa = next(p for p in parties if p["party_type"] == "contratado")
    assert pessoa["document_masked"] == "***.***.***-09"  # CPF


def test_timeline_do_caso(case_id):
    a = str(uuid.uuid4())
    events = _data(tl_h.list_timeline(_event(a, path={"caseId": case_id}), None))
    types = {e["event_type"] for e in events}
    assert {"request_created", "case_created", "party_added",
            "document_attached", "triage_plan_created", "wizard_completed"} <= types
    assert len(events) == 7  # 2 system + 2 party_added + doc + plan + wizard


def test_triagem_do_caso(case_id):
    a = str(uuid.uuid4())
    mods = _data(tr_h.list_triage(_event(a, path={"caseId": case_id}), None))
    # plano executável de analise_contratual com ia_deepseek + analise_contratual_ia:
    # document_parser, ocr, contract_risk_analysis, obligations_mapping, ai_report
    assert len(mods) == 5
    assert all(m["status"] == "not_started" for m in mods)
    assert all(m["provider"].startswith(("mock_", "mock")) for m in mods)


def test_get_case_enriquecido(case_id):
    a = str(uuid.uuid4())
    case = _data(cases_h.get_case(_event(a, path={"caseId": case_id}), None))
    assert case["product_type"] == "analise_contratual"
    assert case["status"] == "awaiting_triage"
    assert case["code"].startswith("REQ-")
    assert case["parties_count"] == 2
    assert case["documents_count"] == 1
    assert case["timeline_count"] == 7
    assert case["triage_count"] == 5  # só módulos habilitados pela seleção
    assert case["pricing"]["total_price_cents"] == 10800


def test_isolamento_outra_org_404(case_id):
    b = str(uuid.uuid4())
    p = {"caseId": case_id}
    assert cp_h.list_case_parties(_event(b, path=p, org_id=OTHER_ORG), None)["statusCode"] == 404
    assert tl_h.list_timeline(_event(b, path=p, org_id=OTHER_ORG), None)["statusCode"] == 404
    assert tr_h.list_triage(_event(b, path=p, org_id=OTHER_ORG), None)["statusCode"] == 404
    assert cases_h.get_case(_event(b, path=p, org_id=OTHER_ORG), None)["statusCode"] == 404


def test_caso_inexistente_404(case_id):
    a = str(uuid.uuid4())
    p = {"caseId": str(uuid.uuid4())}
    assert cp_h.list_case_parties(_event(a, path=p), None)["statusCode"] == 404
    assert tl_h.list_timeline(_event(a, path=p), None)["statusCode"] == 404
    assert tr_h.list_triage(_event(a, path=p), None)["statusCode"] == 404


def test_viewer_pode_ler(case_id):
    v = str(uuid.uuid4())
    assert cp_h.list_case_parties(_event(v, role="viewer", path={"caseId": case_id}),
                                  None)["statusCode"] == 200


def test_get_case_aggregate(case_id):
    a = str(uuid.uuid4())
    agg = _data(cases_h.get_case_aggregate(_event(a, path={"caseId": case_id}), None))
    assert agg["case"]["product_type"] == "analise_contratual"
    assert agg["case"]["code"].startswith("REQ-")
    assert agg["case"]["status"] == "awaiting_triage"
    assert agg["request"] is not None and agg["request"]["code"].startswith("REQ-")
    assert len(agg["parties"]) == 2
    # PII nunca crua no agregado: o shape só expõe *_masked (sem campos crus)
    empresa = next(p for p in agg["parties"] if p["role"] == "contratante")
    assert "document" not in empresa and empresa["document_masked"] == "**.***.***/****-99"
    assert empresa["email"] is None and empresa["email_masked"] == "c******@empresax.com"
    assert len(agg["documents"]) == 1
    assert len(agg["timeline"]) == 7
    assert len(agg["triage_modules"]) == 5  # só módulos habilitados pela seleção
    assert agg["summary"]["parties_count"] == 2 and agg["summary"]["documents_count"] == 1
    assert agg["summary"]["timeline_count"] == 7
    assert agg["summary"]["triage_status"] in ("pending", "not_started")
    assert agg["report"] is None and agg["provider_results"] == []


def test_aggregate_isolamento_404(case_id):
    b = str(uuid.uuid4())
    assert cases_h.get_case_aggregate(
        _event(b, path={"caseId": case_id}, org_id=OTHER_ORG), None)["statusCode"] == 404


def test_criar_e_editar_parte(case_id):
    a = str(uuid.uuid4())
    created = _data(cp_h.create_case_party(_event(a, path={"caseId": case_id}, body={
        "party_type": "testemunha", "name": "Maria Souza", "document": "98765432100",
        "email": "maria@x.com", "phone": "11999998888", "person_type": "individual"}), None))
    assert created["party_type"] == "testemunha" and created["name"] == "Maria Souza"
    assert created["document"] is None and created["document_masked"] == "***.***.***-00"
    assert created["email"] is None and created["email_masked"] == "m****@x.com"
    pid = created["id"]
    # agora há 3 partes (2 do wizard + 1 criada)
    assert len(_data(cp_h.list_case_parties(_event(a, path={"caseId": case_id}), None))) == 3
    upd = _data(cp_h.update_case_party(
        _event(a, path={"caseId": case_id, "partyId": pid}, body={"name": "Maria S. Souza"}), None))
    assert upd["name"] == "Maria S. Souza"


def test_criar_parte_viewer_403(case_id):
    v = str(uuid.uuid4())
    assert cp_h.create_case_party(
        _event(v, role="viewer", path={"caseId": case_id},
               body={"party_type": "x", "name": "y"}), None)["statusCode"] == 403


def test_run_triage_executa_modulos_e_evidencias(case_id):
    a = str(uuid.uuid4())
    result = _data(tr_h.run_triage(_event(a, path={"caseId": case_id}), None))
    # 5 = só módulos habilitados pela seleção (ia_deepseek + analise_contratual_ia)
    assert result["modules_executed"] == 5
    assert result["risk_level"] in ("low", "medium", "high")
    # todos os módulos viraram 'done'
    mods = _data(tr_h.list_triage(_event(a, path={"caseId": case_id}), None))
    assert mods and all(m["status"] == "done" for m in mods)
    # evidências gravadas (provider_results + cache)
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.provider_results WHERE case_id=%s", (case_id,))
        assert cur.fetchone()[0] == 5
        cur.execute("SELECT count(*) FROM public.external_queries_cache WHERE case_id=%s", (case_id,))
        assert cur.fetchone()[0] == 5
    conn.close()
    # idempotente: re-executar não duplica provider_results
    _data(tr_h.run_triage(_event(a, path={"caseId": case_id}), None))
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.provider_results WHERE case_id=%s", (case_id,))
        assert cur.fetchone()[0] == 5
    conn.close()


def test_run_triage_viewer_403(case_id):
    v = str(uuid.uuid4())
    assert tr_h.run_triage(
        _event(v, role="viewer", path={"caseId": case_id}), None)["statusCode"] == 403


def test_gerar_revisar_relatorio_e_aggregate(case_id):
    a = str(uuid.uuid4())
    tr_h.run_triage(_event(a, path={"caseId": case_id}), None)  # gera evidências
    rep = _data(rep_h.generate_case_report(_event(a, path={"caseId": case_id}), None))
    assert rep["status"] == "ready" and rep["version"] == 1  # enum da referência
    assert rep["summary"]
    # recommendation é enum (não texto livre): casa com recommendationLabel do front
    assert rep["recommendation"] in (
        "proceed", "proceed_with_caution", "do_not_proceed", "human_review_required")
    assert len(rep["source_refs"]) == 5  # só módulos habilitados pela seleção
    # aparece no aggregate + summary.report_status
    agg = _data(cases_h.get_case_aggregate(_event(a, path={"caseId": case_id}), None))
    assert agg["report"] and agg["report"]["status"] == "ready"
    assert agg["summary"]["report_status"] == "ready"
    # regerar -> version 2 (idempotente por caso)
    assert _data(rep_h.generate_case_report(_event(a, path={"caseId": case_id}), None))["version"] == 2
    # revisão humana -> approved fecha o caso
    rev = _data(rep_h.review_case_report(
        _event(a, path={"caseId": case_id}, body={"status": "approved", "review_notes": "ok"}), None))
    assert rev["status"] == "approved" and rev["reviewed_by"]
    case = _data(cases_h.get_case(_event(a, path={"caseId": case_id}), None))
    assert case["status"] == "completed"
    # CVS-007: caso concluído não aceita nova triagem nem regeneração de relatório (409)
    assert tr_h.run_triage(_event(a, path={"caseId": case_id}), None)["statusCode"] == 409
    assert rep_h.generate_case_report(
        _event(a, path={"caseId": case_id}), None)["statusCode"] == 409


def test_get_report_antes_de_gerar_404(case_id):
    a = str(uuid.uuid4())
    assert rep_h.get_case_report(
        _event(a, path={"caseId": case_id}), None)["statusCode"] == 404


def test_gerar_relatorio_viewer_403(case_id):
    v = str(uuid.uuid4())
    assert rep_h.generate_case_report(
        _event(v, role="viewer", path={"caseId": case_id}), None)["statusCode"] == 403


def test_list_cases_busca_q(case_id):
    # #27: busca textual (?q=) por título ou código do caso
    a = str(uuid.uuid4())
    ev = _event(a)
    ev["queryStringParameters"] = {"q": "presta"}  # parte de "Contrato de prestação"
    page = _data(cases_h.list_cases(ev, None))
    assert any(c["id"] == case_id for c in page["items"])
    ev2 = _event(a)
    ev2["queryStringParameters"] = {"q": "zzznaoexiste"}
    assert _data(cases_h.list_cases(ev2, None))["items"] == []


def test_soft_delete_bloqueia_filhos_do_caso(case_id):
    # FE4-26-B1: ao arquivar o caso, TODOS os filhos ficam inacessíveis (404), leitura e escrita
    a = str(uuid.uuid4())
    assert cases_h.delete_case(_event(a, path={"caseId": case_id}), None)["statusCode"] == 200
    p = {"caseId": case_id}
    # leituras
    assert cases_h.get_case(_event(a, path=p), None)["statusCode"] == 404
    assert cases_h.get_case_aggregate(_event(a, path=p), None)["statusCode"] == 404
    assert cp_h.list_case_parties(_event(a, path=p), None)["statusCode"] == 404
    assert tl_h.list_timeline(_event(a, path=p), None)["statusCode"] == 404
    assert tr_h.list_triage(_event(a, path=p), None)["statusCode"] == 404
    assert rep_h.get_case_report(_event(a, path=p), None)["statusCode"] == 404
    # escritas
    assert cp_h.create_case_party(
        _event(a, path=p, body={"party_type": "testemunha", "name": "X"}), None)["statusCode"] == 404
    assert tr_h.run_triage(_event(a, path=p), None)["statusCode"] == 404
    assert rep_h.generate_case_report(_event(a, path=p), None)["statusCode"] == 404


def test_cvs007_caso_finalizado_bloqueia_escrita(case_id):
    # CVS-007: caso completed/closed nao aceita escrita de partes/resultados
    a = str(uuid.uuid4())
    p = {"caseId": case_id}
    # finaliza o caso
    assert cases_h.update_case(
        _event(a, path=p, body={"status": "completed"}), None)["statusCode"] == 200
    # criar parte em caso finalizado -> 409
    assert cp_h.create_case_party(
        _event(a, path=p, body={"party_type": "testemunha", "name": "X"}), None)["statusCode"] == 409
    # revisar relatorio de caso finalizado -> 409
    assert rep_h.review_case_report(
        _event(a, path=p, body={"status": "approved"}), None)["statusCode"] == 409


def test_classify_risk_vocabulario_explicito():
    # Varredura-qualidade (BAIXO): a classificação de risco usa vocabulário explícito,
    # não substring de rótulo — o ramo 'high' passa a ser alcançável de forma determinística.
    from src.services.triage_runner import classify_risk
    assert classify_risk([]) == "low"
    assert classify_risk(["clausula_revisar"]) == "medium"
    assert classify_risk(["score_saudavel", "litigio_baixo"]) == "medium"
    assert classify_risk(["litigio_alto"]) == "high"
    assert classify_risk(["clausula_revisar", "score_baixo"]) == "high"


def test_review_valida_recommendation_e_sincroniza_cases(case_id):
    # Varredura-qualidade #4 (MEDIO): recommendation na revisão deve ser validada contra o
    # enum (texto livre -> 400) e sincronizada em cases.recommendation (senão o agregado
    # divergiria: case_reports atualizado, cases defasado).
    a = str(uuid.uuid4())
    tr_h.run_triage(_event(a, path={"caseId": case_id}), None)
    rep_h.generate_case_report(_event(a, path={"caseId": case_id}), None)
    # texto livre é rejeitado
    assert rep_h.review_case_report(_event(a, path={"caseId": case_id},
        body={"status": "reviewed", "recommendation": "qualquer coisa"}), None)["statusCode"] == 400
    # código válido é aceito e sincroniza cases.recommendation
    rev = _data(rep_h.review_case_report(_event(a, path={"caseId": case_id},
        body={"status": "reviewed", "recommendation": "do_not_proceed"}), None))
    assert rev["recommendation"] == "do_not_proceed"
    conn = _admin_conn(); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT recommendation FROM public.cases WHERE id=%s", (case_id,))
        assert cur.fetchone()[0] == "do_not_proceed"
    conn.close()


def test_report_generate_exige_triagem_409(monkeypatch):
    # Regressão pentest #3: gerar parecer sem triagem executada -> 409.
    monkeypatch.setenv("PAYMENT_GATE", "soft")
    _reset()
    a = str(uuid.uuid4())
    case_id = _data(req_h.create_request(_event(a, body=_wizard_payload()), None))["case_id"]
    resp = rep_h.generate_case_report(_event(a, path={"caseId": case_id}), None)
    assert resp["statusCode"] == 409


def test_update_case_assigned_to_invalido_400():
    # Regressão pentest #6: assigned_to deve ser usuário ativo da mesma org -> UUID qualquer = 400.
    _reset()
    a = str(uuid.uuid4())
    case_id = _data(req_h.create_request(_event(a, body=_wizard_payload()), None))["case_id"]
    resp = cases_h.update_case(_event(a, path={"caseId": case_id}, body={"assigned_to": str(uuid.uuid4())}), None)
    assert resp["statusCode"] == 400


def test_create_party_tipo_errado_400_nao_500():
    # Regressão pentest #8: campos com tipo errado -> 400 (nunca 500).
    _reset()
    a = str(uuid.uuid4())
    case_id = _data(req_h.create_request(_event(a, body=_wizard_payload()), None))["case_id"]
    resp = cp_h.create_case_party(_event(a, path={"caseId": case_id}, body={"name": 123, "party_type": "reu"}), None)
    assert resp["statusCode"] == 400
