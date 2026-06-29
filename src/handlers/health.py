import json
import os
import logging
from src.services.database import db
from src.utils.helpers import success_response, error_response
from src.utils.safety import enforce_production_safety

# 🚀 EXECUTA IMEDIATAMENTE NO COLD START (Fase Init da Lambda)
# Se falhar aqui, o container morre antes de expor qualquer dado!
enforce_production_safety()

logger = logging.getLogger()

def health_check(event, context):
    """Health check da API e banco de dados"""
    try:
        # ✅ CONTEXT MANAGER garante desconexão mesmo em caso de erro
        with db as database:
            result = database.execute_query_one("SELECT version();")

        logger.info(json.dumps({
            "event": "HEALTH_CHECK_SUCCESS",
            "region": os.getenv('AWS_REGION', 'sa-east-1')
        }))

        return success_response(
            200,
            "API e banco de dados funcionando",
            {
                'database': 'contrato_visto',
                'region': os.getenv('AWS_REGION', 'sa-east-1'),
                'version': result['version'] if result else 'desconhecida'
            }
        )

    except Exception as e:
        logger.error(json.dumps({
            "event": "HEALTH_CHECK_ERROR",
            "reason": str(e)
        }))
        return error_response(500, f"Erro no health check: {str(e)}")
