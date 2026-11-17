"""The JWT bearer credential kind: a provider signing key exchanged per scope set (RFC 7523 §2.1)."""

from __future__ import annotations

import importlib
import json
import socket
import threading
import time
from contextvars import copy_context
from dataclasses import dataclass, field
from functools import cache
from typing import Any

import httpx2
import pytest
from django.core.exceptions import ValidationError
from django.db import connections
from joserfc import jwt
from joserfc.jwk import ECKey, RSAKey
from rebac import system_context

from angee.graphql.testing.documents import execute_schema, result_data
from angee.integrate.credentials import CredentialKind
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.testing.http import mock_transport, request_fields
from tests.conftest import Credential, addon_schema, create_platform_admin, create_user

iam_schema = importlib.import_module("angee.iam.schema")
integrate_schema = importlib.import_module("angee.integrate.schema")

TOKEN_ENDPOINT = "https://oauth2.provider.test/token"
ISSUER = "calendar-writer@project.iam.provider.test"
KEY_ID = "4f1c-key-id"
JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
CALENDAR = "https://www.googleapis.com/auth/calendar.events"
READONLY = "https://www.googleapis.com/auth/calendar.readonly"


@cache
def signing_key() -> RSAKey:
    """Return one process-wide RSA key, so key generation runs once per test session."""

    return RSAKey.generate_key(2048)


def private_pem() -> str:
    return signing_key().as_pem(private=True).decode()


def google_key_file(**overrides: Any) -> str:
    """Return a key file in Google's service-account format, with its extra members."""

    document = {
        "type": "service_account",
        "project_id": "project",
        "private_key_id": KEY_ID,
        "private_key": private_pem(),
        "client_email": ISSUER,
        "client_id": "1234567890",
        "auth_uri": "https://accounts.provider.test/o/oauth2/auth",
        "token_uri": TOKEN_ENDPOINT,
        "universe_domain": "provider.test",
        **overrides,
    }
    return json.dumps({key: value for key, value in document.items() if value is not None})


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve every hostname to one public address, so save-time address validation passes."""

    def resolve(hostname: str, port: int | None, *, type: int) -> list[Any]:
        del hostname, type
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def create_jwt_bearer(name: str = "calendar-writer", key_file: str | None = None) -> Any:
    """Create one JWT bearer credential through the manager's local-credential verb."""

    user = create_user(f"{name}-owner")
    return Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.JWT_BEARER,
        name=name,
        material={"key_file": key_file or google_key_file()},
    )


@dataclass
class TokenEndpoint:
    """A JWT bearer token endpoint that verifies each assertion and answers with ``status``."""

    status: int = 200
    expires_in: int = 3600
    delay: float = 0.0
    refusal: dict[str, Any] = field(default_factory=lambda: {"error": "invalid_grant"})
    grants: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        fields = request_fields(request)
        token = jwt.decode(fields["assertion"], signing_key(), algorithms=["RS256"])
        self.grants.append({"url": str(request.url), "fields": fields, "header": token.header, "claims": token.claims})
        time.sleep(self.delay)
        if self.status != 200:
            return httpx2.Response(self.status, json=self.refusal)
        number = len(self.grants)
        return httpx2.Response(
            200, json={"access_token": f"access-{number}", "expires_in": self.expires_in, "token_type": "Bearer"}
        )


@pytest.mark.django_db
def test_a_google_key_file_is_stored_as_provider_neutral_signing_material(public_dns: None) -> None:
    """The handler reads Google's names into its own fields and drops the rest of the key file."""

    with system_context(reason="test jwt bearer create"):
        credential = create_jwt_bearer()

    assert credential.kind == CredentialKind.JWT_BEARER
    assert credential.reveal() == {
        "issuer": ISSUER,
        "private_key": private_pem().strip(),
        "key_id": KEY_ID,
        "token_endpoint": TOKEN_ENDPOINT,
        "subject": "",
        "audience": "",
    }
    assert credential.secret_value() == private_pem().strip()
    assert not credential.is_reconnect_required


