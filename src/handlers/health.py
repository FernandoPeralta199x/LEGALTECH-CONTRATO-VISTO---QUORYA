"""Health check (público): verifica a API e a conectividade com o banco.

Não expõe detalhes internos (versão do Postgres, região, `str(e)`).
"""
import json
import logging

from src.services.database import get_connection
from src.utils.helpers import error_response, success_response
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


def health_check(event, context):
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        conn.rollback()  # mantém a conexão ociosa, sem transação aberta
    except Exception as e:
        logger.error(json.dumps({"event": "HEALTH_CHECK_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(503, "Serviço indisponível")
    logger.info(json.dumps({"event": "HEALTH_CHECK_SUCCESS"}))
    return success_response(200, "API e banco de dados operacionais", {"status": "ok"})
