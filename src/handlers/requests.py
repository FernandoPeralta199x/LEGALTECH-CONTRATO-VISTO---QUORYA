"""Handler do Pedido (wizard "Novo Pedido") — POST /requests (orquestrador).

Espelha o ``RequestService`` do legaltech-aws: em UMA transação ``tenant_tx`` cria,
por ``case_id``: ``request`` (+ price_snapshot) + ``case`` (status awaiting_triage) +
``case_parties`` + ``document`` + plano de ``triage_modules`` (providers dos adapters)
+ ``timeline_events``. Regra do wizard: documento é obrigatório.
"""
import json
import logging
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import Json
from pydantic import ValidationError

import uuid

from src.schemas.request_schemas import RequestCreateSchema
from src.services.database import tenant_tx
from src.services.pricing.config import MATRIX, PRODUCTS
from src.services.pricing.estimate import estimate
from src.services.storage import storage_service
from src.services.triage_plan import plan_for_product
from src.utils.context import require_user, require_writer
from src.utils.helpers import error_response, generate_uuid, success_response
from src.utils.lambda_io import fmt_validation_error as _fmt, parse_json_body as _parse_body, valid_uuid as _valid_uuid
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


def _file_ext(filename: str) -> str | None:
    return filename.rsplit(".", 1)[-1][:10].lower() if "." in filename else None


def _next_request_code(cur, organization_id) -> str:
    """Código sequencial REQ-AAAA-NNNN por organização/ano (request_code_sequences)."""
    year = datetime.now(timezone.utc).year
    cur.execute(
        "INSERT INTO public.request_code_sequences (organization_id, year, next_number)"
        " VALUES (%s, %s, 1)"
        " ON CONFLICT (organization_id, year)"
        " DO UPDATE SET next_number = public.request_code_sequences.next_number + 1"
        " RETURNING next_number",
        (organization_id, year),
    )
    seq = cur.fetchone()["next_number"]
    return f"REQ-{year}-{seq:04d}"


