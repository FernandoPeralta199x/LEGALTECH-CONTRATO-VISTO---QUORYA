"""Fase 1 — get_user_from_event: validação de UUID/role (correções pós-Codex)."""
import uuid

from src.utils.context import get_user_from_event


def _event(ctx):
    return {"requestContext": {"authorizer": {"context": ctx}}}


def test_valid_user_is_canonicalized():
    uid = uuid.uuid4()
    user = get_user_from_event(
        _event({"user_id": str(uid).upper(), "email": "a@b.c", "role": "analyst"})
    )
    assert user == {"user_id": str(uid), "email": "a@b.c", "role": "analyst"}


def test_invalid_uuid_rejected():
    assert get_user_from_event(_event({"user_id": "nao-e-uuid", "role": "analyst"})) is None


def test_invalid_role_rejected():
    assert (
        get_user_from_event(_event({"user_id": str(uuid.uuid4()), "role": "hacker"}))
        is None
    )


def test_missing_context_rejected():
    assert get_user_from_event({}) is None


def test_flat_authorizer_shape_is_read():
    # Shape REAL do REST API: claims achatados direto em authorizer.<key>.
    uid = uuid.uuid4()
    event = {"requestContext": {"authorizer": {
        "user_id": str(uid), "email": "a@b.c", "role": "viewer"}}}
    assert get_user_from_event(event) == {
        "user_id": str(uid), "email": "a@b.c", "role": "viewer"}
