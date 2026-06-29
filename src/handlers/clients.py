import json
import logging
from pydantic import ValidationError

from src.utils.helpers import success_response, error_response, generate_uuid, get_timestamp
from src.utils.auth import validate_tenant_access
from src.schemas.client_schemas import ClientCreateSchema, ClientUpdateSchema
from src.utils.safety import enforce_production_safety

# 🚀 EXECUTA IMEDIATAMENTE NO COLD START (Fase Init da Lambda)
# Se falhar aqui, o container morre antes de expor qualquer dado!
enforce_production_safety()

logger = logging.getLogger()

# ============================================================================
# HELPERS PARA ACESSAR USER DO EVENT
# ============================================================================

def get_user_from_event(event):
    """
    Extrai dados do usuário do event (do JWT Authorizer no API Gateway)

    O API Gateway adiciona o payload em:
    event['requestContext']['authorizer']['context']
    """
    try:
        authorizer = event['requestContext']['authorizer']['context']
        return {
            'user_id': authorizer['user_id'],
            'email': authorizer['email'],
            'role': authorizer['role']
        }
    except KeyError as e:
        logger.error(json.dumps({
            "event": "USER_EXTRACT_ERROR",
            "reason": str(e)
        }))
        return None

def require_user(handler_func):
    """Decorator para exigir que user exista no event"""
    def wrapper(event, context):
        user = get_user_from_event(event)
        if not user:
            return error_response(401, 'Usuário não autenticado')
        event['user'] = user
        return handler_func(event, context)
    return wrapper

# ============================================================================
# HANDLERS (SIMPLIFICADOS - JWT já validado no API Gateway)
# ============================================================================

