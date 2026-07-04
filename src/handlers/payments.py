"""POST /cases/{caseId}/payment — aplica um plano de parcelamento ao caso (pagamento simulado).
Recalcula server-side; idempotência própria (chave + hash do payload) em installment_plan.payment."""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone, timedelta

from psycopg2.extras import Json
from pydantic import ValidationError

from src.adapters.payment import PaymentRequest, create_payment_provider
from src.schemas.pricing_schemas import PaymentSelectionSchema
from src.services.database import tenant_tx
from src.services.pricing.installments import InstallmentConfig, compute_installment_options
from src.utils.context import require_user
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import (fmt_validation_error as _fmt, parse_json_body as _parse_body,
                                 valid_uuid)
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()
_BRT = timezone(timedelta(hours=-3))


def _payload_hash(sel: PaymentSelectionSchema) -> str:
    raw = json.dumps({"parcelas": sel.parcelas, "method": sel.method}, sort_keys=True)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


@require_user
def create_case_payment(event, context):
    user = event["user"]
    org = user["organization_id"]
    case_id = valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    body, err = _parse_body(event)
    if err:
        return err
    try:
        sel = PaymentSelectionSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    try:
        # ── TX 1 (curta): ler caso/plano/config e validar. Nenhum I/O externo aqui. ──
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute(
                "SELECT r.id, r.total_price_cents, r.installment_plan, r.payment_status"
                " FROM public.requests r JOIN public.cases c ON c.request_id = r.id"
                "  AND c.organization_id = r.organization_id"
                " WHERE c.id = %s AND c.deleted_at IS NULL", (case_id,))
            row = cur.fetchone()
            if not row:
                return error_response(404, "Caso não encontrado")

            plan = row["installment_plan"]
            new_hash = _payload_hash(sel)
            if plan and plan.get("payment"):
                same_key = plan["payment"].get("idempotency_key") == sel.idempotency_key
                if same_key and plan["payment"].get("payload_hash") == new_hash:
                    return success_response(200, "Pagamento já registrado",
                                            {"payment_status": row["payment_status"],
                                             "installment_plan": plan})
                return error_response(409, "Pagamento já registrado com outros dados")

            icfg, iver = _read_config(cur, org)
            request_id = row["id"]
            total = row["total_price_cents"] or 0

        ref = datetime.now(_BRT).date()
        options = compute_installment_options(total, icfg, ref)
        opt = next((o for o in options if o["parcelas"] == sel.parcelas), None)
        if opt is None:
            return error_response(400, "Número de parcelas não ofertado")
        if sel.method not in opt["allowed_methods"]:
            return error_response(400, "Método não permitido para esta opção")
        if sel.pricing_config_version is not None and sel.pricing_config_version != iver:
            # soft (spec §7): recalcula com a config vigente; só registra a divergência
            logger.info(json.dumps({"event": "PAYMENT_CONFIG_VERSION_DIVERGED",
                                    "sent": sel.pricing_config_version, "current": iver}))

        # ── Provider FORA de transação (gateway real fará I/O de rede — NEW-1) ──
        provider = create_payment_provider()
        result = provider.create_charge(PaymentRequest(
            amount_cents=opt["valor_total_cents"], installments=sel.parcelas,
            method=sel.method, case_reference=str(case_id), organization_id=str(org),
            idempotency_key=sel.idempotency_key, schedule=opt["schedule"],
            mode=os.getenv("PAYMENT_MODE", "mock")))  # type: ignore[arg-type]

        payment = result.to_public()
        payment["idempotency_key"] = sel.idempotency_key
        payment["payload_hash"] = new_hash
        snapshot = {
            "version": 1, "pricing_config_version": iver,
            "selected_at": datetime.now(timezone.utc).isoformat(),
            "source_total_cents": total, "method": sel.method,
            "parcelas": opt["parcelas"], "has_juros": opt["has_juros"],
            "juros_mensal_bps": opt["juros_mensal_bps"],
            "valor_total_cents": opt["valor_total_cents"],
            "acrescimo_cents": opt["acrescimo_cents"], "currency": opt["currency"],
            "schedule": opt["schedule"], "payment": payment,
        }

        # ── TX 2: gravar com guarda anti-corrida (só grava se ainda não há plano) ──
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute(
                "UPDATE public.requests SET installment_plan = %s, payment_status = %s,"
                " pricing_config_version = %s, updated_at = now()"
                " WHERE id = %s AND installment_plan IS NULL",
                (Json(snapshot), result.status, iver, request_id))
            if cur.rowcount == 0:
                return error_response(409, "Pagamento já registrado (concorrência)")
    except Exception as e:
        logger.error(json.dumps({"event": "CASE_PAYMENT_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao registrar pagamento")

    return success_response(201, "Pagamento registrado",
                            {"payment_status": result.status, "installment_plan": snapshot})


def _read_config(cur, org):
    cur.execute("SELECT installment_config, version FROM public.pricing_configs"
                " WHERE organization_id = %s", (org,))
    r = cur.fetchone()
    raw = (r["installment_config"] if r else None) or {}
    ver = r["version"] if r else 0
    try:
        return InstallmentConfig(**raw), ver
    except Exception:
        return InstallmentConfig(), ver
