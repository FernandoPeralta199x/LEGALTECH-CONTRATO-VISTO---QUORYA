"""Fase 4 — testes de integração dos handlers de documents (PG18 + RLS).

`public.documents` tem RLS por `uploaded_by`. Upload exige case visível + papel
writer; download/get filtrado pela RLS (dono ou admin). S3 via backend `local`.
"""
from _dbadmin import admin_conn
import json
import uuid

import psycopg2
import pytest

from src.handlers import cases as cases_h
from src.handlers import documents as d

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _admin_conn():
    return admin_conn()


def _seed_writer(user_id, role, org=SYSTEM_ORG):
    """Semeia o user do token no banco com o papel dado. As rotas de escrita reconsultam
    o papel ATUAL (assert_active_writer, SEC-01), então um user_id não-semeado seria 403.
    DO UPDATE do papel/status: um mesmo user_id é reutilizado com papéis diferentes no
    mesmo teste (ex.: viewer e depois analyst), então o seed reflete o papel do token
    atual — o gate de papel (@require_writer) barra o viewer pelo claim do token."""
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s,%s,'x','Test',%s,'active',%s)"
            " ON CONFLICT (id) DO UPDATE SET role=EXCLUDED.role, status='active',"
            " organization_id=EXCLUDED.organization_id",
            (user_id, f"u_{user_id}@t.c", role, org))
    conn.close()


@pytest.fixture()
def client_id():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.documents, public.cases, public.clients"
                    " RESTART IDENTITY CASCADE")
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


def _event(user_id, role="analyst", body=None, path=None, org_id=SYSTEM_ORG):
    # O user do token existe no banco com o papel do teste (senão o recheck de escrita
    # do SEC-01 recusaria antes de exercitar o comportamento sob teste).
    _seed_writer(user_id, role, org_id)
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c", "role": role, "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": {},
    }


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"])["data"]


def _make_case(user_id, client_id, role="analyst", org_id=SYSTEM_ORG):
    resp = cases_h.create_case(
        _event(user_id, role, body={"client_id": client_id, "case_type": "contract_analysis"},
               org_id=org_id),
        None,
    )
    return _data(resp)["id"]


def _upload(user_id, case_id, role="analyst", org_id=SYSTEM_ORG, **over):
    body = {"case_id": case_id, "file_name": "contrato.pdf", "file_type": "pdf", **over}
    return d.upload_document(_event(user_id, role, body=body, org_id=org_id), None)


def test_process_document_ingestao_e_idempotencia(client_id):
    # SP2: OCR -> chunking -> embeddings, idempotente (limpa-e-regera)
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    doc_id = _data(_upload(a, case_id, file_size_bytes=1024))["document_id"]

    out = _data(d.process_document(_event(a, path={"docId": doc_id}), None))
    assert out["status"] == "done" and out["chunks"] >= 1
    assert out["embeddings"] == out["chunks"]

    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.document_chunks WHERE document_id=%s", (doc_id,))
        n_chunks = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM public.document_embeddings WHERE document_id=%s", (doc_id,))
        n_emb = cur.fetchone()[0]
        cur.execute("SELECT ocr_status, extraction_status FROM public.documents WHERE id=%s", (doc_id,))
        st = cur.fetchone()
    conn.close()
    assert n_chunks == out["chunks"] and n_emb == out["chunks"]
    assert st == ("done", "done")

    # reprocessar NÃO duplica (idempotente)
    out2 = _data(d.process_document(_event(a, path={"docId": doc_id}), None))
    assert out2["chunks"] == out["chunks"]
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.document_chunks WHERE document_id=%s", (doc_id,))
        assert cur.fetchone()[0] == n_chunks
        cur.execute("SELECT count(*) FROM public.document_embeddings WHERE document_id=%s", (doc_id,))
        assert cur.fetchone()[0] == n_emb
    conn.close()


def test_process_document_404(client_id):
    a = str(uuid.uuid4())
    assert d.process_document(
        _event(a, path={"docId": str(uuid.uuid4())}), None)["statusCode"] == 404


