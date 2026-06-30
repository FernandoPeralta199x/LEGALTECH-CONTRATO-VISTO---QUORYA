"""Handlers Lambda de `cases` (migração FastAPI→Serverless, Fase 2).

Protegidos pelo JWT Authorizer (via @require_user) e isolados por RLS via
tenant_tx (app.user_id/app.user_role). Alinhados ao schema real: `cases` NÃO tem
`updated_at`; `created_by` é gravado para a policy de RLS. Erros internos não
vazam ao cliente.
"""
import json
import logging

from psycopg2.extras import Json
from pydantic import ValidationError

from src.schemas.case_schemas import CaseCreate, CaseUpdate
from src.services.database import tenant_tx
from src.utils.context import require_user, require_writer
from src.services.report_generator import get_report as _get_report
from src.utils.pii import mask_document, mask_email, mask_phone
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import fmt_validation_error as _fmt, parse_json_body as _parse_body, parse_pagination as _paginate, valid_uuid as _valid_uuid
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


class _ClientNotFound(Exception):
    """O client_id informado não existe."""


class _ClientInactive(Exception):
    """O client_id existe mas está inativo (não aceita novos casos)."""


@require_user
@require_writer
def create_case(event, context):
    user = event["user"]
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = CaseCreate(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    client_id = _valid_uuid(data.client_id)
    if not client_id:
        return error_response(400, "client_id inválido")
    metadata = {"description": data.description} if data.description else None

    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            # Cliente deve existir (evita FK→500) e estar ATIVO (não criar caso
            # para cliente desativado).
            cur.execute("SELECT status FROM public.clients WHERE id = %s", (client_id,))
            crow = cur.fetchone()
            if crow is None:
                raise _ClientNotFound()
            if crow["status"] != "active":
                raise _ClientInactive()
            cur.execute(
                "INSERT INTO public.cases"
                " (organization_id, client_id, case_type, priority, created_by, metadata)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " RETURNING id, client_id, case_type, status, priority, product_type,"
                " product_label, title, code, risk_level, progress, source_mode,"
                " request_id, created_by, assigned_to, created_at, completed_at,"
                " metadata, submitted_at",
                (user["organization_id"], client_id, data.case_type, data.priority,
                 user["user_id"], Json(metadata) if metadata else None),
            )
            row = cur.fetchone()
    except _ClientNotFound:
        return error_response(400, "client_id inexistente")
    except _ClientInactive:
        return error_response(409, "cliente inativo não aceita novos casos")
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_CREATE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao criar caso")

    logger.info(json.dumps({
        "event": "CASE_CREATED", "case_id": str(row["id"]),
        "created_by": user["user_id"],
    }))
    return success_response(201, "Caso criado com sucesso", _serialize(row))


@require_user
def get_case(event, context):
    user = event["user"]
    case_id = _valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(
                "SELECT id, client_id, case_type, status, priority, product_type,"
                " product_label, title, code, risk_level, progress, source_mode,"
                " request_id, created_by, assigned_to, created_at, completed_at,"
                " metadata, submitted_at"
                " FROM public.cases WHERE id = %s AND deleted_at IS NULL",
                (case_id,),
            )
            row = cur.fetchone()
            if not row:
                return error_response(404, "Caso não encontrado")
            detail = _case_detail(cur, case_id, row.get("request_id"))
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_GET_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao obter caso")
    return success_response(200, "Caso encontrado", {**_serialize(row), **detail})


_PII_KEYS = {"email", "phone", "document", "document_number", "cpf", "cnpj", "rg"}


def _mime(file_type) -> str:
    return {
        "pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "tiff": "image/tiff", "txt": "text/plain",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get((file_type or "").lower(), "application/octet-stream")


def _triage_status(modules) -> str:
    if not modules:
        return "not_started"
    statuses = {m["status"] for m in modules}
    if statuses <= {"done"}:
        return "completed"
    if {"running", "in_progress"} & statuses:
        return "running"
    return "pending"


@require_user
def get_case_aggregate(event, context):
    """Detalhe agregado do caso (shape BackendCaseAggregate) — uma única chamada
    para a tela de detalhe: case + request + partes (PII mascarada) + documentos +
    timeline + módulos de triagem + provider_results + summary.
    """
    user = event["user"]
    org = user["organization_id"]
    case_id = _valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute(
                "SELECT id, request_id, code, created_by, product_type, product_label,"
                " title, description, status, progress, risk_level, recommendation,"
                " source_mode, is_local_simulation, created_at"
                " FROM public.cases WHERE id = %s AND deleted_at IS NULL", (case_id,))
            c = cur.fetchone()
            if not c:
                return error_response(404, "Caso não encontrado")

            request_obj = None
            if c["request_id"]:
                cur.execute(
                    "SELECT id, code, created_by, product_type, product_label, title,"
                    " description, status, source_mode, idempotency_key, created_at"
                    " FROM public.requests WHERE id = %s", (c["request_id"],))
                r = cur.fetchone()
                if r:
                    rc = str(r["created_at"]) if r["created_at"] else None
                    request_obj = {
                        "id": str(r["id"]), "code": r["code"], "organization_id": str(org),
                        "created_by": str(r["created_by"]) if r["created_by"] else "",
                        "product_type": r["product_type"], "product_label": r["product_label"],
                        "title": r["title"], "description": r["description"] or "",
                        "status": r["status"], "source_mode": r["source_mode"],
                        "idempotency_key": r["idempotency_key"],
                        "created_at": rc, "updated_at": rc,
                    }

            cur.execute(
                "SELECT id, case_id, party_type, name, document, metadata, created_at, updated_at"
                " FROM public.case_parties WHERE case_id = %s AND deleted_at IS NULL"
                " ORDER BY created_at", (case_id,))
            parties = []
            for p in cur.fetchall():
                md = dict(p["metadata"] or {})
                raw_email = md.get("email") if isinstance(md.get("email"), str) else None
                raw_phone = md.get("phone") if isinstance(md.get("phone"), str) else None
                parties.append({
                    "id": str(p["id"]), "case_id": str(p["case_id"]), "organization_id": str(org),
                    "name": p["name"], "document_masked": mask_document(p["document"]),
                    "document_type": md.get("document_type") or "",
                    "person_type": md.get("person_type") or "individual",
                    "role": p["party_type"],
                    "email": None, "email_masked": mask_email(raw_email) if raw_email else None,
                    "phone": None, "phone_masked": mask_phone(raw_phone) if raw_phone else None,
                    "status": "pending", "risk_level": "unknown",
                    "provider_status_summary": None,
                    "metadata": {k: v for k, v in md.items() if k not in _PII_KEYS},
                    "created_at": str(p["created_at"]), "updated_at": str(p["updated_at"]),
                })

            cur.execute(
                "SELECT id, case_id, file_name, file_type, file_size_bytes, s3_path,"
                " ocr_status, extraction_status, created_at FROM public.documents"
                " WHERE case_id = %s ORDER BY created_at", (case_id,))
            documents = []
            for d in cur.fetchall():
                created = str(d["created_at"]) if d["created_at"] else None
                documents.append({
                    "id": str(d["id"]), "case_id": str(d["case_id"]), "organization_id": str(org),
                    "filename": d["file_name"], "original_filename": d["file_name"],
                    "mime_type": _mime(d.get("file_type")),
                    "size_bytes": d.get("file_size_bytes") or 0,
                    "storage_provider": "s3", "storage_key": d.get("s3_path") or "",
                    "status": d.get("ocr_status") or "pending_upload",
                    "ocr_status": d.get("ocr_status") or "not_started",
                    "ai_read_status": d.get("extraction_status") or "not_started",
                    "preview_available": False, "download_available": bool(d.get("s3_path")),
                    "uploaded_at": created, "updated_at": created,
                })

            cur.execute(
                "SELECT id, case_id, event_type, title, description, actor, payload, created_at"
                " FROM public.timeline_events WHERE case_id = %s"
                " ORDER BY created_at DESC, id DESC", (case_id,))
            timeline = []
            latest_event_at = None
            for ev in cur.fetchall():
                created = str(ev["created_at"]) if ev["created_at"] else None
                if latest_event_at is None:
                    latest_event_at = created
                timeline.append({
                    "id": str(ev["id"]), "case_id": str(ev["case_id"]), "organization_id": str(org),
                    "type": ev["event_type"], "title": ev["title"],
                    "description": ev["description"] or "", "severity": "info",
                    "source": ev["actor"], "source_mode": "local",
                    "metadata": ev["payload"] or {}, "created_at": created,
                })

            cur.execute(
                "SELECT id, case_id, module_key, module_label, provider, status, source_mode,"
                " required, reason, started_at, finished_at, attempts, error_code,"
                " error_message, summary, result_ref, raw_result_ref, created_at, updated_at"
                " FROM public.triage_modules WHERE case_id = %s ORDER BY created_at, id", (case_id,))
            triage = []
            for t in cur.fetchall():
                triage.append({
                    "id": str(t["id"]), "case_id": str(t["case_id"]), "organization_id": str(org),
                    "module_key": t["module_key"], "module_label": t["module_label"],
                    "provider": t["provider"], "status": t["status"],
                    "source_mode": t["source_mode"], "required": t["required"],
                    "reason": t["reason"],
                    "started_at": str(t["started_at"]) if t["started_at"] else None,
                    "finished_at": str(t["finished_at"]) if t["finished_at"] else None,
                    "attempts": t["attempts"], "error_code": t["error_code"],
                    "error_message": t["error_message"], "summary": t["summary"],
                    "result_ref": t["result_ref"], "raw_result_ref": t["raw_result_ref"],
                    "created_at": str(t["created_at"]), "updated_at": str(t["updated_at"]),
                })

            report = _get_report(cur, org, case_id)
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_AGGREGATE_ERROR", "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao obter o detalhe do caso")

    created = str(c["created_at"]) if c["created_at"] else None
    case_obj = {
        "id": str(c["id"]), "request_id": str(c["request_id"]) if c["request_id"] else None,
        "code": c["code"] or "", "organization_id": str(org),
        "created_by": str(c["created_by"]) if c["created_by"] else "",
        "product_type": c["product_type"] or "", "product_label": c["product_label"] or "",
        "title": c["title"] or "", "description": c["description"] or "",
        "status": c["status"], "progress": c["progress"] or 0,
        "risk_level": c["risk_level"] or "unknown", "recommendation": c["recommendation"],
        "source_mode": c["source_mode"] or "local",
        "is_local_simulation": bool(c["is_local_simulation"]),
        "created_at": created, "updated_at": created,
    }
    summary = {
        "case_id": str(c["id"]), "organization_id": str(org),
        "parties_count": len(parties), "documents_count": len(documents),
        "timeline_count": len(timeline), "triage_status": _triage_status(triage),
        "report_status": report["status"] if report else "not_started",
        "risk_level": c["risk_level"] or "unknown",
        "recommendation": c["recommendation"], "progress": c["progress"] or 0,
        "latest_event_at": latest_event_at, "source_mode": c["source_mode"] or "local",
        "updated_at": created,
    }
    return success_response(200, "Detalhe do caso", {
        "case": case_obj, "request": request_obj, "parties": parties,
        "documents": documents, "timeline": timeline, "triage_modules": triage,
        "provider_results": [], "report": report, "summary": summary,
    })


@require_user
def list_cases(event, context):
    user = event["user"]
    params = event.get("queryStringParameters") or {}
    pag, perr = _paginate(params)
    if perr:
        return perr
    page, page_size, offset = pag
    # busca textual opcional (?q=) por título ou código do caso (header global)
    q = (params.get("q") or "").strip()
    where, fargs = "WHERE deleted_at IS NULL", []
    if q:
        where += " AND (title ILIKE %s OR code ILIKE %s)"
        like = f"%{q}%"
        fargs = [like, like]
    try:
        # A RLS já filtra os casos visíveis ao usuário (não filtramos por mão).
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(f"SELECT count(*) AS n FROM public.cases {where}", tuple(fargs))
            total = cur.fetchone()["n"]
            cur.execute(
                "SELECT id, client_id, case_type, status, priority, product_type,"
                " product_label, title, code, risk_level, progress, source_mode,"
                " request_id, created_by, assigned_to, created_at, completed_at,"
                " metadata, submitted_at"
                f" FROM public.cases {where}"
                " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                tuple(fargs + [page_size, offset]),
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_LIST_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao listar casos")
    total_pages = (total + page_size - 1) // page_size if page_size else 1
    # Página operacional (shape BackendCaseListPage) -> frontend usa mapOperationalCase,
    # tolerante a client_id/metadata nulos dos casos do wizard.
    return success_response(200, f"{total} casos encontrados", {
        "items": [_serialize(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    })


@require_user
@require_writer
def update_case(event, context):
    user = event["user"]
    case_id = _valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = CaseUpdate(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    fields, values = [], []
    if data.status is not None:
        fields.append("status = %s")
        values.append(data.status)
        # completed_at coerente com o status (literal SQL; status é validado por pattern)
        fields.append("completed_at = " + ("NOW()" if data.status == "completed" else "NULL"))
    if data.priority is not None:
        fields.append("priority = %s")
        values.append(data.priority)
    if data.assigned_to is not None:
        assigned = _valid_uuid(data.assigned_to)
        if not assigned:
            return error_response(400, "assigned_to inválido")
        fields.append("assigned_to = %s")
        values.append(assigned)
    if not fields:
        return error_response(400, "Nenhum campo para atualizar")
    values.append(case_id)

    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(
                f"UPDATE public.cases SET {', '.join(fields)} WHERE id = %s",
                tuple(values),
            )
            if not cur.rowcount:
                return error_response(404, "Caso não encontrado")
            cur.execute(
                "SELECT id, client_id, case_type, status, priority, product_type,"
                " product_label, title, code, risk_level, progress, source_mode,"
                " request_id, created_by, assigned_to, created_at, completed_at,"
                " metadata, submitted_at FROM public.cases WHERE id = %s", (case_id,))
            row = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_UPDATE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao atualizar caso")
    logger.info(json.dumps({"event": "CASE_UPDATED", "case_id": case_id}))
    return success_response(200, "Caso atualizado com sucesso", _serialize(row))


@require_user
@require_writer
def delete_case(event, context):
    user = event["user"]
    case_id = _valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            # soft-delete: a UI promete arquivar (recuperável), não apaga de fato
            cur.execute("UPDATE public.cases SET deleted_at = now()"
                        " WHERE id = %s AND deleted_at IS NULL RETURNING deleted_at", (case_id,))
            row = cur.fetchone()
            deleted = cur.rowcount
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_DELETE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao deletar caso")
    if not deleted:
        return error_response(404, "Caso não encontrado")
    logger.info(json.dumps({"event": "CASE_SOFT_DELETED", "case_id": case_id}))
    return success_response(200, "Caso arquivado com sucesso", {
        "id": case_id, "deleted_at": str(row["deleted_at"]) if row else None})


def _case_detail(cur, case_id, request_id) -> dict:
    """Contagens das abas + pricing do pedido vinculado (para a tela de detalhe)."""
    cur.execute("SELECT count(*) AS n FROM public.case_parties"
                " WHERE case_id = %s AND deleted_at IS NULL", (case_id,))
    parties = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM public.documents WHERE case_id = %s", (case_id,))
    documents = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM public.timeline_events WHERE case_id = %s", (case_id,))
    timeline = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM public.triage_modules WHERE case_id = %s", (case_id,))
    triage = cur.fetchone()["n"]
    pricing = None
    if request_id:
        cur.execute("SELECT total_price_cents, price_snapshot FROM public.requests"
                    " WHERE id = %s", (request_id,))
        prow = cur.fetchone()
        if prow:
            pricing = {"total_price_cents": prow["total_price_cents"],
                       "snapshot": prow["price_snapshot"]}
    return {
        "parties_count": parties,
        "documents_count": documents,
        "timeline_count": timeline,
        "triage_count": triage,
        "pricing": pricing,
    }


def _serialize(row) -> dict:
    return {
        "id": str(row["id"]),
        "client_id": str(row["client_id"]) if row["client_id"] else None,
        "case_type": row["case_type"],
        "status": row["status"],
        "priority": row["priority"],
        "product_type": row.get("product_type"),
        "product_label": row.get("product_label"),
        "title": row.get("title"),
        "code": row.get("code"),
        "risk_level": row.get("risk_level"),
        "progress": row.get("progress"),
        "source_mode": row.get("source_mode"),
        "request_id": str(row["request_id"]) if row.get("request_id") else None,
        "created_by": str(row["created_by"]) if row["created_by"] else None,
        "assigned_to": str(row["assigned_to"]) if row["assigned_to"] else None,
        "metadata": row.get("metadata") or {},
        "submitted_at": str(row["submitted_at"]) if row.get("submitted_at") else None,
        "created_at": str(row["created_at"]) if row["created_at"] else None,
        # cases não rastreia updated_at próprio; usa created_at como aproximação.
        "updated_at": str(row["created_at"]) if row.get("created_at") else None,
        "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
    }
