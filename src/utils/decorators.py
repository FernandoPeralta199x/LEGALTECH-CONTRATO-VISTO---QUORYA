"""Decorators para handlers Lambda"""
import json
import logging
from functools import wraps
from src.utils.exceptions import AppError, error_response
from src.services.audit import log_access

logger = logging.getLogger()

def handle_errors(f):
    """Decorator para tratamento de erros"""
    @wraps(f)
    def decorated_function(event, context):
        try:
            return f(event, context)
        except AppError as e:
            logger.error(f"AppError: {e.error_code} - {e.message}")
            return error_response(e)
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}", exc_info=True)
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': 'INTERNAL_SERVER_ERROR',
                    'message': 'Erro interno do servidor'
                }),
                'headers': {'Content-Type': 'application/json'}
            }
    return decorated_function

def validate_request(schema_class):
    """Decorator para validar request contra schema Pydantic"""
    def decorator(f):
        @wraps(f)
        def decorated_function(event, context):
            try:
                body = json.loads(event.get('body', '{}'))
                validated_data = schema_class(**body)
                event['validated_body'] = validated_data
                return f(event, context)
            except Exception as e:
                from src.utils.exceptions import ValidationError
                logger.error(f"Validation error: {str(e)}")
                raise ValidationError(str(e))
        return decorated_function
    return decorator

def require_auth(f):
    """Decorator para exigir autenticação"""
    @wraps(f)
    def decorated_function(event, context):
        from src.utils.auth import verify_token
        from src.utils.exceptions import AuthenticationError
        
        headers = event.get('headers', {})
        auth_header = headers.get('Authorization', '')
        
        if not auth_header.startswith('Bearer '):
            raise AuthenticationError()
        
        token = auth_header.replace('Bearer ', '')
        user_data = verify_token(token)
        
        if not user_data:
            raise AuthenticationError("Token inválido")
        
        event['user'] = user_data
        
        # Log de acesso
        try:
            from src.services.database import db
            db.connect()
            log_access(db, user_data['user_id'], 'access', event['httpMethod'], event['path'])
            db.disconnect()
        except:
            pass
        
        return f(event, context)
    
    return decorated_function

def require_role(*allowed_roles):
    """Decorator para exigir role específico"""
    def decorator(f):
        @wraps(f)
        def decorated_function(event, context):
            from src.utils.auth import verify_token
            from src.utils.exceptions import AuthenticationError, AuthorizationError
            
            headers = event.get('headers', {})
            auth_header = headers.get('Authorization', '')
            
            if not auth_header.startswith('Bearer '):
                raise AuthenticationError()
            
            token = auth_header.replace('Bearer ', '')
            user_data = verify_token(token)
            
            if not user_data:
                raise AuthenticationError("Token inválido")
            
            if user_data.get('role') not in allowed_roles:
                raise AuthorizationError(f"Role {allowed_roles} requerida")
            
            event['user'] = user_data
            return f(event, context)
        
        return decorated_function
    
    return decorator

def log_request(f):
    """Decorator para logar requisições"""
    @wraps(f)
    def decorated_function(event, context):
        logger.info(f"Request: {event.get('httpMethod')} {event.get('path')}")
        if 'user' in event:
            logger.info(f"User: {event['user'].get('user_id')}")
        
        result = f(event, context)
        
        logger.info(f"Response: {result.get('statusCode')}")
        return result
    
    return decorated_function
