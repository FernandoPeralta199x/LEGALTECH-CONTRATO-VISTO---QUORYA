"""Handlers de Tributos e Notas (Fase 5) — controle interno / registro.

Tributos:  GET/POST /financial/taxes
Notas:     GET/POST /financial/fiscal-documents

REGRA (spec 12.1): o sistema NÃO calcula tributo por alíquota, NÃO emite nem gera
número de nota fiscal. Registra valores INFORMATIVOS de entrada MANUAL do admin.
GET = require_user; POST = require_role('admin') + assert_active_admin (fecha a
janela de revogação do JWT). O total do tributo é digitado; o banco só calcula a
divergência (confirmado - estimado).
"""
import json
import logging
from datetime import datetime

import psycopg2
from pydantic import ValidationError

from src.services.financial.period import BR_TZ, resolve_range
from src.schemas.financial_schemas import (CreateFiscalDocumentRequest,
                                           CreateTaxProvisionRequest)
from src.services.database import tenant_tx
from src.services.financial import fiscal_documents as fdoc
from src.services.financial import taxes as tax
from src.utils.context import (CallerRevoked, assert_active_admin, require_perfil, require_role,
                               require_user)
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import (fmt_validation_error, parse_json_body,
                                 parse_pagination, valid_uuid)
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()

_TAX_STATUS = {"estimado", "confirmado", "divergente", "cancelado", "nao_aplicavel"}
_DOC_STATUS = {"not_required", "note_pending", "authorized", "rejected", "note_canceled"}
_TAX_TYPES = {"iss", "pis", "cofins", "irpj", "csll", "inss", "icms", "das", "outro"}
_DOC_TYPES = {"nfse", "nfe", "nfce", "recibo", "outro"}


def _canon_fks(data: dict):
    """Canoniza os UUIDs de vínculo; retorna error_response se algum for inválido."""
    for fk in ("request_id", "case_id", "client_id"):
        if data.get(fk) is not None:
            canon = valid_uuid(data[fk])
            if canon is None:
                return error_response(400, f"{fk} inválido")
            data[fk] = canon
    return None


