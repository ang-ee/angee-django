"""The JWT bearer credential kind: a provider signing key exchanged per scope set (RFC 7523 §2.1)."""

from __future__ import annotations

import importlib
import json
import multiprocessing
import threading
import time
from collections.abc import Callable
from contextvars import copy_context
from dataclasses import dataclass, field
from datetime import timedelta
from functools import cache
from typing import Any

import httpx2
import pytest
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.utils import timezone
from joserfc import jwt
from joserfc.jwk import ECKey, RSAKey
from rebac import system_context

from angee.base.serialization import MAX_JSON_DOCUMENT_DEPTH
from angee.graphql.testing.documents import execute_schema, result_data
from angee.integrate.credentials import CredentialKind
from angee.integrate.errors import IntegrationError
from angee.integrate.models import CredentialStatus
from angee.integrate.net import AddressNotAllowedError
from angee.integrate.oauth.errors import ENDPOINT_NOT_ALLOWED, OAuthFlowError
from angee.integrate.testing.http import mock_transport, request_fields
from tests.conftest import Credential, VcsBridge, Vendor, addon_schema, create_platform_admin, create_user

iam_schema = importlib.import_module("angee.iam.schema")
integrate_schema = importlib.import_module("angee.integrate.schema")

TOKEN_ENDPOINT = "https://auth.provider.test/token"
ISSUER = "room-writer@robots.provider.test"
KEY_ID = "4f1c-key-id"
JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
WRITE = "calendar.write"
READ = "calendar.read"


@cache
def signing_key() -> RSAKey:
    """Return one process-wide RSA key, so key generation runs once per test session."""

    return RSAKey.generate_key(2048)


def private_pem() -> str:
    return signing_key().as_pem(private=True).decode()


def neutral_key_file(**overrides: Any) -> str:
    """Return a key file in the provider-neutral vocabulary."""

    document = {"issuer": ISSUER, "private_key": private_pem(), "key_id": KEY_ID, "token_endpoint": TOKEN_ENDPOINT}
    return json.dumps({key: value for key, value in {**document, **overrides}.items() if value is not None})


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
    }
    return json.dumps({key: value for key, value in {**document, **overrides}.items() if value is not None})


def create_jwt_bearer(name: str = "room-writer", key_file: str | None = None, *, user: Any = None) -> Any:
    """Create, or re-enter, one JWT bearer credential through the manager's local-credential verb."""

    with system_context(reason="test jwt bearer create"):
        return Credential.objects.create_local_credential(
            user or create_user(f"{name}-owner"),
            kind=CredentialKind.JWT_BEARER,
            name=name,
            material={"key_file": key_file or neutral_key_file()},
        )


def set_status(credential: Any, status: str) -> None:
    """Move one credential row to ``status`` through its own save."""

    with system_context(reason="test jwt bearer status"):
        row = Credential.objects.sudo(reason="test jwt bearer status load").get(pk=credential.pk)
        row.status = status
        row.save(update_fields=["status", "updated_at"])


def store_unloadable_key(credential: Any) -> None:
    """Hold a private key that no longer loads, as a damaged row would; no credential verb stores one."""

    with system_context(reason="test jwt bearer damaged key"):
        row = Credential.objects.sudo(reason="test jwt bearer damaged key load").get(pk=credential.pk)
        row.material = row.encode_material({**row.reveal(), "private_key": "no longer a key"})
        row.save(update_fields=["material", "updated_at"])
    credential.refresh_from_db()


