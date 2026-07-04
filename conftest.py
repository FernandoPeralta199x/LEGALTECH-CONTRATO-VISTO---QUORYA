"""Configuração de testes: carrega o .env local e garante `src` no path."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# O gate de pagamento (PAYMENT_GATE) pode estar "hard" no .env local para o dev
# server. A suíte NÃO deve depender disso: força "soft" por padrão (determinístico).
# Os testes do gate ativam "hard" explicitamente via monkeypatch.
os.environ["PAYMENT_GATE"] = "soft"
