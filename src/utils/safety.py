# src/utils/safety.py
import logging
import os

logger = logging.getLogger()

# Valores padrão/inseguros que NÃO podem ir para staging/produção.
_INSECURE_SECRETS = {"", "sua-chave-secreta", "sua-chave-secreta-aqui"}


def enforce_production_safety():
    """Trava fail-closed para Serverless: bloqueia o boot do container se houver
    configuração insegura em staging/produção.

    Usa ``ENVIRONMENT`` (nome real definido no ``serverless.yml``).
    """
    environment = os.getenv("ENVIRONMENT", "local").lower()
    if environment not in ("staging", "production", "prod"):
        return

    violations = []
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret or secret in _INSECURE_SECRETS:
        violations.append("JWT_SECRET_KEY ausente ou com valor padrão inseguro")
    if os.getenv("AI_ANALYSIS_BACKEND") == "mock":
        violations.append("AI_ANALYSIS_BACKEND=mock em ambiente produtivo")
    if os.getenv("EMAIL_BACKEND", "log") in ("log", "mock"):
        violations.append("EMAIL_BACKEND=log/mock em ambiente produtivo (sem envio real)")
    if os.getenv("STORAGE_BACKEND", "local") in ("local", "mock"):
        violations.append("STORAGE_BACKEND=local/mock em ambiente produtivo (sem S3 real)")
    if os.getenv("EMBEDDINGS_BACKEND", "mock") == "mock":
        violations.append("EMBEDDINGS_BACKEND=mock em ambiente produtivo (embeddings falsos)")

    if violations:
        message = f"BOOT BLOQUEADO ({environment}): " + "; ".join(violations)
        logger.critical(message)
        raise RuntimeError(message)