@require_user
def create_client(event, context):
    """
    Criar cliente (PROTEGIDO)

    POST /clients
    Authorization: Bearer TOKEN
    {
      "name": "Empresa XYZ",
      "email": "contato@xyz.com",
      "phone": "(11) 99999-9999",
      "cpf_cnpj": "12.345.678/0001-90",
      "type": "pj"
    }
    """
    try:
        from src.services.database import db

        user = event['user']
        body = json.loads(event.get('body', '{}'))

        # ✅ VALIDAR COM PYDANTIC
        try:
            client_data = ClientCreateSchema(**body)
        except ValidationError as e:
            errors = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]
            return error_response(400, f'Validação falhou: {", ".join(errors)}')

        # ✅ CONTEXT MANAGER
        with db as database:
            client_id = generate_uuid()
            created_at = get_timestamp()

            query = """
            INSERT INTO public.clients (id, created_by, name, email, phone, cpf_cnpj, type, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            database.execute_update(query, (
                client_id,
                user['user_id'],  # ← Vincula ao usuário autenticado
                client_data.name,
                client_data.email,
                client_data.phone,
                client_data.cpf_cnpj,
                client_data.type,
                'active',
                created_at,
                created_at
            ))

        logger.info(json.dumps({
            "event": "CLIENT_CREATED",
            "client_id": client_id,
            "created_by": user['user_id'],
            "name": client_data.name
        }))

        return success_response(201, 'Cliente criado com sucesso', {
            'client_id': client_id,
            'name': client_data.name
        })

    except Exception as e:
        logger.error(json.dumps({
            "event": "CLIENT_CREATE_ERROR",
            "user_id": event.get('user', {}).get('user_id'),
            "reason": str(e)
        }))
        return error_response(500, str(e))

@require_user
def get_client(event, context):
    """
    Obter cliente (PROTEGIDO - com validação de acesso Anti-IDOR)

    GET /clients/{clientId}
    Authorization: Bearer TOKEN
    """
    try:
        from src.services.database import db

        user = event['user']
        client_id = event['pathParameters']['clientId']

        # ✅ CONTEXT MANAGER
        with db as database:
            client = database.execute_query_one(
                "SELECT id, created_by, name, email, phone, cpf_cnpj, type, status, created_at FROM public.clients WHERE id = %s",
                (client_id,)
            )

            if not client:
                return error_response(404, 'Cliente não encontrado')

            # ✅ VALIDAR ACESSO (Anti-IDOR)
            if not validate_tenant_access(user['user_id'], client['created_by'], database):
                logger.error(json.dumps({
                    "event": "TENANT_VIOLATION_ATTEMPT",
                    "user_id": user['user_id'],
                    "client_id": client_id,
                    "action": "get_client"
                }))
                return error_response(403, 'Acesso negado')

        return success_response(200, 'Cliente encontrado', dict(client))

    except Exception as e:
        logger.error(json.dumps({
            "event": "CLIENT_GET_ERROR",
            "client_id": event.get('pathParameters', {}).get('clientId'),
            "reason": str(e)
        }))
        return error_response(500, str(e))

@require_user
def list_clients(event, context):
    """
    Listar clientes (PROTEGIDO - com RBAC)

    GET /clients
    Authorization: Bearer TOKEN
    """
    try:
        from src.services.database import db

        user = event['user']

        # ✅ CONTEXT MANAGER
        with db as database:
            # Admin: vê todos | Outros: veem apenas seus
            if user['role'] == 'admin':
                query = "SELECT id, created_by, name, email, phone, cpf_cnpj, type, status, created_at FROM public.clients WHERE status = %s ORDER BY created_at DESC LIMIT 100"
                clients = database.execute_query(query, ('active',))
            else:
                query = "SELECT id, created_by, name, email, phone, cpf_cnpj, type, status, created_at FROM public.clients WHERE created_by = %s AND status = %s ORDER BY created_at DESC LIMIT 100"
                clients = database.execute_query(query, (user['user_id'], 'active'))

        return success_response(200, f'{len(clients)} clientes encontrados', [dict(c) for c in clients])

    except Exception as e:
        logger.error(json.dumps({
            "event": "CLIENT_LIST_ERROR",
            "user_id": event.get('user', {}).get('user_id'),
            "reason": str(e)
        }))
        return error_response(500, str(e))

@require_user
def update_client(event, context):
    """
    Atualizar cliente (PROTEGIDO)

    PUT /clients/{clientId}
    Authorization: Bearer TOKEN
    """
    try:
        from src.services.database import db

        user = event['user']
        client_id = event['pathParameters']['clientId']
        body = json.loads(event.get('body', '{}'))

        # ✅ VALIDAR COM PYDANTIC
        try:
            client_data = ClientUpdateSchema(**body)
        except ValidationError as e:
            errors = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]
            return error_response(400, f'Validação falhou: {", ".join(errors)}')

        # ✅ CONTEXT MANAGER
        with db as database:
            client = database.execute_query_one(
                "SELECT id, created_by FROM public.clients WHERE id = %s",
                (client_id,)
            )

            if not client:
                return error_response(404, 'Cliente não encontrado')

            # ✅ VALIDAR ACESSO (Anti-IDOR)
            if not validate_tenant_access(user['user_id'], client['created_by'], database):
                logger.error(json.dumps({
                    "event": "TENANT_VIOLATION_ATTEMPT",
                    "user_id": user['user_id'],
                    "client_id": client_id,
                    "action": "update_client"
                }))
                return error_response(403, 'Acesso negado')

            fields = []
            values = []

            if client_data.name:
                fields.append('name = %s')
                values.append(client_data.name)

            if client_data.email:
                fields.append('email = %s')
                values.append(client_data.email)

            if client_data.phone:
                fields.append('phone = %s')
                values.append(client_data.phone)

            if client_data.type:
                fields.append('type = %s')
                values.append(client_data.type)

            if not fields:
                return error_response(400, 'Nenhum campo para atualizar')

            fields.append('updated_at = %s')
            values.append(get_timestamp())
            values.append(client_id)

            query = f"UPDATE public.clients SET {', '.join(fields)} WHERE id = %s"
            database.execute_update(query, tuple(values))

        logger.info(json.dumps({
            "event": "CLIENT_UPDATED",
            "client_id": client_id,
            "user_id": user['user_id'],
            "fields": list(body.keys())
        }))

        return success_response(200, 'Cliente atualizado com sucesso')

    except Exception as e:
        logger.error(json.dumps({
            "event": "CLIENT_UPDATE_ERROR",
            "client_id": event.get('pathParameters', {}).get('clientId'),
            "reason": str(e)
        }))
        return error_response(500, str(e))

@require_user
def delete_client(event, context):
    """
    Deletar cliente (PROTEGIDO - soft delete)

    DELETE /clients/{clientId}
    Authorization: Bearer TOKEN
    """
    try:
        from src.services.database import db

        user = event['user']
        client_id = event['pathParameters']['clientId']

        # ✅ CONTEXT MANAGER
        with db as database:
            client = database.execute_query_one(
                "SELECT id, created_by FROM public.clients WHERE id = %s",
                (client_id,)
            )

            if not client:
                return error_response(404, 'Cliente não encontrado')

            # ✅ VALIDAR ACESSO (Anti-IDOR)
            if not validate_tenant_access(user['user_id'], client['created_by'], database):
                logger.error(json.dumps({
                    "event": "TENANT_VIOLATION_ATTEMPT",
                    "user_id": user['user_id'],
                    "client_id": client_id,
                    "action": "delete_client"
                }))
                return error_response(403, 'Acesso negado')

            database.execute_update(
                "UPDATE public.clients SET status = %s, updated_at = %s WHERE id = %s",
                ('inactive', get_timestamp(), client_id)
            )

        logger.info(json.dumps({
            "event": "CLIENT_DELETED",
            "client_id": client_id,
            "user_id": user['user_id']
        }))

        return success_response(200, 'Cliente deletado com sucesso')

    except Exception as e:
        logger.error(json.dumps({
            "event": "CLIENT_DELETE_ERROR",
            "client_id": event.get('pathParameters', {}).get('clientId'),
            "reason": str(e)
        }))
        return error_response(500, str(e))
