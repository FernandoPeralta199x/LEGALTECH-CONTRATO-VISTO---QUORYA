"""Execução da triagem (SP3) — roda os triage_modules de um caso, gravando
evidências em external_queries_cache (cache por org+provider+hash) e o resultado
normalizado em provider_results. Mock-first: os resultados são simulados por tipo
de provider; na fase AWS (SP4 real) os runners chamam os RealAdapters (HTTP) com
o mesmo contrato de cache. Tudo no cursor de uma transação tenant_tx (RLS por org).
"""
from __future__ import annotations

import hashlib

from psycopg2.extras import Json


def _mock_result(provider: str, module_key: str):
    """Resultado mock (normalized, risk_signals, confidence, summary) por provider."""
    p = provider.lower()
    if "serasa" in p:
        return ({"score": 742, "restricoes": 0, "situacao": "regular"},
                ["score_saudavel"], 0.9, "Score saudável, sem restrições (mock).")
    if "procon" in p:
        return ({"reclamacoes": 0, "indice_resolucao": 1.0},
                [], 0.85, "Sem reclamações no Procon (mock).")
    if "escavador" in p:
        return ({"processos": 1, "como_autor": 0, "como_reu": 1},
                ["litigio_baixo"], 0.8, "1 processo público encontrado (mock).")
    if "ai_report" in p:
        return ({"riscos": ["cláusula de rescisão ampla"], "recomendacao": "revisar"},
                ["clausula_revisar"], 0.75, "Pré-relatório simulado com 1 risco (mock).")
    if "ai_summary" in p:
        return ({"resumo": "Contrato de prestação de serviços; prazo 12 meses."},
                [], 0.78, "Resumo simulado do documento (mock).")
    if "document_parser" in p or "ocr" in p:
        return ({"paginas": 3, "campos_extraidos": ["objeto", "prazo", "valor"]},
                [], 0.95, "Documento lido e estruturado (mock).")
    return ({"ok": True, "module_key": module_key}, [], 0.7, "Módulo simulado (mock).")


def _run_module(cur, org, case_id, user_id, module) -> list[str]:
    """Executa um módulo: cache de evidência + provider_results + atualiza o módulo."""
    provider = module["provider"]
    module_key = module["module_key"]
    module_id = module["id"]
    qhash = hashlib.sha256(f"{case_id}:{provider}:{module_key}".encode("utf-8")).hexdigest()
    normalized, signals, confidence, summary = _mock_result(provider, module_key)
    req_payload = {"case_id": str(case_id), "module_key": module_key, "provider": provider}

    # cache de evidência (idempotente por org+provider+hash)
    cur.execute(
        "INSERT INTO public.external_queries_cache"
        " (organization_id, case_id, provider, query_hash, request_payload,"
        "  response_payload, normalized_payload, status, requested_by)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,'done',%s)"
        " ON CONFLICT (organization_id, provider, query_hash) DO UPDATE SET"
        "  response_payload = EXCLUDED.response_payload,"
        "  normalized_payload = EXCLUDED.normalized_payload,"
        "  status = 'done', updated_at = now()",
        (org, case_id, provider, qhash, Json(req_payload), Json(normalized),
         Json(normalized), user_id))

    # provider_results (limpa-e-regera por módulo)
    cur.execute("DELETE FROM public.provider_results WHERE triage_module_id = %s", (module_id,))
    cur.execute(
        "INSERT INTO public.provider_results"
        " (organization_id, case_id, triage_module_id, provider, source_mode, status,"
        "  input_hash, normalized_result, summary, risk_signals, confidence)"
        " VALUES (%s,%s,%s,%s,'mock','done',%s,%s,%s,%s,%s)",
        (org, case_id, module_id, provider, qhash, Json(normalized), summary,
         Json(signals), confidence))

    cur.execute(
        "UPDATE public.triage_modules SET status='done',"
        " started_at = COALESCE(started_at, now()), finished_at = now(),"
        " attempts = attempts + 1, summary = %s, updated_at = now()"
        " WHERE id = %s", (summary, module_id))
    return signals


def run_case_triage(cur, org, case_id, user_id) -> dict:
    """Executa todos os módulos de triagem do caso; agrega risco e atualiza o caso."""
    cur.execute(
        "SELECT id, provider, module_key FROM public.triage_modules"
        " WHERE case_id = %s ORDER BY created_at, id", (case_id,))
    modules = cur.fetchall()
    all_signals: list[str] = []
    for m in modules:
        all_signals.extend(_run_module(cur, org, case_id, user_id, m))

    risk = "high" if any("alto" in s for s in all_signals) else (
        "medium" if all_signals else "low")
    cur.execute(
        "UPDATE public.cases SET status='triage_completed', progress=60,"
        " risk_level=%s WHERE id = %s", (risk, case_id))
    cur.execute(
        "INSERT INTO public.timeline_events"
        " (organization_id, case_id, event_type, title, description, actor)"
        " VALUES (%s,%s,'triage_executed','Triagem executada',"
        " 'Módulos de triagem processados (mock).','system')", (org, case_id))
    return {"modules_executed": len(modules), "risk_level": risk,
            "risk_signals": all_signals}
