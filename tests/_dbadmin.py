"""Conexão administrativa (``dbadmin``, superuser — BYPASSA RLS) usada pelos testes
para setup, asserts fora do contexto de organização e ``TRUNCATE``.

O ``dbname`` vem de ``DB_NAME`` (o conftest aponta para o banco de teste). NUNCA
hardcode o banco de dev aqui — senão a suíte volta a truncar o dev. Credenciais e
host/porta têm defaults do ambiente local (container ``cv-pg18``), sobrescrevíveis
por env em CI.
"""
import os

import psycopg2


def admin_conn():
    """Nova conexão como superuser ao banco apontado por ``DB_NAME``."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5433")),
        user=os.getenv("DB_ADMIN_USER", "dbadmin"),
        password=os.getenv("DB_ADMIN_PASS", "localdev_cv"),
        dbname=os.environ["DB_NAME"],
        connect_timeout=5,
    )
