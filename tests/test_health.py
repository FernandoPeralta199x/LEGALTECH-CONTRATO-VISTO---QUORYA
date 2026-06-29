"""Health check — conectividade com o PG18 (sem expor detalhes)."""
import json

from src.handlers import health


def test_health_ok():
    resp = health.health_check({}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["data"]["status"] == "ok"
    # não vaza detalhes internos
    assert "version" not in body["data"] and "region" not in body["data"]
