"""Stress test CONTROLADO contra a API viva do Contrato Visto (dev-server).

Rampa de criação de casos em lotes medindo latência, mais um burst de concorrência
e leitura em escala — para achar degradação (índice faltando, N+1) SEM corromper o
sistema. Guardrail: aborta a rampa se um lote passar de ~50% de erros.

Todos os casos são tagueados (``title=STRESS``, idempotency ``stress-*``) para
limpeza posterior com ``--cleanup``.

Uso (com o dev-server em pé — ``tools/local_server.py`` — e a org demo semeada):

    ./.venv/Scripts/python.exe tools/stress_probe.py                       # rampa até 1500
    ./.venv/Scripts/python.exe tools/stress_probe.py --cap 300 --conc 20
    ./.venv/Scripts/python.exe tools/stress_probe.py --cleanup             # remove os casos STRESS ao fim

Sai com 0 se estável, 2 se detectar degradação/falhas sob carga, 1 em erro de setup.
NÃO é pytest — bate numa API real e cria MUITOS casos; use um banco de DEV/descartável.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

STRESS_TAG = "STRESS"


def make_call(base: str):
    def call(method, path, token=None, body=None):
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, (time.perf_counter() - t0) * 1000, r.read()
        except urllib.error.HTTPError as e:
            return e.code, (time.perf_counter() - t0) * 1000, e.read()
        except Exception as e:  # noqa: BLE001
            return -1, (time.perf_counter() - t0) * 1000, str(e).encode()
    return call


def d(b):
    try:
        j = json.loads(b or b"{}")
        return j.get("data", j) if isinstance(j, dict) else j
    except Exception:
        return {}


def cleanup():
    """Remove os casos tagueados ``STRESS`` via conexão admin ao banco do dev-server.
    Só apaga ``WHERE title = 'STRESS'`` — nunca TRUNCATE. Recusa banco que pareça produção."""
    try:
        import psycopg2  # noqa: PLC0415
    except Exception:
        print("[cleanup] psycopg2 indisponível — pulei a limpeza.")
        return
    dbname = os.getenv("DB_NAME", "contrato_visto")
    if "prod" in dbname.lower():
        print(f"[cleanup] DB_NAME={dbname!r} parece produção — RECUSADO.")
        return
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5433")),
            user=os.getenv("DB_ADMIN_USER", "dbadmin"), password=os.getenv("DB_ADMIN_PASS", "localdev_cv"),
            dbname=dbname, connect_timeout=5)
    except Exception as e:  # noqa: BLE001
        print(f"[cleanup] sem conexão admin ({e}) — pulei a limpeza.")
        return
    conn.autocommit = True
    with conn.cursor() as cur:
        # requests<->cases têm FKs compostas circulares ON DELETE SET NULL c/ organization_id
        # (NOT NULL): DELETE escopado normal falha. dbadmin é superuser -> desliga efeitos de FK
        # só nesta sessão, apaga as filhas (por case_id) + os casos STRESS, e RESTAURA.
        cur.execute("SET session_replication_role = replica")
        try:
            cur.execute(
                "SELECT c.table_name FROM information_schema.columns c"
                " JOIN information_schema.tables t"
                "   ON t.table_schema = c.table_schema AND t.table_name = c.table_name"
                " WHERE c.table_schema = 'public' AND c.column_name = 'case_id'"
                "   AND t.table_type = 'BASE TABLE'")  # exclui views
            n_child = 0
            for (t,) in cur.fetchall():
                try:
                    cur.execute(f"DELETE FROM public.{t} WHERE case_id IN"
                                " (SELECT id FROM public.cases WHERE title = %s)", (STRESS_TAG,))
                    n_child += cur.rowcount
                except Exception:  # noqa: BLE001
                    pass
            cur.execute("DELETE FROM public.cases WHERE title = %s", (STRESS_TAG,))
            print(f"[cleanup] casos STRESS removidos: {cur.rowcount} (linhas-filhas: {n_child})")
        finally:
            cur.execute("SET session_replication_role = DEFAULT")
    conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Stress test controlado contra a API viva (dev).")
    ap.add_argument("--base", default="http://127.0.0.1:8000/api/v1")
    ap.add_argument("--cap", type=int, default=1500, help="máximo de casos na rampa")
    ap.add_argument("--batch", type=int, default=100, help="tamanho do lote de reporte")
    ap.add_argument("--conc", type=int, default=30, help="criações simultâneas no burst")
    ap.add_argument("--cleanup", action="store_true", help="remove os casos STRESS ao final")
    args = ap.parse_args()
    call = make_call(args.base)

    st, _, b = call("POST", "/auth/login", body={"email": "demo@contratovisto.com",
                                                 "password": os.environ.get("DEMO_PASSWORD", "")})
    tok = d(b).get("access_token")
    if not tok:
        print(f"[setup] login demo falhou (st={st}) — dev-server no ar e org demo semeada?")
        return 1

    def make_case(tag):
        return call("POST", "/requests", tok, {
            "product_type": "analise_contratual", "selected_modules": [], "source_mode": "local",
            "idempotency_key": "stress-" + tag, "title": STRESS_TAG, "parties": []})

    _, ms_c, _ = make_case("base-" + uuid.uuid4().hex[:8])
    _, ms_l, _ = call("GET", "/cases?page=1&pageSize=24", tok)
    print(f"BASELINE  create={ms_c:.0f}ms  list={ms_l:.0f}ms")

    print(f"\nRAMPA sequencial até {args.cap} casos (lote {args.batch}):")
    created, errors, batch_lat, curve = 0, 0, [], []
    aborted = False
    t_start = time.perf_counter()
    for i in range(args.cap):
        stc, ms, _ = make_case(f"{i}-{uuid.uuid4().hex[:6]}")
        if stc in (200, 201):
            created += 1
            batch_lat.append(ms)
        else:
            errors += 1
        if (i + 1) % args.batch == 0:
            avg = statistics.mean(batch_lat) if batch_lat else 0
            p95 = sorted(batch_lat)[int(len(batch_lat) * 0.95)] if len(batch_lat) > 1 else avg
            curve.append((created, avg, p95))
            print(f"  {created:5d} casos | lote avg={avg:5.0f}ms p95={p95:5.0f}ms | erros={errors}")
            batch_lat = []
            if errors > args.batch // 2:
                print("  !! muitos erros — abortando rampa")
                aborted = True
                break
    dur = time.perf_counter() - t_start
    print(f"  -> {created} casos em {dur:.1f}s ({created/dur:.0f} casos/s), {errors} erros")

    _, ms_l2, _ = call("GET", "/cases?page=1&pageSize=24", tok)
    _, ms_s, sb = call("GET", "/dashboard/stats", tok)
    total = d(sb).get("total_cases", d(sb).get("totals", {}))
    print(f"\nLEITURA EM ESCALA  list={ms_l2:.0f}ms  dashboard/stats={ms_s:.0f}ms  total_reportado={total}")

    print(f"\nCONCORRÊNCIA: {args.conc} criações simultâneas")
    res: list[tuple[int, float]] = []

    def worker(n):
        stc, ms, _ = make_case(f"conc-{n}-{uuid.uuid4().hex[:6]}")
        res.append((stc, ms))

    ths = [threading.Thread(target=worker, args=(n,)) for n in range(args.conc)]
    t0 = time.perf_counter()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    wall = (time.perf_counter() - t0) * 1000
    ok = sum(1 for s, _ in res if s in (200, 201))
    lats = [m for _, m in res] or [0]
    print(f"  ok={ok}/{args.conc}  wall={wall:.0f}ms  avg={statistics.mean(lats):.0f}ms  max={max(lats):.0f}ms")

    print("\n=== VEREDITO ===")
    degraded = False
    if len(curve) >= 2:
        first, last = curve[0][1], curve[-1][1]
        deg = (last - first) / first * 100 if first else 0
        degraded = deg >= 50
        print(f"  latência de criação: {first:.0f}ms -> {last:.0f}ms = {deg:+.0f}% "
              f"({'ESTÁVEL' if not degraded else 'DEGRADAÇÃO — investigar índice/N+1'})")
    conc_ok = ok == args.conc
    read_ok = ms_l2 < ms_l * 3 + 50
    print(f"  concorrência: {'OK' if conc_ok else 'FALHAS sob carga'} ({ok}/{args.conc})")
    print(f"  leitura em escala: list {ms_l2:.0f}ms (baseline {ms_l:.0f}ms) — {'estável' if read_ok else 'degradou'}")

    if args.cleanup:
        cleanup()

    return 0 if (not degraded and conc_ok and read_ok and not aborted) else 2


if __name__ == "__main__":
    sys.exit(main())
