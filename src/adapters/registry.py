"""Registry central provider → adapter externo (camada serverless).

Único ponto de "wiring" entre o ``provider`` de um módulo de triagem
(``triage_modules.provider``, ex.: ``mock_serasa``) e a fábrica do adapter
correspondente (``create_*_adapter``).

Hoje todos resolvem para o **Mock** (as fábricas leem ``*_BACKEND`` e caem em
mock por padrão). Na Fase 7 (AWS), para "ligar o real" basta:
  1. implementar o corpo dos ``Real*Adapter`` (hoje ``NotImplementedError``);
  2. setar ``*_BACKEND=real`` + ``*_API_KEY`` (via SSM/serverless.yml).
Este registry e o ``triage_runner`` **não mudam** — é essa a razão de existir.

Providers "locais" (validação de partes) não têm adapter externo: ``query_provider``
retorna ``None`` e o runner usa um payload local.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.adapters.ai_analysis import create_ai_analysis_adapter
from src.adapters.base import AdapterResult
from src.adapters.cnj import create_cnj_adapter
from src.adapters.escavador import create_escavador_adapter
from src.adapters.ocr import create_ocr_adapter
from src.adapters.procon import create_procon_adapter
from src.adapters.serasa import create_serasa_adapter
from src.adapters.targetdata import create_targetdata_adapter


@dataclass(frozen=True)
class ProviderBinding:
    """Liga uma chave canônica de provider ao seu adapter.

    ``build`` instancia o adapter (mock|real conforme ``*_BACKEND``); ``probe``
    chama o método primário do adapter com o contexto disponível e devolve um
    ``AdapterResult``. No mock o contexto é ignorado; no real (Fase 7) o runner
    preenche ``ctx`` com os insumos reais (documentos das partes, texto do OCR)."""

    key: str
    build: Callable[[], Any]
    probe: Callable[[Any, dict], AdapterResult]


# provider armazenado (triage_modules.provider) -> chave canônica do adapter.
# O prefixo mock_/real_ é removido antes de consultar este mapa.
_ALIASES: dict[str, str] = {
    "local": "local",
    "parties_validation": "local",
    "serasa": "serasa",
    "procon": "procon",
    "escavador": "escavador",
    "public_search": "escavador",
    "cnj": "cnj",
    "targetdata": "targetdata",
    "ai": "ai",
    "ai_summary": "ai",
    "ai_report": "ai",
    "reputation_summary": "ai",
    "ocr": "ocr",
    "document_parser": "ocr",
}


def canonical_provider_key(provider: str) -> str:
    """Normaliza o ``provider`` armazenado numa chave canônica de adapter.

    Remove o prefixo de backend (``mock_``/``real_``) e resolve apelidos
    (``document_parser`` → ``ocr``, ``ai_summary`` → ``ai`` etc.)."""
    p = (provider or "").strip().lower()
    for prefix in ("mock_", "real_"):
        if p.startswith(prefix):
            p = p[len(prefix):]
            break
    return _ALIASES.get(p, p)


PROVIDER_BINDINGS: dict[str, ProviderBinding] = {
    "serasa": ProviderBinding(
        "serasa", create_serasa_adapter,
        lambda a, ctx: a.check_credit(ctx.get("cpf_cnpj", "")),
    ),
    "procon": ProviderBinding(
        "procon", create_procon_adapter,
        lambda a, ctx: a.check_complaints(ctx.get("cpf_cnpj", "")),
    ),
    "escavador": ProviderBinding(
        "escavador", create_escavador_adapter,
        lambda a, ctx: a.search_lawsuits(ctx.get("cpf_cnpj", "")),
    ),
    "cnj": ProviderBinding(
        "cnj", create_cnj_adapter,
        lambda a, ctx: a.search_by_party(ctx.get("name", ""), ctx.get("cpf_cnpj")),
    ),
    "targetdata": ProviderBinding(
        "targetdata", create_targetdata_adapter,
        lambda a, ctx: a.lookup(ctx.get("cpf_cnpj", "")),
    ),
    "ai": ProviderBinding(
        "ai", create_ai_analysis_adapter,
        lambda a, ctx: a.generate_summary(ctx.get("text", "")),
    ),
    "ocr": ProviderBinding(
        "ocr", create_ocr_adapter,
        lambda a, ctx: a.extract_text(
            ctx.get("file_path", ""), ctx.get("mime_type", "application/pdf")),
    ),
}


def resolve_binding(provider: str) -> ProviderBinding | None:
    """Retorna o ``ProviderBinding`` do provider, ou ``None`` se for local/desconhecido."""
    return PROVIDER_BINDINGS.get(canonical_provider_key(provider))


def query_provider(provider: str, ctx: dict | None = None) -> AdapterResult | None:
    """Resolve o provider e executa a consulta no adapter (mock agora, real na Fase 7).

    Retorna ``None`` para providers ``local``/desconhecidos (sem adapter externo).
    Propaga ``ValueError`` (real sem API key) e ``NotImplementedError`` (Real*
    ainda não implementado) — falha explícita é intencional em modo real."""
    binding = resolve_binding(provider)
    if binding is None:
        return None
    adapter = binding.build()
    return binding.probe(adapter, ctx or {})
