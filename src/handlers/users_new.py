import json
import logging
import bcrypt
from datetime import datetime, timedelta
from uuid import uuid4
from src.utils.helpers import success_response, error_response, generate_uuid, get_timestamp

logger = logging.getLogger()

def hash_password(password: str) -> str:
    """Hash uma senha com bcrypt"""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password: str, hash_password: str) -> bool:
    """Verifica se a senha corresponde ao hash"""
    return bcrypt.checkpw(password.encode('utf-8'), hash_password.encode('utf-8'))

def create_user(event, context):
    """Criar novo usuário"""
    try:
        from src.services.database import db
        
        body = json.loads(event.get('body', '{}'))
        
        required = ['email', 'password', 'name', 'role']
        for field in required:
            if field not in body:
                return error_response(400, f'Campo obrigatório: {field}')
        
        db.connect()
        
        existing = db.execute_query_one(
            "SELECT id FROM public.users WHERE email = %s",
            (body['email'],)
        )
        
        if existing:
            db.disconnect()
            return error_response(409, 'Email já cadastrado')
        
        # ✅ HASH COM BCRYPT (não SHA256!)
        password_hash = hash_password(body['password'])
        
        user_id = generate_uuid()
        created_at = get_timestamp()
        
        query = """
        INSERT INTO public.users (id, email, password_hash, name, role, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        db.execute_update(query, (
            user_id,
            body['email'],
            password_hash,
            body['name'],
            body['role'],
            'active',
            created_at,
            created_at
        ))
        
        db.disconnect()
        
        return success_response(201, 'Usuário criado com sucesso', {
            'user_id': user_id,
            'email': body['email'],
            'role': body['role']
        })
    
    except Exception as e:
        logger.error(f"Erro ao criar usuário: {str(e)}")
        return error_response(500, f'Erro ao criar usuário: {str(e)}')

def login(event, context):
    """Login do usuário"""
    try:
        from src.services.database import db
        import jwt
        import os
        
        body = json.loads(event.get('body', '{}'))
        
        if 'email' not in body or 'password' not in body:
            return error_response(400, 'Email e senha obrigatórios')
        
        db.connect()
        
        user = db.execute_query_one(
            "SELECT id, email, password_hash, role FROM public.users WHERE email = %s AND status = %s",
            (body['email'], 'active')
        )
        
        db.disconnect()
        
        if not user:
            return error_response(401, 'Email ou senha inválidos')
        
        # ✅ VERIFICAR COM BCRYPT
        if not verify_password(body['password'], user['password_hash']):
            return error_response(401, 'Email ou senha inválidos')
        
        token_data = {
            'user_id': str(user['id']),
            'email': user['email'],
            'role': user['role']
        }
        
        secret_key = os.getenv('JWT_SECRET_KEY', 'sua-chave-secreta-aqui')
        token = jwt.encode(token_data, secret_key, algorithm='HS256')
        
        return success_response(200, 'Login bem-sucedido', {
            'token': token,
            'user_id': str(user['id']),
            'email': user['email'],
            'role': user['role']
        })
    
    except Exception as e:
        logger.error(f"Erro no login: {str(e)}")
        return error_response(500, f'Erro no login: {str(e)}')

def forgot_password(event, context):
    """Solicitar reset de senha"""
    try:
        from src.services.database import db
        import secrets
        
        body = json.loads(event.get('body', '{}'))
        email = body.get('email')
        
        if not email:
            return error_response(400, 'Email é obrigatório')
        
        db.connect()
        
        user = db.execute_query_one(
            "SELECT id FROM public.users WHERE email = %s",
            (email,)
        )
        
        if not user:
            # Não revelar se email existe (segurança)
            db.disconnect()
            return success_response(200, 'Se o email existir, um link de reset será enviado')
        
        # Gerar token de reset (válido por 1 hora)
        reset_token = secrets.token_urlsafe(32)
        expires_at = (datetime.now() + timedelta(hours=1)).isoformat()
        
        # Salvar token no banco
        query = """
        INSERT INTO public.password_resets (user_id, token, expires_at, created_at)
        VALUES (%s, %s, %s, %s)
        """
        
        db.execute_update(query, (
            user['id'],
            reset_token,
            expires_at,
            get_timestamp()
        ))
        
        db.disconnect()
        
        # TODO: Enviar email com link
        # email_service.send_reset_email(email, reset_token)
        
        return success_response(200, 'Email de reset enviado')
    
    except Exception as e:
        logger.error(f"Erro: {str(e)}")
        return error_response(500, str(e))

def reset_password(event, context):
    """Resetar senha com token"""
    try:
        from src.services.database import db
        
        body = json.loads(event.get('body', '{}'))
        token = body.get('token')
        new_password = body.get('password')
        
        if not token or not new_password:
            return error_response(400, 'Token e senha são obrigatórios')
        
        db.connect()
        
        # Verificar token válido e não expirado
        reset = db.execute_query_one(
            """SELECT user_id FROM public.password_resets 
               WHERE token = %s AND expires_at > NOW()""",
            (token,)
        )
        
        if not reset:
            db.disconnect()
            return error_response(400, 'Link de reset inválido ou expirado')
        
        # Atualizar senha
        password_hash = hash_password(new_password)
        
        query = """
        UPDATE public.users 
        SET password_hash = %s, updated_at = %s 
        WHERE id = %s
        """
        
        db.execute_update(query, (
            password_hash,
            get_timestamp(),
            reset['user_id']
        ))
        
        # Deletar token usado
        db.execute_update(
            "DELETE FROM public.password_resets WHERE token = %s",
            (token,)
        )
        
        db.disconnect()
        
        return success_response(200, 'Senha resetada com sucesso')
    
    except Exception as e:
        logger.error(f"Erro: {str(e)}")
        return error_response(500, str(e))
