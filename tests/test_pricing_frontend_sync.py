"""Anti-drift CRUZADO do catálogo de preços (PRC-04).

Já existem duas guardas anti-drift — produtoConfig.test.ts (FE) e test_pricing.py
(BE) — mas cada uma fixa uma cópia LITERAL dos números no seu próprio repo, então
uma mudança só no backend faz a guarda do BE falhar (o dev atualiza o pin do BE e
segue) sem NENHUM sinal cruzado de que o fallback do FE ficou defasado.

Este teste fecha essa direção: lê o hardcoded do frontend e compara com MODULES do
backend (fonte de verdade). Como os repositórios são separados, faz `skip` se o
arquivo do FE não estiver ao lado (CI isolado) — no ambiente de dev (repos irmãos),
onde as mudanças de preço acontecem, ele morde.
"""
import re
from pathlib import Path

import pytest

from src.services.pricing.config import MODULES

# Repos irmãos: <root>/contrato_visto_backend-main/tests/ e <root>/contrato_visto_frontend/
_FE_PRODUTO_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "contrato_visto_frontend" / "src" / "lib" / "produtoConfig.ts"
)


def _parse_frontend_prices(text: str) -> dict[str, int]:
    # Recorta só o bloco `export const MODULOS = { ... };` — PRODUTOS (acima) também
    # tem `code:` mas sem precoCents, e atravessá-lo desalinha os pares.
    section = re.search(r"export const MODULOS[^=]*=\s*\{([\s\S]*?)\n\};", text)
    if not section:
        return {}
    # Dentro do bloco, cada módulo tem `code: "<x>"` seguido de `precoCents: <n>`.
    pairs = re.findall(r'code:\s*"(\w+)"[\s\S]*?precoCents:\s*(\d+)', section.group(1))
    return {code: int(cents) for code, cents in pairs}


def test_precos_do_frontend_espelham_o_backend():
    if not _FE_PRODUTO_CONFIG.exists():
        pytest.skip(f"produtoConfig.ts do frontend não encontrado em {_FE_PRODUTO_CONFIG}")

    fe = _parse_frontend_prices(_FE_PRODUTO_CONFIG.read_text(encoding="utf-8"))
    assert fe, "não consegui extrair nenhum preço do produtoConfig.ts (regex quebrou?)"

    be = {code: meta.price_cents for code, meta in MODULES.items()}
    for code, cents in be.items():
        assert code in fe, f"módulo '{code}' existe no backend mas não no fallback do FE"
        assert fe[code] == cents, (
            f"drift de preço em '{code}': backend={cents} vs frontend={fe[code]}. "
            "Sincronize produtoConfig.ts com config.py."
        )
