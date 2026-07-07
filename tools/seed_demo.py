"""Re-semeia dados demo no DB de dev depois de um `pytest` (que trunca
clients/cases/documents/audit_log). Cria 1 cliente + 1 caso + 1 documento sob a
organização do usuário demo, REUSANDO os handlers reais — assim os dados nascem
idênticos ao que o app espera (respeita RLS/tenant_tx, validações e triggers de
auditoria), em vez de INSERTs crus que burlariam essas camadas.

Uso (a partir da raiz do backend, com o Postgres de dev na 5433):
    .venv\\Scripts\\python.exe -m tools.seed_demo

Organização/usuário demo (login demo@contratovisto.com) sobrevivem à truncagem —
o pytest só apaga clients/cases/documents. Descobertos por query no DB de dev.
"""
import json
import random
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from src.handlers import cases as cases_h  # noqa: E402
from src.handlers import clients as clients_h  # noqa: E402
from src.handlers import documents as documents_h  # noqa: E402
from src.services.database import tenant_tx  # noqa: E402

DEMO_USER = "fb7f5bb8-224e-441f-a4fd-f4f3a0cb6d39"
DEMO_ORG = "9cb5e294-4891-4578-be38-fab31114c559"
DEMO_ROLE = "admin"
DEMO_EMAIL = "demo@contratovisto.com"


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


def main() -> None:
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

    case = _data(cases_h.create_case(_event({
        "client_id": client["id"],
        "case_type": "contract_analysis",
    }), None))
    print(f"[seed] caso:    {case['id']} {case.get('code', '')}")

    try:
        doc = _data(documents_h.upload_document(_event({
            "case_id": case["id"],
            "file_name": "contrato_demo.pdf",
            "file_type": "pdf",
            "file_size_bytes": 12345,
        }), None))
        print(f"[seed] doc:     {doc.get('document_id', doc.get('id'))}")
    except RuntimeError as exc:
        print(f"[seed] aviso: documento nao criado ({exc}) -- cliente+caso OK")

    with tenant_tx(DEMO_USER, DEMO_ROLE, DEMO_ORG) as cur:
        cur.execute("SELECT count(*) AS n FROM public.clients")
        c = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM public.cases")
        k = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM public.documents")
        d = cur.fetchone()["n"]
    print(f"[seed] OK -- org demo: clients={c} cases={k} documents={d}")


if __name__ == "__main__":
    main()
