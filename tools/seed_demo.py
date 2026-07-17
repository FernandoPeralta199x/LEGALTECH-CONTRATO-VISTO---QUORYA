"""Re-semeia dados demo no banco de DEV, reusando os handlers reais — os dados
nascem idênticos ao que o app espera (RLS/tenant_tx, validações, triggers de
auditoria), em vez de INSERTs crus que burlariam essas camadas.

Uso (a partir da raiz do backend, com o Postgres de dev na 5433):
    .venv\\Scripts\\python.exe -m tools.seed_demo

O que faz (tudo idempotente):
  1. Garante a ORG, o USUÁRIO demo (login demo@contratovisto.com, senha vinda de
     DEMO_PASSWORD no .env local) e um PRICING_CONFIG com parcelamento — recriando-os
     se faltarem. Isso cobre o caso
     de a suíte COMPLETA do pytest ter truncado users/pricing_configs.
  2. Cria 1 cliente + 1 caso PELO WIZARD (create_request) — com pedido, preço e plano
     de parcelamento, então o caso é PAGÁVEL (a tela /cases/{id}/pagamento funciona).

NOTA (histórico): o pytest NÃO trunca mais o banco de dev — desde o banco de teste
separado (contrato_visto_test, ver docs/ADR-002), a suíte roda isolada. Este script
deixou de ser rede de segurança e virou uma ferramenta de RESET INTENCIONAL do dev.

UUIDs demo são fixos (DEMO_USER/DEMO_ORG) e recriados aqui — assim ``created_by`` do
caso aponta para um usuário real (``created_by`` não tem FK, mas o login precisa do
usuário com esse id para o contexto bater com o do seed).
"""
import json
import os
import random
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from src.handlers import clients as clients_h  # noqa: E402
from src.handlers import requests as requests_h  # noqa: E402
from src.handlers.users import hash_password  # noqa: E402
from src.services.database import simple_tx, tenant_tx  # noqa: E402

DEMO_USER = "fb7f5bb8-224e-441f-a4fd-f4f3a0cb6d39"
DEMO_ORG = "9cb5e294-4891-4578-be38-fab31114c559"
DEMO_ROLE = "admin"
DEMO_EMAIL = "demo@contratovisto.com"
# A senha NÃO é versionada: vem obrigatoriamente do ambiente (.env local, gitignorado).
# Ausente => aborta no topo de main() antes de qualquer escrita (nenhuma senha literal aqui).
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD")

# Allowlist de ambientes de DEV (mesmo critério de src/utils/safety.py::_NON_PROD_ENVS).
# Qualquer outro stage (prod, staging, homolog, qa, ...) é tratado como produtivo.
_DEV_ENVS = {"", "local", "dev", "development", "test", "testing"}
_LOCAL_DB_HOSTS = {"localhost", "127.0.0.1"}

# Parcelamento demo: à vista (Pix/Boleto/Débito) + cartão de crédito em até 6x com juros leves.
_DEMO_INSTALLMENT = {
    "enabled": True,
    "max_parcelas": 6,
    "sem_juros_ate": 3,
    "juros_mensal_bps": 199,
    "valor_minimo_parcela_cents": 0,
    "primeiro_vencimento_dias": 30,
    "dia_vencimento": None,
    "allowed_methods": {
        "pix": {"enabled": True, "max_parcelas": 1},
        "boleto": {"enabled": True, "max_parcelas": 1},
        "cartao": {"enabled": True, "max_parcelas": 6},
        "debito": {"enabled": True, "max_parcelas": 1},  # débito: à vista (1x)
    },
}


def _cnpj_dv(base: str, pesos: list) -> str:
    soma = sum(int(base[n]) * pesos[n] for n in range(len(pesos)))
    dv = soma % 11
    return "0" if dv < 2 else str(11 - dv)


