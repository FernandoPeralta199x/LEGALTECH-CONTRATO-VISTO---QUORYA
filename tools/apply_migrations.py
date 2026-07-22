"""DB-06 — runner idempotente de migrations (tabela de controle schema_migrations).

Problema (achado DB-06): várias migrations adicionam constraints SEM guarda de
idempotência; reaplicar via `psql \\i` estoura 'already exists'. Não havia tabela de
controle nem runner — o deploy aplicava uma única vez, à mão.

Este runner registra cada migration aplicada em public.schema_migrations e PULA as já
registradas. As migrations NÃO são editadas (evita risco de cascata ao dropar
constraints referenciadas por FKs). ALGUMAS migrations já são idempotentes (001 usa
DROP POLICY IF EXISTS; 003 usa IF NOT EXISTS), mas várias NÃO são — por isso rodar sem
--baseline contra um banco já migrado à mão reaplica as idempotentes e ABORTA na
primeira não-idempotente. Faça --baseline UMA vez em bancos já migrados.

Uso:
  python -m tools.apply_migrations               # aplica as PENDENTES, em ordem
  python -m tools.apply_migrations --baseline    # marca TODAS as pendentes como
                                                  #   aplicadas SEM rodar (adoção em
                                                  #   banco JÁ 100% migrado: dev/test)
  python -m tools.apply_migrations --dry-run      # só lista o que faria

Conexão (admin — DDL) por env: DB_HOST, DB_PORT, DB_ADMIN_USER, DB_ADMIN_PASS, DB_NAME.
Carrega .env do projeto (o `python -m` NÃO executa conftest.py). Em teste, exporte
DB_NAME=<banco de teste> ANTES, para não tocar o dev.

LIMITAÇÕES conhecidas (ferramenta de deploy, não crítica):
- Cada .sql traz seu próprio BEGIN/COMMIT, então o INSERT de bookkeeping em
  schema_migrations é uma transação SEPARADA: há uma janela de crash entre o COMMIT do
  DDL e o registro. Se cair nesse gap, a migration ficou aplicada mas não-registrada;
  rerodar tentaria reaplicá-la (aborta se não-idempotente). Mitigação: migrations novas
  são idempotentes (022/023/024), e o advisory lock serializa execuções concorrentes.
- --baseline pressupõe banco 100% migrado (não parcial): marca TODO o pendente.
"""
import os
import sys
from pathlib import Path

import psycopg2

# .env do projeto (o runner roda fora do pytest/conftest). Best-effort: sem dotenv,
# depende das vars já exportadas no ambiente (ex.: CI).
try:  # pragma: no cover - conveniência local
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_LOCK_KEY = 0x6D6967726174696E  # chave arbitrária p/ pg_advisory_lock (serializa runners)

_CONTROL_DDL = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename    text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


def _connect():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.environ["DB_ADMIN_USER"],
        password=os.environ["DB_ADMIN_PASS"],
        dbname=os.environ["DB_NAME"],
        connect_timeout=5,
    )


def _sql_files():
    return sorted(p for p in _MIGRATIONS_DIR.glob("*.sql"))


def apply_migrations(baseline=False, dry_run=False):
    """Aplica (ou baselina) as migrations pendentes. Retorna a lista de nomes tocados."""
    conn = _connect()
    conn.autocommit = True  # cada .sql tem seu próprio BEGIN/COMMIT
    touched = []
    try:
        with conn.cursor() as cur:
            if dry_run:
                # Dry-run é estritamente somente leitura. Se a tabela de controle
                # ainda não existe, todas as migrations são consideradas pendentes.
                cur.execute("SELECT to_regclass('public.schema_migrations')")
                if cur.fetchone()[0] is None:
                    done = set()
                else:
                    cur.execute("SELECT filename FROM public.schema_migrations")
                    done = {r[0] for r in cur.fetchall()}
            else:
                # Serializa execuções concorrentes (session lock; solto ao fechar a conexão).
                cur.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
                cur.execute(_CONTROL_DDL)
                cur.execute("SELECT filename FROM public.schema_migrations")
                done = {r[0] for r in cur.fetchall()}

        for path in _sql_files():
            name = path.name
            if name in done:
                continue
            if dry_run:
                print(f"[pendente] {name}" + ("  (baseline: marcaria sem rodar)" if baseline else ""))
                touched.append(name)
                continue
            if not baseline:
                sql = path.read_text(encoding="utf-8-sig")  # tolera BOM acidental (Windows)
                with conn.cursor() as cur:
                    cur.execute(sql)  # BEGIN/COMMIT internos ao arquivo
                print(f"[aplicada]  {name}")
            else:
                print(f"[baseline]  {name}")
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO public.schema_migrations (filename) VALUES (%s)"
                    " ON CONFLICT (filename) DO NOTHING",
                    (name,),
                )
            touched.append(name)
    finally:
        conn.close()  # solta o advisory lock

    verb = "marcadas (baseline)" if baseline else "aplicadas"
    if dry_run:
        print(f"\n{len(touched)} pendente(s) — nada executado (--dry-run).")
    else:
        print(f"\n{len(touched)} migration(s) {verb}.")
    return touched


if __name__ == "__main__":
    apply_migrations(
        baseline="--baseline" in sys.argv,
        dry_run="--dry-run" in sys.argv,
    )
