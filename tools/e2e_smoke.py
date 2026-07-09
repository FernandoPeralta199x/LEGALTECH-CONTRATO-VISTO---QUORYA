"""E2E smoke test do programa (Contrato Visto) contra a API viva (dev-server).

Exercita o fluxo ponta-a-ponta e AFIRMA o invariante-joia: a triagem só executa
os módulos COMPRADOS (o conector de um módulo comercial não-selecionado NÃO roda).

Uso (com o dev-server em pé — ``tools/local_server.py`` — e o PG18 semeado):

    ./.venv/Scripts/python.exe tools/e2e_smoke.py
    ./.venv/Scripts/python.exe tools/e2e_smoke.py --base http://127.0.0.1:8000

Sai com código 0 se tudo passar, 2 se algum check falhar, 1 em erro de setup.
NÃO é um teste pytest (bate numa API real); cria 1 caso no banco apontado pelo
dev-server (use um banco de dev/descartável).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid

# analise_contratual: obrigatórios ia_deepseek + analise_contratual_ia => triagem
# roda só a infra + os técnicos desses; os opcionais (serasa_procon/escavador/
# targetdata) ficam DE FORA. É a prova do fix "triagem por seleção" (ADR-001).
EXPECTED_MODULES = {"document_parser", "ocr", "contract_risk_analysis",
                    "obligations_mapping", "ai_report"}
FORBIDDEN_MODULES = {"serasa", "procon", "escavador", "targetdata"}
FORBIDDEN_PROVIDERS = {"mock_serasa", "mock_procon", "mock_escavador", "mock_targetdata"}


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token: str | None = None

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or "{}")


def _data(b):
    return b.get("data", b) if isinstance(b, dict) else b


def run(base: str, email: str, password: str) -> int:
    c = Client(base)
    fails: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  OK  " if cond else " FAIL ") + msg)
        if not cond:
            fails.append(msg)

    # 1) LOGIN
    st, b = c.call("POST", "/api/v1/auth/login", {"email": email, "password": password})
    c.token = _data(b).get("access_token")
    check(st == 200 and bool(c.token), f"1 LOGIN st={st}")
    if not c.token:
        print("setup: sem token, abortando:", b)
        return 1

    # 2) CRIAR (analise_contratual, seleção mínima)
    idem = "smoke-" + uuid.uuid4().hex[:12]
    st, b = c.call("POST", "/api/v1/requests", {
        "product_type": "analise_contratual", "selected_modules": [],
        "source_mode": "local", "idempotency_key": idem, "title": "E2E smoke",
        "parties": [{"name": "Fulano Smoke", "role": "contratante", "person_type": "individual"}],
    })
    cd = _data(b)
    case_id = cd.get("case_id")
    check(st in (200, 201) and bool(case_id), f"2 CREATE st={st} case={case_id} modules={cd.get('triage_modules_count')}")
    if not case_id:
        print("setup: sem case_id, abortando:", b)
        return 1

    # 3) PAGAR (pix 1x)
    st, b = c.call("POST", f"/api/v1/cases/{case_id}/payment", {"parcelas": 1, "method": "pix", "idempotency_key": "pay-" + idem})
    check(st in (200, 201), f"3 PAY pix 1x st={st}")

    # 4) TRIAGEM
    st, b = c.call("POST", f"/api/v1/cases/{case_id}/triage/run", {})
    check(st == 200, f"4 TRIAGE st={st} modules_executed={_data(b).get('modules_executed')}")

    # 5) INVARIANTE
    st, b = c.call("GET", f"/api/v1/cases/{case_id}/aggregate", None)
    agg = _data(b)
    check(st == 200, f"5 AGGREGATE st={st}")
    keys = {(m.get("module_key") or m.get("moduleKey")) for m in (agg.get("triage_modules") or [])}
    provs = {(p.get("provider") or "") for p in (agg.get("provider_results") or [])}
    print("   module_keys:", sorted(keys))
    print("   providers:", sorted(provs))
    check(not (keys & FORBIDDEN_MODULES), f"   INVARIANTE: conectores não comprados ausentes ({keys & FORBIDDEN_MODULES or 'ok'})")
    check(not (provs & FORBIDDEN_PROVIDERS), f"   INVARIANTE: adapters não comprados não rodaram ({provs & FORBIDDEN_PROVIDERS or 'ok'})")
    check(EXPECTED_MODULES.issubset(keys), f"   módulos esperados presentes (faltando={EXPECTED_MODULES - keys or 'nenhum'})")

    # 6) RELATÓRIO
    st, _ = c.call("POST", f"/api/v1/cases/{case_id}/report/generate", {})
    check(st == 200, f"6a REPORT generate st={st}")
    st, _ = c.call("POST", f"/api/v1/cases/{case_id}/report/review", {"status": "approved", "review_notes": None})
    check(st == 200, f"6b REPORT approve st={st}")

    # 7) ESTADO FINAL
    st, b = c.call("GET", f"/api/v1/cases/{case_id}", None)
    check(st == 200, f"7 FINAL st={st} case_status={_data(b).get('status')}")

    print(f"\n=== SMOKE {'VERDE' if not fails else 'VERMELHO'} (case {case_id}) ===")
    if fails:
        print(f"{len(fails)} falha(s): {fails}")
    return 0 if not fails else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="E2E smoke test contra a API viva do dev-server.")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--email", default="demo@contratovisto.com")
    ap.add_argument("--password", default="DemoLocal#2026")
    args = ap.parse_args()
    return run(args.base, args.email, args.password)


if __name__ == "__main__":
    raise SystemExit(main())
