"""Tributos provisionados (Fase 5) — agregação/listagem/persistência sobre
public.tax_provisions. Cursor de tenant_tx (RLS por org). CONTROLE INTERNO:
valores informativos digitados pelo admin; o banco NÃO deriva tributo de alíquota
(só a divergence_cents = confirmado - estimado, GENERATED).

tax_cents (KPI/overview) = melhor-conhecido (confirmado quando há, senão estimado),
excluindo 'cancelado' e 'nao_aplicavel'. Vazio => 0 (nunca None).
"""

_AGG_SQL = """
    SELECT
      COALESCE(SUM(COALESCE(confirmed_amount_cents, estimated_amount_cents))
               FILTER (WHERE status NOT IN ('cancelado','nao_aplicavel')), 0) AS tax_cents,
      COALESCE(SUM(estimated_amount_cents)
               FILTER (WHERE status IN ('estimado','confirmado','divergente')), 0) AS estimated_cents,
      COALESCE(SUM(confirmed_amount_cents)
               FILTER (WHERE status IN ('confirmado','divergente')), 0)          AS confirmed_cents,
      count(*) FILTER (WHERE status = 'divergente') AS divergent_count,
      count(*)                                      AS count_all
    FROM public.tax_provisions
    WHERE reference_at >= %(start)s AND reference_at < %(end)s
"""

_BY_TYPE_SQL = """
    SELECT tax_type,
           COALESCE(SUM(COALESCE(confirmed_amount_cents, estimated_amount_cents)), 0) AS total_cents
    FROM public.tax_provisions
    WHERE reference_at >= %(start)s AND reference_at < %(end)s
      AND status NOT IN ('cancelado','nao_aplicavel')
    GROUP BY tax_type ORDER BY total_cents DESC, tax_type ASC
"""

_FILTER = (
    " WHERE reference_at >= %(start)s AND reference_at < %(end)s"
    "   AND (%(tax_type)s IS NULL OR tax_type = %(tax_type)s)"
    "   AND (%(status)s IS NULL OR status = %(status)s)"
)

_COLS = (
    "id, tax_type, base_amount_cents, rate_bps, estimated_amount_cents,"
    " confirmed_amount_cents, divergence_cents, status, competencia_ano,"
    " competencia_mes, reference_at, request_id, case_id, client_id,"
    " provider, external_id, notes, created_at"
)

_LIST_SQL = ("SELECT " + _COLS + " FROM public.tax_provisions" + _FILTER +
             " ORDER BY reference_at DESC, id DESC LIMIT %(limit)s OFFSET %(offset)s")
_COUNT_SQL = "SELECT count(*) AS n FROM public.tax_provisions" + _FILTER


def compute_taxes(cur, start, end) -> dict:
    cur.execute(_AGG_SQL, {"start": start, "end": end})
    r = cur.fetchone()
    return {
        "tax_cents": int(r["tax_cents"]),
        "tax_estimated_cents": int(r["estimated_cents"]),
        "tax_confirmed_cents": int(r["confirmed_cents"]),
        "tax_divergent_count": int(r["divergent_count"]),
        "tax_count": int(r["count_all"]),
    }


def taxes_by_type(cur, start, end) -> list:
    cur.execute(_BY_TYPE_SQL, {"start": start, "end": end})
    return [{"tax_type": row["tax_type"], "total_cents": int(row["total_cents"])}
            for row in cur.fetchall()]


def list_taxes(cur, start, end, tax_type, status, limit, offset):
    params = {"start": start, "end": end, "tax_type": tax_type,
              "status": status, "limit": limit, "offset": offset}
    cur.execute(_LIST_SQL, params)
    items = [_serialize(row) for row in cur.fetchall()]
    cur.execute(_COUNT_SQL, params)
    total = int(cur.fetchone()["n"])
    return items, total


def insert_tax(cur, org_id, created_by, data: dict) -> dict:
    cur.execute(
        "INSERT INTO public.tax_provisions"
        " (organization_id, created_by, tax_type, base_amount_cents, rate_bps,"
        "  estimated_amount_cents, confirmed_amount_cents, status, competencia_ano,"
        "  competencia_mes, reference_at, request_id, case_id, client_id, provider,"
        "  external_id, notes, source)"
        " VALUES (%(org)s, %(by)s, %(tax_type)s, %(base)s, %(rate)s, %(estimated)s,"
        "         %(confirmed)s, %(status)s, %(ano)s, %(mes)s, %(ref)s, %(request_id)s,"
        "         %(case_id)s, %(client_id)s, %(provider)s, %(external_id)s, %(notes)s, 'manual')"
        " RETURNING " + _COLS,
        {"org": org_id, "by": created_by, "tax_type": data["tax_type"],
         "base": data["base_amount_cents"], "rate": data.get("rate_bps"),
         "estimated": data["estimated_amount_cents"], "confirmed": data.get("confirmed_amount_cents"),
         "status": data["status"], "ano": data["competencia_ano"], "mes": data["competencia_mes"],
         "ref": data["reference_at"], "request_id": data.get("request_id"),
         "case_id": data.get("case_id"), "client_id": data.get("client_id"),
         "provider": data.get("provider"), "external_id": data.get("external_id"),
         "notes": data.get("notes")},
    )
    return _serialize(cur.fetchone())


def _int_or_none(v):
    return int(v) if v is not None else None


def _serialize(row) -> dict:
    return {
        "id": str(row["id"]),
        "tax_type": row["tax_type"],
        "base_amount_cents": int(row["base_amount_cents"]),
        "rate_bps": _int_or_none(row["rate_bps"]),
        "estimated_amount_cents": int(row["estimated_amount_cents"]),
        "confirmed_amount_cents": _int_or_none(row["confirmed_amount_cents"]),
        "divergence_cents": _int_or_none(row["divergence_cents"]),
        "status": row["status"],
        "competencia_ano": int(row["competencia_ano"]),
        "competencia_mes": int(row["competencia_mes"]),
        "reference_at": str(row["reference_at"]) if row["reference_at"] else None,
        "request_id": str(row["request_id"]) if row["request_id"] else None,
        "case_id": str(row["case_id"]) if row["case_id"] else None,
        "client_id": str(row["client_id"]) if row["client_id"] else None,
        "provider": row["provider"],
        "external_id": row["external_id"],
        "notes": row["notes"],
        "created_at": str(row["created_at"]) if row["created_at"] else None,
    }
