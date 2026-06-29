"""Acesso ao PostgreSQL para handlers Lambda.

- Conexão reutilizada entre invocações (declarada fora do handler) + revalidação
  contra conexão stale (boa prática Lambda).
- ``tenant_tx``: transação que fixa o contexto RLS do usuário autenticado
  (``app.user_id``/``app.user_role``) via ``set_config(..., true)`` (= SET LOCAL).
  Necessário para as POLICIES de RLS e os TRIGGERS de auditoria do banco.
- Mantém ``Database``/instância ``db`` (context manager) por compatibilidade com
  handlers ainda não migrados.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger()

# Timeouts de sessão (evitam queries/locks presos em Lambda)
_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "8000"))
_IDLE_TX_TIMEOUT_MS = int(os.getenv("DB_IDLE_TX_TIMEOUT_MS", "10000"))
_LOCK_TIMEOUT_MS = int(os.getenv("DB_LOCK_TIMEOUT_MS", "5000"))

_conn = None


def _connect():
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        dbname=os.environ["DB_NAME"],
        port=int(os.getenv("DB_PORT", "5432")),
        connect_timeout=5,
    )
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute(
            "SET statement_timeout = %s;"
            "SET idle_in_transaction_session_timeout = %s;"
            "SET lock_timeout = %s",
            (_STATEMENT_TIMEOUT_MS, _IDLE_TX_TIMEOUT_MS, _LOCK_TIMEOUT_MS),
        )
    conn.commit()
    return conn


def get_connection():
    """Conexão reutilizada entre invocações; revalida e reconecta se stale."""
    global _conn
    if _conn is None or _conn.closed:
        _conn = _connect()
        return _conn
    try:
        _conn.rollback()  # descarta qualquer transação/estado pendente vazado
        with _conn.cursor() as cur:
            cur.execute("SELECT 1")
        _conn.rollback()  # mantém a conexão ociosa, sem transação aberta
    except psycopg2.Error:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = _connect()
    return _conn


@contextmanager
def tenant_tx(user_id, role):
    """Transação com o contexto RLS do usuário autenticado.

    ``set_config(..., true)`` aplica o valor SÓ nesta transação (seguro com
    pooling/RDS Proxy; não vaza entre invocações). Todas as queries do request
    devem usar o cursor cedido, para enxergarem o contexto.
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT set_config('app.user_id', %s, true),"
            "       set_config('app.user_role', %s, true)",
            (str(user_id), str(role)),
        )
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


# ── Compatibilidade: Database + `db` (handlers ainda não migrados) ──────────────

class Database:
    """Context manager legado (abre/fecha por uso). Prefira ``tenant_tx``."""

    def __init__(self, host, user, password, database, port=5432):
        self.host, self.user, self.password = host, user, password
        self.database, self.port = database, port
        self.connection = None

    def connect(self):
        self.connection = psycopg2.connect(
            host=self.host, user=self.user, password=self.password,
            dbname=self.database, port=int(self.port), connect_timeout=5,
        )

    def disconnect(self):
        if self.connection:
            self.connection.close()
            self.connection = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def execute_query(self, query, params=None):
        with self.connection.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchall()

    def execute_query_one(self, query, params=None):
        with self.connection.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def execute_update(self, query, params=None):
        try:
            with self.connection.cursor() as cur:
                cur.execute(query, params)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise


db = Database(
    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASS"),
    database=os.getenv("DB_NAME"),
    port=int(os.getenv("DB_PORT", "5432")),
)