def test_a_neutral_key_file_names_its_subject_and_audience(public_dns: None) -> None:
    """A key file in the provider-neutral names is accepted with its optional delegation fields."""

    key_file = json.dumps(
        {
            "issuer": ISSUER,
            "private_key": private_pem(),
            "token_endpoint": TOKEN_ENDPOINT,
            "subject": "room-owner@example.test",
            "audience": "https://api.provider.test/",
        }
    )

    assert CredentialKind.JWT_BEARER.handler.clean({"key_file": key_file}) == {
        "issuer": ISSUER,
        "private_key": private_pem().strip(),
        "key_id": "",
        "token_endpoint": TOKEN_ENDPOINT,
        "subject": "room-owner@example.test",
        "audience": "https://api.provider.test/",
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "key_file",
    [
        pytest.param("", id="missing"),
        pytest.param("-----BEGIN PRIVATE KEY-----", id="not-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param(google_key_file(client_email=None), id="no-issuer"),
        pytest.param(google_key_file(private_key="-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----"),
                     id="malformed-pem"),
        pytest.param(google_key_file(private_key=signing_key().as_pem(private=False).decode()), id="public-key"),
        pytest.param(google_key_file(private_key=ECKey.generate_key("P-256").as_pem(private=True).decode()),
                     id="not-rsa"),
        pytest.param(google_key_file(token_uri="http://oauth2.provider.test/token"), id="plain-http"),
        pytest.param(google_key_file(token_uri="https://10.0.0.1/token"), id="private-address"),
        pytest.param(google_key_file(token_uri="https://169.254.169.254/token"), id="metadata-address"),
    ],
)
def test_an_unusable_key_file_is_refused_on_save(public_dns: None, key_file: str) -> None:
    """Every refusal names the key-file input, echoes none of its values, and stores nothing."""

    user = create_user("jwt-bearer-refused")
    with system_context(reason="test jwt bearer refusal"), pytest.raises(ValidationError) as refusal:
        Credential.objects.create_local_credential(
            user, kind=CredentialKind.JWT_BEARER, name="refused", material={"key_file": key_file}
        )

    assert set(refusal.value.message_dict) == {"key_file"}
    assert "BEGIN" not in str(refusal.value) and ISSUER not in str(refusal.value)
    with system_context(reason="test jwt bearer refusal stored nothing"):
        assert not Credential.objects.filter(user=user).exists()


@pytest.mark.django_db(transaction=True)
def test_a_token_is_minted_with_the_assertion_claims_and_cached_per_scope_set(public_dns: None) -> None:
    """One grant per scope set; order and repeats name the same set; the grant carries RFC 7523 claims."""

    with system_context(reason="test jwt bearer mint setup"):
        credential = create_jwt_bearer()
    endpoint = TokenEndpoint()

    with mock_transport(endpoint) as requests:
        before = int(time.time())
        assert credential.auth_headers(scopes=[CALENDAR, READONLY]) == {"Authorization": "Bearer access-1"}
        assert credential.auth_headers(scopes=[READONLY, CALENDAR, READONLY]) == {"Authorization": "Bearer access-1"}
        assert credential.scoped_access_token(f"{READONLY} {CALENDAR}") == "access-1"
        assert credential.scoped_access_token(CALENDAR) == "access-2"
        assert credential.scoped_access_token([CALENDAR]) == "access-2"

    assert len(requests) == 2
    assert all(request.extensions["allow_private"] is False for request in requests)
    first = endpoint.grants[0]
    assert first["url"] == TOKEN_ENDPOINT
    assert first["fields"]["grant_type"] == JWT_BEARER_GRANT
    assert set(first["fields"]) == {"grant_type", "assertion"}
    assert first["header"] == {"typ": "JWT", "alg": "RS256", "kid": KEY_ID}
    claims = first["claims"]
    assert claims["iss"] == ISSUER
    assert claims["aud"] == TOKEN_ENDPOINT
    assert claims["scope"] == " ".join(sorted([CALENDAR, READONLY]))
    assert "sub" not in claims
    assert before <= claims["iat"] <= int(time.time())
    assert claims["exp"] == claims["iat"] + 3600
    assert claims["jti"] != endpoint.grants[1]["claims"]["jti"]
    reloaded = Credential.objects.sudo(reason="test jwt bearer mint persisted").get(pk=credential.pk)
    assert set(reloaded.reveal()["scoped_tokens"]) == {" ".join(sorted([CALENDAR, READONLY])), CALENDAR}
    assert reloaded.last_refresh_status == "ok"
    assert reloaded.last_refresh_at is not None
    assert reloaded.expires_at is None  # the credential itself never expires; its tokens do


@pytest.mark.django_db(transaction=True)
def test_a_token_within_the_renewal_margin_is_minted_again(public_dns: None) -> None:
    """A token about to expire is never handed out; the next call mints a fresh one."""

    with system_context(reason="test jwt bearer renewal setup"):
        credential = create_jwt_bearer()
    endpoint = TokenEndpoint(expires_in=60)

    with mock_transport(endpoint) as requests:
        assert credential.scoped_access_token([CALENDAR]) == "access-1"
        assert credential.scoped_access_token([CALENDAR]) == "access-2"

    assert len(requests) == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_callers_produce_one_grant(public_dns: None) -> None:
    """The second caller waits on the credential's renewal lock and adopts the persisted token."""

    with system_context(reason="test jwt bearer race setup"):
        credential = create_jwt_bearer()
    endpoint = TokenEndpoint(delay=0.3)
    start = threading.Barrier(2)
    tokens: list[str] = []
    failures: list[BaseException] = []

    def consume() -> None:
        try:
            with system_context(reason="test jwt bearer race consumer"):
                row = Credential.objects.sudo(reason="test jwt bearer race load").get(pk=credential.pk)
                start.wait(timeout=10)
                tokens.append(row.scoped_access_token([CALENDAR]))
        except BaseException as error:  # noqa: BLE001 - the test thread reports any failure below
            failures.append(error)
        finally:
            connections.close_all()

    with mock_transport(endpoint) as requests:
        threads = [threading.Thread(target=copy_context().run, args=(consume,)) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

    assert not failures
    assert tokens == ["access-1", "access-1"]
    assert len(requests) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("status", "reconnect"),
    [
        pytest.param(400, True, id="refused-grant-needs-reconnect"),
        pytest.param(503, False, id="unavailable-endpoint-stays-healthy"),
    ],
)
def test_a_refused_grant_flips_the_reconnect_state(public_dns: None, status: int, reconnect: bool) -> None:
    """A permanent refusal records the failure; a transient one leaves the credential healthy."""

    with system_context(reason="test jwt bearer refusal setup"):
        credential = create_jwt_bearer()

    with mock_transport(TokenEndpoint(status=status)), pytest.raises(OAuthFlowError) as raised:
        credential.scoped_access_token([CALENDAR])

    assert raised.value.transient is not reconnect
    assert credential.is_reconnect_required is reconnect
    assert credential.last_refresh_status == ("failed" if reconnect else "")


@pytest.mark.django_db(transaction=True)
def test_replacing_the_key_file_drops_tokens_minted_from_the_old_key(public_dns: None) -> None:
    """A re-entered key file starts with no cached tokens."""

    with system_context(reason="test jwt bearer replace setup"):
        credential = create_jwt_bearer()
    endpoint = TokenEndpoint()

    with mock_transport(endpoint) as requests:
        assert credential.scoped_access_token([CALENDAR]) == "access-1"
        credential.replace_material({"key_file": google_key_file(private_key_id="rotated-key")})
        assert credential.scoped_access_token([CALENDAR]) == "access-2"

    assert len(requests) == 2
    assert endpoint.grants[1]["header"]["kid"] == "rotated-key"


@pytest.mark.django_db
def test_a_scoped_token_needs_scopes_and_fixed_tokens_ignore_them(public_dns: None) -> None:
    """A JWT bearer credential refuses an empty scope set before any request; other kinds ignore scopes."""

    with system_context(reason="test jwt bearer scopes setup"):
        credential = create_jwt_bearer()
        static = Credential.objects.create_local_credential(
            create_user("static-scopes"), kind=CredentialKind.STATIC_TOKEN, name="static", material={"api_key": "k"}
        )

    with mock_transport(lambda request: pytest.fail("no grant without scopes")), pytest.raises(ValueError):
        credential.auth_headers()
    assert static.auth_headers(scopes=[CALENDAR]) == {"Authorization": "Bearer k"}


@pytest.mark.django_db(transaction=True)
def test_key_material_and_tokens_stay_out_of_logs_repr_and_graphql(
    public_dns: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The console creates the credential from a pasted key file and projects no secret back."""

    admin = create_platform_admin("jwt-bearer-admin")
    schema = addon_schema(integrate_schema.schemas, "console", iam_schema.schemas)
    create = """
        mutation CreateCredential($data: CredentialInput!) {
            create_credential(data: $data) { id kind name status last_refresh_status display_name }
        }
    """
    refused = execute_schema(
        schema, create, {"data": {"name": "bad", "kind": "jwt_bearer", "key_file": google_key_file(token_uri="")}},
        user=admin,
    )
    assert refused.errors is not None
    assert set(refused.errors[0].extensions["validationErrors"]) == {"key_file"}
    created = result_data(
        execute_schema(
            schema, create, {"data": {"name": "room-writer", "kind": "jwt_bearer", "key_file": google_key_file()}},
            user=admin,
        )
    )["create_credential"]
    with system_context(reason="test jwt bearer graphql load"):
        credential = Credential.objects.get(sqid=created["id"])

    with mock_transport(TokenEndpoint()):
        token = credential.scoped_access_token([CALENDAR])
    with mock_transport(TokenEndpoint(status=400)), pytest.raises(OAuthFlowError):
        credential.scoped_access_token([READONLY])
    read = result_data(
        execute_schema(
            schema,
            "query($id: String!) { credentials_by_pk(id: $id) { kind name status last_refresh_status display_name } }",
            {"id": created["id"]},
            user=admin,
        )
    )

    secrets = [token, private_pem().strip().splitlines()[1], KEY_ID]
    projected = json.dumps([created, read]) + repr(credential) + str(credential)
    assert "JWT bearer token request failed" in caplog.text
    for secret in secrets:
        assert secret not in projected
        assert secret not in caplog.text
        assert all(secret not in str(error.message) for error in refused.errors)
    assert read["credentials_by_pk"]["last_refresh_status"] == "failed"