@require_user
@require_writer
def create_request(event, context):
    """Cria um pedido completo (wizard) e o caso operacional vinculado."""
    user = event["user"]
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = RequestCreateSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    if data.product_type not in PRODUCTS:
        return error_response(400, "product_type inválido")
    # documento é OPCIONAL: o wizard permite registrar o pedido sem contrato
    # (anexar depois). Quando ausente, não cria documento nem o evento de anexo.

    org = user["organization_id"]
    uid = user["user_id"]
    product = PRODUCTS[data.product_type]
    product_label = data.product_label or product.title
    title = data.title or product.title
    # módulos: usa os selecionados; se vazio, os required/default do produto
    selected = data.selected_modules or [
        code for code, cfg in MATRIX.get(data.product_type, {}).items()
        if cfg.required or cfg.default
    ]
    request_id = generate_uuid()
    case_id = generate_uuid()
    modules = plan_for_product(data.product_type)

    try:
        with tenant_tx(uid, user["role"], org) as cur:
            # overrides de preço da organização (pricing_configs)
            cur.execute(
                "SELECT module_overrides FROM public.pricing_configs WHERE organization_id = %s",
                (org,),
            )
            row = cur.fetchone()
            overrides = row["module_overrides"] if row else None
            est = estimate(data.product_type, selected, overrides)
            code = _next_request_code(cur, org)

            # FK circular requests.case_id <-> cases.request_id (NOT DEFERRABLE):
            # cria request com case_id NULL, depois o case, e fecha o vínculo no fim.
            cur.execute(
                "INSERT INTO public.requests"
                " (id, organization_id, created_by, code, product_type, product_label, title,"
                "  description, status, source_mode, idempotency_key, case_id,"
                "  total_price_cents, price_snapshot)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'created',%s,%s,NULL,%s,%s)",
                (request_id, org, uid, code, data.product_type, product_label, title,
                 data.description, data.source_mode, data.idempotency_key,
                 est["total_price_cents"], Json(est)),
            )
            cur.execute(
                "INSERT INTO public.cases"
                " (id, organization_id, request_id, client_id, case_type, product_type,"
                "  product_label, title, description, status, source_mode, created_by, code)"
                " VALUES (%s,%s,%s,NULL,%s,%s,%s,%s,%s,'awaiting_triage',%s,%s,%s)",
                (case_id, org, request_id, data.product_type, data.product_type,
                 product_label, title, data.description, data.source_mode, uid, code),
            )
            cur.execute(
                "UPDATE public.requests SET case_id = %s WHERE id = %s",
                (case_id, request_id),
            )

            party_count = 0
            for p in data.parties:
                if not p.name.strip() or not p.role.strip():
                    continue
                cur.execute(
                    "INSERT INTO public.case_parties"
                    " (organization_id, case_id, party_type, name, document, metadata)"
                    " VALUES (%s,%s,%s,%s,%s,%s)",
                    (org, case_id, p.role, p.name.strip(), p.document,
                     Json({**p.metadata, "person_type": p.person_type,
                           "email": p.email, "phone": p.phone,
                           "document_type": p.document_type, "source": "new_case_wizard"})),
                )
                party_count += 1

            has_document = data.document is not None
            if has_document:
                doc = data.document
                # S-01: chave do S3 SEMPRE gerada no backend (case_id/doc_id), nunca do cliente
                doc_id = str(uuid.uuid4())
                s3_key = storage_service.build_key(case_id, doc_id, doc.filename)
                cur.execute(
                    "INSERT INTO public.documents"
                    " (id, organization_id, case_id, s3_url, s3_path, file_name, file_type,"
                    "  file_size_bytes, ocr_status, extraction_status, uploaded_by)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending','pending',%s)",
                    (doc_id, org, case_id, storage_service.object_url(s3_key), s3_key,
                     doc.filename, _file_ext(doc.filename), doc.size_bytes, uid),
                )

            for d in modules:
                cur.execute(
                    "INSERT INTO public.triage_modules"
                    " (organization_id, case_id, module_key, module_label, provider, status,"
                    "  source_mode, required, reason)"
                    " VALUES (%s,%s,%s,%s,%s,'not_started','mock',%s,%s)",
                    (org, case_id, d.module_key, d.module_label, d.provider, d.required, d.reason),
                )

            timeline = [
                ("request_created", "Pedido criado", "Pedido operacional registrado no backend.", "system"),
                ("case_created", "Caso criado", "Caso principal criado a partir do pedido.", "system"),
            ]
            timeline += [("party_added", "Parte adicionada",
                          "Parte informada no Wizard vinculada ao caso.", "user")] * party_count
            if has_document:
                timeline.append(("document_attached", "Documento anexado",
                                 "Documento selecionado no Wizard vinculado ao caso.", "user"))
            timeline += [
                ("triage_plan_created", "Plano de triagem criado",
                 "Módulos de triagem criados para o caso.", "system"),
                ("wizard_completed", "Wizard concluído",
                 "Novo Pedido concluído e conectado ao backend.", "user"),
            ]
            for etype, etitle, edesc, eactor in timeline:
                cur.execute(
                    "INSERT INTO public.timeline_events"
                    " (organization_id, case_id, event_type, title, description, actor)"
                    " VALUES (%s,%s,%s,%s,%s,%s)",
                    (org, case_id, etype, etitle, edesc, eactor),
                )
    except psycopg2.errors.UniqueViolation:
        return error_response(409, "Pedido já registrado (idempotency_key)")
    except Exception as e:
        logger.error(json.dumps({"event": "REQUEST_CREATE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao criar pedido")

    logger.info(json.dumps({"event": "REQUEST_CREATED",
                            "request_id": request_id, "case_id": case_id}))
    return success_response(201, "Pedido criado com sucesso", {
        "id": request_id,
        "request_id": request_id,
        "code": code,
        "case_id": case_id,
        "case_code": code,
        "organization_id": org,
        "created_by": uid,
        "product_type": data.product_type,
        "product_label": product_label,
        "title": title,
        "description": data.description or "",
        "status": "awaiting_triage",
        "request_status": "created",
        "case_status": "awaiting_triage",
        "source_mode": data.source_mode,
        "idempotency_key": data.idempotency_key,
        "total_price_cents": est["total_price_cents"],
        "parties_count": party_count,
        "documents_count": 1 if has_document else 0,
        "triage_modules_count": len(modules),
        "timeline_events_count": len(timeline),
    })


@require_user
def get_request(event, context):
    """Retorna um pedido + contagens vinculadas (request/case/partes/módulos)."""
    user = event["user"]
    request_id = _valid_uuid((event.get("pathParameters") or {}).get("requestId"))
    if not request_id:
        return error_response(400, "requestId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            cur.execute(
                "SELECT id, code, product_type, product_label, title, status, source_mode,"
                " case_id, total_price_cents, price_snapshot, created_at"
                " FROM public.requests WHERE id = %s",
                (request_id,),
            )
            req = cur.fetchone()
            if not req:
                return error_response(404, "Pedido não encontrado")
            cur.execute(
                "SELECT count(*) AS n FROM public.triage_modules WHERE case_id = %s",
                (req["case_id"],),
            )
            modules_count = cur.fetchone()["n"]
            cur.execute(
                "SELECT count(*) AS n FROM public.case_parties"
                " WHERE case_id = %s AND deleted_at IS NULL",
                (req["case_id"],),
            )
            parties_count = cur.fetchone()["n"]
            cur.execute("SELECT status FROM public.cases WHERE id = %s", (req["case_id"],))
            crow = cur.fetchone()
            case_status = crow["status"] if crow else None
    except Exception as e:
        logger.error(json.dumps({"event": "REQUEST_GET_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao obter pedido")

    return success_response(200, "Pedido encontrado", {
        "request_id": str(req["id"]),
        "case_id": str(req["case_id"]) if req["case_id"] else None,
        "code": req["code"],
        "product_type": req["product_type"],
        "product_label": req["product_label"],
        "title": req["title"],
        "status": req["status"],
        "case_status": case_status,
        "total_price_cents": req["total_price_cents"],
        "price_snapshot": req["price_snapshot"],
        "parties_count": parties_count,
        "triage_modules_count": modules_count,
        "created_at": str(req["created_at"]) if req.get("created_at") else None,
    })
