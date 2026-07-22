"""Notas fiscais (Fase 5) — agregação/listagem/persistência sobre
public.fiscal_documents. O sistema REGISTRA notas já emitidas pelo provedor/órgão;
NÃO emite documento fiscal nem gera número (o admin transcreve o número real).

invoices_count (KPI/overview) = COUNT de notas 'authorized' (só nota efetivamente
autorizada conta). Vazio => 0 (nunca None).
"""

_AGG_SQL = """
    SELECT
      count(*) FILTER (WHERE status = 'authorized')    AS authorized_count,
      count(*) FILTER (WHERE status = 'note_pending')  AS pending_count,
      count(*) FILTER (WHERE status = 'rejected')      AS rejected_count,
      count(*) FILTER (WHERE status = 'note_canceled') AS canceled_count,
      COALESCE(SUM(issuance_cost_cents), 0)            AS issuance_cost_cents,
      COALESCE(SUM(amount_cents) FILTER (WHERE status = 'authorized'), 0) AS authorized_amount_cents,
      count(*)                                         AS count_all
    FROM public.fiscal_documents
    WHERE reference_at >= %(start)s AND reference_at < %(end)s
"""

_BY_STATUS_SQL = """
    SELECT status, count(*) AS n
    FROM public.fiscal_documents
    WHERE reference_at >= %(start)s AND reference_at < %(end)s
    GROUP BY status
"""

_FILTER = (
    " WHERE reference_at >= %(start)s AND reference_at < %(end)s"
    "   AND (%(doc_type)s IS NULL OR doc_type = %(doc_type)s)"
    "   AND (%(status)s IS NULL OR status = %(status)s)"
)

_COLS = (
    "id, doc_type, numero, serie, codigo_verificacao, status, issued_at,"
    " authorized_at, amount_cents, tax_amount_cents, issuance_cost_cents,"
    " provider, document_url, xml_ref, rejection_reason, reference_at,"
    " request_id, case_id, client_id, external_id, notes, created_at"
)

_LIST_SQL = ("SELECT " + _COLS + " FROM public.fiscal_documents" + _FILTER +
             " ORDER BY reference_at DESC, id DESC LIMIT %(limit)s OFFSET %(offset)s")
_COUNT_SQL = "SELECT count(*) AS n FROM public.fiscal_documents" + _FILTER


def compute_fiscal_documents(cur, start, end) -> dict:
    cur.execute(_AGG_SQL, {"start": start, "end": end})
    r = cur.fetchone()
    return {
        "invoices_count": int(r["authorized_count"]),      # overview: só autorizadas
        "invoices_authorized_count": int(r["authorized_count"]),
        "invoices_pending_count": int(r["pending_count"]),
        "invoices_rejected_count": int(r["rejected_count"]),
        "invoices_canceled_count": int(r["canceled_count"]),
        "invoices_issuance_cost_cents": int(r["issuance_cost_cents"]),
        "invoices_authorized_amount_cents": int(r["authorized_amount_cents"]),
        "invoices_total_count": int(r["count_all"]),
    }


def fiscal_by_status(cur, start, end) -> list:
    cur.execute(_BY_STATUS_SQL, {"start": start, "end": end})
    return [{"status": row["status"], "count": int(row["n"])} for row in cur.fetchall()]


def list_fiscal_documents(cur, start, end, doc_type, status, limit, offset):
    params = {"start": start, "end": end, "doc_type": doc_type,
              "status": status, "limit": limit, "offset": offset}
    cur.execute(_LIST_SQL, params)
    items = [_serialize(row) for row in cur.fetchall()]
    cur.execute(_COUNT_SQL, params)
    total = int(cur.fetchone()["n"])
    return items, total


def insert_fiscal_document(cur, org_id, created_by, data: dict) -> dict:
    cur.execute(
        "INSERT INTO public.fiscal_documents"
        " (organization_id, created_by, doc_type, numero, serie, codigo_verificacao,"
        "  status, issued_at, authorized_at, amount_cents, tax_amount_cents,"
        "  issuance_cost_cents, provider, document_url, xml_ref, rejection_reason,"
        "  reference_at, request_id, case_id, client_id, external_id, notes, source)"
        " VALUES (%(org)s, %(by)s, %(doc_type)s, %(numero)s, %(serie)s, %(codigo)s,"
        "         %(status)s, %(issued_at)s, %(authorized_at)s, %(amount)s, %(tax)s,"
        "         %(issuance)s, %(provider)s, %(document_url)s, %(xml_ref)s, %(reason)s,"
        "         %(ref)s, %(request_id)s, %(case_id)s, %(client_id)s, %(external_id)s,"
        "         %(notes)s, 'manual')"
        " RETURNING " + _COLS,
        {"org": org_id, "by": created_by, "doc_type": data["doc_type"],
         "numero": data.get("numero"), "serie": data.get("serie"),
         "codigo": data.get("codigo_verificacao"), "status": data["status"],
         "issued_at": data.get("issued_at"), "authorized_at": data.get("authorized_at"),
         "amount": data["amount_cents"], "tax": data["tax_amount_cents"],
         "issuance": data["issuance_cost_cents"], "provider": data.get("provider"),
         "document_url": data.get("document_url"), "xml_ref": data.get("xml_ref"),
         "reason": data.get("rejection_reason"), "ref": data["reference_at"],
         "request_id": data.get("request_id"), "case_id": data.get("case_id"),
         "client_id": data.get("client_id"), "external_id": data.get("external_id"),
         "notes": data.get("notes")},
    )
    return _serialize(cur.fetchone())


def _serialize(row) -> dict:
    return {
        "id": str(row["id"]),
        "doc_type": row["doc_type"],
        "numero": row["numero"],
        "serie": row["serie"],
        "codigo_verificacao": row["codigo_verificacao"],
        "status": row["status"],
        "issued_at": str(row["issued_at"]) if row["issued_at"] else None,
        "authorized_at": str(row["authorized_at"]) if row["authorized_at"] else None,
        "amount_cents": int(row["amount_cents"]),
        "tax_amount_cents": int(row["tax_amount_cents"]),
        "issuance_cost_cents": int(row["issuance_cost_cents"]),
        "provider": row["provider"],
        "document_url": row["document_url"],
        "xml_ref": row["xml_ref"],
        "rejection_reason": row["rejection_reason"],
        "reference_at": str(row["reference_at"]) if row["reference_at"] else None,
        "request_id": str(row["request_id"]) if row["request_id"] else None,
        "case_id": str(row["case_id"]) if row["case_id"] else None,
        "client_id": str(row["client_id"]) if row["client_id"] else None,
        "external_id": row["external_id"],
        "notes": row["notes"],
        "created_at": str(row["created_at"]) if row["created_at"] else None,
    }