def test_viewer_nao_processa(client_id):
    v = str(uuid.uuid4())
    assert d.process_document(
        _event(v, role="viewer", path={"docId": str(uuid.uuid4())}), None)["statusCode"] == 403


def test_upload_and_get(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    up = _upload(a, case_id, file_size_bytes=1024)
    assert up["statusCode"] == 201
    out = _data(up)
    assert out["upload_url"].startswith("https://") and out["s3_path"].startswith("cases/")
    doc_id = out["document_id"]

    got = _data(d.get_document(_event(a, path={"docId": doc_id}), None))
    # get_document agora retorna o shape V2 (filename/status) esperado pelo frontend
    assert got["filename"] == "contrato.pdf" and got["status"] == "pending_upload"
    assert got["download_url"].startswith("https://")
    # download-url dedicado
    du = _data(d.get_document_download_url(_event(a, path={"docId": doc_id}), None))
    assert du["url"].startswith("https://") and du["method"] == "GET"


def test_get_document_viewer_sem_download_url(client_id):
    """be-upload-01: viewer vê a metadata do documento, mas NÃO a URL crua de download
    (só writer/admin/analyst). Fecha o segundo caminho que driblava o guard
    writer-only do endpoint /download-url (achado do pentest 2026-07-09)."""
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    doc_id = _data(_upload(a, case_id, file_size_bytes=1024))["document_id"]
    v = str(uuid.uuid4())  # viewer da MESMA org (SYSTEM_ORG) — enxerga via RLS
    got = _data(d.get_document(_event(v, role="viewer", path={"docId": doc_id}), None))
    assert got["filename"] == "contrato.pdf" and got["status"] == "pending_upload"
    assert got["download_url"] is None and got.get("expires_in") is None
    # o endpoint dedicado permanece writer-only (403 p/ viewer)
    assert d.get_document_download_url(
        _event(v, role="viewer", path={"docId": doc_id}), None)["statusCode"] == 403


def test_upload_file_name_reduz_a_basename(client_id):
    """be-upload-03: file_name é saneado para um basename (sem componentes de caminho)
    antes de persistir — defesa em profundidade p/ Content-Disposition/exibição."""
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    doc_id = _data(_upload(a, case_id, file_name="../../etc/relatorio final.pdf"))["document_id"]
    got = _data(d.get_document(_event(a, path={"docId": doc_id}), None))
    assert got["filename"] == "relatorio final.pdf"


def test_upload_requires_visible_case(client_id):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    # B de OUTRA organização não enxerga o case de A (RLS por org) → upload bloqueado
    assert _upload(b, case_id, org_id=OTHER_ORG)["statusCode"] == 404


def test_upload_nonexistent_case_404(client_id):
    a = str(uuid.uuid4())
    assert _upload(a, str(uuid.uuid4()))["statusCode"] == 404


def test_viewer_cannot_upload(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    assert _upload(a, case_id, role="viewer")["statusCode"] == 403


def test_document_isolation_by_org(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    doc_id = _data(_upload(a, case_id))["document_id"]
    # usuário de OUTRA organização não vê o documento (RLS por org) → 404
    assert d.get_document(
        _event(str(uuid.uuid4()), path={"docId": doc_id}, org_id=OTHER_ORG), None)["statusCode"] == 404
    # admin da MESMA organização vê
    assert d.get_document(
        _event(str(uuid.uuid4()), role="admin", path={"docId": doc_id}), None)["statusCode"] == 200


def test_invalid_file_type_400(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    assert _upload(a, case_id, file_type="exe")["statusCode"] == 400


def test_upload_blocked_on_finalized_case(client_id):
    # B4: case finalizado (completed) não aceita novos documentos
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    cases_h.update_case(_event(a, path={"caseId": case_id}, body={"status": "completed"}), None)
    assert _upload(a, case_id)["statusCode"] == 409


def test_unauthenticated_blocked(client_id):
    assert d.get_document({"requestContext": {}}, None)["statusCode"] == 401


# ── #26: PATCH /documents/{id} (rename + status) ─────────────────────────────
def test_update_document_patch(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    doc_id = _data(_upload(a, case_id))["document_id"]
    upd = _data(d.update_document(_event(a, path={"docId": doc_id},
                body={"filename": "renomeado.pdf", "status": "processed"}), None))
    assert upd["filename"] == "renomeado.pdf"
    assert upd["status"] == "processed"  # V2; persistido como ocr_status='done'
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT file_name, ocr_status FROM public.documents WHERE id=%s", (doc_id,))
        assert cur.fetchone() == ("renomeado.pdf", "done")
    conn.close()


def test_update_document_sanitiza_filename_crlf(client_id):
    # BE-01: rename com CR/LF (e componente de caminho) é sanitizado antes de
    # persistir — senão o nome reapareceria cru em Content-Disposition e permitiria
    # header splitting. O nome guardado não pode conter CR/LF nem "/".
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    doc_id = _data(_upload(a, case_id))["document_id"]
    upd = _data(d.update_document(_event(a, path={"docId": doc_id},
                body={"filename": "../evil\r\nX-Injected: 1.pdf"}), None))
    assert upd["filename"] == "evilX-Injected: 1.pdf"
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT file_name FROM public.documents WHERE id=%s", (doc_id,))
        stored = cur.fetchone()[0]
    conn.close()
    assert "\r" not in stored and "\n" not in stored and "/" not in stored


def test_update_document_sem_campos_400(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    doc_id = _data(_upload(a, case_id))["document_id"]
    assert d.update_document(_event(a, path={"docId": doc_id}, body={}), None)["statusCode"] == 400


def test_update_document_viewer_403(client_id):
    v = str(uuid.uuid4())
    assert d.update_document(_event(v, role="viewer", path={"docId": str(uuid.uuid4())},
                body={"filename": "x.pdf"}), None)["statusCode"] == 403


# ── #26: filtro ?status= em list_documents (V2 -> ocr_status) ─────────────────
def test_list_documents_filtra_status(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    doc1 = _data(_upload(a, case_id, file_name="a.pdf"))["document_id"]
    doc2 = _data(_upload(a, case_id, file_name="b.pdf"))["document_id"]
    d.process_document(_event(a, path={"docId": doc1}), None)  # doc1 -> ocr_status='done'
    ev = _event(a)
    ev["queryStringParameters"] = {"status": "processed"}
    # C3-04: /documents agora devolve envelope paginado {items,total,...}.
    ids = {x["id"] for x in _data(d.list_documents(ev, None))["items"]}
    assert doc1 in ids and doc2 not in ids
    ev2 = _event(a)
    ev2["queryStringParameters"] = {"status": "pending_upload"}
    ids2 = {x["id"] for x in _data(d.list_documents(ev2, None))["items"]}
    assert doc2 in ids2 and doc1 not in ids2


def test_list_documents_paginado(client_id):
    """C3-04: /documents devolve envelope {items,total,page,...} e respeita page_size,
    em vez de truncar em LIMIT 500 fixo e devolver um array cru."""
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    for i in range(3):
        _data(_upload(a, case_id, file_name=f"doc{i}.pdf"))
    ev = _event(a)
    ev["queryStringParameters"] = {"case_id": case_id, "page": "1", "page_size": "2"}
    r1 = _data(d.list_documents(ev, None))
    assert r1["total"] == 3 and r1["page_size"] == 2 and r1["total_pages"] == 2
    assert len(r1["items"]) == 2  # respeitou o LIMIT
    ev["queryStringParameters"]["page"] = "2"
    r2 = _data(d.list_documents(ev, None))
    assert len(r2["items"]) == 1  # a 3ª


# ── Varredura-qualidade #2: filtro ?classification= (separa relatórios finais) ──
def test_list_documents_filtra_por_classification(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    rel = _data(_upload(a, case_id, file_name="relatorio.pdf",
                        document_classification="final_report"))["document_id"]
    _data(_upload(a, case_id, file_name="contrato.pdf"))  # upload comum, sem classificação
    # com o filtro: só o relatório final, e o shape V2 expõe metadata.kind
    ev = _event(a)
    ev["queryStringParameters"] = {"case_id": case_id, "classification": "final_report"}
    docs = _data(d.list_documents(ev, None))["items"]  # C3-04: envelope paginado
    assert len(docs) == 1
    assert docs[0]["id"] == rel
    assert docs[0]["metadata"] == {"kind": "final_report"}
    # sem o filtro: ambos aparecem (era o que inflava o contador no frontend)
    ev_all = _event(a)
    ev_all["queryStringParameters"] = {"case_id": case_id}
    assert _data(d.list_documents(ev_all, None))["total"] == 2


# ── #26/Codex B3: status fora do conjunto mapeável é erro de contrato (400) ──
def test_update_document_status_invalido_400(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    doc_id = _data(_upload(a, case_id))["document_id"]
    assert d.update_document(_event(a, path={"docId": doc_id},
            body={"status": "available"}), None)["statusCode"] == 400


def test_list_documents_status_invalido_400(client_id):
    a = str(uuid.uuid4())
    ev = _event(a)
    ev["queryStringParameters"] = {"status": "available"}
    assert d.list_documents(ev, None)["statusCode"] == 400


# ── #26/Codex B1: documento de caso arquivado fica inacessível ───────────────
def test_documento_de_caso_arquivado_inacessivel(client_id):
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    doc_id = _data(_upload(a, case_id))["document_id"]
    assert cases_h.delete_case(_event(a, path={"caseId": case_id}), None)["statusCode"] == 200
    assert d.get_document(_event(a, path={"docId": doc_id}), None)["statusCode"] == 404
    assert d.get_document_download_url(_event(a, path={"docId": doc_id}), None)["statusCode"] == 404
    assert d.update_document(_event(a, path={"docId": doc_id},
            body={"filename": "x.pdf"}), None)["statusCode"] == 404
    assert d.process_document(_event(a, path={"docId": doc_id}), None)["statusCode"] == 404
    assert doc_id not in {x["id"] for x in _data(d.list_documents(_event(a), None))["items"]}


def test_upload_bloqueado_em_caso_arquivado(client_id):
    # Varredura-qualidade #1 (ALTO): o guard de escrita deve incluir deleted_at IS NULL,
    # senão um caso arquivado (soft-delete, status não-terminal) aceita upload e cria
    # documento órfão (invisível em todos os reads). Deve retornar 404 (caso não visível).
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    assert cases_h.delete_case(_event(a, path={"caseId": case_id}), None)["statusCode"] == 200
    assert _upload(a, case_id)["statusCode"] == 404


def test_aggregate_normaliza_status_done_para_processed(client_id):
    # Varredura-qualidade #2 (MEDIO): o agregado do caso deve mapear ocr_status 'done' ->
    # 'processed' (igual à lista), não expor 'done' cru (que renderiza badge sem rótulo).
    a = str(uuid.uuid4())
    case_id = _make_case(a, client_id)
    doc_id = _data(_upload(a, case_id, file_size_bytes=1024))["document_id"]
    d.process_document(_event(a, path={"docId": doc_id}), None)  # ocr_status -> 'done'
    agg = _data(cases_h.get_case_aggregate(_event(a, path={"caseId": case_id}), None))
    doc = next(x for x in agg["documents"] if x["id"] == doc_id)
    assert doc["status"] == "processed"


def test_download_url_viewer_403():
    # Regressão pentest #5: download-url é writer-only; viewer -> 403 (não expõe doc bruto/PII).
    a = str(uuid.uuid4())
    resp = d.get_document_download_url(_event(a, role="viewer", path={"docId": str(uuid.uuid4())}), None)
    assert resp["statusCode"] == 403
    # writer (analyst) passa do gate de papel (doc inexistente -> 404, não 403)
    resp2 = d.get_document_download_url(_event(a, role="analyst", path={"docId": str(uuid.uuid4())}), None)
    assert resp2["statusCode"] != 403
