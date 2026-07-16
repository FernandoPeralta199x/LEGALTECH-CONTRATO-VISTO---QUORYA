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
from src.utils.pii import mask_document, mask_email, mask_phone

# M13/LGPD (CVS-006): chaves cuja evidência pode carregar PII e que devem ser
# mascaradas ANTES de persistir em external_queries_cache/provider_results. Hoje os
# mocks não emitem PII real (o ctx não é populado); na Fase 7 os Real*Adapter podem
# ecoar documentos/contatos — o masking fecha o caminho de persistência por padrão.
_PII_DOC_KEYS = {"cpf_cnpj", "cpf", "cnpj", "document", "documento",
                 "document_number", "rg"}
_PII_EMAIL_KEYS = {"email", "e_mail"}
_PII_PHONE_KEYS = {"phone", "telefone", "celular"}


def _mask_evidence(value):
    """Mascara PII conhecida (documento/e-mail/telefone) recursivamente numa
    estrutura de evidência, preservando o restante. Aplicado antes de gravar."""
    if isinstance(value, dict):
        masked = {}
        for k, v in value.items():
            kl = str(k).lower()
            if kl in _PII_DOC_KEYS and isinstance(v, str) and v:
                masked[k] = mask_document(v)
            elif kl in _PII_EMAIL_KEYS and isinstance(v, str) and v:
                masked[k] = mask_email(v)
            elif kl in _PII_PHONE_KEYS and isinstance(v, str) and v:
                masked[k] = mask_phone(v)
            else:
                masked[k] = _mask_evidence(v)
        return masked
    if isinstance(value, list):
        return [_mask_evidence(v) for v in value]
    return value

# Vocabulário explícito de sinais de ALTO risco. A classificação NÃO deve depender de
# substring de rótulo livre em PT (o antigo `any("alto" in s)` nunca casava com os sinais
# emitidos, tornando o nível 'high' inalcançável). Os mocks atuais não emitem estes; os
# Real*Adapter da Fase 7 devem usá-los para que 'high' seja alcançável de forma determinística.
HIGH_RISK_SIGNALS = frozenset({
    "litigio_alto", "score_baixo", "restricao_grave", "reclamacao_grave",
    "clausula_critica", "processo_relevante",
})


def classify_risk(signals) -> str:
    """Nível de risco agregado a partir do vocabulário de sinais.

    - ``high``: qualquer sinal de alto risco explícito (``HIGH_RISK_SIGNALS``);
    - ``medium``: há sinais, mas nenhum de alto risco;
    - ``low``: nenhum sinal.

    Preserva o comportamento dos mocks atuais (que não emitem sinais de alto risco) e
    deixa o ramo 'high' alcançável de forma determinística quando os adapters reais entrarem."""
    if any(s in HIGH_RISK_SIGNALS for s in signals):
        return "high"
    return "medium" if signals else "low"


