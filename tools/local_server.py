"""Dev-server HTTP local (DEV-ONLY) — expõe os handlers Lambda como API HTTP para
o frontend Next.js consumir SEM AWS / serverless-offline.

- Deriva as rotas do próprio ``serverless.yml`` (método, path, handler, authorizer).
- Aceita o prefixo ``/api/v1`` (usado pelo rewrite do Next) e normaliza.
- Faz o papel do JWT Authorizer: valida o Bearer (HS256) e injeta os claims em
  ``event.requestContext.authorizer`` (achatado), como o API Gateway faz.
- Converte HTTP <-> event Lambda e chama o MESMO handler que roda na AWS.

NÃO usar em produção. Uso:
    .venv\\Scripts\\python.exe tools\\local_server.py        (porta 8000)
    PORT=9000 .venv\\Scripts\\python.exe tools\\local_server.py
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import re
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import jwt
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader que tolera tags CloudFormation (!Ref/!GetAtt/!Sub/...) do
    serverless.yml — só precisamos das rotas, então as tags viram placeholders."""


def _cfn_passthrough(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_CfnLoader.add_multi_constructor("!", _cfn_passthrough)

API_PREFIX = "/api/v1"
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("local_server")

# A conexão global do psycopg2 (database.py) é reutilizada e NÃO é thread-safe;
# serializamos a execução dos handlers com um lock (dev: 1 usuário).
_db_lock = threading.Lock()

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
}


@dataclass
class Route:
    method: str
    regex: re.Pattern
    func: Callable
    auth: bool
    raw_path: str


def _resolve_handler(handler_str: str) -> Callable:
    """'src/handlers/users.login' -> função login do módulo src.handlers.users."""
    mod_path, func = handler_str.rsplit(".", 1)
    module = importlib.import_module(mod_path.replace("/", "."))
    return getattr(module, func)


def _compile(path: str) -> re.Pattern:
    """'cases/{caseId}' -> regex ancorada com grupos nomeados."""
    pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path.strip("/"))
    return re.compile(f"^/{pattern}$")


def load_routes() -> list[Route]:
    with open(ROOT / "serverless.yml", encoding="utf-8") as f:
        cfg = yaml.load(f, Loader=_CfnLoader)
    routes: list[Route] = []
    for _, fdef in (cfg.get("functions") or {}).items():
        handler = fdef.get("handler")
        if not handler:
            continue
        for ev in fdef.get("events") or []:
            http = ev.get("http")
            if not http:
                continue
            routes.append(Route(
                method=http["method"].upper(),
                regex=_compile(http["path"]),
                func=_resolve_handler(handler),
                auth="authorizer" in http,
                raw_path="/" + http["path"].strip("/"),
            ))
    return routes


ROUTES = load_routes()


def _claims_from_bearer(headers) -> dict | None:
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        return jwt.decode(auth[7:], os.environ["JWT_SECRET_KEY"], algorithms=["HS256"])
    except Exception:
        return None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silencia o log padrão (usamos o nosso)
        pass

    def _write(self, status: int, body: str, headers: dict | None = None):
        payload = body.encode("utf-8")
        self.send_response(status)
        for k, v in {**_CORS, "Content-Type": "application/json", **(headers or {})}.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, obj: dict):
        self._write(status, json.dumps(obj))

    def _dispatch(self, method: str):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith(API_PREFIX):
            path = path[len(API_PREFIX):] or "/"

        if method == "OPTIONS":
            return self._write(204, "")

        route = next((r for r in ROUTES if r.method == method and r.regex.match(path)), None)
        if route is None:
            log.info("%s %s -> 404", method, self.path)
            return self._json(404, {"error": "Rota não encontrada"})

        claims = _claims_from_bearer(self.headers)
        if route.auth and not claims:
            log.info("%s %s -> 401", method, self.path)
            return self._json(401, {"error": "Não autenticado"})

        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length).decode("utf-8") if length else None
        match = route.regex.match(path)
        event = {
            "httpMethod": method,
            "path": path,
            "resource": route.raw_path,
            "pathParameters": match.groupdict() or None,
            "queryStringParameters": {k: v[0] for k, v in parse_qs(parsed.query).items()} or None,
            "headers": dict(self.headers),
            "body": body,
            "requestContext": {"authorizer": claims} if claims else {},
        }
        try:
            with _db_lock:
                resp = route.func(event, None)
        except Exception as e:  # pragma: no cover - rede de segurança do dev-server
            log.exception("handler error")
            return self._json(500, {"error": f"Erro no handler: {type(e).__name__}"})

        status = resp.get("statusCode", 200)
        log.info("%s %s -> %s", method, self.path, status)
        self._write(status, resp.get("body", ""), resp.get("headers"))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_OPTIONS(self):
        self._dispatch("OPTIONS")


def main():
    server = ThreadingHTTPServer((HOST, PORT), _Handler)
    log.info("Dev-server (serverless local) em http://%s:%s", HOST, PORT)
    log.info("Prefixo aceito: %s/*  |  %d rotas carregadas do serverless.yml", API_PREFIX, len(ROUTES))
    log.info("DEV-ONLY: simula o JWT Authorizer e roteia para os handlers Lambda.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("encerrando...")
        server.shutdown()


if __name__ == "__main__":
    main()
