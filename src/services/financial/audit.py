"""Trilha de auditoria financeira (READ-ONLY) sobre audit.audit_log.

A ESCRITA já existe via trigger audit.log_audit (SECURITY DEFINER) em várias
tabelas; aqui só LEMOS, escopado por organização (RLS policy audit_log_org_select,
migration 020) e limitado aos recursos FINANCEIROS já auditados por trigger.

audit_log.created_at é `timestamp` UTC-naive (a sessão do banco é UTC) → os limites
BR-aware de _resolve_range são convertidos para UTC-naive antes de comparar.
"""
from datetime import timezone

# Recursos financeiros auditados por trigger. Opção A: preços, custos de API,
# tributos, notas. Opção B (migration 021) adicionou vendas (requests) e clientes.
FINANCIAL_RESOURCES = ("pricing_configs", "external_api_costs", "tax_provisions",
                       "fiscal_documents", "requests", "clients")
ACTIONS = ("INSERT", "UPDATE", "DELETE")

_NOISE = {"updated_at", "created_at"}
# Pistas de campos "salientes" p/ resumir INSERT/DELETE sem poluir com todos.
_SALIENT_HINTS = ("cents", "amount", "price", "rate", "status", "type", "label",
                  "name", "numero", "serie", "provider", "operation", "quantity")

# Minimização LGPD: a trilha mostra QUE o campo mudou, sem expor o dado pessoal do
# titular. Só o VALOR é mascarado na leitura (o nome do campo permanece). CPF/CNPJ
# (document_number) não é editável nem salient, então nunca chega ao painel.
_MASK = "•••"
_MASKED_PII = {
    "clients": {"email", "phone", "rg", "address_street", "address_city",
                "address_state", "address_zip"},
}


def _redact(resource_type, field, value):
    if value is not None and field in _MASKED_PII.get(resource_type, ()):
        return _MASK
    return value


def _to_utc_naive(dt):
    """BR-aware -> naive UTC (created_at é `timestamp` sem tz, armazenado em UTC)."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _is_salient(key):
    return key not in _NOISE and any(h in key for h in _SALIENT_HINTS)


def _changes(resource_type, action, before, after):
    """Lista compacta de alterações. UPDATE: campos que de fato mudaram (exceto
    ruído). INSERT/DELETE: campos salientes do registro (contexto do criado/removido).
    Valores de PII de contato do cliente são mascarados (minimização LGPD).
    """
    def red(k, v):
        return _redact(resource_type, k, v)

    if action == "UPDATE" and before and after:
        return [{"field": k, "before": red(k, before.get(k)), "after": red(k, v)}
                for k, v in after.items() if k not in _NOISE and before.get(k) != v]
    if action == "INSERT" and after:
        return [{"field": k, "before": None, "after": red(k, v)}
                for k, v in after.items() if _is_salient(k) and v is not None]
    if action == "DELETE" and before:
        return [{"field": k, "before": red(k, v), "after": None}
                for k, v in before.items() if _is_salient(k) and v is not None]
    return []


# period + escopo financeiro (base de summary/by_resource).
_BASE = (" WHERE a.created_at >= %(start)s AND a.created_at < %(end)s"
         "   AND a.resource_type = ANY(%(resources)s)")
# + filtros opcionais de tipo/ação (lista detalhada).
_LIST_FILTER = _BASE + ("   AND (%(rtype)s IS NULL OR a.resource_type = %(rtype)s)"
                        "   AND (%(action)s IS NULL OR a.action = %(action)s)")

_SUMMARY_SQL = """
    SELECT
      count(*)                                AS total,
      count(*) FILTER (WHERE action='INSERT') AS inserts,
      count(*) FILTER (WHERE action='UPDATE') AS updates,
      count(*) FILTER (WHERE action='DELETE') AS deletes,
      count(DISTINCT user_id)                 AS actors
    FROM audit.audit_log a""" + _BASE

_BY_RESOURCE_SQL = ("SELECT resource_type, count(*) AS n FROM audit.audit_log a"
                    + _BASE + " GROUP BY resource_type ORDER BY n DESC, resource_type ASC")

_LIST_SQL = ("""
    SELECT a.id, a.created_at, a.user_id, u.email AS actor_email, u.name AS actor_name,
           a.action, a.resource_type, a.resource_id, a.change_before, a.change_after
    FROM audit.audit_log a
    -- public.users NÃO tem RLS: escopamos o join por org explicitamente para que o
    -- e-mail/nome do ator só apareça se ele for da MESMA org da linha auditada
    -- (que já está escopada pela policy) — defense-in-depth contra vazamento cross-org.
    LEFT JOIN public.users u ON u.id = a.user_id AND u.organization_id = a.organization_id"""
             + _LIST_FILTER + " ORDER BY a.created_at DESC, a.id DESC"
                              " LIMIT %(limit)s OFFSET %(offset)s")

_COUNT_SQL = "SELECT count(*) AS n FROM audit.audit_log a" + _LIST_FILTER


def _base_params(start, end):
    return {"start": _to_utc_naive(start), "end": _to_utc_naive(end),
            "resources": list(FINANCIAL_RESOURCES)}


def compute_audit_summary(cur, start, end) -> dict:
    p = _base_params(start, end)
    cur.execute(_SUMMARY_SQL, p)
    r = cur.fetchone()
    cur.execute(_BY_RESOURCE_SQL, p)
    by_resource = [{"resource_type": row["resource_type"], "count": int(row["n"])}
                   for row in cur.fetchall()]
    return {
        "total": int(r["total"]),
        "inserts": int(r["inserts"]),
        "updates": int(r["updates"]),
        "deletes": int(r["deletes"]),
        "actors": int(r["actors"]),
        "by_resource": by_resource,
    }


def list_audit_events(cur, start, end, rtype, action, limit, offset):
    params = {**_base_params(start, end), "rtype": rtype, "action": action,
              "limit": limit, "offset": offset}
    cur.execute(_LIST_SQL, params)
    items = [_serialize(row) for row in cur.fetchall()]
    cur.execute(_COUNT_SQL, params)
    total = int(cur.fetchone()["n"])
    return items, total


def _serialize(row) -> dict:
    ca = row["created_at"]
    return {
        "id": str(row["id"]),
        # marca como UTC (a coluna é naive-UTC) p/ o frontend formatar no fuso local.
        "created_at": ca.replace(tzinfo=timezone.utc).isoformat() if ca else None,
        "user_id": str(row["user_id"]) if row["user_id"] else None,
        "actor_email": row["actor_email"],
        "actor_name": row["actor_name"],
        "action": row["action"],
        "resource_type": row["resource_type"],
        "resource_id": str(row["resource_id"]) if row["resource_id"] else None,
        "changes": _changes(row["resource_type"], row["action"],
                            row["change_before"], row["change_after"]),
    }
