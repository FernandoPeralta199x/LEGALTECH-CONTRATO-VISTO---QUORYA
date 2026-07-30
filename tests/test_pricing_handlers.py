"""Coração 4 — testes de integração dos handlers de pricing (PG18 + RLS).

Cobre catálogo (com override da org), estimativa servidor, get/put config
(parcial + bump de version + RBAC admin), limit-check e isolamento por org.
"""
from _dbadmin import admin_conn
import json
import uuid

import psycopg2
import pytest

from src.handlers import pricing as pr_h

SYSTEM_ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"


def _admin_conn():
    return admin_conn()


def _reset():
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE public.pricing_configs RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE public.cases RESTART IDENTITY CASCADE")
    conn.close()


def _seed_user(user_id, org=SYSTEM_ORG, role="admin", status="active"):
    """Semeia public.users — necessário porque o PUT reconsulta o papel ATUAL no banco
    (assert_active_admin) para fechar a janela de revogação do JWT (SEC-02)."""
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s,%s,'x','Test',%s,%s,%s)"
            " ON CONFLICT (id) DO UPDATE SET role=EXCLUDED.role, status=EXCLUDED.status",
            (user_id, f"u_{user_id}@t.c", role, status, org))
    conn.close()


def _admin(org=SYSTEM_ORG):
    """user_id de um admin ATIVO e existente no banco (o PUT reconsulta o papel real)."""
    uid = str(uuid.uuid4())
    _seed_user(uid, org=org, role="admin", status="active")
    return uid


def _event(user_id, role="admin", body=None, path=None, org_id=SYSTEM_ORG):
    return {
        "requestContext": {"authorizer": {"user_id": user_id, "email": "u@t.c",
                                          "role": role, "perfil": "administrador", "organization_id": org_id}},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": {},
    }


def _data(resp):
    assert resp["statusCode"] in (200, 201), resp
    return json.loads(resp["body"])["data"]


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield


def test_catalogo_padrao():
    a = _admin()
    cat = _data(pr_h.get_pricing(_event(a), None))
    assert len(cat["products"]) == 4
    assert len(cat["modules"]) == 7
    assert cat["currency"] == "BRL"
    esc = next(m for m in cat["modules"] if m["code"] == "escavador")
    assert esc["price_cents"] == 6000
    # dados_partes: base = soma dos required (escavador+targetdata+ia) = 12800
    dp = next(p for p in cat["products"] if p["code"] == "dados_partes")
    assert dp["base_price_cents"] == 12800


def test_catalogo_aplica_override_da_org():
    a = _admin()
    # admin define override de Escavador para 6500
    _data(pr_h.update_pricing_config(
        _event(a, body={"module_overrides": {"escavador": {"price_cents": 6500}}}), None))
    cat = _data(pr_h.get_pricing(_event(a), None))
    esc = next(m for m in cat["modules"] if m["code"] == "escavador")
    assert esc["price_cents"] == 6500
    # base do dados_partes reflete o override: 6500+3900+2900 = 13300
    dp = next(p for p in cat["products"] if p["code"] == "dados_partes")
    assert dp["base_price_cents"] == 13300


def test_estimate_endpoint():
    a = _admin()
    est = _data(pr_h.estimate_pricing(
        _event(a, body={"product": "analise_contratual",
                        "modules": ["ia_deepseek", "analise_contratual_ia", "revisao_humana"]}), None))
    assert est["base_price_cents"] == 10800
    assert est["total_price_cents"] == 23700
    assert est["sla_hours"] == 72


def test_estimate_product_invalido_400():
    a = _admin()
    resp = pr_h.estimate_pricing(_event(a, body={"product": "xpto", "modules": []}), None)
    assert resp["statusCode"] == 400


def test_estimate_modulo_invalido_400():
    a = _admin()
    resp = pr_h.estimate_pricing(
        _event(a, body={"product": "dados_partes", "modules": ["nao_existe"]}), None)
    assert resp["statusCode"] == 400


def test_get_config_default_quando_nao_existe():
    a = _admin()
    cfg = _data(pr_h.get_pricing_config(_event(a), None))
    assert cfg["cases_limit"] is None
    assert cfg["module_overrides"] == {}
    assert cfg["version"] == 0


def test_put_config_parcial_e_bump_de_version():
    a = _admin()
    c1 = _data(pr_h.update_pricing_config(_event(a, body={"cases_limit": 10}), None))
    assert c1["cases_limit"] == 10 and c1["version"] == 1
    # atualização parcial: muda só notes, mantém cases_limit
    c2 = _data(pr_h.update_pricing_config(_event(a, body={"notes": "limite trimestral"}), None))
    assert c2["cases_limit"] == 10  # preservado
    assert c2["notes"] == "limite trimestral"
    assert c2["version"] == 2  # bump


