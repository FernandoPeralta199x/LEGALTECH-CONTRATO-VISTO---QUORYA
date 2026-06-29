import json
import logging
from src.services.database import db
from src.utils.helpers import success_response, error_response, generate_uuid, get_timestamp
from src.utils.safety import enforce_production_safety

# 🚀 EXECUTA IMEDIATAMENTE NO COLD START (Fase Init da Lambda)
# Se falhar aqui, o container morre antes de expor qualquer dado!
enforce_production_safety()

logger = logging.getLogger()

def create_case(event, context):
    """Criar novo caso"""
    try:
        body = json.loads(event.get('body', '{}'))

        # ✅ VALIDAR CAMPOS OBRIGATÓRIOS
        required_fields = ['client_id', 'case_type']
        for field in required_fields:
            if field not in body:
                return error_response(400, f'Campo obrigatório: {field}')

        case_id = generate_uuid()
        created_at = get_timestamp()

        # ✅ CONTEXT MANAGER garante desconexão mesmo em caso de erro
        with db as database:
            query = """
            INSERT INTO public.cases (id, client_id, case_type, status, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """
            database.execute_update(query, (
                case_id,
                body['client_id'],
                body['case_type'],
                'open',
                created_at
            ))

        logger.info(json.dumps({
            "event": "CASE_CREATED",
            "case_id": case_id,
            "client_id": body['client_id'],
            "case_type": body['case_type']
        }))

        return success_response(201, 'Caso criado com sucesso', {
            'case_id': case_id,
            'status': 'open',
            'created_at': created_at
        })

    except Exception as e:
        logger.error(json.dumps({
            "event": "CASE_CREATE_ERROR",
            "reason": str(e)
        }))
        return error_response(500, f'Erro ao criar caso: {str(e)}')

def get_case(event, context):
    """Obter caso por ID"""
    try:
        case_id = event['pathParameters']['caseId']

        if not case_id:
            return error_response(400, 'caseId é obrigatório')

        # ✅ CONTEXT MANAGER
        with db as database:
            query = """
            SELECT id, client_id, case_type, status, created_at, updated_at
            FROM public.cases
            WHERE id = %s
            """
            result = database.execute_query_one(query, (case_id,))

        if not result:
            return error_response(404, 'Caso não encontrado')

        return success_response(200, 'Caso encontrado', dict(result))

    except Exception as e:
        logger.error(json.dumps({
            "event": "CASE_GET_ERROR",
            "case_id": event.get('pathParameters', {}).get('caseId'),
            "reason": str(e)
        }))
        return error_response(500, f'Erro ao obter caso: {str(e)}')

def list_cases(event, context):
    """Listar todos os casos"""
    try:
        # ✅ CONTEXT MANAGER
        with db as database:
            query = """
            SELECT id, client_id, case_type, status, created_at
            FROM public.cases
            ORDER BY created_at DESC
            LIMIT 100
            """
            results = database.execute_query(query)

        return success_response(200, f'{len(results)} casos encontrados', [dict(r) for r in results])

    except Exception as e:
        logger.error(json.dumps({
            "event": "CASE_LIST_ERROR",
            "reason": str(e)
        }))
        return error_response(500, f'Erro ao listar casos: {str(e)}')

def update_case(event, context):
    """Atualizar caso"""
    try:
        case_id = event['pathParameters']['caseId']
        body = json.loads(event.get('body', '{}'))

        fields_to_update = []
        values = []

        if 'status' in body:
            fields_to_update.append('status = %s')
            values.append(body['status'])

        if not fields_to_update:
            return error_response(400, 'Nenhum campo para atualizar')

        fields_to_update.append('updated_at = %s')
        values.append(get_timestamp())
        values.append(case_id)

        # ✅ CONTEXT MANAGER
        with db as database:
            query = f"""
            UPDATE public.cases
            SET {', '.join(fields_to_update)}
            WHERE id = %s
            """
            database.execute_update(query, tuple(values))

        logger.info(json.dumps({
            "event": "CASE_UPDATED",
            "case_id": case_id,
            "fields": list(body.keys())
        }))

        return success_response(200, 'Caso atualizado com sucesso')

    except Exception as e:
        logger.error(json.dumps({
            "event": "CASE_UPDATE_ERROR",
            "case_id": event.get('pathParameters', {}).get('caseId'),
            "reason": str(e)
        }))
        return error_response(500, f'Erro ao atualizar caso: {str(e)}')

def delete_case(event, context):
    """Deletar caso"""
    try:
        case_id = event['pathParameters']['caseId']

        # ✅ CONTEXT MANAGER
        with db as database:
            database.execute_update(
                "DELETE FROM public.cases WHERE id = %s",
                (case_id,)
            )

        logger.info(json.dumps({
            "event": "CASE_DELETED",
            "case_id": case_id
        }))

        return success_response(200, 'Caso deletado com sucesso')

    except Exception as e:
        logger.error(json.dumps({
            "event": "CASE_DELETE_ERROR",
            "case_id": event.get('pathParameters', {}).get('caseId'),
            "reason": str(e)
        }))
        return error_response(500, f'Erro ao deletar caso: {str(e)}')
