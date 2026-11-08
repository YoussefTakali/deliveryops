"""OpenID Connect building blocks: ID-token validation (signature, audience, nonce, issuer, tenant)
and which e-mail a provider really vouches for. The full sign-in flow is tests/_sso_flow.py."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import oidc
from app.routes.auth import _safe_next

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
MS = oidc.Provider("microsoft", "ms-client", "secret", "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration")
GOOGLE = oidc.Provider("google", "g-client", "secret", "https://accounts.google.com/.well-known/openid-configuration")


class FakeJWKS:
    def __init__(self, key):
        self.public = key.public_key()

    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": self.public})()


def token(claims: dict, key=KEY) -> str:
    now = int(time.time())
    return jwt.encode({"iat": now, "exp": now + 300, **claims}, key, algorithm="RS256")


def ms_claims(**over):
    return {"iss": "https://login.microsoftonline.com/t-acme/v2.0", "tid": "t-acme", "aud": "ms-client", "sub": "s",
            "oid": "o", "nonce": "n1", "preferred_username": "eve@acme.com", **over}


def test_a_valid_microsoft_token_is_accepted():
    claims = oidc.validate_id_token(MS, token(ms_claims()), "n1", FakeJWKS(KEY))
    assert claims["oid"] == "o"


@pytest.mark.parametrize("change, reason", [
    ({"aud": "someone-else"}, "audience"),
    ({"nonce": "replayed"}, "nonce"),
    ({"iss": "https://login.microsoftonline.com/t-other/v2.0"}, "issuer/tenant mismatch"),
    ({"exp": int(time.time()) - 3600}, "expired"),
])
def test_tampered_tokens_are_refused(change, reason):
    with pytest.raises(oidc.OIDCError):
        oidc.validate_id_token(MS, token(ms_claims(**change)), "n1", FakeJWKS(KEY))


def test_a_token_signed_by_another_key_is_refused():
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(oidc.OIDCError):
        oidc.validate_id_token(MS, token(ms_claims(), key=other), "n1", FakeJWKS(KEY))


def test_single_tenant_deployments_only_accept_their_tenant(monkeypatch):
    monkeypatch.setattr(oidc.settings, "microsoft_tenant_id", "t-acme")
    oidc.validate_id_token(MS, token(ms_claims()), "n1", FakeJWKS(KEY))
    other = ms_claims(tid="t-evil", iss="https://login.microsoftonline.com/t-evil/v2.0")
    with pytest.raises(oidc.OIDCError):
        oidc.validate_id_token(MS, token(other), "n1", FakeJWKS(KEY))


def test_google_issuer_and_workspace_domain(monkeypatch):
    good = {"iss": "https://accounts.google.com", "aud": "g-client", "sub": "g1", "nonce": "n1", "hd": "acme.com"}
    oidc.validate_id_token(GOOGLE, token(good), "n1", FakeJWKS(KEY))
    monkeypatch.setattr(oidc.settings, "google_hosted_domain", "acme.com")
    with pytest.raises(oidc.OIDCError):
        oidc.validate_id_token(GOOGLE, token({**good, "hd": "gmail.com"}), "n1", FakeJWKS(KEY))


def test_only_provider_verified_emails_are_trusted():
    # Microsoft: the UPN (domain verified in the tenant) yes; a guest UPN or a bare `email` claim no ("nOAuth")
    assert oidc.identity(MS, ms_claims()).email == "eve@acme.com"
    assert oidc.identity(MS, ms_claims(preferred_username="x#EXT#@t.onmicrosoft.com", email="eve@acme.com")).email is None
    assert oidc.identity(MS, ms_claims(preferred_username="", email="eve@acme.com", xms_edov=True)).email == "eve@acme.com"
    assert oidc.identity(MS, ms_claims()).subject == "o"                       # immutable object id, not the e-mail
    # Google: only with email_verified
    g = {"sub": "g1", "email": "Nina@Acme.com", "email_verified": True}
    assert oidc.identity(GOOGLE, g).email == "nina@acme.com"
    assert oidc.identity(GOOGLE, {**g, "email_verified": False}).email is None


def test_domain_allowlist(monkeypatch):
    monkeypatch.setattr(oidc.settings, "auth_allowed_domains", "acme.com, @acme.fr")
    assert oidc.domain_allowed("a@acme.com") and oidc.domain_allowed("a@acme.fr")
    assert not oidc.domain_allowed("a@gmail.com")
    monkeypatch.setattr(oidc.settings, "auth_allowed_domains", "")
    assert oidc.domain_allowed("a@anything.org")


@pytest.mark.parametrize("value, expected", [
    ("/projects", "/projects"), ("//evil.com", "/"), ("https://evil.com", "/"), ("/\\evil.com", "/"),
    ("/api/admin", "/"), ("", "/"), (None, "/"),
])
def test_post_sign_in_redirect_stays_on_the_site(value, expected):
    assert _safe_next(value) == expected
