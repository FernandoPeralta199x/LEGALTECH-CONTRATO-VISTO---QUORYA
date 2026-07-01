"""Handlers de pricing (serverless) — catálogo, estimativa e config por organização.

Portado de ``legaltech-aws/apps/api/src/modules/pricing/router.py``:
- GET  /pricing                    catálogo (com overrides de módulo da org)
- POST /pricing/estimate           estimativa servidor (base + módulos + SLA)
- GET  /pricing/config             config da org (overrides + cases_limit + notes)
- PUT  /pricing/config             atualiza config (admin) + bump de version
- GET  /pricing/config/limit-check pode criar novo caso? (cases_limit)

Leitura: qualquer usuário autenticado. Mutação de config: admin.
Auditoria: mudanças em ``pricing_configs`` (INSERT/UPDATE) gravam change_before/
change_after em ``audit.audit_log`` via trigger ``audit.log_audit`` (SECURITY DEFINER,
migration 013) — além de version + updated_by + updated_at.
"""
import json
import logging

from psycopg2.extras import Json
from pydantic import ValidationError

from src.schemas.pricing_schemas import PricingEstimateRequest, UpdatePricingConfigRequest
from src.services.database import tenant_tx
from src.services.pricing.catalog import build_catalog
from src.services.pricing.config import MODULES, PRODUCTS
from src.services.pricing.estimate import estimate as compute_estimate
from src.utils.context import require_role, require_user
from src.utils.helpers import error_response, success_response
from src.utils.lambda_io import fmt_validation_error as _fmt, parse_json_body as _parse_body
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


def _org_module_overrides(cur, org):
    """Lê ``pricing_configs.module_overrides`` da org (dict ou None)."""
    cur.execute("SELECT module_overrides FROM public.pricing_configs WHERE organization_id = %s",
                (org,))
    row = cur.fetchone()
    return row["module_overrides"] if row else None