@dataclass
class TokenEndpoint:
    """A JWT bearer token endpoint that verifies each assertion and answers with ``status``.

    A non-200 status, or an explicit ``refusal`` body (even on a 2xx), answers with that
    JSON body, ``{"error": "invalid_grant"}`` by default; otherwise it grants a token.
    """

    status: int = 200
    expires_in: int | None = 3600
    granted_scope: str | None = None
    delay: float = 0.0
    refusal: dict[str, Any] | None = None
    on_grant: Callable[[], None] | None = None
    grants: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        fields = request_fields(request)
        token = jwt.decode(fields["assertion"], signing_key(), algorithms=["RS256"])
        self.grants.append({"url": str(request.url), "fields": fields, "header": token.header, "claims": token.claims})
        time.sleep(self.delay)
        if self.on_grant is not None:
            self.on_grant()
        if self.refusal is not None or self.status != 200:
            refusal = {"error": "invalid_grant"} if self.refusal is None else self.refusal
            return httpx2.Response(self.status, json=refusal)
        body: dict[str, Any] = {"access_token": f"access-{len(self.grants)}", "token_type": "Bearer"}
        if self.expires_in is not None:
            body["expires_in"] = self.expires_in
        if self.granted_scope is not None:
            body["scope"] = self.granted_scope
        return httpx2.Response(200, json=body)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Hold ``timezone.now`` at ``clock[0]``; a test advances it by assignment."""

    now = [timezone.now()]
    monkeypatch.setattr(timezone, "now", lambda: now[0])
    return now


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("key_file", "placement"),
    [
        pytest.param(neutral_key_file(subject="room-owner@example.test", audience="https://api.provider.test/"),
                     "parameter", id="neutral"),
        pytest.param(google_key_file(), "claim", id="google-service-account"),
    ],
)
def test_a_key_file_is_stored_as_provider_neutral_signing_material(key_file: str, placement: str) -> None:
    """Each vocabulary lands in the neutral names; the rest of a key file is dropped; no secret is generic."""

    credential = create_jwt_bearer(key_file=key_file)
    document = json.loads(key_file)

    assert credential.reveal() == {
        "issuer": ISSUER,
        "private_key": private_pem().strip(),
        "key_id": KEY_ID,
        "token_endpoint": TOKEN_ENDPOINT,
        "subject": document.get("subject", ""),
        "audience": document.get("audience", ""),
        "scope_placement": placement,
    }
    assert credential.secret_value() == ""
    assert not credential.is_reconnect_required


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("key_file", "message"),
    [
        pytest.param("", "This field is required.", id="missing"),
        pytest.param("-----BEGIN PRIVATE KEY-----", "Paste the provider's JSON key file.", id="not-json"),
        pytest.param("[]", "Paste the provider's JSON key file.", id="not-an-object"),
        pytest.param('{"type": ' + "[" * MAX_JSON_DOCUMENT_DEPTH + "0" + "]" * MAX_JSON_DOCUMENT_DEPTH + "}",
                     "Paste the provider's JSON key file.", id="nested-past-the-document-bound"),
        pytest.param(neutral_key_file(issuer=None), "The key file has no issuer.", id="neutral-no-issuer"),
        pytest.param(google_key_file(client_email=None), "The key file has no client_email.", id="google-no-issuer"),
        pytest.param(neutral_key_file(private_key="-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----"),
                     "The key file's private_key is not a PEM RSA private key.", id="malformed-pem"),
        pytest.param(neutral_key_file(private_key=ECKey.generate_key("P-256").as_pem(private=True).decode()),
                     "The key file's private_key is not a PEM RSA private key.", id="not-rsa"),
        pytest.param(neutral_key_file(private_key=signing_key().as_pem(private=False).decode()),
                     "The key file's private_key is a public key.", id="public-key"),
        pytest.param(neutral_key_file(token_endpoint="http://auth.provider.test/token"),
                     "The key file's token_endpoint must be an https URL with a host.", id="plain-http"),
        pytest.param(google_key_file(token_uri="https:///token"),
                     "The key file's token_uri must be an https URL with a host.", id="no-host"),
    ],
)
def test_an_unusable_key_file_is_refused_on_save(key_file: str, message: str) -> None:
    """Every refusal names the key-file input in its own vocabulary, echoes none of its values, and stores nothing."""

    user = create_user("jwt-bearer-refused")
    with system_context(reason="test jwt bearer refusal"), pytest.raises(ValidationError) as refusal:
        Credential.objects.create_local_credential(
            user, kind=CredentialKind.JWT_BEARER, name="refused", material={"key_file": key_file}
        )

    assert refusal.value.message_dict == {"key_file": [message]}
    with system_context(reason="test jwt bearer refusal stored nothing"):
        assert not Credential.objects.filter(user=user).exists()


@pytest.mark.django_db(transaction=True)
def test_a_google_key_file_puts_the_scope_in_the_assertion_claims() -> None:
    """Google reads the scope from the assertion, so the request carries only the grant and the assertion."""

    credential = create_jwt_bearer(key_file=google_key_file())
    scopes = ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.readonly"]
    endpoint = TokenEndpoint()

    with mock_transport(endpoint) as requests:
        before = int(time.time())
        assert credential.auth_headers(scopes=scopes) == {"Authorization": "Bearer access-1"}

    assert requests[0].extensions["allow_private"] is False
    grant = endpoint.grants[0]
    assert grant["url"] == TOKEN_ENDPOINT
    assert grant["fields"] == {"grant_type": JWT_BEARER_GRANT, "assertion": grant["fields"]["assertion"]}
    assert grant["header"] == {"typ": "JWT", "alg": "RS256", "kid": KEY_ID}
    claims = grant["claims"]
    assert (claims["iss"], claims["aud"], claims["scope"]) == (ISSUER, TOKEN_ENDPOINT, " ".join(sorted(scopes)))
    assert "sub" not in claims
    assert before <= claims["iat"] <= int(time.time())
    assert claims["exp"] == claims["iat"] + 3600


@pytest.mark.django_db(transaction=True)
def test_a_neutral_key_file_sends_the_scope_as_a_request_parameter_and_caches_per_scope_set() -> None:
    """One grant per scope set; order and repeats name the same set; the granted scope is kept."""

    credential = create_jwt_bearer(
        key_file=neutral_key_file(subject="room-owner@example.test", audience="https://api.provider.test/")
    )
    endpoint = TokenEndpoint(granted_scope=f"{READ} {WRITE} calendar.free_busy")

    with mock_transport(endpoint) as requests:
        assert credential.scoped_access_token([WRITE, READ]) == "access-1"
        assert credential.scoped_access_token((READ, WRITE, READ)) == "access-1"
        assert credential.scoped_access_token([f" {WRITE}  {READ} "]) == "access-1"  # elements split on whitespace
        assert credential.auth_headers(scopes={WRITE}) == {"Authorization": "Bearer access-2"}
        assert credential.scoped_access_token([WRITE]) == "access-2"

    assert len(requests) == 2
    first = endpoint.grants[0]
    assert first["fields"]["scope"] == f"{READ} {WRITE}"
    assert "scope" not in first["claims"]
    assert (first["claims"]["sub"], first["claims"]["aud"]) == ("room-owner@example.test", "https://api.provider.test/")
    assert first["claims"]["jti"] != endpoint.grants[1]["claims"]["jti"]
    reloaded = Credential.objects.sudo(reason="test jwt bearer mint persisted").get(pk=credential.pk)
    cached = reloaded.reveal()["scoped_tokens"]
    assert set(cached) == {f"{READ} {WRITE}", WRITE}
    assert cached[f"{READ} {WRITE}"]["scope"] == f"{READ} {WRITE} calendar.free_busy"
    assert reloaded.last_refresh_status == "ok" and reloaded.last_refresh_at is not None
    assert reloaded.expires_at is None  # the credential itself never expires; its tokens do


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("expires_in", "renews_after"),
    [
        pytest.param(3600, 3300, id="long-lifetime-renews-five-minutes-early"),
        pytest.param(60, 30, id="short-lifetime-renews-at-half-life"),
        pytest.param(None, 150, id="missing-expires-in-assumes-five-minutes"),
        pytest.param(0, 150, id="non-positive-expires-in-assumes-five-minutes"),
    ],
)
def test_a_token_renews_at_the_earlier_of_five_minutes_and_half_its_lifetime(
    clock: list[Any], expires_in: int | None, renews_after: int,
) -> None:
    """A cached token is reused until its renewal time and minted again after it."""

    credential = create_jwt_bearer()
    started = clock[0]

    with mock_transport(TokenEndpoint(expires_in=expires_in)) as requests:
        assert credential.scoped_access_token([WRITE]) == "access-1"
        clock[0] = started + timedelta(seconds=renews_after - 1)
        assert credential.scoped_access_token([WRITE]) == "access-1"
        clock[0] = started + timedelta(seconds=renews_after + 1)
        assert credential.scoped_access_token([WRITE]) == "access-2"

    assert len(requests) == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("second", [WRITE, READ], ids=["same-scope-set", "different-scope-sets"])
def test_concurrent_callers_mint_once_per_scope_set(second: str) -> None:
    """Callers serialize on the credential's renewal lock; a same-set waiter adopts the persisted token."""

    credential = create_jwt_bearer()
    endpoint = TokenEndpoint(delay=0.3)
    start = threading.Barrier(2)
    tokens: dict[str, str] = {}
    failures: list[BaseException] = []

    def consume(scope: str, key: str) -> None:
        try:
            with system_context(reason="test jwt bearer race consumer"):
                row = Credential.objects.sudo(reason="test jwt bearer race load").get(pk=credential.pk)
                start.wait(timeout=10)
                tokens[key] = row.scoped_access_token([scope])
        except BaseException as error:  # noqa: BLE001 - the test thread reports any failure below
            failures.append(error)
        finally:
            connections.close_all()

    with mock_transport(endpoint) as requests:
        threads = [
            threading.Thread(target=copy_context().run, args=(consume, scope, key))
            for scope, key in ((WRITE, "first"), (second, "second"))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

    assert not failures
    reloaded = Credential.objects.sudo(reason="test jwt bearer race verify").get(pk=credential.pk)
    cached = {scope: entry["access_token"] for scope, entry in reloaded.reveal()["scoped_tokens"].items()}
    if second == WRITE:
        assert len(requests) == 1
        assert tokens == {"first": "access-1", "second": "access-1"}
    else:
        assert len(requests) == 2
        assert sorted(tokens.values()) == ["access-1", "access-2"]
        assert cached == {WRITE: tokens["first"], READ: tokens["second"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("status", "refusal", "reported", "transient", "recorded"),
    [
        pytest.param(400, {"error": "invalid_grant"}, 400, False, True, id="refused-grant"),
        pytest.param(401, {"error": "invalid_client"}, 401, False, True, id="refused-client"),
        pytest.param(400, {"error": "unauthorized_client"}, 400, False, True, id="unauthorized-client"),
        pytest.param(400, {"error": "invalid_scope"}, 400, False, False, id="refused-scope-is-not-the-key"),
        pytest.param(429, {"error": "rate_limit_exceeded"}, 429, True, False, id="json-rate-limit"),
        pytest.param(429, {}, 429, True, False, id="json-rate-limit-without-error"),
        pytest.param(200, {"error": "invalid_grant"}, 400, False, True, id="2xx-carrying-error"),
        pytest.param(400, {"error": "temporarily_unavailable"}, 503, True, False, id="temporarily-unavailable"),
        pytest.param(503, {"error": "server_error"}, 503, True, False, id="server-error"),
    ],
)
def test_only_key_or_identity_refusals_require_reconnect(
    status: int, refusal: dict[str, Any], reported: int, transient: bool, recorded: bool,
) -> None:
    """Every refusal is raised with the response's real error status; only a key or identity refusal is recorded."""

    credential = create_jwt_bearer()
    endpoint = TokenEndpoint(status=status, refusal=refusal)

    with mock_transport(endpoint), pytest.raises(OAuthFlowError) as raised:
        credential.scoped_access_token([WRITE])

    assert raised.value.http_status == reported
    assert raised.value.transient is transient
    assert credential.is_reconnect_required is recorded
    assert credential.last_refresh_status == ("failed" if recorded else "")


@pytest.mark.django_db(transaction=True)
def test_a_token_endpoint_the_address_policy_refuses_is_a_recorded_refusal() -> None:
    """No retry reaches an endpoint the pinned transport refuses, so the key file must be re-entered."""

    credential = create_jwt_bearer()

    def refuse_address(request: httpx2.Request) -> httpx2.Response:
        raise AddressNotAllowedError()

    with mock_transport(refuse_address), pytest.raises(OAuthFlowError) as raised:
        credential.scoped_access_token([WRITE])

    assert (raised.value.http_status, raised.value.transient) == (400, False)
    assert raised.value.body == {"error": ENDPOINT_NOT_ALLOWED}
    assert credential.is_reconnect_required


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("path", ["replace_material", "create_local_credential"])
def test_re_entering_the_key_file_clears_the_failure_and_the_cached_tokens(path: str) -> None:
    """A refused credential is healthy again once its key file is re-entered, on either re-entry path."""

    owner = create_user(f"re-entry-{path}")
    credential = create_jwt_bearer(user=owner)
    with mock_transport(TokenEndpoint()):
        assert credential.scoped_access_token([READ]) == "access-1"
    with mock_transport(TokenEndpoint(status=400)), pytest.raises(OAuthFlowError):
        credential.scoped_access_token([WRITE])
    assert credential.is_reconnect_required

    rotated = neutral_key_file(key_id="rotated-key")
    if path == "replace_material":
        credential.replace_material({"key_file": rotated})
    else:
        create_jwt_bearer(key_file=rotated, user=owner)
        credential.refresh_from_db()

    assert not credential.is_reconnect_required
    assert (credential.last_refresh_status, credential.last_refresh_at) == ("", None)
    assert "scoped_tokens" not in credential.reveal()
    endpoint = TokenEndpoint()
    with mock_transport(endpoint):
        assert credential.scoped_access_token([READ]) == "access-1"
    assert endpoint.grants[0]["header"]["kid"] == "rotated-key"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("edit", "outcome"),
    [
        pytest.param("key_file", "access-1", id="key-edit-returns-the-token-uncached"),
        pytest.param("deactivate", None, id="deactivation-refuses-the-token"),
    ],
)
def test_a_fence_mismatch_during_a_mint_keeps_the_token_out_of_the_cache(edit: str, outcome: str | None) -> None:
    """A row edited while its token was minted wins: the token is never cached, and is refused if inactive."""

    credential = create_jwt_bearer()

    def edit_row() -> None:
        row = Credential.objects.sudo(reason="test jwt bearer fence edit").get(pk=credential.pk)
        if edit == "key_file":
            row.replace_material({"key_file": neutral_key_file(key_id="edited-key")})
        else:
            set_status(row, CredentialStatus.REVOKED)

    with mock_transport(TokenEndpoint(on_grant=edit_row)):
        if outcome is None:
            with pytest.raises(ValueError, match="not active"):
                credential.scoped_access_token([WRITE])
        else:
            assert credential.scoped_access_token([WRITE]) == outcome

    assert "scoped_tokens" not in credential.reveal()
    assert credential.last_refresh_status == ""


@pytest.mark.django_db(transaction=True)
def test_an_inactive_credential_mints_nothing() -> None:
    credential = create_jwt_bearer()
    set_status(credential, CredentialStatus.REVOKED)

    with mock_transport(lambda request: pytest.fail("an inactive credential must not mint")):
        with pytest.raises(ValueError, match="not active"):
            credential.scoped_access_token([WRITE])


@pytest.mark.django_db(transaction=True)
def test_a_revoked_credential_serves_no_cached_token() -> None:
    """Fail closed: revoking a credential refuses its cached token while that token is still live."""

    credential = create_jwt_bearer()
    with mock_transport(TokenEndpoint()):
        assert credential.scoped_access_token([WRITE]) == "access-1"

    set_status(credential, CredentialStatus.REVOKED)
    credential.refresh_from_db()  # a consumer reloads the row, as Integration.fresh_credential() does
    entry = credential.reveal()["scoped_tokens"][WRITE]
    assert entry["renew_at"] > timezone.now().timestamp()  # the cached token is still within its lifetime

    with mock_transport(lambda request: pytest.fail("a revoked credential must not mint")):
        with pytest.raises(ValueError, match="This credential is not active, so it serves no tokens."):
            credential.scoped_access_token([WRITE])
        with pytest.raises(ValueError, match="not active"):
            credential.auth_headers(scopes=[WRITE])


@pytest.mark.django_db(transaction=True)
def test_scoped_tokens_refuse_bad_calls_before_any_request() -> None:
    """Atomic blocks, bare strings, empty scope sets and kinds that do not mint are refused up front."""

    credential = create_jwt_bearer()
    with system_context(reason="test jwt bearer static"):
        static = Credential.objects.create_local_credential(
            create_user("static-scopes"), kind=CredentialKind.STATIC_TOKEN, name="static", material={"api_key": "k"}
        )
    with mock_transport(TokenEndpoint()):
        assert credential.scoped_access_token([WRITE]) == "access-1"

    with mock_transport(lambda request: pytest.fail("no request for a refused call")):
        with transaction.atomic(), pytest.raises(RuntimeError, match="autocommit"):
            credential.scoped_access_token([WRITE])  # refused even though the token is cached
        with pytest.raises(TypeError):
            credential.scoped_access_token(WRITE)
        with pytest.raises(ValueError, match="at least one scope"):
            credential.auth_headers()
        with pytest.raises(ValueError, match="do not mint"):
            static.scoped_access_token([WRITE])
    assert static.auth_headers(scopes=[WRITE]) == {"Authorization": "Bearer k"}


@pytest.mark.django_db
def test_the_connection_test_signs_locally_without_a_request() -> None:
    """Test connection proves the key loads and signs; a key that does not load is not usable."""

    owner = create_user("jwt-probe-owner")
    credential = create_jwt_bearer(user=owner)
    with system_context(reason="test jwt bearer probe setup"):
        vendor = Vendor.objects.create(slug="jwt-probe", display_name="JWT probe")
        integration = VcsBridge.objects.create(
            owner=owner, vendor=vendor, credential=credential, backend_class="local", lifecycle="connected",
        )

    with (
        mock_transport(lambda request: pytest.fail("the probe must not call the token endpoint")),
        system_context(reason="test jwt bearer probe"),
    ):
        assert integration.probe_credential() == "Credential is usable."
        store_unloadable_key(credential)
        integration.refresh_from_db()
        with pytest.raises(IntegrationError) as refused:
            integration.probe_credential()
    assert refused.value.public_message == "Credential is not usable."


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("state", ["refused", "revoked"])
def test_the_connection_test_fails_closed_for_a_refused_or_inactive_credential(state: str) -> None:
    """A key that still signs locally is not usable once the provider refused it or it was revoked."""

    owner = create_user(f"jwt-probe-{state}")
    credential = create_jwt_bearer(user=owner)
    with system_context(reason="test jwt bearer probe setup"):
        vendor = Vendor.objects.create(slug=f"jwt-probe-{state}", display_name="JWT probe")
        integration = VcsBridge.objects.create(
            owner=owner, vendor=vendor, credential=credential, backend_class="local", lifecycle="connected",
        )
    if state == "refused":
        with mock_transport(TokenEndpoint(status=400)), pytest.raises(OAuthFlowError):
            credential.scoped_access_token([WRITE])
    else:
        set_status(credential, CredentialStatus.REVOKED)

    with system_context(reason="test jwt bearer probe"), pytest.raises(IntegrationError) as refused:
        integration.refresh_from_db()
        integration.probe_credential()
    assert refused.value.public_message == "Reconnect this account."


@pytest.mark.django_db(transaction=True)
def test_update_material_refuses_what_the_key_file_became_and_merges_transient_material() -> None:
    """The stored signing fields are the kind's own secret: re-entered through replace_material, never merged."""

    credential = create_jwt_bearer()
    signing = credential.reveal()
    for signing_field in ("private_key", "issuer"):
        with pytest.raises(ValueError, match="replace_material"):
            credential.update_material(**{signing_field: "another"})
    credential.update_material(password="transient-answer")
    credential.refresh_from_db()
    assert credential.reveal() == {**signing, "password": "transient-answer"}


@pytest.mark.django_db(transaction=True)
def test_a_key_that_does_not_load_is_a_recorded_refusal_before_any_request() -> None:
    """An unloadable stored key condemns the key file: refused permanently, recorded, never sent."""

    credential = create_jwt_bearer()
    store_unloadable_key(credential)

    with mock_transport(lambda request: pytest.fail("an unloadable key must not reach the endpoint")):
        with pytest.raises(OAuthFlowError) as raised:
            credential.scoped_access_token([WRITE])

    assert not raised.value.transient
    assert raised.value.body == {"error": "invalid_signing_key"}
    assert credential.last_refresh_status == "failed" and credential.is_reconnect_required


@pytest.mark.django_db(transaction=True)
def test_key_material_and_tokens_stay_out_of_logs_repr_and_graphql(caplog: pytest.LogCaptureFixture) -> None:
    """The console creates the credential from a pasted key file and projects or reveals no secret."""

    admin = create_platform_admin("jwt-bearer-admin")
    schema = addon_schema(integrate_schema.schemas, "console", iam_schema.schemas)
    create = """
        mutation CreateCredential($data: CredentialInput!) {
            create_credential(data: $data) { id kind name status last_refresh_status display_name }
        }
    """
    bad_key_file = neutral_key_file(token_endpoint="")
    refused = execute_schema(
        schema, create, {"data": {"name": "bad", "kind": "jwt_bearer", "key_file": bad_key_file}}, user=admin,
    )
    assert refused.errors is not None
    assert set(refused.errors[0].extensions["validationErrors"]) == {"key_file"}
    created = result_data(
        execute_schema(
            schema, create, {"data": {"name": "room-writer", "kind": "jwt_bearer", "key_file": neutral_key_file()}},
            user=admin,
        )
    )["create_credential"]
    with system_context(reason="test jwt bearer graphql load"):
        credential = Credential.objects.get(sqid=created["id"])

    with mock_transport(TokenEndpoint()):
        token = credential.scoped_access_token([WRITE])
    with mock_transport(TokenEndpoint(status=400)), pytest.raises(OAuthFlowError):
        credential.scoped_access_token([READ])
    read = result_data(
        execute_schema(
            schema,
            "query($id: String!) { credentials_by_pk(id: $id) { kind name status last_refresh_status display_name } }",
            {"id": created["id"]},
            user=admin,
        )
    )
    revealed = result_data(
        execute_schema(
            schema, "mutation($id: ID!) { reveal_credential(id: $id) { secret } }", {"id": created["id"]}, user=admin,
        )
    )

    assert revealed == {"reveal_credential": {"secret": ""}}
    secrets = [token, private_pem().strip().splitlines()[1], KEY_ID]
    projected = json.dumps([created, read, revealed]) + repr(credential) + str(credential)
    assert "JWT bearer token request failed" in caplog.text
    for secret in secrets:
        assert secret not in projected
        assert secret not in caplog.text
        assert all(secret not in str(error.message) for error in refused.errors)
    assert read["credentials_by_pk"]["last_refresh_status"] == "failed"


def _mint_in_process(credential_pk: int, scope: str, start: Any, results: Any) -> None:
    """Race one real mint from a separate process against the shared token endpoint queue."""

    import django

    django.setup()

    def grant(request: httpx2.Request) -> httpx2.Response:
        results.put(("grant", request_fields(request)["scope"]))
        time.sleep(0.3)
        return httpx2.Response(200, json={"access_token": f"access-{scope}-{multiprocessing.current_process().pid}",
                                          "expires_in": 3600})

    try:
        with system_context(reason="test cross-process jwt bearer mint"), mock_transport(grant):
            credential = Credential.objects.sudo(reason="test cross-process jwt bearer load").get(pk=credential_pk)
            if not start.wait(timeout=10):
                raise TimeoutError("the mint race did not start")
            results.put(("result", scope, credential.scoped_access_token([scope])))
    finally:
        connections.close_all()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL advisory-lock race across processes")
@pytest.mark.parametrize("second", [WRITE, READ], ids=["same-scope-set", "different-scope-sets"])
def test_concurrent_mints_serialize_across_processes_on_postgresql(second: str) -> None:
    """Two worker processes mint once per scope set, and same-set callers share one token."""

    processes: list[multiprocessing.Process] = []
    results: Any | None = None
    try:
        credential = create_jwt_bearer()
        connections.close_all()
        context = multiprocessing.get_context("fork")
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(target=_mint_in_process, args=(credential.pk, scope, start, results))
            for scope in (WRITE, second)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode == 0

        events = [results.get(timeout=5) for _ in range(3 if second == WRITE else 4)]
        grants = [event for event in events if event[0] == "grant"]
        minted = {event[2] for event in events if event[0] == "result"}
        assert len(grants) == (1 if second == WRITE else 2)
        assert len(minted) == len(grants)
        reloaded = Credential.objects.sudo(reason="test cross-process jwt bearer verify").get(pk=credential.pk)
        assert set(reloaded.reveal()["scoped_tokens"]) == {WRITE, second}
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        if results is not None:
            results.close()
            results.join_thread()
