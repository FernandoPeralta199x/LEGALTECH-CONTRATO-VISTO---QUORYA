import json
import logging
from src.services.database import db
from src.utils.helpers import success_response, error_response
from src.utils.safety import enforce_production_safety

# 🚀 EXECUTA IMEDIATAMENTE NO COLD START (Fase Init da Lambda)
# Se falhar aqui, o container morre antes de expor qualquer dado!
enforce_production_safety()

logger = logging.getLogger()

def search_clauses(event, context):
    """Buscar cláusulas em documentos (busca básica)"""
    try:
        body = json.loads(event.get('body', '{}'))

        if 'case_id' not in body:
            return error_response(400, 'case_id é obrigatório')

        case_id = body['case_id']

        # ✅ CONTEXT MANAGER garante desconexão mesmo em caso de erro
        with db as database:
            query = """
            SELECT d.id, d.file_name, d.case_id
            FROM public.documents d
            WHERE d.case_id = %s
            ORDER BY d.created_at DESC
            LIMIT 20
            """
            results = database.execute_query(query, (case_id,))

        logger.info(json.dumps({
            "event": "SEARCH_CLAUSES_SUCCESS",
            "case_id": case_id,
            "results_count": len(results)
        }))

        return success_response(200, f'{len(results)} documentos encontrados', [dict(r) for r in results])

    except Exception as e:
        logger.error(json.dumps({
            "event": "SEARCH_CLAUSES_ERROR",
            "case_id": body.get('case_id') if 'body' in dir() else None,
            "reason": str(e)
        }))
        return error_response(500, f'Erro na busca: {str(e)}')
