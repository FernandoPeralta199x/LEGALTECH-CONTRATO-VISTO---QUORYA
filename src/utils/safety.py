# src/utils/safety.py
import logging
import os

logger = logging.getLogger()

# Valores padrão/inseguros que NÃO podem ir para staging/produção.
_INSECURE_SECRETS = {"", "sua-chave-secreta", "sua-chave-secreta-aqui",
                     "troque-este-segredo-local"}  # placeholder do .env.example

# Apenas estes ambientes são tratados como NÃO-produtivos. Qualquer outro stage
# (prod, prd, staging, homolog, qa, sandbox, ...) é tratado como produtivo
# (fail-safe: um stage desconhecido NÃO deve escapar das travas).
_NON_PROD_ENVS = {"", "local", "dev", "development", "test", "testing"}


def enforce_production_safety():
    """Trava fail-closed para Serverless: bloqueia o boot do container se houver
    configuração insegura fora de ambientes de desenvolvimento.

    Usa ``ENVIRONMENT`` (nome real definido no ``serverless.yml``). Fail-safe:
    qualquer ambiente que não esteja na allowlist de dev é considerado produtivo.
    """
    environment = os.getenv("ENVIRONMENT", "local").lower()
    if environment in _NON_PROD_ENVS:
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
    # create_ocr_adapter só trata "real" como adapter real; qualquer outro valor
    # (ausente, mock, typo, ou "textract" ainda não implementado) cai no mock.
    # Fail-closed: exigir exatamente "real" em produção.
    if os.getenv("OCR_BACKEND", "mock") != "real":
        violations.append("OCR_BACKEND != 'real' em ambiente produtivo (OCR falso/mock)")
    payment_provider = os.getenv("PAYMENT_PROVIDER", "mock")
    payment_mode = os.getenv("PAYMENT_MODE", "mock")
    if payment_provider == "mock" or payment_mode == "mock":
        violations.append("PAYMENT_PROVIDER/PAYMENT_MODE=mock em ambiente produtivo")
    elif not os.getenv("PAYMENT_API_KEY"):
        violations.append("PAYMENT_API_KEY ausente para gateway real (sandbox/live)")

    if violations:
        message = f"BOOT BLOQUEADO ({environment}): " + "; ".join(violations)
        logger.critical(message)
        raise RuntimeError(message)
