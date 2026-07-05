"""Handlers do relatório/parecer do caso (SP3): gerar, revisar (revisão humana) e obter.

Geração (mock-first) consolida as evidências da triagem; revisão é a aprovação
humana. Leitura: qualquer autenticado. Geração/revisão: papel writer (admin/analyst).
"""
import json
import logging

from src.services.case_lifecycle import (
    CaseFinalized,
    CaseNotVisible,
    CasePaymentRequired,
    assert_case_paid,
    assert_case_writable,
)
from src.services.database import tenant_tx
from src.services.report_generator import (
    generate_report as _generate,
    get_report as _get,
    review_report as _review,
)
from src.utils.context import require_user, require_writer
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import parse_json_body as _parse_body, valid_uuid as _valid_uuid
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()

_VALID_REVIEW = {"reviewed", "approved"}


def _case_id(event):
    return _valid_uuid((event.get("pathParameters") or {}).get("caseId"))


@require_user
def get_case_report(event, context):
    """Retorna o relatório do caso (404 se ainda não gerado)."""
    user = event["user"]
    org = user["organization_id"]
    case_id = _case_id(event)
    if not case_id:
        return error_response(400, "caseId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute("SELECT 1 FROM public.cases WHERE id = %s AND deleted_at IS NULL", (case_id,))
            if cur.fetchone() is None:
                return error_response(404, "Caso não encontrado")
            report = _get(cur, org, case_id)
    except Exception as e:
        logger.error(json.dumps({"event": "REPORT_GET_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao obter o relatório")
    if not report:
        return error_response(404, "Relatório ainda não gerado")
    return success_response(200, "Relatório do caso", report)


@require_user
@require_writer
def generate_case_report(event, context):
    """Gera (ou regenera) o relatório do caso a partir da triagem."""
    user = event["user"]
    org = user["organization_id"]
    case_id = _case_id(event)
    if not case_id:
        return error_response(400, "caseId inválido")
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            # CVS-007: caso inexistente/deletado (404) ou finalizado (409) não regera relatório.
            assert_case_writable(cur, case_id)
            # Gate de pagamento (PAYMENT_GATE=hard): relatório é trabalho pago.
            assert_case_paid(cur, case_id)
            report = _generate(cur, org, case_id, user["user_id"])
    except CaseNotVisible:
        return error_response(404, "Caso não encontrado")
    except CaseFinalized:
        return error_response(409, "caso finalizado não aceita regeneração de relatório")
    except CasePaymentRequired:
        return error_response(402, "Pagamento pendente: conclua o pagamento antes de gerar o relatório")
    except Exception as e:
        logger.error(json.dumps({"event": "REPORT_GENERATE_ERROR", "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao gerar o relatório")
    logger.info(json.dumps({"event": "REPORT_GENERATED", "case_id": case_id}))
    return success_response(200, "Relatório gerado", report)


@require_user
@require_writer
def review_case_report(event, context):
    """Revisão humana do relatório: status 'reviewed' ou 'approved' (+ edição opcional)."""
    user = event["user"]
    org = user["organization_id"]
    case_id = _case_id(event)
    if not case_id:
        return error_response(400, "caseId inválido")
    body, err = _parse_body(event)
    if err:
        return err
    status = (body.get("status") or "reviewed").strip()
    if status not in _VALID_REVIEW:
        return error_response(400, "status inválido (use 'reviewed' ou 'approved')")
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            # CVS-007: não revisar relatório de caso inexistente/deletado (404)
            # nem de caso finalizado (409)
            assert_case_writable(cur, case_id)
            report = _review(cur, org, case_id, user["user_id"], status,
                             notes=body.get("review_notes"), summary=body.get("summary"),
                             recommendation=body.get("recommendation"))
            if report is None:
                return error_response(404, "Relatório ainda não gerado")
    except CaseNotVisible:
        return error_response(404, "Caso não encontrado")
    except CaseFinalized:
        return error_response(409, "caso finalizado não aceita revisão de relatório")
    except Exception as e:
        logger.error(json.dumps({"event": "REPORT_REVIEW_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao revisar o relatório")
    logger.info(json.dumps({"event": "REPORT_REVIEWED", "case_id": case_id, "status": status}))
    return success_response(200, "Relatório revisado", report)
