"""Configuração de testes: carrega o `.env` e REDIRECIONA a suíte para o banco de
teste, isolando o banco de desenvolvimento.

A suíte TRUNCA tabelas (clients/cases/documents/users/pricing_configs/...). Nunca
pode rodar contra o banco de dev. Este conftest:

1. força ``DB_NAME`` para ``TEST_DB_NAME`` (default ``contrato_visto_test``) — isso
   redireciona TANTO o app (handlers via env ``DB_*``) QUANTO a conexão admin dos
   testes (``tests/_dbadmin.admin_conn`` lê ``DB_NAME``);
2. ABORTA a coleta (guardrail) se o alvo ainda for o banco de dev, para que um
   ``.env`` mal configurado nunca truque o dev de novo.

Criar/atualizar o banco de teste: ver ``docs/ADR-002-banco-de-teste-separado.md``.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))  # garante importar tests/_dbadmin por nome
load_dotenv(ROOT / ".env")

DEV_DB_NAME = os.getenv("DEV_DB_NAME", "contrato_visto")
TEST_DB_NAME = os.getenv("TEST_DB_NAME", "contrato_visto_test")

# Guardrail: recusa rodar se o banco de teste coincide com o de dev (truncaria o dev).
if TEST_DB_NAME == DEV_DB_NAME:
    raise RuntimeError(
        f"TEST_DB_NAME ({TEST_DB_NAME!r}) coincide com o banco de dev "
        f"({DEV_DB_NAME!r}): a suíte truncaria o dev. Configure um banco separado "
        "(ver docs/ADR-002-banco-de-teste-separado.md)."
    )

# Redireciona TODAS as conexões (app via env + admin via tests/_dbadmin) para o teste.
os.environ["DB_NAME"] = TEST_DB_NAME

# O gate de pagamento (PAYMENT_GATE) pode estar "hard" no .env local para o dev
# server. A suíte NÃO deve depender disso: força "soft" por padrão (determinístico).
# Os testes do gate ativam "hard" explicitamente via monkeypatch.
os.environ["PAYMENT_GATE"] = "soft"
