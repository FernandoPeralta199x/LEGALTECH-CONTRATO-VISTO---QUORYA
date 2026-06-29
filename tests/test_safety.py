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


def test_production_blocks_mock_email_backend(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "um-segredo-forte-de-verdade-0123456789")
    monkeypatch.delenv("AI_ANALYSIS_BACKEND", raising=False)
    monkeypatch.setenv("EMAIL_BACKEND", "log")  # backend sem envio real
    with pytest.raises(RuntimeError):
        enforce_production_safety()


def test_production_passes_with_strong_secret(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "um-segredo-forte-de-verdade-0123456789")
    monkeypatch.delenv("AI_ANALYSIS_BACKEND", raising=False)
    monkeypatch.setenv("EMAIL_BACKEND", "ses")  # envio real configurado
    enforce_production_safety()  # não levanta com segredo forte + email real
