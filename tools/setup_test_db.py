"""(Re)constrói o banco de TESTE isolado do dev — de forma reproduzível.

Motivação: a suíte pytest TRUNCA tabelas. Rodá-la contra o banco de dev destrói o
seed (demo/pricing). Este script cria um banco separado (``TEST_DB_NAME``, default
``contrato_visto_test``) com o MESMO schema do dev (clone schema-only, garantindo
paridade exata: schema_referencia + 17 migrations + grants do ``cv_app``) e semeia
o mínimo que a suíte assume existir: as duas organizações-base referenciadas por
UUID fixo nos testes (sistema ``…0001`` e isolamento ``…00ff``) — o clone é
schema-only, então nenhum dado (inclusive o seed de org da migração 005) vem junto.

Local-only: orquestra o container Docker do Postgres (default ``cv-pg18``), como o
próprio bootstrap do dev (docs/PROGRESSO.md). Configurável por env.

Uso (raiz do backend):
    .venv\\Scripts\\python.exe -m tools.setup_test_db

Depois, a suíte já usa o banco de teste automaticamente (conftest.py redireciona
DB_NAME). Env relevantes: PG_CONTAINER, DEV_DB_NAME, TEST_DB_NAME, DB_ADMIN_USER,
DB_ADMIN_PASS.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

PG_CONTAINER = os.getenv("PG_CONTAINER", "cv-pg18")
DEV_DB = os.getenv("DEV_DB_NAME", "contrato_visto")
TEST_DB = os.getenv("TEST_DB_NAME", "contrato_visto_test")
ADMIN_USER = os.getenv("DB_ADMIN_USER", "dbadmin")
ADMIN_PASS = os.getenv("DB_ADMIN_PASS", "localdev_cv")

# Orgs-base que os testes referenciam por UUID fixo (ver tests/test_v2_tables.py,
# tests/test_requests_handlers.py). Precisam existir por causa das FKs
# organization_id -> organizations. Nenhum teste trunca organizations.
BASE_ORGS = [
    ("00000000-0000-0000-0000-000000000001", "Organização de Sistema"),
    ("00000000-0000-0000-0000-0000000000ff", "Org de Teste (isolamento)"),
]


def _guard() -> None:
    if TEST_DB == DEV_DB:
        sys.exit(f"ABORTADO: TEST_DB_NAME ({TEST_DB!r}) == banco de dev ({DEV_DB!r}). "
                 "Isso destruiria o dev.")


def _docker_exec(args: list[str], *, input_sql: str | None = None) -> subprocess.CompletedProcess:
    """Roda `docker exec [-i] PG_CONTAINER <args>` com PGPASSWORD do admin."""
    cmd = ["docker", "exec"]
    if input_sql is not None:
        cmd.append("-i")
    cmd += ["-e", "PGCLIENTENCODING=UTF8", "-e", f"PGPASSWORD={ADMIN_PASS}", PG_CONTAINER] + args
    # encoding=utf-8: no Windows, text=True usaria cp1252 no stdin e corromperia
    # acentos (ç/ã) enviados ao psql, que espera UTF-8.
    return subprocess.run(cmd, input=input_sql, text=True, encoding="utf-8", capture_output=True)


def _psql(db: str, sql: str) -> subprocess.CompletedProcess:
    return _docker_exec(
        ["psql", "-U", ADMIN_USER, "-d", db, "-v", "ON_ERROR_STOP=1", "-q"],
        input_sql=sql,
    )


def _check(cp: subprocess.CompletedProcess, what: str) -> None:
    if cp.returncode != 0:
        sys.exit(f"FALHA em {what}:\n{cp.stderr.strip() or cp.stdout.strip()}")


def main() -> None:
    _guard()
    print(f"[setup] container={PG_CONTAINER} dev={DEV_DB} -> test={TEST_DB}")

    # 1) encerra conexões e recria o banco de teste (idempotente)
    drop_create = (
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{TEST_DB}' AND pid <> pg_backend_pid();\n"
        f"DROP DATABASE IF EXISTS {TEST_DB};\n"
        f"CREATE DATABASE {TEST_DB} OWNER {ADMIN_USER};\n"
    )
    _check(_psql("postgres", drop_create), "drop/create do banco de teste")
    print(f"[setup] banco {TEST_DB} recriado")

    # 2) clona o schema do dev (schema-only: estrutura + grants, sem dados)
    clone = _docker_exec([
        "sh", "-c",
        f"pg_dump -U {ADMIN_USER} --schema-only {DEV_DB} | "
        f"psql -U {ADMIN_USER} -d {TEST_DB} -v ON_ERROR_STOP=1 -q",
    ])
    _check(clone, "clone do schema (pg_dump | psql)")
    print("[setup] schema clonado do dev")

    # 3) seed idempotente das orgs-base (RLS forçada exige app.organization_id por linha)
    seed = "".join(
        f"SELECT set_config('app.user_id', '{oid}', false);\n"
        f"SELECT set_config('app.organization_id', '{oid}', false);\n"
        f"INSERT INTO public.organizations (id, name) VALUES ('{oid}', '{name}') "
        f"ON CONFLICT (id) DO NOTHING;\n"
        for oid, name in BASE_ORGS
    )
    _check(_psql(TEST_DB, seed), "seed das orgs-base")

    # 4) resumo
    summary = _psql(TEST_DB,
                    "SELECT 'tabelas='||count(*) FROM information_schema.tables "
                    "WHERE table_schema IN ('public','audit');"
                    "SELECT 'organizations='||count(*) FROM public.organizations;")
    print("[setup] OK\n" + summary.stdout.strip())


if __name__ == "__main__":
    main()
