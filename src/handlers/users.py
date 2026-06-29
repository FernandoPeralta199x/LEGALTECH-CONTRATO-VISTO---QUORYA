import json
import logging
import bcrypt
import secrets
from datetime import datetime, timedelta
from pydantic import ValidationError

from src.utils.helpers import success_response, error_response, generate_uuid, get_timestamp
from src.schemas.user_schemas import (
    UserLoginSchema,
    UserSignupSchema,
    ForgotPasswordSchema,
    ResetPasswordSchema
)
from src.utils.safety import enforce_production_safety

# 🚀 EXECUTA IMEDIATAMENTE NO COLD START (Fase Init da Lambda)
# Se falhar aqui, o container morre antes de expor qualquer dado!
enforce_production_safety()

logger = logging.getLogger()

# ============================================================================
# FUNÇÕES AUXILIARES DE CRIPTOGRAFIA
# ============================================================================

def hash_password(password: str) -> str:
    """Hash uma senha com bcrypt (rounds=12 para segurança)"""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password: str, password_hash: str) -> bool:
    """Verifica se a senha corresponde ao hash bcrypt"""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception as e:
        logger.error(json.dumps({
            "event": "PASSWORD_VERIFY_ERROR",
            "reason": str(e)
        }))
        return False

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

def create_user(event, context):
    """
    Criar novo usuário (PÚBLICO - sem autenticação necessária)

    POST /users
    {
      "email": "novo@email.com",
      "password": "Senha@123",
      "name": "João",
      "role": "analyst"
    }
    """
    try:
        from src.services.database import db

        body = json.loads(event.get('body', '{}'))

        # ✅ VALIDAR COM PYDANTIC
        try:
            user_data = UserSignupSchema(**body)
        except ValidationError as e:
            errors = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]
            return error_response(400, f'Validação falhou: {", ".join(errors)}')

        # ✅ CONTEXT MANAGER GARANTE DESCONEXÃO
        with db as database:
            existing = database.execute_query_one(
                "SELECT id FROM public.users WHERE email = %s",
                (user_data.email,)
            )

            if existing:
                return error_response(409, 'Email já cadastrado')

            # ✅ HASH COM BCRYPT
            password_hash = hash_password(user_data.password)

            user_id = generate_uuid()
            created_at = get_timestamp()

            query = """
            INSERT INTO public.users (id, email, password_hash, name, role, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """

            database.execute_update(query, (
                user_id,
                user_data.email,
                password_hash,
                user_data.name,
                user_data.role,
                'active',
                created_at,
                created_at
            ))

        logger.info(json.dumps({
            "event": "USER_CREATED",
            "user_id": user_id,
            "email": user_data.email,
            "role": user_data.role
        }))

        return success_response(201, 'Usuário criado com sucesso', {
            'user_id': user_id,
            'email': user_data.email,
            'role': user_data.role
        })

    except Exception as e:
        logger.error(json.dumps({
            "event": "USER_CREATE_ERROR",
            "reason": str(e)
        }))
        return error_response(500, f'Erro ao criar usuário: {str(e)}')

def login(event, context):
    """
    Login (PÚBLICO - sem autenticação necessária)

    POST /users/login
    {
      "email": "admin@test.com",
      "password": "Admin@12345"
    }
    """
    try:
        from src.services.database import db
        import jwt
        import os

        body = json.loads(event.get('body', '{}'))

        # ✅ VALIDAR COM PYDANTIC
        try:
            login_data = UserLoginSchema(**body)
        except ValidationError as e:
            errors = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]
            return error_response(400, f'Validação falhou: {", ".join(errors)}')

        # ✅ CONTEXT MANAGER
        with db as database:
            user = database.execute_query_one(
                "SELECT id, email, password_hash, role FROM public.users WHERE email = %s AND status = %s",
                (login_data.email, 'active')
            )

            if not user:
                logger.warning(json.dumps({
                    "event": "AUTH_LOGIN_FAILED",
                    "email": login_data.email,
                    "reason": "User not found"
                }))
                return error_response(401, 'Email ou senha inválidos')

            # ✅ VERIFICAR COM BCRYPT
            if not verify_password(login_data.password, user['password_hash']):
                logger.warning(json.dumps({
                    "event": "AUTH_LOGIN_FAILED",
                    "email": login_data.email,
                    "reason": "Invalid password"
                }))
                return error_response(401, 'Email ou senha inválidos')

            token_data = {
                'user_id': str(user['id']),
                'email': user['email'],
                'role': user['role']
            }

            secret_key = os.getenv('JWT_SECRET_KEY', 'sua-chave-secreta-aqui')
            token = jwt.encode(token_data, secret_key, algorithm='HS256')

            logger.info(json.dumps({
                "event": "AUTH_LOGIN_SUCCESS",
                "user_id": str(user['id']),
                "email": user['email']
            }))

        return success_response(200, 'Login bem-sucedido', {
            'token': token,
            'user_id': str(user['id']),
            'email': user['email'],
            'role': user['role']
        })

    except Exception as e:
        logger.error(json.dumps({
            "event": "AUTH_LOGIN_ERROR",
            "reason": str(e)
        }))
        return error_response(500, f'Erro no login: {str(e)}')

