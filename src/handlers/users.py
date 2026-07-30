"""Handlers Lambda de `users` / autenticação (migração FastAPI→Serverless, Fase A).

- Rotas PÚBLICAS: `create_user` (signup), `login`, `forgot_password`,
  `reset_password`. As demais são protegidas pelo JWT Authorizer (`@require_user`).
- `public.users` NÃO tem RLS → usa `simple_tx` (transação global, sem contexto).
- Segurança: signup cria o 1º usuário da org como `admin` (dono do tenant; papéis
  alterados depois por admin via `update_user`);
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
from src.services.database import signup_tx, simple_tx
from src.services.email import email_service
from src.utils.auth import TOKEN_TTL_SECONDS, create_access_token
from src.utils.context import (
    CallerRevoked,
    assert_active_admin,
    effective_telas,
    load_active_caller,
    require_perfil,
    require_role,
    require_user,
)
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


class _LastAdmin(Exception):
    """A operação deixaria o sistema sem nenhum administrador ativo."""


def _is_last_active_admin(cur, target_id, organization_id) -> bool:
    """True se `target_id` é admin ativo e é o ÚNICO admin ativo da sua organização."""
    cur.execute("SELECT role, status FROM public.users WHERE id = %s AND organization_id = %s",
                (target_id, organization_id))
    t = cur.fetchone()
    if not t or t["role"] != "admin" or t["status"] != "active":
        return False
    # AUTH-05: trava as linhas dos admins ativos da org (FOR UPDATE) ANTES de contar,
    # serializando mutações concorrentes. Sem o lock, duas requisições rebaixando/
    # desativando admins DIFERENTES leem count=2 cada, nenhuma barra, e a org fica sem
    # admin. Com o lock, a 2ª transação espera a 1ª commitar e re-lê o count atualizado.
    cur.execute("SELECT id FROM public.users"
                " WHERE role='admin' AND status='active' AND organization_id = %s FOR UPDATE",
                (organization_id,))
    return len(cur.fetchall()) <= 1


# ── rotas públicas ───────────────────────────────────────────────────────────
def create_user(event, context):
    """Signup PÚBLICO: cria uma organização INDIVIDUAL nova e o usuário como
    `cliente_comum` (Modelo B — todo cadastro público nasce cliente comum, org solo;
    admin/empresarial são criados manualmente). `role='admin'` da PRÓPRIA org (ele
    escreve os próprios pedidos); o `perfil` é que restringe as telas."""
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = UserSignupSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    user_id = generate_uuid()
    org_id = generate_uuid()
    try:
        with signup_tx(org_id) as cur:
            cur.execute("SELECT 1 FROM public.users WHERE email = %s", (data.email,))
            if cur.fetchone():
                return error_response(409, "Email já cadastrado")
            cur.execute(
                "INSERT INTO public.organizations (id, name, type)"
                " VALUES (%s, %s, 'individual')",
                (org_id, f"Org de {data.name}"),
            )
            cur.execute(
                "INSERT INTO public.users"
                " (id, email, password_hash, name, role, status, organization_id, perfil)"
                " VALUES (%s, %s, %s, %s, 'admin', 'active', %s, 'cliente_comum')",
                (user_id, data.email, hash_password(data.password), data.name, org_id),
            )
    except psycopg2.errors.UniqueViolation:
        # corrida: 2 signups simultâneos passam pelo SELECT; o índice único garante
        return error_response(409, "Email já cadastrado")
    except Exception as e:
        logger.error(json.dumps({"event": "USER_CREATE_ERROR",
                                 "error": type(e).__name__,
                                 "pgcode": getattr(e, "pgcode", None)}), exc_info=True)
        return error_response(500, "Erro ao criar usuário")

    logger.info(json.dumps({"event": "USER_CREATED", "user_id": user_id,
                            "role": "admin", "perfil": "cliente_comum"}))
    return success_response(201, "Usuário criado com sucesso",
                            {"user_id": user_id, "role": "admin",
                             "perfil": "cliente_comum", "organization_id": org_id})


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
                "SELECT id, email, name, password_hash, role, organization_id, perfil,"
                " telas_extra FROM public.users WHERE email = %s AND status = 'active'",
                (data.email,),
            )
            user = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "AUTH_LOGIN_ERROR",
                                 "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro no login")

    # Sempre executa bcrypt (hash dummy se o usuário não existe) p/ tempo uniforme.
    stored_hash = user["password_hash"] if user else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(data.password, stored_hash)
    if not user or not password_ok:
        logger.warning(json.dumps({"event": "AUTH_LOGIN_FAILED"}))  # sem PII
        return error_response(401, "Email ou senha inválidos")

    token = create_access_token({  # sem email no token (menos PII)
        "user_id": str(user["id"]), "role": user["role"],
        "organization_id": str(user["organization_id"]),
        "perfil": user["perfil"],  # perfil de acesso (telas) — eixo separado do role
        "telas_extra": list(user["telas_extra"] or []),  # Modelo B: abas liberadas
        # claims exigidos pelo frontend (sessão local): sub + token_use
        "sub": str(user["id"]), "token_use": "dev",
    })
    logger.info(json.dumps({"event": "AUTH_LOGIN_SUCCESS", "user_id": str(user["id"])}))
    # Shape alinhado ao contrato do frontend (authApi.AuthTokenResult).
    return success_response(200, "Login bem-sucedido", {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_SECONDS,
        "user": {
            "id": str(user["id"]),
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
            "perfil": user["perfil"],
            "telas": sorted(effective_telas(user["perfil"], user["telas_extra"])),
            "organization_id": str(user["organization_id"]),
        },
    })


@require_user
def me(event, context):
    """Retorna o usuário autenticado (GET /auth/me). Mantém a sessão no frontend."""
    user = event["user"]
    try:
        with simple_tx() as cur:
            cur.execute(
                "SELECT id, email, name, role, organization_id, perfil, telas_extra"
                " FROM public.users WHERE id = %s AND status = 'active'",
                (user["user_id"],),
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(json.dumps({"event": "AUTH_ME_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao obter usuário atual")
    if not row:
        return error_response(404, "Usuário não encontrado")
    return success_response(200, "Usuário atual", {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "perfil": row["perfil"],
        "telas": sorted(effective_telas(row["perfil"], row["telas_extra"])),
        "organization_id": str(row["organization_id"]),
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
            # upsert atômico: 1 token por usuário (UNIQUE user_id); evita corrida de
            # múltiplos tokens válidos. Guarda apenas o HASH do token.
            # SEC-08: COOLDOWN atômico — o DO UPDATE só troca o token/reenvia se o
            # anterior for mais velho que o cooldown (reusa created_at existente, sem
            # schema novo). RETURNING indica se um novo token foi emitido; dentro do
            # cooldown, nada é retornado e o token vigente é preservado (anti-spam).
            cur.execute(
                "INSERT INTO public.password_resets (user_id, token, expires_at)"
                " VALUES (%s, %s, NOW() + INTERVAL '1 hour')"
                " ON CONFLICT (user_id) DO UPDATE SET token = EXCLUDED.token,"
                " expires_at = EXCLUDED.expires_at, created_at = NOW()"
                " WHERE public.password_resets.created_at < NOW() - INTERVAL '60 seconds'"
                " RETURNING user_id",
                (user["id"], _token_hash(reset_token)),
            )
            issued = cur.fetchone() is not None
    except Exception as e:
        logger.error(json.dumps({"event": "PASSWORD_RESET_REQUEST_ERROR",
                                 "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao solicitar reset")

    # SEC-08: só envia (e registra a solicitação) quando um NOVO token foi emitido.
    # Dentro do cooldown, mantém o token vigente e NÃO reenvia. A resposta é SEMPRE
    # genérica (anti-enumeração), tenha havido emissão ou não.
    if issued:
        sent = email_service.send_reset_password_email(data.email, reset_token, user["name"])
        if not sent:  # falha de envio não muda a resposta, mas é logada
            logger.error(json.dumps({"event": "PASSWORD_RESET_EMAIL_FAILED",
                                     "user_id": str(user["id"])}))
        logger.info(json.dumps({"event": "PASSWORD_RESET_REQUESTED", "user_id": str(user["id"])}))
    else:
        logger.info(json.dumps({"event": "PASSWORD_RESET_THROTTLED", "user_id": str(user["id"])}))
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
            # AUTH-02: password_changed_at revoga tokens emitidos ANTES do reset
            # (load_active_caller rejeita iat < password_changed_at).
            cur.execute(
                "UPDATE public.users"
                " SET password_hash = %s, password_changed_at = NOW(), updated_at = NOW()"
                " WHERE id = %s",
                (hash_password(data.password), reset["user_id"]),
            )
            # remove quaisquer outros tokens pendentes do usuário (defensivo)
            cur.execute("DELETE FROM public.password_resets WHERE user_id = %s",
                        (reset["user_id"],))
    except Exception as e:
        logger.error(json.dumps({"event": "PASSWORD_RESET_ERROR",
                                 "error": type(e).__name__}), exc_info=True)
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
    try:
        with simple_tx() as cur:
            # SEC-04: leitura de PII de terceiros exige papel ATUAL no banco, não o
            # papel histórico do token (admin revogado não pode mais ler outros).
            caller_role = load_active_caller(cur, user["user_id"], user["organization_id"])
            if target != user["user_id"] and caller_role != "admin":
                logger.warning(json.dumps({"event": "RBAC_VIOLATION_ATTEMPT",
                                           "user_id": user["user_id"], "action": "get_user"}))
                return error_response(403, "Acesso negado")
            cur.execute(
                "SELECT id, email, name, role, status, created_at"
                " FROM public.users WHERE id = %s AND organization_id = %s",
                (target, user["organization_id"]),
            )
            row = cur.fetchone()
    except CallerRevoked:
        logger.warning(json.dumps({"event": "AUTHZ_REVOKED", "action": "get_user",
                                   "user_id": user["user_id"]}))
        return error_response(403, "Acesso negado")
    except Exception as e:
        logger.error(json.dumps({"event": "USER_GET_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao obter usuário")
    if not row:
        return error_response(404, "Usuário não encontrado")
    return success_response(200, "Usuário encontrado", _serialize(row))


@require_user
@require_role("admin")
@require_perfil("administrador")
def list_users(event, context):
    params = event.get("queryStringParameters") or {}
    pag, perr = _paginate(params)
    if perr:
        return perr
    page, page_size, offset = pag
    caller = event["user"]
    try:
        with simple_tx() as cur:
            # SEC-01: o papel 'admin' precisa valer AGORA no banco (não só no token).
            assert_active_admin(cur, caller["user_id"], caller["organization_id"])
            cur.execute(
                "SELECT id, email, name, role, status, created_at FROM public.users"
                " WHERE organization_id = %s"
                " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (caller["organization_id"], page_size, offset),
            )
            rows = cur.fetchall()
    except CallerRevoked:
        logger.warning(json.dumps({"event": "AUTHZ_REVOKED", "action": "list_users",
                                   "user_id": caller["user_id"]}))
        return error_response(403, "Acesso negado")
    except Exception as e:
        logger.error(json.dumps({"event": "USER_LIST_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao listar usuários")
    return success_response(200, f"{len(rows)} usuários encontrados",
                            [_serialize(r) for r in rows])


@require_user
def update_user(event, context):
    user = event["user"]
    target = _valid_uuid((event.get("pathParameters") or {}).get("userId"))
    if not target:
        return error_response(400, "userId inválido")
    body, err = _parse_body(event)
    if err:
        return err
    try:
        data = UserUpdateSchema(**body)
    except ValidationError as e:
        return error_response(400, f"Validação falhou: {_fmt(e)}")

    try:
        with simple_tx() as cur:
            # SEC-02: quem pode escrever (e alterar role/status) é decidido pelo papel
            # ATUAL do caller no banco, nunca pelo role do token. Um admin rebaixado
            # com JWT antigo não elava a si mesmo nem edita terceiros.
            caller_role = load_active_caller(cur, user["user_id"], user["organization_id"])
            if target != user["user_id"] and caller_role != "admin":
                logger.warning(json.dumps({"event": "RBAC_VIOLATION_ATTEMPT",
                                           "user_id": user["user_id"], "action": "update_user"}))
                return error_response(403, "Acesso negado")

            fields, values = [], []
            if data.name is not None:
                fields.append("name = %s")
                values.append(data.name)
            # role/status só por admin ATUAL (ignorados p/ não-admin do banco).
            if caller_role == "admin":
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

            demoting = caller_role == "admin" and data.role is not None and data.role != "admin"
            deactivating = caller_role == "admin" and data.status == "inactive"
            if (demoting or deactivating) and _is_last_active_admin(
                cur, target, user["organization_id"]
            ):
                raise _LastAdmin()
            cur.execute(
                f"UPDATE public.users SET {', '.join(fields)}"
                " WHERE id = %s AND organization_id = %s",
                (*values, user["organization_id"]),
            )
            updated = cur.rowcount
    except CallerRevoked:
        logger.warning(json.dumps({"event": "AUTHZ_REVOKED", "action": "update_user",
                                   "user_id": user["user_id"]}))
        return error_response(403, "Acesso negado")
    except _LastAdmin:
        return error_response(409, "não é possível rebaixar/desativar o último administrador")
    except Exception as e:
        logger.error(json.dumps({"event": "USER_UPDATE_ERROR", "error": type(e).__name__}), exc_info=True)
        return error_response(500, "Erro ao atualizar usuário")
    if not updated:
        return error_response(404, "Usuário não encontrado")
    logger.info(json.dumps({"event": "USER_UPDATED", "user_id": user["user_id"],
                            "target_user_id": target}))
    return success_response(200, "Usuário atualizado com sucesso")


@require_user
@require_role("admin")
@require_perfil("administrador")
def delete_user(event, context):
    user = event["user"]
    target = _valid_uuid((event.get("pathParameters") or {}).get("userId"))
    if not target:
        return error_response(400, "userId inválido")
    try:
        with simple_tx() as cur:
            # SEC-03: mutação administrativa exige papel admin ATUAL no banco.
            assert_active_admin(cur, user["user_id"], user["organization_id"])
            if _is_last_active_admin(cur, target, user["organization_id"]):
                raise _LastAdmin()
            cur.execute(
                "UPDATE public.users SET status = 'inactive', updated_at = NOW()"
                " WHERE id = %s AND organization_id = %s",
                (target, user["organization_id"]),
            )
            updated = cur.rowcount
    except CallerRevoked:
        logger.warning(json.dumps({"event": "AUTHZ_REVOKED", "action": "delete_user",
                                   "user_id": user["user_id"]}))
        return error_response(403, "Acesso negado")
    except _LastAdmin:
        return error_response(409, "não é possível desativar o último administrador")
    except Exception as e:
        logger.error(json.dumps({"event": "USER_DELETE_ERROR", "error": type(e).__name__}), exc_info=True)
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
