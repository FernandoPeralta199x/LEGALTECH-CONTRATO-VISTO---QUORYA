"""
Response Helper Module
Funções padronizadas para retornar respostas HTTP com CORS
"""

import json
from datetime import datetime, timezone
from uuid import uuid4

# Headers CORS padrão
CORS_HEADERS = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS,HEAD',
    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token,X-Amz-User-Agent,X-Amzn-Trace-Id'
}


def success_response(status_code=200, message="Sucesso", data=None):
    """
    Retorna resposta de sucesso com headers CORS
    
    Args:
        status_code (int): HTTP status code (200, 201, etc)
        message (str): Mensagem de sucesso
        data (dict/list): Dados para retornar
    
    Returns:
        dict: Resposta formatada para Lambda
    """
    body = {
        'message': message,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    
    if data is not None:
        body['data'] = data
    
    return {
        'statusCode': status_code,
        'headers': CORS_HEADERS,
        'body': json.dumps(body)
    }


def error_response(status_code=400, error_message="Erro na requisição"):
    """
    Retorna resposta de erro com headers CORS
    
    Args:
        status_code (int): HTTP status code (400, 401, 404, 500, etc)
        error_message (str): Mensagem de erro
    
    Returns:
        dict: Resposta formatada para Lambda
    """
    return {
        'statusCode': status_code,
        'headers': CORS_HEADERS,
        'body': json.dumps({
            'error': str(error_message),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    }


def options_response():
    """
    Retorna resposta para requisições OPTIONS (CORS preflight)
    
    Returns:
        dict: Resposta vazia com headers CORS
    """
    return {
        'statusCode': 200,
        'headers': CORS_HEADERS,
        'body': json.dumps({})
    }


def generate_uuid():
    """Gera um UUID v4 como string"""
    return str(uuid4())


def get_timestamp():
    """Retorna timestamp ISO 8601 em UTC"""
    return datetime.now(timezone.utc).isoformat()


def format_db_result(result):
    """
    Converte resultado do banco de dados para dict serializável
    
    Args:
        result: Resultado do banco (pode ser dict-like ou tuple)
    
    Returns:
        dict: Resultado formatado
    """
    if result is None:
        return None
    
    # Se já é dict, apenas converter tipos não-serializáveis
    if isinstance(result, dict):
        return {k: str(v) if hasattr(v, 'isoformat') else v 
                for k, v in result.items()}
    
    # Se é tuple/row do banco, converter para dict
    if hasattr(result, 'keys'):
        return {k: str(v) if hasattr(v, 'isoformat') else v 
                for k, v in zip(result.keys(), result)}
    
    return result


def paginate(items, page=1, per_page=50):
    """
    Pagina uma lista de itens
    
    Args:
        items (list): Lista de itens
        page (int): Número da página (começa em 1)
        per_page (int): Itens por página
    
    Returns:
        tuple: (items_paginados, total, página_atual, total_páginas)
    """
    total = len(items)
    total_pages = (total + per_page - 1) // per_page
    
    start = (page - 1) * per_page
    end = start + per_page
    
    return items[start:end], total, page, total_pages
