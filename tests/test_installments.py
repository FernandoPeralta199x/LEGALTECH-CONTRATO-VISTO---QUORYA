# tests/test_installments.py
from datetime import date
from src.services.pricing.installments import InstallmentConfig, compute_installment_options

REF = date(2026, 7, 3)

def _cfg(**kw):
    base = dict(enabled=True, max_parcelas=12, sem_juros_ate=3, juros_mensal_bps=299,
                valor_minimo_parcela_cents=0, primeiro_vencimento_dias=30, dia_vencimento=10,
                allowed_methods={"pix": {"enabled": True, "max_parcelas": 1},
                                 "boleto": {"enabled": True, "max_parcelas": 1},
                                 "cartao": {"enabled": True, "max_parcelas": 12}})
    base.update(kw)
    return InstallmentConfig(**base)

def test_sem_juros_divide_exato_e_ultima_absorve_residuo():
    opts = compute_installment_options(10000, _cfg(), REF)
    by_n = {o["parcelas"]: o for o in opts}
    assert by_n[1]["valor_total_cents"] == 10000 and by_n[1]["has_juros"] is False
    tres = by_n[3]
    assert tres["has_juros"] is False and tres["valor_total_cents"] == 10000
    assert sum(i["valor_cents"] for i in tres["schedule"]) == 10000
    assert tres["schedule"][-1]["valor_cents"] == 3334  # 3333,3333,3334

def test_config_desabilitada_so_1x():
    opts = compute_installment_options(10000, _cfg(enabled=False), REF)
    assert [o["parcelas"] for o in opts] == [1]

def test_total_zero_retorna_somente_1x_de_zero():
    opts = compute_installment_options(0, _cfg(), REF)
    assert len(opts) == 1  # spec §4.6: total 0 => APENAS 1x
    assert opts[0]["parcelas"] == 1 and opts[0]["valor_total_cents"] == 0

def test_total_negativo_falha():
    import pytest
    with pytest.raises(ValueError):
        compute_installment_options(-1, _cfg(), REF)

def test_sem_juros_ate_maior_que_max_falha():
    import pytest
    with pytest.raises(Exception):
        _cfg(sem_juros_ate=20, max_parcelas=12)

def test_com_juros_soma_exata_e_ultima_fecha():
    opts = compute_installment_options(23700, _cfg(), REF)
    seis = next(o for o in opts if o["parcelas"] == 6)
    assert seis["has_juros"] is True and seis["juros_mensal_bps"] == 299
    assert sum(i["valor_cents"] for i in seis["schedule"]) == seis["valor_total_cents"]
    assert seis["valor_total_cents"] > 23700 and seis["acrescimo_cents"] > 0
    assert all(i["valor_cents"] == seis["valor_parcela_cents"] for i in seis["schedule"][:-1])

def test_juros_zero_nunca_cobra_juros():
    opts = compute_installment_options(10000, _cfg(juros_mensal_bps=0), REF)
    assert all(o["has_juros"] is False for o in opts)

def test_valor_minimo_descarta_opcoes():
    opts = compute_installment_options(10000, _cfg(valor_minimo_parcela_cents=3000), REF)
    ns = [o["parcelas"] for o in opts]
    assert 1 in ns and max(ns) <= 3  # 4x=2500 < 3000 é descartado

def test_cronograma_vira_o_ano_e_dia_fixo():
    opts = compute_installment_options(23700, _cfg(primeiro_vencimento_dias=180), REF)
    seis = next(o for o in opts if o["parcelas"] == 6)
    dias = {i["vencimento"][8:10] for i in seis["schedule"]}
    assert dias == {"10"}  # todos no dia 10
    assert any(i["vencimento"].startswith("2027") for i in seis["schedule"])


def test_somente_cartao_parcela_pix_boleto_1x():
    import pytest
    # pix/boleto com max > 1 é rejeitado
    with pytest.raises(Exception):
        _cfg(allowed_methods={"pix": {"enabled": True, "max_parcelas": 3},
                              "cartao": {"enabled": True, "max_parcelas": 12}})
    with pytest.raises(Exception):
        _cfg(allowed_methods={"boleto": {"enabled": True, "max_parcelas": 2}})
    # cartão parcela normalmente; pix/boleto 1x são aceitos
    cfg = _cfg(allowed_methods={"pix": {"enabled": True, "max_parcelas": 1},
                                "boleto": {"enabled": True, "max_parcelas": 1},
                                "cartao": {"enabled": True, "max_parcelas": 6}})
    # em 3x (parcelado) só o cartão é ofertado
    opts = compute_installment_options(23700, cfg, REF)
    tres = next(o for o in opts if o["parcelas"] == 3)
    assert tres["allowed_methods"] == ["cartao"]
    umx = next(o for o in opts if o["parcelas"] == 1)
    assert set(umx["allowed_methods"]) == {"pix", "boleto", "cartao"}
