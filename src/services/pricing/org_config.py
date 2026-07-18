"""Leitura org-scoped de ``public.pricing_configs`` (dentro de uma ``tenant_tx`` aberta).

Ponto ÚNICO de leitura + fail-safe da config de pricing por organização
(be-dry-02/03). As funções operam sobre um cursor JÁ aberto no contexto RLS
(``app.organization_id``), como ``case_lifecycle`` — NÃO abrem conexão/transação
próprias. Antes, cada leitura estava duplicada em cases/payments/pricing/requests com
fail-safe divergente (só payments logava config inválida).
"""
from __future__ import annotations

import json
import logging

from src.services.pricing.installments import InstallmentConfig

logger = logging.getLogger()


def read_installment_config(cur, org) -> tuple[InstallmentConfig, int]:
    """``(InstallmentConfig, version)`` da org. Fail-safe ÚNICO: config inválida/ausente
    => ``InstallmentConfig()`` (só 1x) e ``version=0``; loga ``INSTALLMENT_CONFIG_INVALID``
    quando a config existe mas não valida (be-sec-06)."""
    cur.execute("SELECT installment_config, version FROM public.pricing_configs"
                " WHERE organization_id = %s", (org,))
    row = cur.fetchone()
    raw = (row["installment_config"] if row else None) or {}
    version = row["version"] if row else 0
    try:
        return InstallmentConfig(**raw), version
    except Exception as e:
        logger.warning(json.dumps({"event": "INSTALLMENT_CONFIG_INVALID",
                                   "error": type(e).__name__, "version": version}))
        return InstallmentConfig(), version


def read_module_overrides(cur, org):
    """``pricing_configs.module_overrides`` da org (dict ou ``None``)."""
    cur.execute("SELECT module_overrides FROM public.pricing_configs"
                " WHERE organization_id = %s", (org,))
    row = cur.fetchone()
    return row["module_overrides"] if row else None
