"""Exceções personalizadas da aplicação"""

class AppError(Exception):
    """Exceção base da aplicação"""
    def __init__(self, message: str, status_code: int = 500, error_code: str = None):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code or "INTERNAL_ERROR"
        super().__init__(self.message)

class ValidationError(AppError):
    """Erro de validação"""
    def __init__(self, message: str, field: str = None):
        self.field = field
        super().__init__(message, 400, "VALIDATION_ERROR")

class AuthenticationError(AppError):
    """Erro de autenticação"""
    def __init__(self, message: str = "Autenticação necessária"):
        super().__init__(message, 401, "AUTHENTICATION_ERROR")

class AuthorizationError(AppError):
    """Erro de autorização"""
    def __init__(self, message: str = "Acesso negado"):
        super().__init__(message, 403, "AUTHORIZATION_ERROR")

class NotFoundError(AppError):
    """Recurso não encontrado"""
    def __init__(self, resource: str, resource_id: str = None):
        message = f"{resource} não encontrado"
        if resource_id:
            message += f": {resource_id}"
        super().__init__(message, 404, "NOT_FOUND")

class ConflictError(AppError):
    """Conflito (ex: email já existe)"""
    def __init__(self, message: str):
        super().__init__(message, 409, "CONFLICT")

class DatabaseError(AppError):
    """Erro de banco de dados"""
    def __init__(self, message: str):
        super().__init__(message, 500, "DATABASE_ERROR")

def error_response(error: AppError):
    """Converter exceção para resposta HTTP"""
    import json
    return {
        'statusCode': error.status_code,
        'body': json.dumps({
            'error': error.error_code,
            'message': error.message
        }),
        'headers': {'Content-Type': 'application/json'}
    }
