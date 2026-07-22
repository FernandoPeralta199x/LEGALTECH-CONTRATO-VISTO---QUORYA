"""Fundação V2 — get_user_from_event: validação de UUID/role/organization_id."""
import uuid

from src.utils.context import get_user_from_event

ORG = str(uuid.uuid4())


def _event(ctx):
    return {"requestContext": {"authorizer": {"context": ctx}}}


def test_valid_user_is_canonicalized():
    uid = uuid.uuid4()
    user = get_user_from_event(
        _event({"user_id": str(uid).upper(), "email": "a@b.c", "role": "analyst",
                "organization_id": ORG})
    )
    assert user == {"user_id": str(uid), "organization_id": ORG,
                    "email": "a@b.c", "role": "analyst", "token_iat": None}


def test_invalid_uuid_rejected():
    assert get_user_from_event(
        _event({"user_id": "nao-e-uuid", "role": "analyst", "organization_id": ORG})
    ) is None


def test_invalid_role_rejected():
    assert (
        get_user_from_event(
            _event({"user_id": str(uuid.uuid4()), "role": "hacker", "organization_id": ORG})
        )
        is None
    )


def test_missing_context_rejected():
    assert get_user_from_event({}) is None


def test_flat_authorizer_shape_is_read():
    # Shape REAL do REST API: claims achatados direto em authorizer.<key>.
    uid = uuid.uuid4()
    event = {"requestContext": {"authorizer": {
        "user_id": str(uid), "email": "a@b.c", "role": "viewer",
        "organization_id": ORG}}}
    assert get_user_from_event(event) == {
        "user_id": str(uid), "organization_id": ORG, "email": "a@b.c", "role": "viewer",
        "token_iat": None}


def test_token_iat_parsed_from_context_int_and_str():
    # AUTH-02: local_server injeta iat como int; API Gateway serializa como string.
    # Ambos viram int (epoch); ausente/vazio/inválido => None (sem checagem de revogação).
    uid = str(uuid.uuid4())
    casos = [(1700000000, 1700000000), ("1700000000", 1700000000),
             ("1700000000.0", 1700000000), ("", None), ("nao-num", None)]
    for raw, esperado in casos:
        ctx = {"user_id": uid, "role": "admin", "organization_id": ORG, "iat": raw}
        assert get_user_from_event(_event(ctx))["token_iat"] == esperado
    # chave iat ausente => None
    ctx = {"user_id": uid, "role": "admin", "organization_id": ORG}
    assert get_user_from_event(_event(ctx))["token_iat"] is None


def test_context_requires_organization_id():
    uid = str(uuid.uuid4())
    ev_no_org = {"requestContext": {"authorizer": {"user_id": uid, "role": "analyst"}}}
    assert get_user_from_event(ev_no_org) is None  # sem org → inválido
