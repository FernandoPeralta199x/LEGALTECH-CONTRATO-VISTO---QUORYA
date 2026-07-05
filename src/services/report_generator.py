"""Relatório/parecer do caso (SP3) — gera um parecer consolidando as evidências
da triagem (provider_results) e gerencia a revisão humana. Mock-first: o texto é
simulado; na fase AWS a geração chama um agente de IA (Bedrock). Opera no cursor
de uma transação tenant_tx (RLS por org). Um relatório por caso (version+1 ao regerar).
"""
from __future__ import annotations

from psycopg2.extras import Json

_COLS = (
    "id, case_id, status, version, summary, findings, legal_risks, commercial_risks,"
    " reputational_risks, contractual_risks, missing_information, recommendation,"
    " confidence, limitations, source_refs, generated_by, generated_at,"
    " reviewed_by, reviewed_at, review_notes, updated_at"
)

# recomendação: enum único alinhado à referência (ReportRecommendation) usado tanto
# em case_reports.recommendation quanto em cases.recommendation (varchar(32)). O front
# (recommendationLabel) só rotula esses códigos; texto livre apareceria cru.
_REC_CODE = {"low": "proceed", "medium": "proceed_with_caution", "high": "do_not_proceed"}
# conjunto fechado de códigos válidos de recommendation (o front só rotula estes; texto
# livre apareceria cru). Usado para validar a edição na revisão humana.
VALID_RECOMMENDATION = set(_REC_CODE.values()) | {"human_review_required"}
# explicação humana (não é o status/enum): vai para o summary, não para recommendation.
_REC_TEXT = {
    "proceed": "Apto a prosseguir; sem riscos relevantes identificados (simulado).",
    "proceed_with_caution": "Prosseguir com ressalvas; recomenda-se revisão humana dos pontos sinalizados.",
    "do_not_proceed": "Não recomendado sem mitigação prévia dos riscos identificados.",
    "human_review_required": "Evidências insuficientes; revisão humana obrigatória antes de decidir.",
}


def serialize_report(row, org) -> dict:
    return {
        "id": str(row["id"]), "case_id": str(row["case_id"]), "organization_id": str(org),
        "status": row["status"], "version": row["version"], "summary": row["summary"],
        "findings": row["findings"] or [], "legal_risks": row["legal_risks"] or [],
        "commercial_risks": row["commercial_risks"] or [],
        "reputational_risks": row["reputational_risks"] or [],
        "contractual_risks": row["contractual_risks"] or [],
        "missing_information": row["missing_information"] or [],
        "recommendation": row["recommendation"], "confidence": row["confidence"],
        "limitations": row["limitations"] or [], "source_refs": row["source_refs"] or [],
        "generated_by": str(row["generated_by"]) if row["generated_by"] else None,
        "generated_at": str(row["generated_at"]) if row["generated_at"] else None,
        "reviewed_by": str(row["reviewed_by"]) if row["reviewed_by"] else None,
        "reviewed_at": str(row["reviewed_at"]) if row["reviewed_at"] else None,
        "review_notes": row["review_notes"],
        "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
    }


def get_report(cur, org, case_id):
    cur.execute(f"SELECT {_COLS} FROM public.case_reports WHERE case_id = %s", (case_id,))
    row = cur.fetchone()
    return serialize_report(row, org) if row else None