def test_m5_for_update_serializa_config_concorrente():
    """M5: a leitura da config no PUT usa FOR UPDATE, travando a linha da org e
    serializando admins concorrentes (impede lost update de version)."""
    a = _admin()
    _data(pr_h.update_pricing_config(_event(a, body={"cases_limit": 10}), None))  # cria a linha (v1)
    ca = _admin_conn(); ca.autocommit = False
    cb = _admin_conn(); cb.autocommit = False
    try:
        with ca.cursor() as cur_a:
            cur_a.execute("SELECT version FROM public.pricing_configs"
                          " WHERE organization_id=%s FOR UPDATE", (SYSTEM_ORG,))
            assert cur_a.fetchone() is not None  # A detém o lock da linha
            with cb.cursor() as cur_b:
                cur_b.execute("SET lock_timeout='400ms'")
                with pytest.raises(psycopg2.errors.LockNotAvailable):
                    cur_b.execute("SELECT version FROM public.pricing_configs"
                                  " WHERE organization_id=%s FOR UPDATE", (SYSTEM_ORG,))
        ca.commit()
    finally:
        for c in (ca, cb):
            try:
                c.rollback()
            except Exception:
                pass
            c.close()


def test_put_config_sem_campos_400():
    a = _admin()
    assert pr_h.update_pricing_config(_event(a, body={}), None)["statusCode"] == 400


def test_put_config_modulo_invalido_400():
    a = _admin()
    resp = pr_h.update_pricing_config(
        _event(a, body={"module_overrides": {"inexistente": {"price_cents": 100}}}), None)
    assert resp["statusCode"] == 400


def test_admin_revogado_nao_atualiza_config():
    """SEC-02: fecha a janela de revogação do JWT no PUT /pricing/config.

    O token continua dizendo `admin` (válido por ~2h), mas o banco já diz
    viewer/inactive. O handler tem de reconsultar o papel ATUAL e recusar —
    sem escrever nada. Antes da correção este PUT gravava normalmente.
    """
    a = _admin()
    c1 = _data(pr_h.update_pricing_config(_event(a, body={"cases_limit": 10}), None))
    assert c1["version"] == 1  # baseline: o admin legítimo escreve

    # (a) rebaixado a viewer no banco DEPOIS da emissão do token
    _seed_user(a, role="viewer", status="active")
    resp = pr_h.update_pricing_config(_event(a, body={"cases_limit": 99}), None)
    assert resp["statusCode"] == 403, resp

    # (b) conta desativada (papel ainda admin)
    _seed_user(a, role="admin", status="inactive")
    resp = pr_h.update_pricing_config(_event(a, body={"cases_limit": 99}), None)
    assert resp["statusCode"] == 403, resp

    # (c) nada foi escrito: o valor e a version seguem os do admin legítimo
    _seed_user(a, role="admin", status="active")
    cfg = _data(pr_h.get_pricing_config(_event(a), None))
    assert cfg["cases_limit"] == 10, "o PUT revogado não pode ter alterado o valor"
    assert cfg["version"] == 1, "o PUT revogado não pode ter bumpado a version"


def test_viewer_nao_atualiza_config():
    v = str(uuid.uuid4())
    assert pr_h.update_pricing_config(
        _event(v, role="viewer", body={"cases_limit": 5}), None)["statusCode"] == 403
    # analyst também não (config é admin-only)
    an = str(uuid.uuid4())
    assert pr_h.update_pricing_config(
        _event(an, role="analyst", body={"cases_limit": 5}), None)["statusCode"] == 403


def test_limit_check():
    a = _admin()
    # sem limite -> permitido
    chk = _data(pr_h.check_cases_limit(_event(a), None))
    assert chk["cases_limit"] is None and chk["allowed"] is True
    # define limite 0... (cases_limit>=1) então usa 1 e cria 1 caso ativo
    _data(pr_h.update_pricing_config(_event(a, body={"cases_limit": 1}), None))
    conn = _admin_conn()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.organization_id', %s, false)", (SYSTEM_ORG,))
        cur.execute("INSERT INTO public.cases (organization_id, case_type, status, product_type,"
                    " product_label, title) VALUES (%s,'analise_contratual','open',"
                    " 'analise_contratual','Análise','Caso')", (SYSTEM_ORG,))
    conn.close()
    chk2 = _data(pr_h.check_cases_limit(_event(a), None))
    assert chk2["cases_limit"] == 1 and chk2["active_cases_count"] == 1
    assert chk2["allowed"] is False


def test_isolamento_config_por_org():
    # `a` escreve a config (PUT reconsulta o papel no banco -> precisa existir);
    # `b` só faz GET noutra org, então não precisa de seed.
    a, b = _admin(), str(uuid.uuid4())
    _data(pr_h.update_pricing_config(_event(a, body={"cases_limit": 7}), None))
    # outra org não enxerga a config (RLS) -> default
    cfg_other = _data(pr_h.get_pricing_config(_event(b, org_id=OTHER_ORG), None))
    assert cfg_other["cases_limit"] is None and cfg_other["version"] == 0


