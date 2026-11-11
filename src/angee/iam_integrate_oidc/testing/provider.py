"""A fake OpenID provider for tests of OIDC login, account linking and OAuth connect."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import cache
from typing import Any

import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

KEY_ID = "oidc-provider-fake"


@cache
def signing_key() -> rsa.RSAPrivateKey:
    """Return the process-wide provider key, so the cached JWKS client verifies every test's tokens."""

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class OidcProvider:
    """An OpenID provider for one client, answering requests the integrate HTTP seam routes to it.

    Pass it as the ``mock_transport`` handler (``angee.integrate.testing.http``). Under
    ``issuer`` it serves discovery, the JWKS, the token endpoint for form and JSON
    grants, userinfo and revocation, at the paths ``metadata`` advertises. The token
    endpoint answers ``token``; with ``id_claims`` it adds an RS256 ID token signed by
    the key the JWKS publishes, so login runs PyJWT's real verification.
    """

    issuer: str = "https://issuer.example"
    client_id: str = "oidc-client"
    token: dict[str, Any] = field(default_factory=lambda: {"access_token": "access"})
    id_claims: dict[str, Any] | None = None
    userinfo: dict[str, Any] = field(default_factory=dict)

    @property
    def metadata(self) -> dict[str, str]:
        """Return the discovery document."""

        return {
            "issuer": self.issuer,
            **{
                key: f"{self.issuer}/oauth/{path}"
                for key, path in (
                    ("authorization_endpoint", "authorize"),
                    ("token_endpoint", "token"),
                    ("userinfo_endpoint", "userinfo"),
                    ("jwks_uri", "jwks"),
                    ("revocation_endpoint", "revoke"),
                )
            },
        }

    def id_token(self, **claims: Any) -> str:
        """Sign an ID token for this client; ``claims`` add to or override the issued defaults."""

        now = int(time.time())
        payload = {"iss": self.issuer, "aud": self.client_id, "iat": now, "exp": now + 300, **claims}
        return jwt.encode(payload, signing_key(), algorithm="RS256", headers={"kid": KEY_ID})

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Answer one request by its endpoint path."""

        endpoint = request.url.path.rsplit("/", 1)[-1]
        if endpoint == "openid-configuration":
            return httpx2.Response(200, json=self.metadata)
        if endpoint == "jwks":
            public = RSAAlgorithm.to_jwk(signing_key().public_key(), as_dict=True)
            return httpx2.Response(200, json={"keys": [{**public, "kid": KEY_ID, "use": "sig"}]})
        if endpoint == "token":
            issued = dict(self.token)
            if self.id_claims is not None:
                issued["id_token"] = self.id_token(**self.id_claims)
            return httpx2.Response(200, json=issued)
        if endpoint == "userinfo":
            return httpx2.Response(200, json=self.userinfo)
        if endpoint == "revoke":
            return httpx2.Response(200, json={})
        return httpx2.Response(404)
