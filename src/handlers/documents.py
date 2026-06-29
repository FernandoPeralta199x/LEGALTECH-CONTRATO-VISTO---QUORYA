import json
import logging
from src.services.database import db
from src.utils.helpers import success_response, error_response, generate_uuid, get_timestamp
from src.utils.safety import enforce_production_safety

# 🚀 EXECUTA IMEDIATAMENTE NO COLD START (Fase Init da Lambda)
# Se falhar aqui, o container morre antes de expor qualquer dado!
enforce_production_safety()

logger = logging.getLogger()

def upload_document(event, context):
    """Upload de documento"""
    try:
        body = json.loads(event.get('body', '{}'))

        # ✅ VALIDAR CAMPOS OBRIGATÓRIOS
        required_fields = ['case_id', 'file_name', 'file_type']
        for field in required_fields:
            if field not in body:
                return error_response(400, f'Campo obrigatório: {field}')

        doc_id = generate_uuid()
        created_at = get_timestamp()

        # ✅ CONTEXT MANAGER garante desconexão mesmo em caso de erro
        with db as database:
            query = """
            INSERT INTO public.documents (id, case_id, file_name, file_type, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """
            database.execute_update(query, (
                doc_id,
                body['case_id'],
                body['file_name'],
                body['file_type'],
                created_at
            ))

        logger.info(json.dumps({
            "event": "DOCUMENT_UPLOADED",
            "document_id": doc_id,
            "case_id": body['case_id'],
            "file_name": body['file_name'],
            "file_type": body['file_type']
        }))

        return success_response(201, 'Documento enviado com sucesso', {
            'document_id': doc_id,
            'created_at': created_at
        })

    except Exception as e:
        logger.error(json.dumps({
            "event": "DOCUMENT_UPLOAD_ERROR",
            "reason": str(e)
        }))
        return error_response(500, f'Erro ao fazer upload: {str(e)}')

def get_document(event, context):
    """Obter documento"""
    try:
        doc_id = event['pathParameters']['docId']

        # ✅ CONTEXT MANAGER
        with db as database:
            query = """
            SELECT id, case_id, file_name, file_type, created_at
            FROM public.documents
            WHERE id = %s
            """
            result = database.execute_query_one(query, (doc_id,))

        if not result:
            return error_response(404, 'Documento não encontrado')

        return success_response(200, 'Documento encontrado', dict(result))

    except Exception as e:
        logger.error(json.dumps({
            "event": "DOCUMENT_GET_ERROR",
            "document_id": event.get('pathParameters', {}).get('docId'),
            "reason": str(e)
        }))
        return error_response(500, f'Erro ao obter documento: {str(e)}')