def test_estimate_inclui_installment_options_e_version():
    a = _admin()
    # admin habilita parcelamento
    cfg = {"installment_config": {"enabled": True, "max_parcelas": 6, "sem_juros_ate": 3,
           "juros_mensal_bps": 299, "valor_minimo_parcela_cents": 0,
           "primeiro_vencimento_dias": 30, "dia_vencimento": 10,
           "allowed_methods": {"cartao": {"enabled": True, "max_parcelas": 6}}}}
    pr_h.update_pricing_config(_event(a, body=cfg), None)
    resp = pr_h.estimate_pricing(_event(a, body={"product": "analise_contratual", "modules": []}), None)
    data = _data(resp)
    assert "installment_options" in data and len(data["installment_options"]) >= 1
    assert "pricing_config_version" in data and "payment_mode" in data


def test_put_config_todos_metodos_desabilitados_400():
    # P-1 server-side: allowed_methods com TODOS enabled=False deixaria a org sem
    # forma de cobrar -> 400 (espelha a trava P-1 da tela, antes só no cliente).
    a = _admin()
    cfg = {"installment_config": {"enabled": True, "max_parcelas": 1, "allowed_methods": {
        "pix": {"enabled": False, "max_parcelas": 1},
        "boleto": {"enabled": False, "max_parcelas": 1},
        "cartao": {"enabled": False, "max_parcelas": 1},
        "debito": {"enabled": False, "max_parcelas": 1}}}}
    resp = pr_h.update_pricing_config(_event(a, body=cfg), None)
    assert resp["statusCode"] == 400
    assert "método de pagamento" in json.loads(resp["body"])["error"]


def test_put_config_um_metodo_habilitado_ok():
    a = _admin()
    cfg = {"installment_config": {"enabled": True, "max_parcelas": 1, "allowed_methods": {
        "pix": {"enabled": True, "max_parcelas": 1},
        "boleto": {"enabled": False, "max_parcelas": 1},
        "cartao": {"enabled": False, "max_parcelas": 1}}}}
    assert pr_h.update_pricing_config(_event(a, body=cfg), None)["statusCode"] in (200, 201)


def test_put_config_allowed_methods_vazio_ok():
    # allowed_methods vazio => o serviço aplica os defaults (todos habilitados); não bloqueia
    a = _admin()
    cfg = {"installment_config": {"enabled": True, "max_parcelas": 1}}
    assert pr_h.update_pricing_config(_event(a, body=cfg), None)["statusCode"] in (200, 201)


def test_schema_payment_selection_valida():
    from src.schemas.pricing_schemas import PaymentSelectionSchema
    s = PaymentSelectionSchema(parcelas=3, method="cartao", idempotency_key="k",
                               card_token="tok_mock_x")
    assert s.parcelas == 3 and s.method == "cartao"
    import pytest
    # cartão exige card_token (tokenização client-side)
    with pytest.raises(Exception):
        PaymentSelectionSchema(parcelas=3, method="cartao", idempotency_key="k")
    # campo cru de cartão é rejeitado (extra="forbid")
    with pytest.raises(Exception):
        PaymentSelectionSchema(parcelas=1, method="cartao", idempotency_key="k",
                               card_token="t", card_number="4111111111111111")
    with pytest.raises(Exception):
        PaymentSelectionSchema(parcelas=0, method="pix", idempotency_key="k")
    with pytest.raises(Exception):
        PaymentSelectionSchema(parcelas=1, method="doge", idempotency_key="k")


def test_update_config_grava_auditoria():
    # #26 + migration 013: mudança de config grava change_after em audit.audit_log
    a = _admin()
    _data(pr_h.update_pricing_config(_event(a, body={"cases_limit": 4242}), None))
    conn = _admin_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT action, resource_type, change_after FROM audit.audit_log"
                    " WHERE resource_type='pricing_configs'"
                    " ORDER BY created_at DESC, id DESC LIMIT 1")
        row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row[0] in ("INSERT", "UPDATE") and row[1] == "pricing_configs"
    assert row[2] and row[2].get("cases_limit") == 4242  # change_after audita o novo valor


def test_put_config_override_acima_do_teto_400():
    # Regressão pentest #7: override de preço absurdo (> MAX_PRICE_CENTS) -> 400.
    _reset()
    a = _admin()
    cfg = {"module_overrides": {"escavador": {"price_cents": 10 ** 15}}}
    resp = pr_h.update_pricing_config(_event(a, body=cfg), None)
    assert resp["statusCode"] == 400


def test_put_config_product_override_rejeitado_400():
    # product_overrides é vestigial (preço do produto = soma dos módulos). Um override de
    # produto NÃO pode ser persistido silenciosamente (config "morta" que engana o admin):
    # o handler rejeita 400 e nada é gravado (o catálogo/estimate seguem o padrão).
    _reset()
    a = _admin()
    resp = pr_h.update_pricing_config(
        _event(a, body={"product_overrides": {"analise_contratual": {"base_price_cents": 999}}}),
        None)
    assert resp["statusCode"] == 400
    assert "produto" in json.loads(resp["body"])["error"].lower()
    # nada persistido: config segue default (sem row) -> version 0, sem overrides
    cfg = _data(pr_h.get_pricing_config(_event(a), None))
    assert cfg["product_overrides"] == {} and cfg["version"] == 0
