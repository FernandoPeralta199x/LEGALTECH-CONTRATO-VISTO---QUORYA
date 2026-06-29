import jwt
import os
from functools import wraps
import json

def verify_token(token):
    """Verificar JWT token"""
    try:
        secret_key = os.getenv('JWT_SECRET_KEY', 'sua-chave-secreta-aqui')
        decoded = jwt.decode(token, secret_key, algorithms=['HS256'])
        return decoded
    except jwt.InvalidTokenError:
        return None

def require_auth(f):
    """Decorator para exigir autenticação"""
    @wraps(f)
    def decorated_function(event, context):
        # Extrair token do header
        headers = event.get('headers', {})
        auth_header = headers.get('Authorization', '')
        
        if not auth_header.startswith('Bearer '):
            return {
                'statusCode': 401,
                'body': json.dumps({'error': 'Token não fornecido'}),
                'headers': {'Content-Type': 'application/json'}
            }
        
        token = auth_header.replace('Bearer ', '')
        
        # Verificar token
        user_data = verify_token(token)
        
        if not user_data:
            return {
                'statusCode': 401,
                'body': json.dumps({'error': 'Token inválido'}),
                'headers': {'Content-Type': 'application/json'}
            }
        
        # Adicionar user_data ao event (disponível no handler)
        event['user'] = user_data
        
        return f(event, context)
    
    return decorated_function

def require_role(required_role):
    """Decorator para exigir role específica"""
    def decorator(f):
        @wraps(f)
        def decorated_function(event, context):
            # Extrair token
            headers = event.get('headers', {})
            auth_header = headers.get('Authorization', '')
            
            if not auth_header.startswith('Bearer '):
                return {
                    'statusCode': 401,
                    'body': json.dumps({'error': 'Token não fornecido'}),
                    'headers': {'Content-Type': 'application/json'}
                }
            
            token = auth_header.replace('Bearer ', '')
            user_data = verify_token(token)
            
            if not user_data:
                return {
                    'statusCode': 401,
                    'body': json.dumps({'error': 'Token inválido'}),
                    'headers': {'Content-Type': 'application/json'}
                }
            
            # Verificar role
            if user_data.get('role') != required_role and required_role != 'admin':
                return {
                    'statusCode': 403,
                    'body': json.dumps({'error': f'Role {required_role} requerida'}),
                    'headers': {'Content-Type': 'application/json'}
                }
            
            event['user'] = user_data
            return f(event, context)
        
        return decorated_function
    
    return decorator