def gen_valid_cnpj() -> str:
    """CNPJ com dígitos verificadores corretos e único por execução (evita colisão
    de document_number ao re-rodar o seed)."""
    base = "".join(str(random.randint(0, 9)) for _ in range(8)) + "0001"
    d1 = _cnpj_dv(base, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = _cnpj_dv(base + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base + d1 + d2


def _event(body=None, path=None, query=None) -> dict:
    return {
        "requestContext": {"authorizer": {
            "user_id": DEMO_USER, "email": DEMO_EMAIL, "role": DEMO_ROLE,
            "organization_id": DEMO_ORG,
        }},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": query or {},
    }


def _data(resp: dict) -> dict:
    if resp["statusCode"] not in (200, 201):
        raise RuntimeError(f"handler retornou {resp['statusCode']}: {resp.get('body')}")
    return json.loads(resp["body"])["data"]


def ensure_org_user_pricing() -> None:
    """Recria org + usuário demo + pricing_config quando faltarem (idempotente).

    Necessário porque a suíte de users/pricing do pytest (se rodada contra o dev,
    o que o banco de teste separado agora evita) trunca essas tabelas globais."""
    # 1) organização — RLS forçada exige app.organization_id (tenant_tx cede isso)
    with tenant_tx(DEMO_USER, DEMO_ROLE, DEMO_ORG) as cur:
        cur.execute(
            "INSERT INTO public.organizations (id, name) VALUES (%s, %s)"
            " ON CONFLICT (id) DO NOTHING",
            (DEMO_ORG, "Organização Demo"))
    # 2) usuário — public.users é global (sem RLS) -> simple_tx; senha via bcrypt
    with simple_tx() as cur:
        # ON CONFLICT DO UPDATE (não DO NOTHING): re-rodar com uma DEMO_PASSWORD nova aplica
        # de fato a nova senha. Com DO NOTHING, a senha antiga permaneceria e a mensagem final
        # anunciaria uma senha que não foi gravada (enganoso).
        cur.execute(
            "INSERT INTO public.users"
            " (id, email, password_hash, name, role, status, organization_id)"
            " VALUES (%s, %s, %s, %s, 'admin', 'active', %s)"
            " ON CONFLICT (id) DO UPDATE SET password_hash = EXCLUDED.password_hash,"
            "   status = 'active'",
            (DEMO_USER, DEMO_EMAIL, hash_password(DEMO_PASSWORD), "Demo Admin", DEMO_ORG))
    # 3) pricing_config com parcelamento — RLS + trigger de auditoria (app.user_id
    #    já setado pelo tenant_tx). Só cria se ainda não existir (não sobrescreve).
    with tenant_tx(DEMO_USER, DEMO_ROLE, DEMO_ORG) as cur:
        cur.execute(
            "SELECT 1 FROM public.pricing_configs WHERE organization_id = %s", (DEMO_ORG,))
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO public.pricing_configs"
                " (organization_id, cases_limit, product_overrides, module_overrides,"
                "  installment_config, notes, updated_by, version)"
                " VALUES (%s, NULL, '{}'::jsonb, '{}'::jsonb, %s::jsonb, NULL, %s, 1)",
                (DEMO_ORG, json.dumps(_DEMO_INSTALLMENT), DEMO_USER))
    print("[seed] org/usuário/pricing demo garantidos")


def _abortar_se_destino_nao_for_local() -> None:
    """Trava fail-closed: só permite semear se o destino for o banco LOCAL de dev.

    Sem isto, rodar o seed apontando por engano para um banco compartilhado/produção
    criaria um admin com senha conhecida. Espelha o critério de ambiente de
    src/utils/safety.py (allowlist de dev) e exige host de banco local."""
    db_host = os.getenv("DB_HOST", "localhost")
    environment = os.getenv("ENVIRONMENT", "local").lower()
    if db_host not in _LOCAL_DB_HOSTS or environment not in _DEV_ENVS:
        sys.exit(
            f"seed_demo é só para banco LOCAL de desenvolvimento "
            f"(DB_HOST={db_host}, ENVIRONMENT={environment}) — abortado para não "
            f"criar admin com senha conhecida fora de dev")


def main() -> None:
    # TRAVA DE DESTINO (fail-closed) ANTES de qualquer escrita: aborta se o alvo não
    # for o banco local de dev, ou se a senha demo não foi definida no ambiente.
    _abortar_se_destino_nao_for_local()
    if not DEMO_PASSWORD:
        sys.exit(
            "DEMO_PASSWORD não definido — defina DEMO_PASSWORD no seu .env local "
            "(já gitignorado) antes de rodar o seed; nenhuma senha é versionada aqui")
    ensure_org_user_pricing()

    cnpj = gen_valid_cnpj()
    client = _data(clients_h.create_client(_event({
        "legal_name": "Cliente Demo Ltda",
        "document_type": "cnpj",
        "document_number": cnpj,
        "email": "contato@clientedemo.com.br",
        "phone": "11999990000",
        "address_city": "Sao Paulo",
        "address_state": "SP",
    }), None))
    print(f"[seed] cliente: {client['id']} (CNPJ {cnpj})")

    # Caso pelo WIZARD (create_request): nasce com pedido + preço + plano de triagem,
    # então é PAGÁVEL (diferente do antigo create_case, que criava caso sem pedido).
    req = _data(requests_h.create_request(_event({
        "product_type": "analise_contratual",
        "title": "Caso Demo — Análise Contratual",
        "client_id": client["id"],
        "parties": [
            {"name": "Cliente Demo Ltda", "role": "contratante", "person_type": "company",
             "document": cnpj, "document_type": "cnpj"},
            {"name": "Contraparte Demo", "role": "contratado", "person_type": "individual"},
        ],
        "selected_modules": ["ia_deepseek", "analise_contratual_ia"],
        "document": {"filename": "contrato_demo.pdf", "size_bytes": 12345},
    }), None))
    total = req.get("total_price_cents")
    print(f"[seed] caso:    {req['case_id']} {req.get('case_code', '')} "
          f"(pedido {req.get('request_id')}, total R$ {(total or 0) / 100:.2f})")

    with tenant_tx(DEMO_USER, DEMO_ROLE, DEMO_ORG) as cur:
        cur.execute("SELECT count(*) AS n FROM public.clients")
        c = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM public.cases")
        k = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM public.documents")
        d = cur.fetchone()["n"]
    print(f"[seed] OK -- org demo: clients={c} cases={k} documents={d}")
    # Não imprime a senha em texto puro (vazaria no stdout/log). Ela é a DEMO_PASSWORD do .env.
    print(f"[seed] login: {DEMO_EMAIL} / (senha = DEMO_PASSWORD do seu .env local)")


if __name__ == "__main__":
    main()
