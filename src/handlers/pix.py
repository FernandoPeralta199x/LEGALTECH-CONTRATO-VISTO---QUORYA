"""Handlers Pix (prepare — atrás do seam mock).

- createPixCharge  POST cases/{caseId}/pix-charge  (writer): cria cobrança dinâmica ->
  PENDING_PAYMENT. NUNCA marca pago. Valor 1x recomputado server-side; idempotente;
  índice único parcial garante 1 cobrança ativa por pedido.
- pixStatus        GET  cases/{caseId}/pix-charge/status  (user): backend-autoritativo (polling).
- pixWebhook       POST webhooks/pix/{provider}  (SEM JWT): assinatura -> dedup -> cross-check
  de valor -> transição guardada -> PAID -> projeta requests.payment_status -> enfileira.
- simulatePixPaid  POST cases/{caseId}/pix-charge/simulate  (writer, DEV-ONLY): dispara um
  webhook assinado do mock, exercitando o caminho real. Não existe em produção.
"""
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import Json
from pydantic import ValidationError

from src.adapters.pix import PixOrder, PixSignatureError, create_pix_provider
from src.schemas.pix_schemas import PixChargeRequestSchema
from src.services.database import simple_tx, tenant_tx
from src.services.pix import states
from src.services.pricing.installments import compute_installment_options
from src.services.pricing.org_config import read_installment_config
from src.utils.context import require_user, require_writer
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import (fmt_validation_error as _fmt, parse_json_body as _parse_body,
                                 valid_uuid)
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()

# Mapa dos status do provider (normalizados) para a máquina de estados interna.
_STATUS_MAP = {
    "PAID": states.PAID, "EXPIRED": states.EXPIRED,
    "PAYMENT_FAILED": states.PAYMENT_FAILED, "REFUNDED": states.REFUNDED,
    "CANCELLED": states.CANCELLED,
}


def _provider_name() -> str:
    return os.getenv("PIX_PROVIDER", "mock")


def _payload_hash(amount_cents: int) -> str:
    # Payload da cobrança Pix = método (implícito 'pix', 1x) + valor derivado server-side.
    raw = json.dumps({"method": "pix", "amount_cents": amount_cents}, sort_keys=True)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


# ─────────────────────────── createPixCharge ───────────────────────────

