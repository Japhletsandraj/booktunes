"""Password hashing and JWT behaviour."""

import uuid
from datetime import timedelta

import pytest
from jose import JWTError

from app.core import security


def test_password_round_trip():
    hashed = security.hash_password("correct-horse-9")
    assert hashed != "correct-horse-9"
    assert security.verify_password("correct-horse-9", hashed)
    assert not security.verify_password("wrong-password-1", hashed)


def test_password_over_72_bytes_is_rejected():
    """bcrypt truncates past 72 bytes; silently accepting would mean two
    different long passwords authenticate each other."""
    with pytest.raises(ValueError):
        security.hash_password("a" * 73)


def test_multibyte_password_counted_in_bytes():
    # 30 emoji = 120 bytes despite being 30 characters.
    with pytest.raises(ValueError):
        security.hash_password("🔒" * 30)


def test_verify_password_survives_malformed_hash():
    assert not security.verify_password("anything", "not-a-bcrypt-hash")


def test_access_token_round_trip():
    subject = str(uuid.uuid4())
    token = security.create_access_token(subject)
    claims = security.decode_token(token, expected_type=security.ACCESS_TOKEN)
    assert claims["sub"] == subject
    assert claims["type"] == "access"
    assert claims["jti"]


def test_token_type_is_enforced():
    """A refresh token must not be usable as an access token."""
    refresh = security.create_refresh_token(str(uuid.uuid4()))
    with pytest.raises(JWTError):
        security.decode_token(refresh, expected_type=security.ACCESS_TOKEN)


def test_tampered_token_rejected():
    token = security.create_access_token(str(uuid.uuid4()))
    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    with pytest.raises(JWTError):
        security.decode_token(tampered)


def test_expired_token_rejected():
    token = security._create_token(
        "user-1", security.ACCESS_TOKEN, timedelta(seconds=-10)
    )
    with pytest.raises(JWTError):
        security.decode_token(token)


def test_each_token_has_a_unique_jti():
    """Logout revokes by jti, so collisions would sign out unrelated sessions."""
    subject = str(uuid.uuid4())
    a = security.decode_token(security.create_access_token(subject))
    b = security.decode_token(security.create_access_token(subject))
    assert a["jti"] != b["jti"]
