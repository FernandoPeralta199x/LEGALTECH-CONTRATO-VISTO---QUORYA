"""A2 — a interpretação de triagem deriva sinais da EVIDÊNCIA do adapter, não do
nome do provider. Sem banco: exercita `_interpret` + `classify_risk` diretamente.

Antes: `_interpret(provider, module_key)` devolvia sinais hardcoded por nome do
provider e ignorava `adapter_result.data`; a partir de dados reais, o nível 'high'
era INALCANÇÁVEL. Estes testes travam a correção.
"""
from src.services.triage_runner import _interpret, _mask_evidence, classify_risk


def test_m13_mask_evidence_mascara_pii_conhecida():
    ev = {
        "cpf_cnpj": "12345678909",
        "score": 720,
        "email": "fulano@example.com",
        "phone": "11987654321",
        "lawsuits": [{"parties": ["A", "B"], "document": "11222333000181"}],
        "nested": {"rg": "123456789"},
    }
    out = _mask_evidence(ev)
    assert out["cpf_cnpj"] == "***.***.***-09"          # documento mascarado
    assert out["score"] == 720                            # não-PII preservado
    assert out["email"] == "f*****@example.com"           # e-mail mascarado
    assert out["phone"] == "(11) ****-**21"               # telefone mascarado
    assert out["lawsuits"][0]["document"] == "**.***.***/****-81"  # recursivo em lista
    assert out["nested"]["rg"] not in (None, "123456789")  # rg nunca cru
    # imutabilidade: não altera o dict original
    assert ev["cpf_cnpj"] == "12345678909"


def _risk(provider, evidence):
    signals, _conf, _summary = _interpret(provider, "modk", evidence)
    return signals, classify_risk(signals)


# ── Serasa: risco vem do score/restritivos, não do nome ──

def test_serasa_evidencia_limpa_nao_e_alto():
    signals, risk = _risk("mock_serasa",
                          {"score": 720, "pendencies": 0, "protests": 0, "bounced_checks": 0})
    assert "score_baixo" not in signals and "restricao_grave" not in signals
    assert risk != "high"


def test_serasa_evidencia_de_risco_gera_high():
    signals, risk = _risk("real_serasa",
                          {"score": 210, "pendencies": 3, "protests": 1, "bounced_checks": 0})
    assert "restricao_grave" in signals and "score_baixo" in signals
    assert risk == "high"


def test_interpret_ignora_nome_do_provider():
    """Mesmo provider ('serasa'), evidências opostas -> sinais opostos.
    Prova que a decisão vem da evidência, não do rótulo (A2)."""
    limpo, _ = _risk("serasa", {"score": 800, "pendencies": 0})
    risco, _ = _risk("serasa", {"score": 100, "pendencies": 5})
    assert limpo == [] and "score_baixo" in risco


# ── Demais bureaus ──

def test_procon_reclamacao_grave_gera_high():
    _, risk = _risk("real_procon", {"complaints": 5, "resolution_rate": 0.2})
    assert risk == "high"


def test_escavador_volume_alto_gera_high_e_baixo_e_medium():
    _, high = _risk("real_escavador", {"total": 7})
    _, med = _risk("mock_escavador", {"total": 2})
    _, low = _risk("mock_escavador", {"total": 0})
    assert (high, med, low) == ("high", "medium", "low")


def test_targetdata_irregular_gera_high_e_regular_limpo():
    _, irregular = _risk("real_targetdata", {"found": True, "registration_status": "irregular"})
    signals_reg, regular = _risk("mock_targetdata", {"found": True, "registration_status": "regular"})
    assert irregular == "high"
    assert signals_reg == [] and regular == "low"


def test_ai_clausula_critica_gera_high():
    _, critico = _risk("real_ai_report", {"critical_clauses": ["rescisão abusiva"]})
    _, moderado = _risk("mock_ai_summary", {"summary": "ok", "risks": ["x"]})
    assert critico == "high" and moderado == "medium"


# ── Fail-safe: sem evidência, não inventa risco ──

def test_evidencia_ausente_nao_gera_sinal():
    for provider in ("serasa", "procon", "escavador", "cnj", "targetdata", "ai_summary"):
        signals, _c, _s = _interpret(provider, "modk", {})
        assert all(s not in ({"score_baixo", "restricao_grave", "reclamacao_grave",
                              "litigio_alto", "processo_relevante", "clausula_critica"})
                   for s in signals), f"{provider} inventou risco de evidência vazia"


def test_high_alcancavel_ponta_a_ponta():
    """O bug original: 'high' era inalcançável a partir de evidência real. Agora não."""
    signals, _c, _s = _interpret("real_serasa", "credito",
                                 {"score": 150, "pendencies": 4})
    assert classify_risk(signals) == "high"