def forgot_password(event, context):
    """
    Recuperar senha (PÚBLICO)

    POST /users/forgot-password
    {"email": "user@email.com"}
    """
    try:
        from src.services.database import db
        from src.services.email import email_service

        body = json.loads(event.get('body', '{}'))

        # ✅ VALIDAR COM PYDANTIC
        try:
            data = ForgotPasswordSchema(**body)
        except ValidationError as e:
            errors = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]
            return error_response(400, f'Validação falhou: {", ".join(errors)}')

        # ✅ CONTEXT MANAGER
        with db as database:
            user = database.execute_query_one(
                "SELECT id, name FROM public.users WHERE email = %s",
                (data.email,)
            )

            if not user:
                # Resposta genérica para não vazar se o email existe
                return success_response(200, 'Se o email existir, um link será enviado')

            # ✅ GERAR TOKEN SEGURO
            reset_token = secrets.token_urlsafe(32)
            expires_at = (datetime.utcnow() + timedelta(hours=1)).isoformat()

            query = """
            INSERT INTO public.password_resets (user_id, token, expires_at)
            VALUES (%s, %s, %s)
            """

            database.execute_update(query, (
                user['id'],
                reset_token,
                expires_at
            ))

            # ✅ ENVIAR EMAIL COM LINK
            email_sent = email_service.send_reset_password_email(
                to_email=data.email,
                reset_token=reset_token,
                user_name=user['name']
            )

            if not email_sent:
                logger.warning(json.dumps({
                    "event": "PASSWORD_RESET_EMAIL_FAILED",
                    "user_id": str(user['id'])
                }))

        logger.info(json.dumps({
            "event": "PASSWORD_RESET_REQUESTED",
            "user_id": str(user['id'])
        }))

        return success_response(200, 'Se o email existir, um link será enviado')

    except Exception as e:
        logger.error(json.dumps({
            "event": "PASSWORD_RESET_REQUEST_ERROR",
            "reason": str(e)
        }))
        return error_response(500, str(e))

def reset_password(event, context):
    """
    Resetar senha (PÚBLICO)

    POST /users/reset-password
    {"token": "token-recebido-por-email", "password": "NovaSenha@123"}
    """
    try:
        from src.services.database import db

        body = json.loads(event.get('body', '{}'))

        # ✅ VALIDAR COM PYDANTIC
        try:
            data = ResetPasswordSchema(**body)
        except ValidationError as e:
            errors = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]
            return error_response(400, f'Validação falhou: {", ".join(errors)}')

        # ✅ CONTEXT MANAGER
        with db as database:
            reset = database.execute_query_one(
                """SELECT user_id FROM public.password_resets
                   WHERE token = %s AND expires_at > NOW()""",
                (data.token,)
            )

            if not reset:
                return error_response(400, 'Link de reset inválido ou expirado')

            # ✅ HASH COM BCRYPT
            password_hash = hash_password(data.password)

            database.execute_update(
                "UPDATE public.users SET password_hash = %s, updated_at = %s WHERE id = %s",
                (password_hash, get_timestamp(), reset['user_id'])
            )

            # ✅ DELETAR TOKEN APÓS USO
            database.execute_update(
                "DELETE FROM public.password_resets WHERE token = %s",
                (data.token,)
            )

        logger.info(json.dumps({
            "event": "PASSWORD_RESET_SUCCESS",
            "user_id": str(reset['user_id'])
        }))

        return success_response(200, 'Senha resetada com sucesso')

    except Exception as e:
        logger.error(json.dumps({
            "event": "PASSWORD_RESET_ERROR",
            "reason": str(e)
        }))
        return error_response(500, str(e))

@require_user
def get_user(event, context):
    """
    Obter usuário (PROTEGIDO - JWT Authorizer valida no API Gateway)

    GET /users/{userId}
    Authorization: Bearer TOKEN
    """
    try:
        from src.services.database import db

        user = event['user']  # ← Adicionado pelo decorator
        user_id = event['pathParameters']['userId']

        # ✅ VALIDAR ACESSO
        if user_id != user['user_id'] and user['role'] != 'admin':
            logger.warning(json.dumps({
                "event": "RBAC_VIOLATION_ATTEMPT",
                "user_id": user['user_id'],
                "requested_action": "get_user",
                "target_user_id": user_id
            }))
            return error_response(403, 'Acesso negado')

        # ✅ CONTEXT MANAGER
        with db as database:
            query = "SELECT id, email, name, role, status, created_at FROM public.users WHERE id = %s"
            user_data = database.execute_query_one(query, (user_id,))

            if not user_data:
                return error_response(404, 'Usuário não encontrado')

            return success_response(200, 'Usuário encontrado', dict(user_data))

    except Exception as e:
        logger.error(json.dumps({
            "event": "USER_GET_ERROR",
            "target_user_id": event.get('pathParameters', {}).get('userId'),
            "reason": str(e)
        }))
        return error_response(500, str(e))

