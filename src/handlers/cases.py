"""Handlers Lambda de `cases` (migração FastAPI→Serverless, Fase 2).

Protegidos pelo JWT Authorizer (via @require_user) e isolados por RLS via
tenant_tx (app.user_id/app.user_role). Alinhados ao schema real: `cases` NÃO tem
`updated_at`; `created_by` é gravado para a policy de RLS. Erros internos não
vazam ao cliente.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from psycopg2.extras import Json
from pydantic import ValidationError

from src.schemas.case_schemas import CaseCreate, CaseUpdate
from src.services.database import tenant_tx
from src.services.pricing.installments import InstallmentConfig, compute_installment_options
from src.handlers.requests import _next_request_code
from src.utils.context import require_user, require_writer
from src.utils.doc_status import to_v2_status
from src.utils.mime import mime_for
from src.services.case_lifecycle import CasesLimitReached, assert_cases_within_limit
from src.services.report_generator import get_report as _get_report
from src.utils.pii import mask_document, mask_email, mask_phone
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import fmt_validation_error as _fmt, parse_json_body as _parse_body, parse_pagination as _paginate, valid_uuid as _valid_uuid
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()
_BRT = timezone(timedelta(hours=-3))


def _installment_options_for(cur, org, total_cents):
    """Opções de parcelamento do caso, calculadas do total do caso + config da org
    (fail-safe: config inválida/ausente => apenas 1x). Evita o front re-estimar por
    módulos (os module_keys da triagem NÃO são os códigos do pricing)."""
    cur.execute("SELECT installment_config, version FROM public.pricing_configs"
                " WHERE organization_id = %s", (org,))
    row = cur.fetchone()
    raw = (row["installment_config"] if row else None) or {}
    version = row["version"] if row else 0
    try:
        cfg = InstallmentConfig(**raw)
    except Exception:
        cfg = InstallmentConfig()
    options = compute_installment_options(total_cents or 0, cfg, datetime.now(_BRT).date())
    return options, version


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
    # "Criar caso rápido": title/product/notes chegam dentro de metadata
    meta_in = data.metadata or {}
    title = (data.title or meta_in.get("title") or "").strip() or None
    product = data.product_type or meta_in.get("product")
    notes = (meta_in.get("notes") or "").strip() or None
    description = data.description or notes
    stored_meta = {k: v for k, v in {
        "description": description, "notes": notes,
        "source": meta_in.get("source", "api"),
    }.items() if v}

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
            # quota de casos da organização (pricing_configs.cases_limit); NULL => ilimitado
            assert_cases_within_limit(cur, user["organization_id"])
            code = _next_request_code(cur, user["organization_id"])
            cur.execute(
                "INSERT INTO public.cases"
                " (organization_id, client_id, case_type, priority, created_by, metadata,"
                "  title, product_type, code, source_mode)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'local')"
                " RETURNING id, client_id, case_type, status, priority, product_type,"
                " product_label, title, code, risk_level, progress, source_mode,"
                " request_id, created_by, assigned_to, created_at, completed_at,"
                " metadata, submitted_at",
                (user["organization_id"], client_id, data.case_type, data.priority,
                 user["user_id"], Json(stored_meta) if stored_meta else None,
                 title, product, code),
            )
            row = cur.fetchone()
    except _ClientNotFound:
        return error_response(400, "client_id inexistente")
    except _ClientInactive:
        return error_response(409, "cliente inativo não aceita novos casos")
    except CasesLimitReached:
        return error_response(402, "limite de casos da organização atingido")
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


def _triage_status(modules) -> str:
    if not modules:
        return "not_started"
    statuses = {m["status"] for m in modules}
    if statuses <= {"done"}:
        return "completed"
    if {"running", "in_progress"} & statuses:
        return "running"
    return "pending"


# ── get_case_aggregate: serializers por entidade (refactor do god-handler) ──
# Mesmo SQL e mesmo mapeamento de antes; apenas fatiados em helpers puros para
# leitura/teste. Cada _agg_* recebe o cursor da MESMA tenant_tx (RLS preservada).

def _agg_request(cur, org, request_id):
    """Request pai do caso -> (request_obj, total_price_cents)."""
    if not request_id:
        return None, 0
    cur.execute(
        "SELECT id, code, created_by, product_type, product_label, title,"
        " description, status, source_mode, idempotency_key, created_at,"
        " payment_status, installment_plan, total_price_cents"
        " FROM public.requests WHERE id = %s", (request_id,))
    r = cur.fetchone()
    if not r:
        return None, 0
    rc = str(r["created_at"]) if r["created_at"] else None
    request_obj = {
        "id": str(r["id"]), "code": r["code"], "organization_id": str(org),
        "created_by": str(r["created_by"]) if r["created_by"] else "",
        "product_type": r["product_type"], "product_label": r["product_label"],
        "title": r["title"], "description": r["description"] or "",
        "status": r["status"], "source_mode": r["source_mode"],
        "idempotency_key": r["idempotency_key"],
        "payment_status": r["payment_status"] or "pending",
        "installment_plan": r["installment_plan"],
        "created_at": rc, "updated_at": rc,
    }
    return request_obj, (r["total_price_cents"] or 0)


def _agg_parties(cur, org, case_id):
    """Partes do caso com PII mascarada (document/email/phone)."""
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
            # ModuleStatus válido (o tipo do front não conhece 'pending')
            "status": "not_started", "risk_level": "unknown",
            "provider_status_summary": None,
            "metadata": {k: v for k, v in md.items() if k not in _PII_KEYS},
            "created_at": str(p["created_at"]), "updated_at": str(p["updated_at"]),
        })
    return parties


def _agg_documents(cur, org, case_id):
    """Documentos do caso no shape V2 (ocr_status normalizado)."""
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
            "mime_type": mime_for(d.get("file_type")),
            "size_bytes": d.get("file_size_bytes") or 0,
            "storage_provider": "s3", "storage_key": d.get("s3_path") or "",
            # mesma normalização da lista (ocr_status 'done' -> 'processed'): fonte única
            "status": to_v2_status(d.get("ocr_status")),
            "ocr_status": d.get("ocr_status") or "not_started",
            "ai_read_status": d.get("extraction_status") or "not_started",
            "preview_available": False, "download_available": bool(d.get("s3_path")),
            "uploaded_at": created, "updated_at": created,
        })
    return documents


def _agg_timeline(cur, org, case_id):
    """Timeline do caso (mais recentes primeiro) -> (eventos, latest_event_at)."""
    cur.execute(
        "SELECT id, case_id, event_type, title, description, actor, payload, created_at"
        " FROM public.timeline_events WHERE case_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 500", (case_id,))
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
    return timeline, latest_event_at


def _agg_triage(cur, org, case_id):
    """Módulos de triagem do caso (ordem de criação)."""
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
    return triage


def _agg_provider_results(cur, org, case_id):
    """Resultados de provedores externos ligados à triagem do caso."""
    cur.execute(
        "SELECT id, case_id, triage_module_id, provider, source_mode, status,"
        " input_hash, normalized_result, summary, risk_signals, confidence,"
        " created_at, updated_at FROM public.provider_results"
        " WHERE case_id = %s ORDER BY created_at, id", (case_id,))
    provider_results = []
    for r in cur.fetchall():
        provider_results.append({
            "id": str(r["id"]), "case_id": str(r["case_id"]),
            "triage_module_id": str(r["triage_module_id"]),
            "organization_id": str(org), "provider": r["provider"],
            "provider_request_id": None, "source_mode": r["source_mode"],
            "status": r["status"], "input_hash": r["input_hash"],
            "raw_result_ref": None, "normalized_result": r["normalized_result"] or {},
            "summary": r["summary"], "risk_signals": r["risk_signals"] or [],
            "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            "error_code": None, "error_message": None,
            "created_at": str(r["created_at"]), "updated_at": str(r["updated_at"]),
        })
    return provider_results


def _agg_case_obj(c, org):
    """Linha de public.cases -> shape BackendAggregateCase."""
    created = str(c["created_at"]) if c["created_at"] else None
    return {
        "id": str(c["id"]), "request_id": str(c["request_id"]) if c["request_id"] else None,
        "code": c["code"] or "", "organization_id": str(org),
        "client_id": str(c["client_id"]) if c["client_id"] else None,
        "client_name": c.get("client_name"),
        "created_by": str(c["created_by"]) if c["created_by"] else "",
        "product_type": c["product_type"] or "", "product_label": c["product_label"] or "",
        "title": c["title"] or "", "description": c["description"] or "",
        "status": c["status"], "progress": c["progress"] or 0,
        "risk_level": c["risk_level"] or "unknown", "recommendation": c["recommendation"],
        "source_mode": c["source_mode"] or "local",
        "is_local_simulation": bool(c["is_local_simulation"]),
        "created_at": created, "updated_at": created,
    }


def _agg_summary(c, org, parties, documents, timeline, triage, report, latest_event_at):
    """Resumo operacional do caso (contagens + status derivados)."""
    created = str(c["created_at"]) if c["created_at"] else None
    return {
        "case_id": str(c["id"]), "organization_id": str(org),
        "parties_count": len(parties), "documents_count": len(documents),
        "timeline_count": len(timeline), "triage_status": _triage_status(triage),
        "report_status": report["status"] if report else "not_started",
        "risk_level": c["risk_level"] or "unknown",
        "recommendation": c["recommendation"], "progress": c["progress"] or 0,
        "latest_event_at": latest_event_at, "source_mode": c["source_mode"] or "local",
        "updated_at": created,
    }


@require_user
def get_case_aggregate(event, context):
    """Detalhe agregado do caso (shape BackendCaseAggregate) — uma única chamada
    para a tela de detalhe: case + request + partes (PII mascarada) + documentos +
    timeline + módulos de triagem + provider_results + summary.

    Orquestra os serializers _agg_* acima dentro de UMA tenant_tx (RLS por org).
    """
    user = event["user"]
    org = user["organization_id"]
    case_id = _valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute(
                "SELECT id, request_id, code, created_by, client_id, product_type, product_label,"
                " title, description, status, progress, risk_level, recommendation,"
                " source_mode, is_local_simulation, created_at,"
                " (SELECT cl.legal_name FROM public.clients cl"
                "  WHERE cl.id = public.cases.client_id)"
                " AS client_name"
                " FROM public.cases WHERE id = %s AND deleted_at IS NULL", (case_id,))
            c = cur.fetchone()
            if not c:
                return error_response(404, "Caso não encontrado")

            request_obj, request_total = _agg_request(cur, org, c["request_id"])
            parties = _agg_parties(cur, org, case_id)
            documents = _agg_documents(cur, org, case_id)
            timeline, latest_event_at = _agg_timeline(cur, org, case_id)
            triage = _agg_triage(cur, org, case_id)
            provider_results = _agg_provider_results(cur, org, case_id)
            report = _get_report(cur, org, case_id)
            installment_options, config_version = _installment_options_for(cur, org, request_total)
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_AGGREGATE_ERROR", "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao obter o detalhe do caso")

    return success_response(200, "Detalhe do caso", {
        "case": _agg_case_obj(c, org), "request": request_obj, "parties": parties,
        "documents": documents, "timeline": timeline, "triage_modules": triage,
        "provider_results": provider_results, "report": report,
        "summary": _agg_summary(c, org, parties, documents, timeline, triage,
                                report, latest_event_at),
        "payment_status": (request_obj or {}).get("payment_status", "pending"),
        "installment_plan": (request_obj or {}).get("installment_plan"),
        "installment_options": installment_options,
        "pricing_config_version": config_version,
        "total_price_cents": request_total,
        "payment_mode": os.getenv("PAYMENT_MODE", "mock"),
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
    status = (params.get("status") or "").strip()
    where, fargs = "WHERE deleted_at IS NULL", []
    if q:
        where += " AND (title ILIKE %s OR code ILIKE %s)"
        like = f"%{q}%"
        fargs = [like, like]
    if status:
        where += " AND status = %s"
        fargs.append(status)
    try:
        # A RLS já filtra os casos visíveis ao usuário (não filtramos por mão).
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(f"SELECT count(*) AS n FROM public.cases {where}", tuple(fargs))
            total = cur.fetchone()["n"]
            cur.execute(
                "SELECT id, client_id, case_type, status, priority, product_type,"
                " product_label, title, code, risk_level, progress, source_mode,"
                " request_id, created_by, assigned_to, created_at, completed_at,"
                " metadata, submitted_at,"
                " (SELECT count(*) FROM public.case_parties cp"
                "  WHERE cp.case_id = public.cases.id AND cp.deleted_at IS NULL)"
                " AS parties_count,"
                " (SELECT cl.legal_name FROM public.clients cl"
                "  WHERE cl.id = public.cases.client_id)"
                " AS client_name"
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
        # completed_at: setar/preservar em estados finalizados (completed/closed); limpar só
        # ao reabrir. COALESCE evita que completed->closed perca a data de conclusão.
        # (literal SQL; status é validado por pattern)
        fields.append("completed_at = " + (
            "COALESCE(completed_at, NOW())" if data.status in ("completed", "closed") else "NULL"))
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
            # #6: assigned_to deve ser um usuário ATIVO da MESMA organização — não um
            # UUID qualquer/de outra org (achado do pentest 2026-07-09).
            if data.assigned_to is not None:
                cur.execute("SELECT 1 FROM public.users WHERE id = %s"
                            " AND organization_id = %s AND status = 'active'",
                            (assigned, user["organization_id"]))
                if cur.fetchone() is None:
                    return error_response(400, "assigned_to deve ser um usuário ativo da organização")
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
    payment_status, installment_plan = "pending", None
    if request_id:
        cur.execute("SELECT total_price_cents, price_snapshot, payment_status,"
                    " installment_plan FROM public.requests"
                    " WHERE id = %s", (request_id,))
        prow = cur.fetchone()
        if prow:
            pricing = {"total_price_cents": prow["total_price_cents"],
                       "snapshot": prow["price_snapshot"]}
            payment_status = prow["payment_status"] or "pending"
            installment_plan = prow["installment_plan"]
    return {
        "parties_count": parties,
        "documents_count": documents,
        "timeline_count": timeline,
        "triage_count": triage,
        "pricing": pricing,
        "payment_status": payment_status,
        "installment_plan": installment_plan,
    }


def _serialize(row) -> dict:
    return {
        "id": str(row["id"]),
        "client_id": str(row["client_id"]) if row["client_id"] else None,
        "client_name": row.get("client_name"),
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
        "parties_count": row.get("parties_count") or 0,
        "submitted_at": str(row["submitted_at"]) if row.get("submitted_at") else None,
        "created_at": str(row["created_at"]) if row["created_at"] else None,
        # cases não rastreia updated_at próprio; usa created_at como aproximação.
        "updated_at": str(row["created_at"]) if row.get("created_at") else None,
        "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
    }
