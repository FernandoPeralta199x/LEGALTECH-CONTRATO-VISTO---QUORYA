import json
import jwt
import os
import logging

from src.utils.safety import enforce_production_safety

# 🚀 EXECUTA IMEDIATAMENTE NO COLD START (Fase Init da Lambda)
# Se falhar aqui, o container morre antes de expor qualquer dado!
enforce_production_safety()

logger = logging.getLogger()

JWT_SECRET = os.getenv('JWT_SECRET_KEY')
if not JWT_SECRET:
    raise RuntimeError("Variável de ambiente JWT_SECRET_KEY não configurada. Boot interrompido.")
    
def authorize(event, context):
    """
    JWT Authorizer para API Gateway
    Valida token ANTES da Lambda de negócio ser executada
    
    Input:
    {
      "type": "TOKEN",
      "authorizationToken": "Bearer eyJhbGc...",
      "methodArn": "arn:aws:execute-api:..."
    }
    
    Output (Sucesso):
    {
      "principalId": "user-id",
      "policyDocument": { ... Allow ... },
      "context": { user_id, email, role }
    }
    
    Output (Erro):
    {
      "principalId": "user",
      "policyDocument": { ... Deny ... },
      "context": { error: "..." }
    }
    """
    
    token = event.get('authorizationToken', '')
    
    # ✅ Validar formato do token
    if not token.startswith('Bearer '):
        logger.warning(json.dumps({
            "event": "AUTH_TOKEN_MALFORMED",
            "reason": "Token não começa com 'Bearer '",
            "methodArn": event.get('methodArn')
        }))
        return deny_access('Malformed token')
    
    # Extrair token sem "Bearer "
    token = token.replace('Bearer ', '').strip()
    
    try:
        # ✅ VALIDAR JWT NO API GATEWAY (antes da Lambda de negócio)
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'],
                             options={"require": ["exp"]})  # rejeita token sem expiração
        
        organization_id = payload['organization_id']  # KeyError → token inválido → deny

        logger.info(json.dumps({
            "event": "AUTH_TOKEN_VALID",
            "user_id": payload['user_id'],
            "role": payload['role'],  # sem email (PII)
            "methodArn": event.get('methodArn')
        }))

        # ✅ Se OK, retornar política de PERMISSÃO + dados do usuário
        return {
            'principalId': payload['user_id'],  # ID único (obrigatório)
            'policyDocument': {
                'Version': '2012-10-17',
                'Statement': [
                    {
                        'Action': 'execute-api:Invoke',
                        'Effect': 'Allow',  # ← Permite acesso
                        'Resource': event['methodArn']
                    }
                ]
            },
            'context': {  # sem email (menos PII propagada aos handlers)
                'user_id': payload['user_id'],
                'role': payload['role'],
                'organization_id': organization_id,
                # AUTH-02: propaga o iat (epoch) p/ load_active_caller revogar tokens
                # anteriores ao último reset de senha. API Gateway serializa como string.
                'iat': payload.get('iat')
            }
        }
    
    except jwt.ExpiredSignatureError:
        logger.warning(json.dumps({
            "event": "AUTH_TOKEN_EXPIRED",
            "methodArn": event.get('methodArn')
        }))
        return deny_access('Token expirado')
    
    except jwt.InvalidTokenError as e:
        logger.warning(json.dumps({
            "event": "AUTH_TOKEN_INVALID",
            "error": type(e).__name__,
            "methodArn": event.get('methodArn')
        }))
        return deny_access('Token inválido')

    except Exception as e:
        logger.error(json.dumps({
            "event": "AUTH_ERROR",
            "error": type(e).__name__,
            "methodArn": event.get('methodArn')
        }))
        return deny_access('Erro interno na autorização')

def deny_access(message):
    """
    Negar acesso. Uma policy `Deny` faz o API Gateway responder 403 Forbidden
    (401 só ocorre quando o authorizer lança a exceção literal "Unauthorized").
    A Lambda de negócio NUNCA executa.
    """
    return {
        'principalId': 'user',
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Action': 'execute-api:Invoke',
                    'Effect': 'Deny',  # ← Nega acesso
                    'Resource': '*'
                }
            ]
        },
        'context': {
            'error': message
        }
    }