@require_user
@require_writer
def create_pix_charge(event, context):
    user = event["user"]
    org = user["organization_id"]
    case_id = valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    body, err = _parse_body(event)
    if err:
        return err
    try:
        req = PixChargeRequestSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    try:
        # ── TX1 (curta, leitura): valida caso/pedido, checa cobrança ativa (replay/único). ──
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute(
                "SELECT r.id AS request_id, r.total_price_cents, r.payment_status"
                " FROM public.cases c LEFT JOIN public.requests r"
                "  ON r.id = c.request_id AND r.organization_id = c.organization_id"
                " WHERE c.id = %s AND c.deleted_at IS NULL", (case_id,))
            row = cur.fetchone()
            if not row:
                return error_response(404, "Caso não encontrado")
            if not row["request_id"]:
                return error_response(409, "Caso sem pedido associado: não há nada a pagar")
            if row["payment_status"] != "pending":
                return error_response(409, "Pagamento já registrado ou em processamento")
            request_id = row["request_id"]
            total = row["total_price_cents"] or 0
            payload_hash = _payload_hash(total)

            # Cobrança ativa existente? (pix_charges sem RLS: escopo EXPLÍCITO por org.)
            cur.execute(
                "SELECT payment_id, status, pix_copy_paste, qr_code_image, expires_at,"
                " idempotency_key, payload_hash FROM public.pix_charges"
                " WHERE request_id = %s AND organization_id = %s AND status = 'PENDING_PAYMENT'"
                " ORDER BY created_at DESC LIMIT 1", (request_id, org))
            active = cur.fetchone()
            if active:
                if (active["idempotency_key"] == req.idempotency_key
                        and active["payload_hash"] == payload_hash):
                    return success_response(200, "Cobrança Pix já criada", {
                        "orderId": str(request_id), "paymentId": active["payment_id"],
                        "qrCodeImage": active["qr_code_image"],
                        "pixCopyPaste": active["pix_copy_paste"],
                        "expiresAt": _iso(active["expires_at"]), "status": active["status"]})
                return error_response(409, "Já existe uma cobrança Pix ativa para este pedido")

            icfg, _iver = read_installment_config(cur, org)

        # ── Valor à vista (1x) recomputado server-side; Pix precisa estar habilitado. ──
        ref = datetime.now(timezone.utc).date()
        options = compute_installment_options(total, icfg, ref)
        opt = next((o for o in options if o["parcelas"] == 1), None)
        if opt is None:
            return error_response(400, "Opção à vista indisponível")
        if "pix" not in opt["allowed_methods"]:
            return error_response(400, "Pix não está habilitado para esta organização")
        amount_cents = opt["valor_total_cents"]
        currency = opt["currency"]

        # ── Provider FORA de transação (I/O). Mock: gera paymentId/QR/copia-e-cola. ──
        charge = create_pix_provider().create_dynamic_charge(PixOrder(
            amount_cents=amount_cents, case_reference=str(case_id),
            organization_id=str(org), idempotency_key=req.idempotency_key, currency=currency))

        # ── INSERT: o índice único parcial (1 ativa por request) garante charge única
        #    (concorrente => UniqueViolation, propagada e traduzida em 409). ──
        try:
            with tenant_tx(user["user_id"], user["role"], org) as cur:
                cur.execute(
                    "INSERT INTO public.pix_charges (request_id, organization_id, provider,"
                    " payment_id, status, amount_cents, currency, pix_copy_paste, qr_code_image,"
                    " expires_at, idempotency_key, payload_hash)"
                    " VALUES (%s,%s,%s,%s,'PENDING_PAYMENT',%s,%s,%s,%s,%s,%s,%s)",
                    (request_id, org, _provider_name(), charge.payment_id, amount_cents,
                     currency, charge.pix_copy_paste, charge.qr_code_image, charge.expires_at,
                     req.idempotency_key, payload_hash))
        except psycopg2.errors.UniqueViolation:
            return error_response(409, "Já existe uma cobrança Pix ativa para este pedido")
    except Exception as e:
        logger.error(json.dumps({"event": "PIX_CHARGE_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao criar cobrança Pix")

    return success_response(201, "Cobrança Pix criada", charge.to_public(str(request_id)))


# ─────────────────────────── pixStatus (polling) ───────────────────────────

@require_user
def get_pix_status(event, context):
    user = event["user"]
    org = user["organization_id"]
    case_id = valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute(
                "SELECT r.id AS request_id FROM public.cases c LEFT JOIN public.requests r"
                "  ON r.id = c.request_id AND r.organization_id = c.organization_id"
                " WHERE c.id = %s AND c.deleted_at IS NULL", (case_id,))
            row = cur.fetchone()
            if not row or not row["request_id"]:
                return error_response(404, "Caso ou pedido não encontrado")
            cur.execute(
                "SELECT payment_id, status, expires_at FROM public.pix_charges"
                " WHERE request_id = %s AND organization_id = %s"
                " ORDER BY created_at DESC LIMIT 1", (row["request_id"], org))
            charge = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "PIX_STATUS_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao consultar status")

    if not charge:
        return success_response(200, "Sem cobrança",
                                {"status": None, "paymentId": None, "expiresAt": None})
    return success_response(200, "Status da cobrança", {
        "status": charge["status"], "paymentId": charge["payment_id"],
        "expiresAt": _iso(charge["expires_at"])})


# ─────────────────────────── pixWebhook (confirmação) ───────────────────────────

def _project_paid(org, request_id, amount_cents, currency) -> None:
    """Projeta o PAID em requests.payment_status (que tem RLS => precisa de contexto de org).
    A org vem da cobrança já verificada por assinatura. Grava um snapshot mínimo do plano
    (Pix 1x) para o agregado do caso exibir "Pagamento já registrado"."""
    snapshot = {
        "version": 1, "method": "pix", "parcelas": 1,
        "valor_total_cents": amount_cents, "currency": currency,
        "paid_at": datetime.now(timezone.utc).isoformat(),
        "payment": {"provider": _provider_name(), "mode": os.getenv("PAYMENT_MODE", "mock"),
                    "status": "paid", "method": "pix",
                    "payment_form": {"type": "pix"}},
    }
    with tenant_tx(str(org), "admin", str(org)) as cur:
        cur.execute(
            "UPDATE public.requests SET payment_status = 'paid', installment_plan = %s,"
            " updated_at = now() WHERE id = %s AND payment_status IN ('pending', 'processing')",
            (Json(snapshot), request_id))


def _enqueue_pix_paid(request_id, payment_id) -> None:
    """Pós-pagamento. Prepare: inline (log). Fase 7: publica na SQS PixPostPaymentQueue
    (baixa/e-mail/timeline), idempotente, DLQ."""
    logger.info(json.dumps({"event": "PIX_PAID", "request_id": str(request_id),
                            "payment_id": payment_id}))


def pix_webhook(event, context):
    # SEM require_user: a autenticação É a ASSINATURA. Verificada ANTES de qualquer banco.
    provider_name = (event.get("pathParameters") or {}).get("provider") or _provider_name()
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    signature = headers.get("x-pix-signature", "")
    raw = event.get("body") or ""
    payload = raw.encode() if isinstance(raw, str) else raw

    try:
        ev = create_pix_provider().handle_webhook(payload, signature)
    except PixSignatureError:
        logger.warning(json.dumps({"event": "PIX_WEBHOOK_BAD_SIGNATURE"}))
        return error_response(401, "Assinatura inválida")
    except Exception:
        return error_response(400, "Payload inválido")

    try:
        # ── Dedup (append-only): mesmo event_id 2x => 200 ACK sem reprocessar. ──
        with simple_tx() as cur:
            cur.execute(
                "INSERT INTO public.pix_webhook_events (event_id, provider, payment_id, status)"
                " VALUES (%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING",
                (ev.event_id, provider_name, ev.payment_id, ev.status))
            if cur.rowcount == 0:
                return success_response(200, "Evento já processado", {"deduped": True})

        # ── Carrega a cobrança por payment_id (tabela de sistema, sem RLS). ──
        with simple_tx() as cur:
            cur.execute(
                "SELECT request_id, organization_id, status, amount_cents, currency"
                " FROM public.pix_charges WHERE provider = %s AND payment_id = %s",
                (provider_name, ev.payment_id))
            charge = cur.fetchone()
        if not charge:
            logger.warning(json.dumps({"event": "PIX_WEBHOOK_UNKNOWN_CHARGE"}))
            return success_response(200, "Cobrança desconhecida", {"ignored": True})

        # ── Cross-check de valor/moeda: divergência NÃO marca pago (alerta). ──
        if ev.amount_cents != charge["amount_cents"] or ev.currency != charge["currency"]:
            logger.error(json.dumps({"event": "PIX_WEBHOOK_AMOUNT_MISMATCH",
                                     "payment_id": ev.payment_id}))
            return success_response(200, "Divergência de valor — ignorado", {"ignored": True})

        target = _STATUS_MAP.get(ev.status)
        if not target or not states.is_valid_transition(charge["status"], target):
            return success_response(200, "Transição não aplicável", {"ignored": True})

        paid = target == states.PAID
        # ── Transição guardada (idempotente pelo WHERE do status atual). ──
        with simple_tx() as cur:
            cur.execute(
                "UPDATE public.pix_charges SET status = %s,"
                " paid_at = CASE WHEN %s THEN now() ELSE paid_at END, updated_at = now()"
                " WHERE provider = %s AND payment_id = %s AND status = %s",
                (target, paid, provider_name, ev.payment_id, charge["status"]))
            applied = cur.rowcount

        if applied and paid:
            _project_paid(charge["organization_id"], charge["request_id"],
                          charge["amount_cents"], charge["currency"])
            _enqueue_pix_paid(charge["request_id"], ev.payment_id)
    except Exception as e:
        logger.error(json.dumps({"event": "PIX_WEBHOOK_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao processar webhook")

    return success_response(200, "Webhook processado", {"status": target})


# ─────────────────────────── simulatePixPaid (DEV-ONLY) ───────────────────────────

@require_user
@require_writer
def simulate_pix_paid(event, context):
    """DEV-ONLY (PAYMENT_MODE=mock): dispara um webhook Pix ASSINADO para a cobrança ativa
    do caso — exercita o caminho real (assinatura -> dedup -> cross-check -> PAID) sem
    gateway. NÃO existe em produção (lá o provider real dispara o webhook)."""
    if os.getenv("PAYMENT_MODE", "mock") != "mock":
        return error_response(404, "Indisponível")
    user = event["user"]
    org = user["organization_id"]
    case_id = valid_uuid((event.get("pathParameters") or {}).get("caseId"))
    if not case_id:
        return error_response(400, "caseId inválido")

    with tenant_tx(user["user_id"], user["role"], org) as cur:
        cur.execute(
            "SELECT p.payment_id, p.amount_cents, p.currency FROM public.cases c"
            " JOIN public.pix_charges p"
            "  ON p.request_id = c.request_id AND p.organization_id = c.organization_id"
            " WHERE c.id = %s AND p.status = 'PENDING_PAYMENT'"
            " ORDER BY p.created_at DESC LIMIT 1", (case_id,))
        charge = cur.fetchone()
    if not charge:
        return error_response(404, "Nenhuma cobrança Pix ativa para simular")

    provider = create_pix_provider()
    payload = json.dumps({
        "event_id": f"sim_{uuid.uuid4().hex}", "payment_id": charge["payment_id"],
        "status": "PAID", "amount_cents": charge["amount_cents"],
        "currency": charge["currency"],
    }).encode()
    signature = provider.sign(payload)  # MockPixProvider (garantido por PAYMENT_MODE=mock)
    return pix_webhook(
        {"pathParameters": {"provider": _provider_name()},
         "headers": {"x-pix-signature": signature}, "body": payload.decode()}, context)
