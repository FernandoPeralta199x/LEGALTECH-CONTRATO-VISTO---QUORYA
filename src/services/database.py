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

import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

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
def tenant_tx(user_id, role, organization_id):
    """Transação com o contexto RLS do usuário autenticado e da sua organização.

    ``set_config(..., true)`` aplica o valor SÓ nesta transação (seguro com
    pooling/RDS Proxy; não vaza entre invocações). Seta app.user_id,
    app.user_role e app.organization_id. Todas as queries do request devem usar
    o cursor cedido, para enxergarem o contexto.
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT set_config('app.user_id', %s, true),"
            "       set_config('app.user_role', %s, true),"
            "       set_config('app.organization_id', %s, true)",
            (str(user_id), str(role), str(organization_id)),
        )
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


@contextmanager
def operator_tx(user_id, role, target_org_id, endpoint=None):
    """Transação de "operar como org": a firma (operador) lê/processa os dados de
    uma org-CLIENTE, escopada pela RLS ao ``target_org_id`` — nunca um bypass.

    Igual a ``tenant_tx``, mas com ``app.organization_id = target_org_id`` (a org
    ALVO) e, como PRIMEIRA instrução, a função SECURITY DEFINER
    ``audit.begin_operator_impersonation`` (migration 029), que:
      - exige que ``user_id`` seja admin ATIVO de uma org ``operador`` — a autoridade
        vem do BANCO, não do claim ``role``/``perfil`` (levanta 42501 -> 403);
      - valida que o alvo é org-cliente ``empresarial``/``individual`` (senão 22023 -> 400);
      - grava ``OPERATOR_IMPERSONATION`` na trilha da org-alvo (transparência LGPD).
    Falha em qualquer etapa => rollback: sem acesso sem auditoria. O cursor cedido
    enxerga os dados da org-alvo (RLS). ``user_id``/``role`` seguem os do operador
    (escrita e auditoria atribuídas a ele)."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT set_config('app.user_id', %s, true),"
            "       set_config('app.user_role', %s, true),"
            "       set_config('app.organization_id', %s, true)",
            (str(user_id), str(role), str(target_org_id)),
        )
        # Gate + auditoria ANTES de qualquer leitura do alvo (raise => rollback).
        cur.execute("SELECT audit.begin_operator_impersonation(%s, %s)",
                    (str(target_org_id), endpoint))
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


@contextmanager
def signup_tx(organization_id, role="admin"):
    """Transação de onboarding: seta app.organization_id ANTES dos INSERTs para
    satisfazer a RLS de ``organizations`` (id = app.organization_id). ``users``
    não tem RLS, mas a mesma transação cria org + usuário admin atomicamente.
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT set_config('app.organization_id', %s, true),"
            "       set_config('app.user_role', %s, true)",
            (str(organization_id), str(role)),
        )
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
