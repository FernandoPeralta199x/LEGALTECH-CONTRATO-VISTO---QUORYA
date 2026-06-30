"""Handlers Lambda de `users` / autenticação (migração FastAPI→Serverless, Fase A).

- Rotas PÚBLICAS: `create_user` (signup), `login`, `forgot_password`,
  `reset_password`. As demais são protegidas pelo JWT Authorizer (`@require_user`).
- `public.users` NÃO tem RLS → usa `simple_tx` (transação global, sem contexto).
- Segurança: signup cria sempre `viewer` (menor privilégio; promoção via admin);
  nunca loga PII (email/token) nem `str(e)`; senha com bcrypt; token via
  `create_access_token` (sem segredo padrão).
"""
import hashlib
import json
import logging
import secrets

import bcrypt
import psycopg2
from pydantic import ValidationError

from src.schemas.user_schemas import (
    ForgotPasswordSchema,
    ResetPasswordSchema,
    UserLoginSchema,
    UserSignupSchema,
    UserUpdateSchema,
)
from src.services.database import simple_tx
from src.services.email import email_service
from src.utils.auth import create_access_token
from src.utils.context import require_role, require_user
from src.utils.helpers import error_response, generate_uuid, success_response
from src.utils.lambda_io import fmt_validation_error as _fmt, parse_json_body as _parse_body, parse_pagination as _paginate, valid_uuid as _valid_uuid
from src.utils.safety import enforce_production_safety

enforce_production_safety()
logger = logging.getLogger()


# ── helpers ────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception as e:
        logger.error(json.dumps({"event": "PASSWORD_VERIFY_ERROR",
                                 "error": type(e).__name__}))
        return False


# Hash fixo para uniformizar o tempo do login quando o usuário não existe
# (mitiga enumeração por timing — sempre executa um bcrypt).
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"timing-uniform", bcrypt.gensalt(rounds=12)).decode("utf-8")


