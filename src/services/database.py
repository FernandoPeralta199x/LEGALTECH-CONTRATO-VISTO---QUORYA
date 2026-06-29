"""Acesso ao PostgreSQL para handlers Lambda.

- Conexão reutilizada entre invocações (declarada fora do handler) + revalidação
  contra conexão stale (boa prática Lambda).
- ``tenant_tx``: transação que fixa o contexto RLS do usuário autenticado
  (``app.user_id``/``app.user_role``) via ``set_config(..., true)`` (= SET LOCAL).
  Necessário para as POLICIES de RLS e os TRIGGERS de auditoria do banco.
- ``simple_tx``: transação SEM contexto RLS, para tabelas globais (``users``,
  ``clients``, ``password_resets``).
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
def simple_tx():
    """Transação SEM contexto RLS, para tabelas globais (ex.: ``public.users``,
    ``public.password_resets``) que não têm Row Level Security.

    Reusa a conexão global (com timeouts) e faz commit/rollback. Use ``tenant_tx``
    para tabelas com RLS (cases/case_results/documents).
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


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
