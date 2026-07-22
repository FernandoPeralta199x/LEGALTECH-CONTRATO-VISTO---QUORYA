"""Fase 1 — fail-closed de enforce_production_safety (correções pós-Codex).

enforce_production_safety lê os.getenv em runtime, então basta ajustar o ambiente.
"""
import pytest

from src.utils.safety import enforce_production_safety, is_production


@pytest.mark.parametrize("env", ["", "local", "dev", "development", "test", "testing"])
def test_is_production_false_em_dev(monkeypatch, env):
    monkeypatch.setenv("ENVIRONMENT", env)
    assert is_production() is False


@pytest.mark.parametrize("env", ["production", "prod", "prd", "staging", "qa", "homolog", "sandbox"])
def test_is_production_true_fora_da_allowlist(monkeypatch, env):
    # fail-safe: qualquer stage fora da allowlist de dev é produtivo
    monkeypatch.setenv("ENVIRONMENT", env)
    assert is_production() is True


def test_is_production_default_local(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert is_production() is False  # default "local"


def test_local_does_not_enforce(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    enforce_production_safety()  # não levanta em local


def test_production_blocks_missing_secret(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        enforce_production_safety()


def test_production_blocks_default_secret(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "sua-chave-secreta-aqui")
    with pytest.raises(RuntimeError):
        enforce_production_safety()


def test_unknown_stage_is_treated_as_production(monkeypatch):
    # fail-safe: stage não-dev (ex.: 'prd', 'qa', 'homolog') deve aplicar as travas
    for stage in ("prd", "qa", "homolog", "sandbox"):
        monkeypatch.setenv("ENVIRONMENT", stage)
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError):
            enforce_production_safety()


def test_production_blocks_mock_email_backend(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "um-segredo-forte-de-verdade-0123456789")
    monkeypatch.delenv("AI_ANALYSIS_BACKEND", raising=False)
    monkeypatch.setenv("EMAIL_BACKEND", "log")  # backend sem envio real
    with pytest.raises(RuntimeError):
        enforce_production_safety()


def test_production_blocks_local_storage(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "um-segredo-forte-de-verdade-0123456789")
    monkeypatch.delenv("AI_ANALYSIS_BACKEND", raising=False)
    monkeypatch.setenv("EMAIL_BACKEND", "ses")
    monkeypatch.setenv("STORAGE_BACKEND", "local")  # sem S3 real
    with pytest.raises(RuntimeError):
        enforce_production_safety()


def test_production_blocks_mock_embeddings(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "um-segredo-forte-de-verdade-0123456789")
    monkeypatch.delenv("AI_ANALYSIS_BACKEND", raising=False)
    monkeypatch.setenv("EMAIL_BACKEND", "ses")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "mock")  # embeddings falsos
    with pytest.raises(RuntimeError):
        enforce_production_safety()


def test_production_blocks_mock_ocr(monkeypatch):
    # CVS-005: OCR mock/ausente em producao geraria texto fabricado -> fail-closed
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "um-segredo-forte-de-verdade-0123456789")
    monkeypatch.delenv("AI_ANALYSIS_BACKEND", raising=False)
    monkeypatch.setenv("EMAIL_BACKEND", "ses")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "openai")
    monkeypatch.delenv("OCR_BACKEND", raising=False)  # ausente -> default mock
    with pytest.raises(RuntimeError):
        enforce_production_safety()
    monkeypatch.setenv("OCR_BACKEND", "mock")  # explicitamente mock
    with pytest.raises(RuntimeError):
        enforce_production_safety()
    # backend desconhecido (create_ocr_adapter cai no mock p/ != 'real') -> bloqueia
    monkeypatch.setenv("OCR_BACKEND", "textract")
    with pytest.raises(RuntimeError):
        enforce_production_safety()


def test_production_passes_with_strong_secret(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "um-segredo-forte-de-verdade-0123456789")
    monkeypatch.setenv("AI_ANALYSIS_BACKEND", "real")  # IA real configurada
    monkeypatch.setenv("EMAIL_BACKEND", "ses")  # envio real configurado
    monkeypatch.setenv("STORAGE_BACKEND", "s3")  # S3 real configurado
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "openai")  # embeddings reais
    monkeypatch.setenv("OCR_BACKEND", "real")  # OCR real configurado
    # A1: os 5 bureaus de triagem também precisam do backend real em produção.
    for bureau in ("SERASA", "PROCON", "ESCAVADOR", "CNJ", "TARGETDATA"):
        monkeypatch.setenv(f"{bureau}_BACKEND", "real")
    # Cartão/tokenização: gateway real configurado (mock de pagamento é bloqueado fora de dev)
    monkeypatch.setenv("PAYMENT_PROVIDER", "pagarme")
    monkeypatch.setenv("PAYMENT_MODE", "live")
    monkeypatch.setenv("PAYMENT_API_KEY", "chave-de-gateway-real")
    # Pix: provider real (!= mock) + segredo de webhook forte configurados
    monkeypatch.setenv("PIX_PROVIDER", "asaas")
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", "segredo-pix-forte-de-verdade-0123456789")
    monkeypatch.setenv("PAYMENT_GATE", "hard")  # SEC-11/12: gate exigido fora de dev
    enforce_production_safety()  # não levanta com tudo configurado para produção


# ── Task 1 (plano 2026-07-04-cartao-tokenizacao): mock de pagamento fora de dev ──

