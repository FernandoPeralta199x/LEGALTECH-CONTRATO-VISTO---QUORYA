import json
import logging
from src.services.database import db
from src.utils.helpers import success_response, error_response, generate_uuid, get_timestamp
from src.utils.safety import enforce_production_safety

# 🚀 EXECUTA IMEDIATAMENTE NO COLD START (Fase Init da Lambda)
# Se falhar aqui, o container morre antes de expor qualquer dado!
enforce_production_safety()

logger = logging.getLogger()

VALID_RISK_LEVELS = ['low', 'medium', 'high', 'critical']

def create_case_result(event, context):
    """Criar resultado de análise de caso"""
    try:
        body = json.loads(event.get('body', '{}'))

        # ✅ VALIDAR CAMPOS OBRIGATÓRIOS
        required = ['case_id', 'result_type', 'findings', 'risk_level']
        for field in required:
            if field not in body:
                return error_response(400, f'Campo obrigatório: {field}')

        # ✅ VALIDAR RISK LEVEL
        if body['risk_level'] not in VALID_RISK_LEVELS:
            return error_response(400, f'Risk level deve ser um de: {", ".join(VALID_RISK_LEVELS)}')

        # ✅ CONTEXT MANAGER garante desconexão mesmo em caso de erro
        with db as database:
            case = database.execute_query_one(
                "SELECT id FROM public.cases WHERE id = %s::uuid",
                (body['case_id'],)
            )

            if not case:
                return error_response(404, 'Caso não encontrado')

            result_id = generate_uuid()
            created_at = get_timestamp()

            query = """
            INSERT INTO public.case_results
            (id, case_id, result_type, result_data, risk_level, summary_text, detailed_findings, recommendations, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            database.execute_update(query, (
                result_id,
                body['case_id'],
                body['result_type'],
                json.dumps(body['findings']),
                body['risk_level'],
                body.get('summary_text'),
                body.get('detailed_findings'),
                body.get('recommendations'),
                created_at
            ))

        logger.info(json.dumps({
            "event": "CASE_RESULT_CREATED",
            "result_id": result_id,
            "case_id": body['case_id'],
            "risk_level": body['risk_level'],
            "result_type": body['result_type']
        }))

        return success_response(201, 'Resultado de análise criado com sucesso', {
            'result_id': result_id,
            'case_id': body['case_id'],
            'risk_level': body['risk_level']
        })

    except Exception as e:
        logger.error(json.dumps({
            "event": "CASE_RESULT_CREATE_ERROR",
            "reason": str(e)
        }))
        return error_response(500, f'Erro ao criar resultado: {str(e)}')

def get_case_result(event, context):
    """Obter resultado de análise"""
    try:
        result_id = event['pathParameters']['resultId']

        # ✅ CONTEXT MANAGER
        with db as database:
            query = """
            SELECT id, case_id, result_type, result_data, risk_level, summary_text, created_at
            FROM public.case_results
            WHERE id = %s::uuid
            """
            result = database.execute_query_one(query, (result_id,))

        if not result:
            return error_response(404, 'Resultado não encontrado')

        return success_response(200, 'Resultado encontrado', {
            'id': str(result['id']),
            'case_id': str(result['case_id']),
            'result_type': result['result_type'],
            'findings': json.loads(result['result_data']) if result['result_data'] else {},
            'risk_level': result['risk_level'],
            'summary_text': result['summary_text'],
            'created_at': str(result['created_at'])
        })

    except Exception as e:
        logger.error(json.dumps({
            "event": "CASE_RESULT_GET_ERROR",
            "result_id": event.get('pathParameters', {}).get('resultId'),
            "reason": str(e)
        }))
        return error_response(500, str(e))

def list_case_results(event, context):
    """Listar resultados de um caso"""
    try:
        case_id = event.get('queryStringParameters', {}).get('caseId')

        if not case_id:
            return error_response(400, 'Parâmetro caseId é obrigatório')

        # ✅ CONTEXT MANAGER
        with db as database:
            query = """
            SELECT id, case_id, result_type, risk_level, created_at
            FROM public.case_results
            WHERE case_id = %s::uuid
            ORDER BY created_at DESC
            """
            results = database.execute_query(query, (case_id,))

        return success_response(200, f'{len(results)} resultados encontrados', [
            {
                'id': str(r['id']),
                'case_id': str(r['case_id']),
                'result_type': r['result_type'],
                'risk_level': r['risk_level'],
                'created_at': str(r['created_at'])
            }
            for r in results
        ])

    except Exception as e:
        logger.error(json.dumps({
            "event": "CASE_RESULT_LIST_ERROR",
            "case_id": event.get('queryStringParameters', {}).get('caseId'),
            "reason": str(e)
        }))
        return error_response(500, str(e))

def update_case_result(event, context):
    """Atualizar resultado de análise"""
    try:
        result_id = event['pathParameters']['resultId']
        body = json.loads(event.get('body', '{}'))

        fields = []
        values = []

        if 'risk_level' in body:
            if body['risk_level'] not in VALID_RISK_LEVELS:
                return error_response(400, f'Risk level inválido. Deve ser um de: {", ".join(VALID_RISK_LEVELS)}')
            fields.append('risk_level = %s')
            values.append(body['risk_level'])

        if 'summary_text' in body:
            fields.append('summary_text = %s')
            values.append(body['summary_text'])

        if 'recommendations' in body:
            fields.append('recommendations = %s')
            values.append(body['recommendations'])

        if not fields:
            return error_response(400, 'Nenhum campo para atualizar')

        values.append(result_id)

        # ✅ CONTEXT MANAGER
        with db as database:
            database.execute_update(
                f"UPDATE public.case_results SET {', '.join(fields)} WHERE id = %s::uuid",
                tuple(values)
            )

        logger.info(json.dumps({
            "event": "CASE_RESULT_UPDATED",
            "result_id": result_id,
            "fields": list(body.keys())
        }))

        return success_response(200, 'Resultado atualizado com sucesso')

    except Exception as e:
        logger.error(json.dumps({
            "event": "CASE_RESULT_UPDATE_ERROR",
            "result_id": event.get('pathParameters', {}).get('resultId'),
            "reason": str(e)
        }))
        return error_response(500, str(e))

def delete_case_result(event, context):
    """Deletar resultado de análise"""
    try:
        result_id = event['pathParameters']['resultId']

        # ✅ CONTEXT MANAGER
        with db as database:
            database.execute_update(
                "DELETE FROM public.case_results WHERE id = %s::uuid",
                (result_id,)
            )

        logger.info(json.dumps({
            "event": "CASE_RESULT_DELETED",
            "result_id": result_id
        }))

        return success_response(200, 'Resultado deletado com sucesso')

    except Exception as e:
        logger.error(json.dumps({
            "event": "CASE_RESULT_DELETE_ERROR",
            "result_id": event.get('pathParameters', {}).get('resultId'),
            "reason": str(e)
        }))
        return error_response(500, str(e))
