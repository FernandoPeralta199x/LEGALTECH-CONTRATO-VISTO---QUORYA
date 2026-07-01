"""Execução da triagem (SP3) — roda os triage_modules de um caso, gravando
evidências em external_queries_cache (cache por org+provider+hash) e o resultado
normalizado em provider_results. A EVIDÊNCIA (payload normalizado) vem da camada de
adapters, resolvida pelo registry central (``src/adapters/registry.py``): hoje cai
no Mock (``*_BACKEND=mock``), e na Fase 7 basta implementar os ``Real*Adapter`` +
``*_BACKEND=real`` que este runner passa a consumir dados reais sem mudar. A
INTERPRETAÇÃO de triagem (risco/confiança/resumo) fica aqui, na camada de negócio.
Tudo no cursor de uma transação tenant_tx (RLS por org).
"""
from __future__ import annotations

import hashlib

from psycopg2.extras import Json

from src.adapters.registry import query_provider


def _interpret(provider: str, module_key: str):
    """Sinais de risco, confiança e resumo de triagem por provider (camada de negócio).

    Independe de a evidência vir do Mock ou do Real — só traduz o provider em
    semântica de triagem. Retorna ``(risk_signals, confidence, summary)``."""
    p = provider.lower()
    if "serasa" in p:
        return (["score_saudavel"], 0.9, "Score saudável, sem restrições (mock).")
    if "procon" in p:
        return ([], 0.85, "Sem reclamações no Procon (mock).")
    if "escavador" in p:
        return (["litigio_baixo"], 0.8, "1 processo público encontrado (mock).")
    if "targetdata" in p:
        return ([], 0.82, "Cadastro localizado e regular (mock).")
    if "ai_report" in p:
        return (["clausula_revisar"], 0.75, "Pré-relatório simulado com 1 risco (mock).")
    if "ai_summary" in p:
        return ([], 0.78, "Resumo simulado do documento (mock).")
    if "document_parser" in p or "ocr" in p:
        return ([], 0.95, "Documento lido e estruturado (mock).")
    return ([], 0.7, "Módulo simulado (mock).")


def _run_module(cur, org, case_id, user_id, module) -> list[str]:
    """Executa um módulo: cache de evidência + provider_results + atualiza o módulo."""
    provider = module["provider"]
    module_key = module["module_key"]
    module_id = module["id"]
    qhash = hashlib.sha256(f"{case_id}:{provider}:{module_key}".encode("utf-8")).hexdigest()

    # Evidência: resolvida pelo registry (adapter mock agora, real na Fase 7). Providers
    # locais (validação de partes) não têm adapter externo -> payload local mínimo.
    #
    # WIRING DA FASE 7 (antes de ligar *_BACKEND=real) — dois pontos:
    #  1) POPULAR o ctx com os insumos reais do caso: cpf_cnpj/name de case_parties
    #     (Serasa/Procon/Escavador/CNJ/TargetData) e file_path/text do documento
    #     ingerido (OCR/IA). Hoje o ctx só tem case_id/module_key e os mock ignoram o
    #     resto. ATENÇÃO LGPD: mascarar PII antes de gravar em external_queries_cache/
    #     provider_results (ver CVS-006) — os providers podem ecoar o documento na
    #     resposta. Fan-out por parte é decisão de produto.
    #  2) CACHE-FIRST: consultar external_queries_cache por (org, provider, qhash) e
    #     reusar o payload 'done' ANTES de chamar query_provider, evitando repetir
    #     chamada externa paga/rate-limited numa reexecução (idempotência real;
    #     provavelmente exige rastrear source_mode no cache).
    ctx = {"case_id": str(case_id), "module_key": module_key}
    adapter_result = query_provider(provider, ctx)
    if adapter_result is not None and adapter_result.success:
        normalized = adapter_result.data
        source_mode = adapter_result.source  # "mock" | "real"
    else:
        normalized = {"module_key": module_key, "ok": True}
        source_mode = "mock"

    signals, confidence, summary = _interpret(provider, module_key)
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
        " VALUES (%s,%s,%s,%s,%s,'done',%s,%s,%s,%s,%s)",
        (org, case_id, module_id, provider, source_mode, qhash, Json(normalized), summary,
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