def _prod_env(monkeypatch, **over):
    # Baseline de produção VÁLIDO. Correções: (a) JWT forte e variado (o antigo
    # "x"*40 é fraco por baixa entropia — M3); (b) EMBEDDINGS_BACKEND="openai"
    # (a fábrica só trata "openai" como real; "real" caía no mock — bug latente do
    # baseline); (c) os 5 bureaus e PAYMENT_GATE=hard passam a ser exigidos.
    base = {"ENVIRONMENT": "prod",
            "JWT_SECRET_KEY": "s3gr3d0-forte-com-variedade-0123456789ab",
            "AI_ANALYSIS_BACKEND": "real", "EMAIL_BACKEND": "ses",
            "STORAGE_BACKEND": "s3", "EMBEDDINGS_BACKEND": "openai", "OCR_BACKEND": "real",
            "SERASA_BACKEND": "real", "PROCON_BACKEND": "real", "ESCAVADOR_BACKEND": "real",
            "CNJ_BACKEND": "real", "TARGETDATA_BACKEND": "real",
            "PAYMENT_PROVIDER": "pagarme", "PAYMENT_MODE": "live", "PAYMENT_API_KEY": "k",
            "PIX_PROVIDER": "asaas",
            "PIX_WEBHOOK_SECRET": "pix-forte-com-variedade-0123456789abcdef",
            "PAYMENT_GATE": "hard"}
    base.update(over)
    for k, v in base.items():
        monkeypatch.setenv(k, v)


def test_bloqueia_payment_mock_em_producao(monkeypatch):
    _prod_env(monkeypatch, PAYMENT_MODE="mock")
    with pytest.raises(RuntimeError, match="PAYMENT"):
        enforce_production_safety()


def test_bloqueia_provider_mock_em_producao(monkeypatch):
    _prod_env(monkeypatch, PAYMENT_PROVIDER="mock")
    with pytest.raises(RuntimeError, match="PAYMENT"):
        enforce_production_safety()


def test_exige_api_key_para_gateway_real(monkeypatch):
    _prod_env(monkeypatch, PAYMENT_API_KEY="")
    with pytest.raises(RuntimeError, match="PAYMENT_API_KEY"):
        enforce_production_safety()


def test_prod_valido_nao_bloqueia(monkeypatch):
    _prod_env(monkeypatch)
    enforce_production_safety()  # não levanta


# ── A1: os 5 bureaus de triagem/enriquecimento devem ser fail-closed em produção ──

_BUREAUS = ["SERASA", "PROCON", "ESCAVADOR", "CNJ", "TARGETDATA"]


@pytest.mark.parametrize("bureau", _BUREAUS)
def test_bloqueia_bureau_mock_em_producao(monkeypatch, bureau):
    _prod_env(monkeypatch, **{f"{bureau}_BACKEND": "mock"})
    with pytest.raises(RuntimeError, match=f"{bureau}_BACKEND"):
        enforce_production_safety()


@pytest.mark.parametrize("bureau", _BUREAUS)
def test_bloqueia_bureau_ausente_em_producao(monkeypatch, bureau):
    _prod_env(monkeypatch)
    monkeypatch.delenv(f"{bureau}_BACKEND", raising=False)  # ausente -> default mock
    with pytest.raises(RuntimeError, match=f"{bureau}_BACKEND"):
        enforce_production_safety()


# ── M1: fail-OPEN por typo — valor diferente do real exato bloqueia o boot ──

@pytest.mark.parametrize("env,typo", [
    ("AI_ANALYSIS_BACKEND", "Real"),      # a fábrica só reconhece "real" (case-sensitive)
    ("EMBEDDINGS_BACKEND", "openia"),     # typo comum -> cairia no mock silenciosamente
    ("SERASA_BACKEND", "REAL"),           # caixa alta -> fábrica trata como mock
    ("OCR_BACKEND", "textract"),          # backend ainda não implementado -> mock
])
def test_bloqueia_typo_de_backend(monkeypatch, env, typo):
    _prod_env(monkeypatch, **{env: typo})
    with pytest.raises(RuntimeError, match=env):
        enforce_production_safety()


# ── M3: JWT_SECRET_KEY exige comprimento e entropia mínimos em produção ──

def test_bloqueia_segredo_repetitivo(monkeypatch):
    # "x"*40 não é placeholder conhecido, mas é força-brutável (1 char distinto).
    _prod_env(monkeypatch, JWT_SECRET_KEY="x" * 40)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        enforce_production_safety()


def test_bloqueia_segredo_curto(monkeypatch):
    _prod_env(monkeypatch, JWT_SECRET_KEY="aB3!xYz9")  # variado, porém < 32 chars
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        enforce_production_safety()


# ── SEC-11/SEC-12: PAYMENT_GATE deve ser 'hard' fora de dev (fail-closed) ──

def test_bloqueia_payment_gate_soft_em_producao(monkeypatch):
    _prod_env(monkeypatch, PAYMENT_GATE="soft")
    with pytest.raises(RuntimeError, match="PAYMENT_GATE"):
        enforce_production_safety()


def test_bloqueia_payment_gate_ausente_em_producao(monkeypatch):
    _prod_env(monkeypatch)
    monkeypatch.delenv("PAYMENT_GATE", raising=False)  # ausente -> default soft
    with pytest.raises(RuntimeError, match="PAYMENT_GATE"):
        enforce_production_safety()