def _to_int(value) -> int:
    """Coerção defensiva para int (evidência pode vir ausente ou como string)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _interpret_serasa(ev: dict):
    score = ev.get("score")
    restritivos = (_to_int(ev.get("pendencies")) + _to_int(ev.get("protests"))
                   + _to_int(ev.get("bounced_checks")))
    signals = []
    if restritivos > 0:
        signals.append("restricao_grave")
    if isinstance(score, (int, float)) and score < 500:
        signals.append("score_baixo")
    if signals:
        return (signals, 0.9,
                f"Restrições de crédito (score={score}, {restritivos} restritivo(s)).")
    if score is None:
        return ([], 0.6, "Consulta de crédito sem dados de score.")
    return ([], 0.9, f"Score de crédito saudável ({score}), sem restrições.")


def _interpret_procon(ev: dict):
    complaints = _to_int(ev.get("complaints"))
    resolution = ev.get("resolution_rate")
    low_resolution = isinstance(resolution, (int, float)) and resolution < 0.5
    if complaints >= 3 or (complaints > 0 and low_resolution):
        return (["reclamacao_grave"], 0.85,
                f"{complaints} reclamação(ões) no Procon com baixa resolução.")
    if complaints > 0:
        return (["clausula_revisar"], 0.85,
                f"{complaints} reclamação(ões) no Procon, majoritariamente resolvidas.")
    return ([], 0.85, "Sem reclamações no Procon.")


def _interpret_escavador(ev: dict):
    total = _to_int(ev.get("total")) or len(ev.get("lawsuits") or [])
    if total >= 5:
        return (["litigio_alto"], 0.8, f"{total} processos públicos (volume alto).")
    if total >= 1:
        return (["litigio_baixo"], 0.8, f"{total} processo(s) público(s) encontrado(s).")
    return ([], 0.8, "Nenhum processo público encontrado.")


def _interpret_cnj(ev: dict):
    total = _to_int(ev.get("total")) or len(ev.get("lawsuits") or ev.get("processes") or [])
    if total >= 5:
        return (["processo_relevante"], 0.8, f"{total} processos no CNJ (volume relevante).")
    if total >= 1 or ev.get("process_number"):
        return (["litigio_baixo"], 0.8, "Processo(s) localizado(s) no CNJ.")
    return ([], 0.8, "Nenhum processo localizado no CNJ.")


def _interpret_targetdata(ev: dict):
    if ev.get("found") is False:
        return (["cadastro_nao_localizado"], 0.6, "Cadastro não localizado no enriquecimento.")
    status = (ev.get("registration_status") or "").lower()
    if status and status not in ("regular", "ativa", "ativo", "active"):
        return (["restricao_grave"], 0.82, f"Situação cadastral irregular: {status}.")
    return ([], 0.82, "Cadastro localizado e regular.")


def _interpret_ai(ev: dict):
    critical = ev.get("critical_clauses") or ev.get("high_risk_clauses") or []
    risks = ev.get("risks")
    n_risks = len(risks) if isinstance(risks, list) else _to_int(risks)
    overall = (ev.get("overall_risk") or "").lower()
    summary = str(ev.get("summary") or "Análise de IA executada.")[:500]
    conf = ev.get("confidence")
    conf = float(conf) if isinstance(conf, (int, float)) else 0.75
    if critical or overall in ("high", "alto", "critical", "critico"):
        return (["clausula_critica"], conf, summary)
    if n_risks > 0 or overall in ("medium", "moderado", "medio"):
        return (["clausula_revisar"], conf, summary)
    return ([], conf, summary)


def _interpret(provider: str, module_key: str, evidence):
    """Deriva ``(risk_signals, confidence, summary)`` a partir da EVIDÊNCIA
    normalizada do adapter — NÃO do nome do provider (A2).

    Com a evidência limpa dos mocks o risco fica baixo/médio; quando os
    ``Real*Adapter`` (Fase 7) devolverem evidência de risco (score baixo,
    restritivos, muitos processos, cláusula crítica) o sinal entra em
    ``HIGH_RISK_SIGNALS`` e ``classify_risk`` alcança ``'high'`` de forma
    determinística. Fail-safe: evidência ausente -> sem sinais (não inventa risco)."""
    ev = evidence if isinstance(evidence, dict) else {}
    p = (provider or "").lower()
    if "serasa" in p:
        return _interpret_serasa(ev)
    if "procon" in p:
        return _interpret_procon(ev)
    if "escavador" in p or "public_search" in p:
        return _interpret_escavador(ev)
    if "cnj" in p:
        return _interpret_cnj(ev)
    if "targetdata" in p:
        return _interpret_targetdata(ev)
    if "ai" in p or "reputation" in p:
        return _interpret_ai(ev)
    if "ocr" in p or "document_parser" in p:
        return ([], 0.95, "Documento lido e estruturado.")
    return ([], 0.7, "Módulo executado sem sinais de risco.")


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

    # A3: TRÊS estados distintos (antes "sem adapter" e "adapter falhou" eram
    # colapsados no mesmo 'done'/source_mode='mock'/ok=True, mascarando falha real).
    error_message = None
    if adapter_result is None:
        # provider local (validação de partes): não há adapter externo.
        normalized = {"module_key": module_key, "ok": True}
        source_mode = "local"
        status = "done"
    elif adapter_result.success:
        normalized = adapter_result.data
        source_mode = adapter_result.source  # "mock" | "real"
        status = "done"
    else:
        # FALHA do provider (real indisponível): NÃO forjar mock/ok=True — registra
        # erro honesto, preservando a mensagem do adapter, sem gerar evidência falsa.
        normalized = {"module_key": module_key, "ok": False}
        source_mode = adapter_result.source
        status = "error"
        error_message = adapter_result.error or "provider externo indisponível"

    # A2: interpreta a EVIDÊNCIA (não o nome do provider). Em falha, sem sinais de risco.
    if status == "error":
        signals, confidence, summary = [], 0.0, f"Falha ao consultar {provider}."
    else:
        signals, confidence, summary = _interpret(provider, module_key, normalized)
    # M13/LGPD: interpreta na evidência CRUA (acima), mas persiste MASCARADA (abaixo).
    normalized = _mask_evidence(normalized)
    req_payload = {"case_id": str(case_id), "module_key": module_key, "provider": provider}

    # cache de evidência (idempotente por org+provider+hash)
    cur.execute(
        "INSERT INTO public.external_queries_cache"
        " (organization_id, case_id, provider, query_hash, request_payload,"
        "  response_payload, normalized_payload, status, error_message, requested_by)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (organization_id, provider, query_hash) DO UPDATE SET"
        "  response_payload = EXCLUDED.response_payload,"
        "  normalized_payload = EXCLUDED.normalized_payload,"
        "  status = EXCLUDED.status, error_message = EXCLUDED.error_message,"
        "  updated_at = now()",
        (org, case_id, provider, qhash, Json(req_payload), Json(normalized),
         Json(normalized), status, error_message, user_id))

    # provider_results (limpa-e-regera por módulo)
    cur.execute("DELETE FROM public.provider_results WHERE triage_module_id = %s", (module_id,))
    cur.execute(
        "INSERT INTO public.provider_results"
        " (organization_id, case_id, triage_module_id, provider, source_mode, status,"
        "  input_hash, normalized_result, summary, risk_signals, confidence, error_message)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (org, case_id, module_id, provider, source_mode, status, qhash, Json(normalized),
         summary, Json(signals), confidence, error_message))

    cur.execute(
        "UPDATE public.triage_modules SET status=%s,"
        " started_at = COALESCE(started_at, now()), finished_at = now(),"
        " attempts = attempts + 1, summary = %s, error_message = %s, updated_at = now()"
        " WHERE id = %s", (status, summary, error_message, module_id))
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

    risk = classify_risk(all_signals)
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
