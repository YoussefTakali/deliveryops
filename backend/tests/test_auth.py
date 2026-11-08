"""Unit tests for the Keycloak JWT validation and role/client derivation in app.auth.

These exercise the real signature/issuer/audience/expiry checks (a self-signed RSA keypair
stands in for Keycloak's), not the dev-mode persona header path — that path is exercised by the
rest of the suite through the `client` fixture.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import auth as auth_module
from app.auth import InvalidToken, decode_token, user_from_claims

ISSUER = "https://kc.example.com/realms/deliveryops"
AUDIENCE = "deliveryops-api"


@pytest.fixture(scope="module")
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(autouse=True)
def _configure(monkeypatch, keypair):
    """Point app.auth at our fake issuer/audience and a JWKS client backed by the test keypair,
    without touching the real network or the cached global Settings object other tests rely on."""
    _, public_key = keypair

    class _StubSigningKey:
        def __init__(self, key):
            self.key = key

    class _StubJWKSClient:
        def get_signing_key_from_jwt(self, token):
            return _StubSigningKey(public_key)

    monkeypatch.setattr(auth_module.settings, "keycloak_issuer", ISSUER, raising=False)
    monkeypatch.setattr(auth_module.settings, "keycloak_audience", AUDIENCE, raising=False)
    monkeypatch.setattr(auth_module, "_jwks_client", lambda: _StubJWKSClient())


def make_token(keypair, *, exp_delta=3600, issuer=ISSUER, audience=AUDIENCE, **claims):
    private_key, _ = keypair
    now = int(time.time())
    payload = {"iss": issuer, "aud": audience, "sub": "kc-user-1", "iat": now, "exp": now + exp_delta, **claims}
    return jwt.encode(payload, private_key, algorithm="RS256")


def test_decode_token_accepts_valid_signature_issuer_audience(keypair):
    token = make_token(keypair, preferred_username="karim", realm_access={"roles": ["delivery_manager"]})
    claims = decode_token(token)
    assert claims["preferred_username"] == "karim"


def test_decode_token_rejects_wrong_audience(keypair):
    token = make_token(keypair, audience="some-other-api")
    with pytest.raises(InvalidToken):
        decode_token(token)


def test_decode_token_rejects_wrong_issuer(keypair):
    token = make_token(keypair, issuer="https://attacker.example.com/realms/deliveryops")
    with pytest.raises(InvalidToken):
        decode_token(token)


def test_decode_token_rejects_expired(keypair):
    token = make_token(keypair, exp_delta=-3600)
    with pytest.raises(InvalidToken):
        decode_token(token)


def test_decode_token_rejects_tampered_signature(keypair):
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "iat": now, "exp": now + 3600}, other_private_key, algorithm="RS256")
    with pytest.raises(InvalidToken):
        decode_token(token)


def test_user_from_claims_director_sees_all_clients():
    user = user_from_claims({"sub": "u1", "preferred_username": "sarah", "realm_access": {"roles": ["director"]}})
    assert user.role == "director"
    assert user.clients == ["*"]
    assert user.can_see("any-client-id")


def test_user_from_claims_delivery_manager_scoped_to_clients_attribute():
    user = user_from_claims({"sub": "u2", "preferred_username": "karim", "realm_access": {"roles": ["delivery_manager"]}, "clients": ["cli-atlas"]})
    assert user.role == "delivery_manager"
    assert user.clients == ["cli-atlas"]
    assert user.can_see("cli-atlas") and not user.can_see("cli-horizon")
    assert user.can_decide


def test_user_from_claims_parses_comma_separated_clients_string():
    user = user_from_claims({"sub": "u3", "realm_access": {"roles": ["consultant"]}, "clients": "cli-atlas, cli-horizon"})
    assert sorted(user.clients) == ["cli-atlas", "cli-horizon"]
    assert not user.can_decide


def test_user_from_claims_merges_client_groups():
    user = user_from_claims({"sub": "u4", "realm_access": {"roles": ["consultant"]}, "groups": ["/client:cli-atlas", "/other-group"]})
    assert user.clients == ["cli-atlas"]


def test_user_from_claims_rejects_missing_role():
    with pytest.raises(InvalidToken):
        user_from_claims({"sub": "u5", "realm_access": {"roles": ["some-unrelated-role"]}})


def test_user_from_claims_prefers_highest_privilege_role():
    # a user should never end up with two DeliveryOps roles, but if realm config drifts,
    # the most privileged one must win rather than an arbitrary one
    user = user_from_claims({"sub": "u6", "realm_access": {"roles": ["consultant", "director"]}})
    assert user.role == "director"
