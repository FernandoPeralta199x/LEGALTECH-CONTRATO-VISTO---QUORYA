# src/utils/safety.py
import logging
import os

logger = logging.getLogger()

# Valores padrão/inseguros que NÃO podem ir para staging/produção.
_INSECURE_SECRETS = {"", "sua-chave-secreta", "sua-chave-secreta-aqui",
                     "troque-este-segredo-local"}  # placeholder do .env.example

# Entropia mínima do JWT_SECRET_KEY em produção. HS256 é simétrico: um segredo
# curto ou repetitivo (ex.: "x"*40) é força-brutável offline e permite forjar
# tokens de admin de qualquer organização (M3). Não basta não ser placeholder.
_MIN_SECRET_LEN = 32
_MIN_SECRET_DISTINCT = 8

# Backends que, FORA de dev, DEVEM estar exatamente no valor "real" canônico. Um
# valor ausente, "mock" ou digitado errado ("Real"/"prod"/"textract") é
# fail-closed e bloqueia o boot. Isso fecha DOIS buracos:
#   - A1: os 5 bureaus de triagem/enriquecimento não tinham NENHUMA trava — um
#     deploy produtivo podia bootar em mock e a triagem devolvia laudo fabricado.
#   - M1: comparação exata contra "mock"/lista permitia o fail-OPEN por typo
#     ("Real" != "mock" => passava o boot, mas a fábrica servia o Mock).
# O valor exigido espelha EXATAMENTE o que cada fábrica reconhece como real
# (ai/ocr/bureaus == "real"; email == "ses"; storage == "s3"; embeddings ==
# "openai"), de modo que "boot liberado" implica "fábrica seleciona o real".
_REQUIRED_REAL_BACKENDS = (
    # (env, valor_real_exato, rótulo)
    ("AI_ANALYSIS_BACKEND", "real", "IA de análise"),
    ("EMAIL_BACKEND", "ses", "envio de e-mail (SES)"),
    ("STORAGE_BACKEND", "s3", "armazenamento S3"),
    ("EMBEDDINGS_BACKEND", "openai", "embeddings"),
    ("OCR_BACKEND", "real", "OCR"),
    ("SERASA_BACKEND", "real", "bureau Serasa (crédito)"),
    ("PROCON_BACKEND", "real", "bureau Procon (reclamações)"),
    ("ESCAVADOR_BACKEND", "real", "bureau Escavador (processos)"),
    ("CNJ_BACKEND", "real", "bureau CNJ (processos)"),
    ("TARGETDATA_BACKEND", "real", "bureau TargetData (cadastro)"),
)

# Apenas estes ambientes são tratados como NÃO-produtivos. Qualquer outro stage
# (prod, prd, staging, homolog, qa, sandbox, ...) é tratado como produtivo
# (fail-safe: um stage desconhecido NÃO deve escapar das travas).
_NON_PROD_ENVS = {"", "local", "dev", "development", "test", "testing"}


def is_production() -> bool:
    """``True`` se o ambiente (``ENVIRONMENT``) é produtivo. Fonte única do
    discriminador dev/prod — usa a MESMA allowlist de ``enforce_production_safety``.
    Fail-safe: qualquer stage fora da allowlist de dev é tratado como produtivo."""
    return os.getenv("ENVIRONMENT", "local").lower() not in _NON_PROD_ENVS


def _is_weak_secret(secret: str) -> bool:
    """Segredo curto ou de baixa variedade de caracteres (ex.: ``"x"*40``)."""
    return len(secret) < _MIN_SECRET_LEN or len(set(secret)) < _MIN_SECRET_DISTINCT


def enforce_production_safety():
    """Trava fail-closed para Serverless: bloqueia o boot do container se houver
    configuração insegura fora de ambientes de desenvolvimento.

    Usa ``ENVIRONMENT`` (nome real definido no ``serverless.yml``). Fail-safe:
    qualquer ambiente que não esteja na allowlist de dev é considerado produtivo.
    """
    environment = os.getenv("ENVIRONMENT", "local").lower()
    if not is_production():
        return

    violations = []

    secret = os.getenv("JWT_SECRET_KEY")
    if not secret or secret in _INSECURE_SECRETS:
        violations.append("JWT_SECRET_KEY ausente ou com valor padrão inseguro")
    elif _is_weak_secret(secret):
        violations.append(
            f"JWT_SECRET_KEY fraco em ambiente produtivo (exige >= {_MIN_SECRET_LEN} "
            f"chars e >= {_MIN_SECRET_DISTINCT} caracteres distintos — HS256 é "
            "força-brutável offline)")

    # A1 + M1: todos os backends externos precisam do valor real EXATO (fail-closed
    # contra ausência, mock e typo). Um único laço mantém a lista sincronizada com
    # as fábricas e impede que um novo backend fique sem trava.
    for env_name, expected, label in _REQUIRED_REAL_BACKENDS:
        if os.getenv(env_name) != expected:
            violations.append(
                f"{env_name} != '{expected}' em ambiente produtivo "
                f"({label} em mock/fabricado ou não configurado)")

    payment_provider = os.getenv("PAYMENT_PROVIDER", "mock")
    payment_mode = os.getenv("PAYMENT_MODE", "mock")
    if payment_provider == "mock" or payment_mode == "mock":
        violations.append("PAYMENT_PROVIDER/PAYMENT_MODE=mock em ambiente produtivo")
    elif not os.getenv("PAYMENT_API_KEY"):
        violations.append("PAYMENT_API_KEY ausente para gateway real (sandbox/live)")

    # SEC-11/SEC-12: o gate de pagamento (triagem/relatório exigem pedido pago) é
    # NO-OP quando PAYMENT_GATE != 'hard'. Fora de dev, esquecê-lo libera operação
    # paga sem pagamento — fail-closed: exigir 'hard'.
    if os.getenv("PAYMENT_GATE", "soft") != "hard":
        violations.append(
            "PAYMENT_GATE != 'hard' em ambiente produtivo "
            "(triagem/relatório liberados sem pagamento)")

    if violations:
        message = f"BOOT BLOQUEADO ({environment}): " + "; ".join(violations)
        logger.critical(message)
        raise RuntimeError(message)