def _parse_dt(value):
    """ISO -> datetime BRT (naive vira BRT). Levanta ValueError se inválido."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=BR_TZ) if dt.tzinfo is None else dt


# ── Tributos ────────────────────────────────────────────────────────────────
@require_user
@require_role("admin")
@require_perfil("administrador")
def list_taxes(event, context):
    user = event["user"]
    params = event.get("queryStringParameters") or {}
    try:
        start, end, period = resolve_range(params)
    except ValueError as e:
        return error_response(400, str(e))
    pg, err = parse_pagination(params)
    if err:
        return err
    _p, page_size, offset = pg
    tax_type = (params.get("tax_type") or "").strip() or None
    status = (params.get("status") or "").strip() or None
    if tax_type is not None and tax_type not in _TAX_TYPES:
        return error_response(400, "tax_type inválido")
    if status is not None and status not in _TAX_STATUS:
        return error_response(400, "status inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            # SEC-02b: Financeiro é admin-only; fecha a janela de revogação (~2h) do token.
            assert_active_admin(cur, user["user_id"], user["organization_id"])
            summary = tax.compute_taxes(cur, start, end)
            by_type = tax.taxes_by_type(cur, start, end)
            items, total = tax.list_taxes(cur, start, end, tax_type, status, page_size, offset)
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except Exception as e:
        logger.error(json.dumps({"event": "TAXES_LIST_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao listar tributos")
    return success_response(200, "Tributos", {
        "period": period,
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "currency": "BRL",
        "disclaimer": "Valores informativos — sujeitos a validação contábil/fiscal.",
        "summary": {**summary, "by_type": by_type},
        "items": items,
        "total": total,
    })


@require_user
@require_role("admin")
@require_perfil("administrador")
def create_tax(event, context):
    user = event["user"]
    body, err = parse_json_body(event)
    if err:
        return err
    try:
        payload = CreateTaxProvisionRequest(**body)
    except ValidationError as e:
        return error_response(400, fmt_validation_error(e))
    data = payload.model_dump()

    fk_err = _canon_fks(data)
    if fk_err:
        return fk_err
    # reference_at = 1º dia da competência (agrupa o período por competência fiscal).
    data["reference_at"] = datetime(data["competencia_ano"], data["competencia_mes"], 1, tzinfo=BR_TZ)

    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            assert_active_admin(cur, user["user_id"], user["organization_id"])
            row = tax.insert_tax(cur, user["organization_id"], user["user_id"], data)
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except psycopg2.errors.UniqueViolation:
        return error_response(409, "Já existe um tributo com este external_id")
    except psycopg2.IntegrityError as e:
        logger.info(json.dumps({"event": "TAXES_INTEGRITY", "error": type(e).__name__}))
        return error_response(400, "Vínculo (venda/caso/cliente) inválido para esta organização")
    except Exception as e:
        logger.error(json.dumps({"event": "TAXES_CREATE_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao registrar tributo")
    return success_response(201, "Tributo registrado", row)


# ── Notas fiscais ─────────────────────────────────────────────────────────────
@require_user
@require_role("admin")
@require_perfil("administrador")
def list_fiscal_documents(event, context):
    user = event["user"]
    params = event.get("queryStringParameters") or {}
    try:
        start, end, period = resolve_range(params)
    except ValueError as e:
        return error_response(400, str(e))
    pg, err = parse_pagination(params)
    if err:
        return err
    _p, page_size, offset = pg
    doc_type = (params.get("doc_type") or "").strip() or None
    status = (params.get("status") or "").strip() or None
    if doc_type is not None and doc_type not in _DOC_TYPES:
        return error_response(400, "doc_type inválido")
    if status is not None and status not in _DOC_STATUS:
        return error_response(400, "status inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            # SEC-02b: Financeiro é admin-only; fecha a janela de revogação (~2h) do token.
            assert_active_admin(cur, user["user_id"], user["organization_id"])
            summary = fdoc.compute_fiscal_documents(cur, start, end)
            by_status = fdoc.fiscal_by_status(cur, start, end)
            items, total = fdoc.list_fiscal_documents(cur, start, end, doc_type, status, page_size, offset)
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except Exception as e:
        logger.error(json.dumps({"event": "FISCAL_LIST_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao listar notas fiscais")
    return success_response(200, "Notas fiscais", {
        "period": period,
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "currency": "BRL",
        "disclaimer": "O sistema registra notas já emitidas — não emite documento fiscal.",
        "summary": {**summary, "by_status": by_status},
        "items": items,
        "total": total,
    })


@require_user
@require_role("admin")
@require_perfil("administrador")
def create_fiscal_document(event, context):
    user = event["user"]
    body, err = parse_json_body(event)
    if err:
        return err
    try:
        payload = CreateFiscalDocumentRequest(**body)
    except ValidationError as e:
        return error_response(400, fmt_validation_error(e))
    data = payload.model_dump()

    fk_err = _canon_fks(data)
    if fk_err:
        return fk_err
    # issued_at (ISO) → datetime; reference_at = emissão (ou now) para o período.
    issued_dt = None
    if data.get("issued_at"):
        try:
            issued_dt = _parse_dt(data["issued_at"])
        except ValueError:
            return error_response(400, "issued_at inválido")
    now = datetime.now(BR_TZ)
    data["issued_at"] = issued_dt
    # nota autorizada sempre tem authorized_at (usa a emissão ou o registro).
    data["authorized_at"] = (issued_dt or now) if data["status"] == "authorized" else None
    data["reference_at"] = issued_dt or now

    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            assert_active_admin(cur, user["user_id"], user["organization_id"])
            row = fdoc.insert_fiscal_document(cur, user["organization_id"], user["user_id"], data)
    except CallerRevoked:
        return error_response(403, "Permissão administrativa revogada")
    except psycopg2.errors.UniqueViolation:
        return error_response(409, "Nota com este número/série (ou external_id) já registrada")
    except psycopg2.IntegrityError as e:
        logger.info(json.dumps({"event": "FISCAL_INTEGRITY", "error": type(e).__name__}))
        return error_response(400, "Vínculo (venda/caso/cliente) inválido para esta organização")
    except Exception as e:
        logger.error(json.dumps({"event": "FISCAL_CREATE_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao registrar nota fiscal")
    return success_response(201, "Nota fiscal registrada", row)