@require_user
def list_users(event, context):
    """
    Listar usuários (PROTEGIDO - apenas admin)

    GET /users
    Authorization: Bearer TOKEN
    """
    try:
        from src.services.database import db

        user = event['user']

        # ✅ VALIDAR ROLE
        if user['role'] != 'admin':
            logger.warning(json.dumps({
                "event": "RBAC_VIOLATION_ATTEMPT",
                "user_id": user['user_id'],
                "requested_action": "list_users"
            }))
            return error_response(403, 'Acesso negado - apenas admin')

        # ✅ CONTEXT MANAGER
        with db as database:
            query = "SELECT id, email, name, role, status, created_at FROM public.users ORDER BY created_at DESC LIMIT 100"
            users = database.execute_query(query)

            return success_response(200, f'{len(users)} usuários encontrados', [dict(u) for u in users])

    except Exception as e:
        logger.error(json.dumps({
            "event": "USER_LIST_ERROR",
            "reason": str(e)
        }))
        return error_response(500, str(e))

@require_user
def update_user(event, context):
    """
    Atualizar usuário (PROTEGIDO)

    PUT /users/{userId}
    Authorization: Bearer TOKEN
    """
    try:
        from src.services.database import db

        user = event['user']
        user_id = event['pathParameters']['userId']
        body = json.loads(event.get('body', '{}'))

        # ✅ VALIDAR ACESSO
        if user_id != user['user_id'] and user['role'] != 'admin':
            logger.warning(json.dumps({
                "event": "RBAC_VIOLATION_ATTEMPT",
                "user_id": user['user_id'],
                "requested_action": "update_user",
                "target_user_id": user_id
            }))
            return error_response(403, 'Acesso negado')

        # ✅ CONTEXT MANAGER
        with db as database:
            fields = []
            values = []

            if 'name' in body and body['name']:
                fields.append('name = %s')
                values.append(body['name'])

            if user['role'] == 'admin':
                if 'role' in body and body['role']:
                    fields.append('role = %s')
                    values.append(body['role'])

                if 'status' in body and body['status']:
                    fields.append('status = %s')
                    values.append(body['status'])

            if not fields:
                return error_response(400, 'Nenhum campo para atualizar')

            fields.append('updated_at = %s')
            values.append(get_timestamp())
            values.append(user_id)

            query = f"UPDATE public.users SET {', '.join(fields)} WHERE id = %s"
            database.execute_update(query, tuple(values))

        logger.info(json.dumps({
            "event": "USER_UPDATED",
            "user_id": user['user_id'],
            "target_user_id": user_id,
            "fields": list(body.keys())
        }))

        return success_response(200, 'Usuário atualizado com sucesso')

    except Exception as e:
        logger.error(json.dumps({
            "event": "USER_UPDATE_ERROR",
            "target_user_id": event.get('pathParameters', {}).get('userId'),
            "reason": str(e)
        }))
        return error_response(500, str(e))

@require_user
def delete_user(event, context):
    """
    Deletar usuário (PROTEGIDO - apenas admin)

    DELETE /users/{userId}
    Authorization: Bearer TOKEN
    """
    try:
        from src.services.database import db

        user = event['user']
        user_id = event['pathParameters']['userId']

        # ✅ VALIDAR ROLE
        if user['role'] != 'admin':
            logger.error(json.dumps({
                "event": "RBAC_VIOLATION_ATTEMPT",
                "user_id": user['user_id'],
                "requested_action": "delete_user",
                "target_user_id": user_id
            }))
            return error_response(403, 'Acesso negado - apenas admin')

        # ✅ CONTEXT MANAGER
        with db as database:
            database.execute_update(
                "UPDATE public.users SET status = %s, updated_at = %s WHERE id = %s",
                ('inactive', get_timestamp(), user_id)
            )

        logger.info(json.dumps({
            "event": "USER_DELETED",
            "user_id": user['user_id'],
            "target_user_id": user_id
        }))

        return success_response(200, 'Usuário deletado com sucesso')

    except Exception as e:
        logger.error(json.dumps({
            "event": "USER_DELETE_ERROR",
            "target_user_id": event.get('pathParameters', {}).get('userId'),
            "reason": str(e)
        }))
        return error_response(500, str(e))
