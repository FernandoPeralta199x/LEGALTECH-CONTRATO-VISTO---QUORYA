"""Regressões do runner de migrations que não exigem banco real."""
from contextlib import contextmanager

from tools import apply_migrations as runner


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows = []

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.connection.statements.append((normalized, params))
        if "to_regclass" in normalized:
            self._rows = [(self.connection.control_table,)]
        elif normalized.startswith("SELECT filename"):
            self._rows = [(name,) for name in self.connection.applied]

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, *, control_table=None, applied=()):
        self.control_table = control_table
        self.applied = applied
        self.statements = []
        self.autocommit = False
        self.closed = False

    @contextmanager
    def cursor(self):
        yield _FakeCursor(self)

    def close(self):
        self.closed = True


def _migration_files(tmp_path):
    first = tmp_path / "001_first.sql"
    second = tmp_path / "002_second.sql"
    first.write_text("SELECT 1;", encoding="utf-8")
    second.write_text("SELECT 2;", encoding="utf-8")
    return [first, second]


def test_dry_run_sem_tabela_de_controle_nao_escreve(monkeypatch, tmp_path):
    conn = _FakeConnection(control_table=None)
    monkeypatch.setattr(runner, "_connect", lambda: conn)
    monkeypatch.setattr(runner, "_sql_files", lambda: _migration_files(tmp_path))

    touched = runner.apply_migrations(dry_run=True)

    assert touched == ["001_first.sql", "002_second.sql"]
    assert conn.closed is True
    statements = [sql for sql, _ in conn.statements]
    assert statements == ["SELECT to_regclass('public.schema_migrations')"]


def test_dry_run_consulta_controle_sem_lock_ou_ddl(monkeypatch, tmp_path):
    conn = _FakeConnection(
        control_table="schema_migrations",
        applied=("001_first.sql",),
    )
    monkeypatch.setattr(runner, "_connect", lambda: conn)
    monkeypatch.setattr(runner, "_sql_files", lambda: _migration_files(tmp_path))

    touched = runner.apply_migrations(dry_run=True)

    assert touched == ["002_second.sql"]
    statements = [sql for sql, _ in conn.statements]
    assert statements == [
        "SELECT to_regclass('public.schema_migrations')",
        "SELECT filename FROM public.schema_migrations",
    ]
    assert all("CREATE TABLE" not in sql for sql in statements)
    assert all("pg_advisory_lock" not in sql for sql in statements)
    assert all("INSERT INTO" not in sql for sql in statements)