def _token_hash(raw: str) -> str:
    """SHA-256 hex do token de reset (guardamos só o hash no banco)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── rotas públicas ───────────────────────────────────────────────────────────
def create_user(event, context):
    """Signup PÚBLICO. Cria sempre como `viewer` (menor privilégio)."""
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = UserSignupSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    user_id = generate_uuid()
    try:
        with simple_tx() as cur:
            cur.execute("SELECT 1 FROM public.users WHERE email = %s", (data.email,))
            if cur.fetchone():
                return error_response(409, "Email já cadastrado")
            cur.execute(
                "INSERT INTO public.users (id, email, password_hash, name, role, status)"
                " VALUES (%s, %s, %s, %s, 'viewer', 'active')",
                (user_id, data.email, hash_password(data.password), data.name),
            )
    except psycopg2.errors.UniqueViolation:
        # corrida: 2 signups simultâneos passam pelo SELECT; o índice único garante
        return error_response(409, "Email já cadastrado")
    except Exception as e:
        logger.error(json.dumps({"event": "USER_CREATE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}))
        return error_response(500, "Erro ao criar usuário")

    logger.info(json.dumps({"event": "USER_CREATED", "user_id": user_id, "role": "viewer"}))
    return success_response(201, "Usuário criado com sucesso",
                            {"user_id": user_id, "role": "viewer"})


def login(event, context):
    """Login PÚBLICO. Retorna JWT (HS256) com user_id/email/role."""
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = UserLoginSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    try:
        with simple_tx() as cur:
            cur.execute(
                "SELECT id, email, password_hash, role FROM public.users"
                " WHERE email = %s AND status = 'active'",
                (data.email,),
            )
            user = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "AUTH_LOGIN_ERROR",
                                 "error": type(e).__name__}))
        return error_response(500, "Erro no login")

    # Sempre executa bcrypt (hash dummy se o usuário não existe) p/ tempo uniforme.
    stored_hash = user["password_hash"] if user else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(data.password, stored_hash)
    if not user or not password_ok:
        logger.warning(json.dumps({"event": "AUTH_LOGIN_FAILED"}))  # sem PII
        return error_response(401, "Email ou senha inválidos")

    token = create_access_token({  # sem email no token (menos PII)
        "user_id": str(user["id"]), "role": user["role"],
    })
    logger.info(json.dumps({"event": "AUTH_LOGIN_SUCCESS", "user_id": str(user["id"])}))
    return success_response(200, "Login bem-sucedido", {
        "token": token, "user_id": str(user["id"]), "role": user["role"],
    })


def forgot_password(event, context):
    """Solicita reset. Resposta sempre genérica (não revela se o email existe)."""
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = ForgotPasswordSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    generic = success_response(200, "Se o email existir, um link de reset será enviado")
    try:
        with simple_tx() as cur:
            cur.execute("SELECT id, name FROM public.users WHERE email = %s", (data.email,))
            user = cur.fetchone()
            if not user:
                return generic
            reset_token = secrets.token_urlsafe(32)  # vai em claro SÓ no e-mail
            # invalida pedidos anteriores e guarda apenas o HASH do token
            cur.execute("DELETE FROM public.password_resets WHERE user_id = %s", (user["id"],))
            cur.execute(
                "INSERT INTO public.password_resets (user_id, token, expires_at)"
                " VALUES (%s, %s, NOW() + INTERVAL '1 hour')",
                (user["id"], _token_hash(reset_token)),
            )
    except Exception as e:
        logger.error(json.dumps({"event": "PASSWORD_RESET_REQUEST_ERROR",
                                 "error": type(e).__name__}))
        return error_response(500, "Erro ao solicitar reset")

    sent = email_service.send_reset_password_email(data.email, reset_token, user["name"])
    if not sent:  # falha de envio não muda a resposta (anti-enumeração), mas é logada
        logger.error(json.dumps({"event": "PASSWORD_RESET_EMAIL_FAILED",
                                 "user_id": str(user["id"])}))
    logger.info(json.dumps({"event": "PASSWORD_RESET_REQUESTED", "user_id": str(user["id"])}))
    return generic


def reset_password(event, context):
    """Reseta a senha a partir de um token válido e não expirado."""
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = ResetPasswordSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    try:
        with simple_tx() as cur:
            # Consumo ATÔMICO: deleta e retorna o user_id se válido e não expirado.
            cur.execute(
                "DELETE FROM public.password_resets"
                " WHERE token = %s AND expires_at > NOW() RETURNING user_id",
                (_token_hash(data.token),),
            )
            reset = cur.fetchone()
            if not reset:
                return error_response(400, "Link de reset inválido ou expirado")
            cur.execute(
                "UPDATE public.users SET password_hash = %s, updated_at = NOW()"
                " WHERE id = %s",
                (hash_password(data.password), reset["user_id"]),
            )
            # remove quaisquer outros tokens pendentes do usuário (defensivo)
            cur.execute("DELETE FROM public.password_resets WHERE user_id = %s",
                        (reset["user_id"],))
    except Exception as e:
        logger.error(json.dumps({"event": "PASSWORD_RESET_ERROR",
                                 "error": type(e).__name__}))
        return error_response(500, "Erro ao resetar senha")

    logger.info(json.dumps({"event": "PASSWORD_RESET_SUCCESS",
                            "user_id": str(reset["user_id"])}))
    return success_response(200, "Senha resetada com sucesso")


# ── rotas protegidas (JWT Authorizer) ────────────────────────────────────────
@require_user
def get_user(event, context):
    user = event["user"]
    target = _valid_uuid((event.get("pathParameters") or {}).get("userId"))
    if not target:
        return error_response(400, "userId inválido")
    if target != user["user_id"] and user["role"] != "admin":
        logger.warning(json.dumps({"event": "RBAC_VIOLATION_ATTEMPT",
                                   "user_id": user["user_id"], "action": "get_user"}))
        return error_response(403, "Acesso negado")
    try:
        with simple_tx() as cur:
            cur.execute(
                "SELECT id, email, name, role, status, created_at"
                " FROM public.users WHERE id = %s",
                (target,),
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "USER_GET_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao obter usuário")
    if not row:
        return error_response(404, "Usuário não encontrado")
    return success_response(200, "Usuário encontrado", _serialize(row))


@require_user
@require_role("admin")
def list_users(event, context):
    params = event.get("queryStringParameters") or {}
    pag, perr = _paginate(params)
    if perr:
        return perr
    page, page_size, offset = pag
    try:
        with simple_tx() as cur:
            cur.execute(
                "SELECT id, email, name, role, status, created_at FROM public.users"
                " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (page_size, offset),
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.error(json.dumps({"event": "USER_LIST_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao listar usuários")
    return success_response(200, f"{len(rows)} usuários encontrados",
                            [_serialize(r) for r in rows])


@require_user
def update_user(event, context):
    user = event["user"]
    target = _valid_uuid((event.get("pathParameters") or {}).get("userId"))
    if not target:
        return error_response(400, "userId inválido")
    if target != user["user_id"] and user["role"] != "admin":
        logger.warning(json.dumps({"event": "RBAC_VIOLATION_ATTEMPT",
                                   "user_id": user["user_id"], "action": "update_user"}))
        return error_response(403, "Acesso negado")
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = UserUpdateSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    fields, values = [], []
    if data.name is not None:
        fields.append("name = %s")
        values.append(data.name)
    # role/status só por admin (silenciosamente ignorados para não-admin).
    if user["role"] == "admin":
        if data.role is not None:
            fields.append("role = %s")
            values.append(data.role)
        if data.status is not None:
            fields.append("status = %s")
            values.append(data.status)
    if not fields:
        return error_response(400, "Nenhum campo para atualizar")
    fields.append("updated_at = NOW()")
    values.append(target)

    try:
        with simple_tx() as cur:
            cur.execute(
                f"UPDATE public.users SET {', '.join(fields)} WHERE id = %s",
                tuple(values),
            )
            updated = cur.rowcount
    except Exception as e:
        logger.error(json.dumps({"event": "USER_UPDATE_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao atualizar usuário")
    if not updated:
        return error_response(404, "Usuário não encontrado")
    logger.info(json.dumps({"event": "USER_UPDATED", "user_id": user["user_id"],
                            "target_user_id": target}))
    return success_response(200, "Usuário atualizado com sucesso")


@require_user
@require_role("admin")
def delete_user(event, context):
    user = event["user"]
    target = _valid_uuid((event.get("pathParameters") or {}).get("userId"))
    if not target:
        return error_response(400, "userId inválido")
    try:
        with simple_tx() as cur:
            cur.execute(
                "UPDATE public.users SET status = 'inactive', updated_at = NOW()"
                " WHERE id = %s",
                (target,),
            )
            updated = cur.rowcount
    except Exception as e:
        logger.error(json.dumps({"event": "USER_DELETE_ERROR", "error": type(e).__name__}))
        return error_response(500, "Erro ao deletar usuário")
    if not updated:
        return error_response(404, "Usuário não encontrado")
    logger.info(json.dumps({"event": "USER_DELETED", "user_id": user["user_id"],
                            "target_user_id": target}))
    return success_response(200, "Usuário desativado com sucesso")


def _serialize(row) -> dict:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "status": row["status"],
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
    }