@require_user
def get_pricing(event, context):
    """Catálogo de pricing com overrides de módulo da organização aplicados."""
    user = event["user"]
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            overrides = _org_module_overrides(cur, user["organization_id"])
        catalog = build_catalog(overrides)
    except Exception as e:
        logger.error(json.dumps({"event": "PRICING_CATALOG_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao obter catálogo de pricing")
    return success_response(200, "Catálogo de pricing", catalog)


@require_user
def estimate_pricing(event, context):
    """Estimativa de preço/SLA para um produto + módulos selecionados (com overrides)."""
    user = event["user"]
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = PricingEstimateRequest(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")
    if data.product not in PRODUCTS:
        return error_response(400, "product inválido")
    invalid = [m for m in data.modules if m not in MODULES]
    if invalid:
        return error_response(400, f"módulos inválidos: {', '.join(invalid)}")
    try:
        with tenant_tx(user["user_id"], user["role"], user["organization_id"]) as cur:
            overrides = _org_module_overrides(cur, user["organization_id"])
        est = compute_estimate(data.product, list(data.modules), overrides)
    except Exception as e:
        logger.error(json.dumps({"event": "PRICING_ESTIMATE_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao estimar pricing")
    return success_response(200, "Estimativa de pricing", est)


def _config_payload(row, org):
    """Monta o corpo de resposta da config (com defaults quando não existe)."""
    if not row:
        return {
            "organization_id": str(org),
            "cases_limit": None,
            "product_overrides": {},
            "module_overrides": {},
            "version": 0,
            "notes": None,
            "updated_by": None,
            "updated_at": None,
        }
    return {
        "organization_id": str(row["organization_id"]),
        "cases_limit": row["cases_limit"],
        "product_overrides": row["product_overrides"] or {},
        "module_overrides": row["module_overrides"] or {},
        "version": row["version"],
        "notes": row["notes"],
        "updated_by": str(row["updated_by"]) if row["updated_by"] else None,
        "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
    }


@require_user
def get_pricing_config(event, context):
    """Config de pricing da organização (overrides + cases_limit + notes + version)."""
    user = event["user"]
    org = user["organization_id"]
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute(
                "SELECT organization_id, cases_limit, product_overrides, module_overrides,"
                " version, notes, updated_by, updated_at"
                " FROM public.pricing_configs WHERE organization_id = %s", (org,))
            row = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "PRICING_CONFIG_GET_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao obter config de pricing")
    return success_response(200, "Config de pricing", _config_payload(row, org))


@require_user
@require_role("admin")
def update_pricing_config(event, context):
    """Atualiza (parcialmente) a config de pricing da org. Admin only. Bump de version."""
    user = event["user"]
    org = user["organization_id"]
    uid = user["user_id"]
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = UpdatePricingConfigRequest(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    changed = data.model_fields_set
    if not changed:
        return error_response(400, "Nenhum campo para atualizar")
    # valida códigos dos overrides contra o catálogo
    if data.product_overrides:
        bad = [c for c in data.product_overrides if c not in PRODUCTS]
        if bad:
            return error_response(400, f"produtos inválidos: {', '.join(bad)}")
    if data.module_overrides:
        bad = [c for c in data.module_overrides if c not in MODULES]
        if bad:
            return error_response(400, f"módulos inválidos: {', '.join(bad)}")

    try:
        with tenant_tx(uid, user["role"], org) as cur:
            cur.execute(
                "SELECT cases_limit, product_overrides, module_overrides, notes, version"
                " FROM public.pricing_configs WHERE organization_id = %s", (org,))
            cur_row = cur.fetchone()
            cur_cases = cur_row["cases_limit"] if cur_row else None
            cur_prod = cur_row["product_overrides"] if cur_row else {}
            cur_mod = cur_row["module_overrides"] if cur_row else {}
            cur_notes = cur_row["notes"] if cur_row else None
            cur_ver = cur_row["version"] if cur_row else 0

            new_cases = data.cases_limit if "cases_limit" in changed else cur_cases
            new_notes = data.notes if "notes" in changed else cur_notes
            new_prod = ({c: v.model_dump() for c, v in (data.product_overrides or {}).items()}
                        if "product_overrides" in changed else cur_prod)
            new_mod = ({c: v.model_dump() for c, v in (data.module_overrides or {}).items()}
                       if "module_overrides" in changed else cur_mod)
            new_ver = (cur_ver or 0) + 1

            cur.execute(
                "INSERT INTO public.pricing_configs"
                " (organization_id, cases_limit, product_overrides, module_overrides,"
                "  notes, updated_by, version)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (organization_id) DO UPDATE SET"
                "  cases_limit = EXCLUDED.cases_limit,"
                "  product_overrides = EXCLUDED.product_overrides,"
                "  module_overrides = EXCLUDED.module_overrides,"
                "  notes = EXCLUDED.notes, updated_by = EXCLUDED.updated_by,"
                "  version = EXCLUDED.version, updated_at = now()"
                " RETURNING organization_id, cases_limit, product_overrides, module_overrides,"
                "  version, notes, updated_by, updated_at",
                (org, new_cases, Json(new_prod), Json(new_mod), new_notes, uid, new_ver))
            row = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "PRICING_CONFIG_PUT_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao atualizar config de pricing")

    logger.info(json.dumps({"event": "PRICING_CONFIG_UPDATED", "version": row["version"]}))
    return success_response(200, "Configuração de pricing atualizada", _config_payload(row, org))


@require_user
def check_cases_limit(event, context):
    """Informa se a organização pode criar um novo caso (cases_limit vs ativos)."""
    user = event["user"]
    org = user["organization_id"]
    try:
        with tenant_tx(user["user_id"], user["role"], org) as cur:
            cur.execute("SELECT cases_limit FROM public.pricing_configs WHERE organization_id = %s",
                        (org,))
            row = cur.fetchone()
            cases_limit = row["cases_limit"] if row else None
            cur.execute("SELECT count(*) AS n FROM public.cases WHERE deleted_at IS NULL")
            active = cur.fetchone()["n"]
    except Exception as e:
        logger.error(json.dumps({"event": "PRICING_LIMIT_CHECK_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao verificar limite de casos")
    allowed = cases_limit is None or active < cases_limit
    return success_response(200, "Verificação de limite de casos", {
        "cases_limit": cases_limit,
        "active_cases_count": active,
        "allowed": allowed,
    })
