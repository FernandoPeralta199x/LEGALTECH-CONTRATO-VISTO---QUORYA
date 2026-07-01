"""Fase 1 — fail-closed de enforce_production_safety (correções pós-Codex).

enforce_production_safety lê os.getenv em runtime, então basta ajustar o ambiente.
"""
import pytest

from src.utils.safety import enforce_production_safety


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
    monkeypatch.delenv("AI_ANALYSIS_BACKEND", raising=False)
    monkeypatch.setenv("EMAIL_BACKEND", "ses")  # envio real configurado
    monkeypatch.setenv("STORAGE_BACKEND", "s3")  # S3 real configurado
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "openai")  # embeddings reais
    monkeypatch.setenv("OCR_BACKEND", "real")  # OCR real configurado
    enforce_production_safety()  # não levanta com tudo configurado para produção