def generate_report(cur, org, case_id, user_id) -> dict:
    """Consolida provider_results/triagem num parecer (mock) e faz upsert do relatório."""
    cur.execute("SELECT module_key, provider FROM public.triage_modules"
                " WHERE case_id = %s ORDER BY created_at, id", (case_id,))
    modules = cur.fetchall()
    cur.execute("SELECT provider, summary, risk_signals, confidence"
                " FROM public.provider_results WHERE case_id = %s", (case_id,))
    results = cur.fetchall()

    findings = [r["summary"] for r in results if r["summary"]]
    signals: list[str] = []
    for r in results:
        signals.extend(r["risk_signals"] or [])

    def _by(substr):
        return [r["summary"] for r in results if substr in r["provider"] and r["summary"]]

    legal = _by("escavador")
    commercial = _by("serasa")
    reputational = _by("procon")
    contractual = [r["summary"] for r in results
                   if ("ai" in r["provider"] or "document" in r["provider"] or "ocr" in r["provider"])
                   and r["summary"]]
    confs = [r["confidence"] for r in results if r["confidence"] is not None]
    confidence = round(sum(confs) / len(confs), 2) if confs else None
    risk = "high" if any("alto" in s for s in signals) else ("medium" if signals else "low")
    # sem evidências -> revisão humana obrigatória; senão deriva do nível de risco
    recommendation = "human_review_required" if not results else _REC_CODE[risk]
    missing = [] if results else ["Triagem ainda não executada — execute a triagem antes de gerar o relatório."]
    summary = (f"Parecer consolidado a partir de {len(modules)} módulos de triagem"
               f" ({len(results)} resultados). Nível de risco estimado: {risk}."
               f" {_REC_TEXT[recommendation]}")
    source_refs = [{"type": "triage_module", "module_key": m["module_key"],
                    "provider": m["provider"]} for m in modules]
    limitations = ["Parecer gerado em modo simulado (mock); não substitui análise jurídica real."]

    cur.execute(
        "INSERT INTO public.case_reports"
        " (organization_id, case_id, status, version, summary, findings, legal_risks,"
        "  commercial_risks, reputational_risks, contractual_risks, missing_information,"
        "  recommendation, confidence, limitations, source_refs, generated_by, generated_at)"
        " VALUES (%s,%s,'ready',1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())"
        " ON CONFLICT (organization_id, case_id) DO UPDATE SET"
        "  status='ready', version = public.case_reports.version + 1,"
        "  summary=EXCLUDED.summary, findings=EXCLUDED.findings,"
        "  legal_risks=EXCLUDED.legal_risks, commercial_risks=EXCLUDED.commercial_risks,"
        "  reputational_risks=EXCLUDED.reputational_risks,"
        "  contractual_risks=EXCLUDED.contractual_risks,"
        "  missing_information=EXCLUDED.missing_information,"
        "  recommendation=EXCLUDED.recommendation, confidence=EXCLUDED.confidence,"
        "  limitations=EXCLUDED.limitations, source_refs=EXCLUDED.source_refs,"
        "  generated_by=EXCLUDED.generated_by, generated_at=now(), updated_at=now()"
        f" RETURNING {_COLS}",
        (org, case_id, summary, Json(findings), Json(legal), Json(commercial),
         Json(reputational), Json(contractual), Json(missing), recommendation,
         confidence, Json(limitations), Json(source_refs), user_id))
    row = cur.fetchone()

    cur.execute(
        "UPDATE public.cases SET status='report_ready', progress=85,"
        " risk_level=%s, recommendation=%s WHERE id = %s",
        (risk, recommendation, case_id))
    cur.execute(
        "INSERT INTO public.timeline_events"
        " (organization_id, case_id, event_type, title, description, actor)"
        " VALUES (%s,%s,'report_generated','Relatório gerado',"
        " 'Parecer consolidado a partir da triagem (mock).','system')", (org, case_id))
    return serialize_report(row, org)


def review_report(cur, org, case_id, user_id, status, notes=None,
                  summary=None, recommendation=None):
    """Revisão humana: status 'reviewed' ou 'approved'; permite editar summary/recommendation.

    O status do relatório segue o enum da referência: 'ready' (gerado). Só 'approved'
    promove o estado (FE-válido); 'reviewed' apenas registra o revisor sem regredir o status.
    """
    fields = ["reviewed_by = %s", "reviewed_at = now()", "updated_at = now()"]
    values: list = [user_id]
    if status == "approved":
        fields.insert(0, "status = 'approved'")
    if notes is not None:
        fields.append("review_notes = %s")
        values.append(notes)
    if summary is not None:
        fields.append("summary = %s")
        values.append(summary)
    if recommendation is not None:
        fields.append("recommendation = %s")
        values.append(recommendation)
    values.append(case_id)
    cur.execute(
        f"UPDATE public.case_reports SET {', '.join(fields)} WHERE case_id = %s"
        f" RETURNING {_COLS}", tuple(values))
    row = cur.fetchone()
    if not row:
        return None
    # sincroniza cases.recommendation com a edição da revisão (senão o agregado divergiria:
    # case_reports.recommendation atualizado, mas cases.recommendation defasado).
    if recommendation is not None:
        cur.execute("UPDATE public.cases SET recommendation=%s WHERE id = %s",
                    (recommendation, case_id))
    if status == "approved":
        cur.execute("UPDATE public.cases SET status='completed', progress=100,"
                    " completed_at=now() WHERE id = %s", (case_id,))
    cur.execute(
        "INSERT INTO public.timeline_events"
        " (organization_id, case_id, event_type, title, description, actor)"
        " VALUES (%s,%s,%s,%s,%s,'user')",
        (org, case_id, f"report_{status}",
         "Relatório revisado" if status == "reviewed" else "Relatório aprovado",
         "Revisão humana do parecer do caso."))
    return serialize_report(row, org)
