"""Tests for the integration connection model managers."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib import parse

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test.utils import override_settings
from django.utils import timezone
from rebac import system_context, to_object_ref, to_subject_ref
from rebac.models import active_relationship_model

from angee.integrate.credentials import CredentialKind, StaticTokenCredentialHandler
from angee.integrate.models import AccountStatus
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.errors import TOKEN_EXCHANGE_FAILED, OAuthFlowError
from tests.conftest import Credential, ExternalAccount, OAuthClient, _create_missing_tables


def test_oauth_client_blank_client_id_means_needs_client() -> None:
    """Unconfigured public-provider placeholders validate without a client id."""

    oauth_client = OAuthClient(
        slug="anthropic",
        display_name="Anthropic",
        client_id="",
        is_enabled=True,
    )

    oauth_client.full_clean(validate_unique=False, validate_constraints=False)

    assert oauth_client.configuration_state == "needs_client"


def test_resolve_connect_redirect_picks_auto_or_manual() -> None:
    """A fixed public client redirects back only on localhost; elsewhere it pastes."""

    plain = OAuthClient(slug="google", client_id="g")
    assert plain.resolve_connect_redirect("https://app.example/callback") == (
        "https://app.example/callback",
        "auto",
    )

    manual = "https://platform.claude.com/oauth/code/callback"
    fixed = OAuthClient(
        slug="anthropic",
        client_id="a",
        manual_redirect_uri=manual,
        loopback_redirect_path="/callback",
    )
    # On localhost the proposed path is rewritten to the client's allow-listed loopback
    # path (origin preserved), so the provider can round-trip back in auto mode.
    assert fixed.resolve_connect_redirect("http://localhost:5177/integrate/oauth/callback") == (
        "http://localhost:5177/callback",
        "auto",
    )
    # 127.0.0.1 is not the allow-listed loopback host, and a remote origin can't round-trip
    # the session — both fall back to the manual paste callback.
    assert fixed.resolve_connect_redirect("http://127.0.0.1:5177/callback") == (manual, "manual")
    assert fixed.resolve_connect_redirect("https://console.example/callback") == (manual, "manual")
    # A fixed public client with no declared loopback path cannot round-trip even on
    # localhost — its allow-list registers only the manual paste page.
    no_loopback = OAuthClient(slug="grok", client_id="a", manual_redirect_uri=manual)
    assert no_loopback.resolve_connect_redirect("http://localhost:5177/integrate/oauth/callback") == (
        manual,
        "manual",
    )
    # The loopback origin is rebuilt from validated parts: userinfo is dropped, and a
    # schemeless proposal can't go auto — neither leaks into the provider redirect.
    assert fixed.resolve_connect_redirect("http://evil.test@localhost:5177/x") == (
        "http://localhost:5177/callback",
        "auto",
    )
    assert fixed.resolve_connect_redirect("//localhost:5177/x") == (manual, "manual")


def test_install_fixture_seeds_anthropic_loopback_callback_path() -> None:
    """The seeded loopback path must equal the `/callback` route the frontend mounts.

    The backend stores the path as data and the SPA mounts a static route at the literal;
    they can't share one symbol across the boundary, so pin both ends to the same value
    (here and in the frontend ``index.test.ts``) so a drift fails a test, not a connect.
    """

    import yaml

    import angee.integrate

    fixture = Path(angee.integrate.__file__).parent / "resources/install/010_integrate.oauthclient.yaml"
    entries = {entry["slug"]: entry for entry in yaml.safe_load(fixture.read_text())}
    for slug in ("anthropic-platform", "anthropic-personal"):
        assert entries[slug]["loopback_redirect_path"] == "/callback"


def test_oauth_flow_error_public_message_prefers_safe_provider_body() -> None:
    """The OAuth error object owns the user-facing safe fallback text."""

    provider_error = OAuthFlowError(
        TOKEN_EXCHANGE_FAILED,
        body={"error": {"message": " Rate limited. "}},
    )

    assert provider_error.public_message == "Rate limited."
    assert OAuthFlowError("invalid_state").public_message == "invalid_state"


def test_oauth_protocol_discovers_missing_authorize_endpoint() -> None:
    """A discovery-only OAuth client can start an authorization flow."""

    oauth_client = OAuthClient(
        slug="discovered",
        display_name="Discovered",
        client_id="client-id",
        discovery_url="https://provider.example/.well-known/oauth-authorization-server",
    )
    calls = 0

    def discover_endpoints() -> dict[str, str]:
        nonlocal calls
        calls += 1
        oauth_client.authorize_endpoint = "https://provider.example/oauth/authorize"
        oauth_client.token_endpoint = "https://provider.example/oauth/token"
        return {"authorization_endpoint": oauth_client.authorize_endpoint}

    oauth_client.discover_endpoints = discover_endpoints  # type: ignore[method-assign]

    assert oauth_client.configuration_state == "ready"
    url = OAuthClientProtocol(oauth_client).authorize_url(
        state="state-token",
        redirect_uri="https://app.example/callback",
        scopes=("profile",),
    )

    parsed = parse.urlsplit(url)
    assert calls == 1
    assert parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")) == (
        "https://provider.example/oauth/authorize"
    )
    assert dict(parse.parse_qsl(parsed.query))["client_id"] == "client-id"


def test_oauth_client_claim_accessors_support_dotted_paths() -> None:
    """Provider profile documents can expose identity claims below nested objects."""

    oauth_client = OAuthClient(
        slug="anthropic",
        display_name="Anthropic",
        client_id="anthropic-client",
        external_id_claim="account.uuid",
        email_claim="account.email_address",
        display_name_claim="account.display_name",
        avatar_url_claim="account.avatar_url",
    )
    claims = {
        "account": {
            "uuid": "acct_123",
            "email_address": "claude@example.com",
            "display_name": "Claude User",
            "avatar_url": "https://avatar.example/claude.png",
        },
    }

    assert oauth_client.external_id_from_claims(claims) == "acct_123"
    assert oauth_client.email_from_claims(claims) == "claude@example.com"
    assert oauth_client.display_name_from_claims(claims, "fallback@example.com") == "Claude User"
    assert oauth_client.avatar_url_from_claims(claims) == "https://avatar.example/claude.png"


@pytest.mark.django_db(transaction=True)
def test_connection_managers_are_idempotent_and_delegate_static_token_material() -> None:
    """External account linking and credential upsert are idempotent."""

    created_models = _create_missing_tables()

    try:
        user = get_user_model().objects.create_user(
            username="connection-alice",
            email="alice@example.com",
        )
        other_user = get_user_model().objects.create_user(
            username="connection-bob",
            email="bob@example.com",
        )
        call_command("rebac", "sync", verbosity=0)

        with system_context(reason="test connections"):
            oauth_client = OAuthClient.objects.create(
                slug="example",
                display_name="Example prod",
                client_id="example-client",
                client_secret="secret",
            )

            first_account = ExternalAccount.objects.link(
                oauth_client,
                "ext-123",
                email="alice@example.com",
                display_name="Alice",
                status=AccountStatus.REVOKED,
                identity_claims={"sub": "ext-123"},
                last_error="needs review",
                owner=user,
            )
            second_account = ExternalAccount.objects.link(
                oauth_client,
                "ext-123",
            )

            assert second_account.pk == first_account.pk
            assert ExternalAccount.objects.count() == 1
            second_account.refresh_from_db()
            assert second_account.identity_claims == {"sub": "ext-123"}
            assert second_account.status == AccountStatus.REVOKED
            assert second_account.last_error == "needs review"
            assert _owner_tuple_exists(user, second_account)

            expires_at = timezone.now() + timedelta(hours=1)
            first_credential = Credential.objects.upsert_for_user(
                user,
                oauth_client,
                CredentialKind.STATIC_TOKEN,
                {"api_key": "first-key"},
                external_account=second_account,
                expires_at=expires_at,
            )
            second_credential = Credential.objects.upsert_for_user(
                user,
                oauth_client,
                CredentialKind.STATIC_TOKEN,
                {"api_key": "second-key"},
            )

            assert second_credential.pk == first_credential.pk
            assert Credential.objects.count() == 1
            assert not _owner_tuple_exists(user, second_credential)
            assert Credential.objects.with_actor(user).filter(pk=second_credential.pk).exists()
            assert not Credential.objects.with_actor(other_user).filter(pk=second_credential.pk).exists()

            second_credential.refresh_from_db()
            assert second_credential.external_account_id == second_account.pk
            assert second_credential.expires_at == expires_at
            assert second_credential.reveal() == {"api_key": "second-key"}
            assert isinstance(second_credential.handler, StaticTokenCredentialHandler)
            assert second_credential.auth_headers() == {"Authorization": "Bearer second-key"}

            with pytest.raises(ValueError, match="owned by the manager: kind"):
                Credential.objects.upsert_for_user(
                    user,
                    oauth_client,
                    CredentialKind.STATIC_TOKEN,
                    {"api_key": "third-key"},
                    **{"kind": CredentialKind.OAUTH},
                )
            with pytest.raises(ValueError, match="owned by the manager: material"):
                Credential.objects.upsert_for_user(
                    user,
                    oauth_client,
                    CredentialKind.STATIC_TOKEN,
                    {"api_key": "third-key"},
                    **{"material": {"api_key": "override"}},
                )
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_connection_managers_authorize_their_own_writes() -> None:
    """link()/upsert_for_user() succeed without an ambient system_context."""

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(
            username="connection-bob",
            email="bob@example.com",
        )
        call_command("rebac", "sync", verbosity=0)
        with system_context(reason="test setup"):
            oauth_client = OAuthClient.objects.create(
                slug="selfsuff",
                display_name="SelfSuff prod",
                client_id="selfsuff-client",
                client_secret="secret",
            )

        # No ambient system_context here: the managers authorize their own writes.
        account = ExternalAccount.objects.link(oauth_client, "ext-self", owner=user, email="bob@example.com")
        credential = Credential.objects.upsert_for_user(
            user,
            oauth_client,
            CredentialKind.STATIC_TOKEN,
            {"api_key": "k"},
            external_account=account,
        )

        assert account.pk is not None
        assert credential.pk is not None
        assert _owner_tuple_exists(user, account)
        assert not _owner_tuple_exists(user, credential)
        assert Credential.objects.with_actor(user).filter(pk=credential.pk).exists()
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_upsert_for_user_labels_the_credential_from_provider_and_subject() -> None:
    """A provider credential is named on create from its provider + subject, create-only.

    The relation-picker representation reads the ``name`` column, so an OAuth row (which
    carries no name of its own) needs one to be pickable.
    """

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(username="namer", email="namer@example.com")
        call_command("rebac", "sync", verbosity=0)
        with system_context(reason="test name"):
            oauth_client = OAuthClient.objects.create(
                slug="example",
                display_name="Example prod",
                icon="example.svg",
                client_id="example-client",
            )
            account = ExternalAccount.objects.link(oauth_client, "ext-name", owner=user, email="picker@example.com")
            credential = Credential.objects.upsert_for_user(
                user,
                oauth_client,
                CredentialKind.STATIC_TOKEN,
                {"api_key": "k"},
                external_account=account,
            )
            assert account.provider_slug == "example"
            assert account.provider_environment == "prod"
            assert account.provider_label == "Example prod"
            assert account.provider_icon == "example.svg"
            assert credential.name == "Example prod (picker@example.com)"
            assert credential.display_name == "Example prod (picker@example.com)"

            legacy = Credential(
                oauth_client=oauth_client,
                external_account=account,
                kind=CredentialKind.OAUTH,
                material="{}",
            )
            assert legacy.display_name == "example: ext-name"

            # Create-only: a rename survives a later upsert (token refresh / reconnect).
            credential.name = "Renamed"
            credential.save(update_fields=["name", "updated_at"])
            again = Credential.objects.upsert_for_user(
                user,
                oauth_client,
                CredentialKind.STATIC_TOKEN,
                {"api_key": "k2"},
                external_account=account,
            )
            assert again.pk == credential.pk
            assert again.name == "Renamed"
            assert again.display_name == "Renamed"
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_create_local_credential_needs_no_provider_and_keys_by_name() -> None:
    """A static-token credential is minted with no provider, identified by ``name``."""

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(username="local-alice", email="a@example.com")
        call_command("rebac", "sync", verbosity=0)

        # `create_local_credential` self-authorizes its own write (no ambient context).
        credential = Credential.objects.create_local_credential(
            user,
            kind=CredentialKind.STATIC_TOKEN,
            name="github-pat",
            material={"api_key": "ghp_one"},
        )
        assert credential.oauth_client_id is None
        assert credential.name == "github-pat"
        assert isinstance(credential.handler, StaticTokenCredentialHandler)
        assert credential.auth_headers() == {"Authorization": "Bearer ghp_one"}

        # Idempotent by (user, name): a second mint updates the row in place.
        again = Credential.objects.create_local_credential(
            user,
            kind=CredentialKind.STATIC_TOKEN,
            name="github-pat",
            material={"api_key": "ghp_two"},
        )
        assert again.pk == credential.pk
        assert again.auth_headers() == {"Authorization": "Bearer ghp_two"}
        with system_context(reason="test local credential read"):
            assert Credential.objects.filter(user=user).count() == 1

        # OAuth credentials are minted by the login flow, not here.
        with pytest.raises(ValueError, match="login flow"):
            Credential.objects.create_local_credential(
                user,
                kind=CredentialKind.OAUTH,
                name="oauth-x",
                material={"access_token": "tok"},
            )
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_static_token_credential_allows_an_empty_api_key() -> None:
    """A static-token credential may be minted with no key — a placeholder to fill later.

    Unlike OAuth (whose identity is the token), a static token is optional: an integration
    can be wired before its key is connected. ``secret_value`` is ``""`` so consumers gate
    on a usable secret (e.g. agent provisioning refuses an empty inference credential).
    """

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(username="empty-bob", email="b@example.com")
        call_command("rebac", "sync", verbosity=0)

        credential = Credential.objects.create_local_credential(
            user,
            kind=CredentialKind.STATIC_TOKEN,
            name="anthropic-placeholder",
            material={"api_key": ""},
        )
        assert credential.secret_value() == ""
        with system_context(reason="test empty credential read"):
            assert credential.reveal() == {"api_key": ""}
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_oauth_credential_requires_a_provider_at_the_database() -> None:
    """The check constraint rejects an ``oauth`` credential with no provider."""

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(username="inv-alice", email="i@example.com")
        with system_context(reason="test invariant"), pytest.raises(IntegrityError):
            with transaction.atomic():
                Credential.objects.create(
                    user=user,
                    oauth_client=None,
                    kind=CredentialKind.OAUTH,
                    material="{}",
                    name="",
                )
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
@override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
def test_external_account_owner_lookup_uses_active_relationship_storage() -> None:
    """owner_for() works with REBAC's registry-backed relationship model."""

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(
            username="registry-owner",
            email="registry@example.com",
        )
        call_command("rebac", "sync", verbosity=0)
        with system_context(reason="test setup"):
            oauth_client = OAuthClient.objects.create(
                slug="registry",
                display_name="Registry prod",
                client_id="registry-client",
                client_secret="secret",
            )

        account = ExternalAccount.objects.link(
            oauth_client,
            "registry-sub",
            owner=user,
            email="registry@example.com",
        )

        assert _owner_tuple_exists(user, account)
        assert ExternalAccount.objects.owner_for(account) == user
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_oauth_client_manager_syncs_shape_and_secret_from_settings(settings: Any) -> None:
    """OAuthClient seeds are settings-authored and keep secrets out of resource files."""

    created_models = _create_missing_tables()
    try:
        settings.ANGEE_INTEGRATE_OAUTH_CLIENTS = (
            {
                "slug": "google",
                "environment": "prod",
                "display_name": "Google Login",
                "client_id": "google-client",
                "client_secret": "from-settings",
                "authorize_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_endpoint": "https://oauth2.googleapis.com/token",
                "token_request_format": "json",
                "default_scopes": ["openid", "email"],
            },
        )

        synced = OAuthClient.objects.sync_from_settings()

        assert len(synced) == 1
        with system_context(reason="test assertions"):
            oauth_client = OAuthClient.objects.get(slug="google", environment="prod")
        assert oauth_client.display_name == "Google Login"
        assert oauth_client.client_secret == "from-settings"
        assert oauth_client.default_scopes == ["openid", "email"]
        assert oauth_client.token_request_format == "json"
        # Integrate's settings sync is OAuth-only; the OIDC refinement that marks a
        # login provider is seeded by the iam_integrate_oidc addon, not from here.

        settings.ANGEE_INTEGRATE_OAUTH_CLIENTS = (
            {
                "slug": "google",
                "environment": "prod",
                "display_name": "Google Login Updated",
                "client_id": "google-client-updated",
                "is_enabled": False,
            },
        )

        OAuthClient.objects.sync_from_settings()

        oauth_client.refresh_from_db()
        assert oauth_client.display_name == "Google Login Updated"
        assert oauth_client.client_id == "google-client-updated"
        assert oauth_client.client_secret == "from-settings"
        assert oauth_client.is_enabled is False
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_oauth_clients_command_runs_the_settings_sync(settings: Any) -> None:
    """The ``oauth_clients`` command is a thin trigger for ``sync_from_settings``."""

    created_models = _create_missing_tables()
    try:
        settings.ANGEE_INTEGRATE_OAUTH_CLIENTS = (
            {
                "slug": "github",
                "display_name": "GitHub Login",
                "client_id": "gh-client",
                "client_secret": "gh-secret",
            },
        )

        call_command("oauth_clients", verbosity=0)

        with system_context(reason="test assertions"):
            oauth_client = OAuthClient.objects.get(slug="github", environment="prod")
        assert oauth_client.display_name == "GitHub Login"
        assert oauth_client.client_id == "gh-client"
        assert oauth_client.client_secret == "gh-secret"
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_ensure_fresh_renews_an_expiring_oauth_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ensure_fresh` renews an expiring OAuth token through the provider refresh grant."""

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(username="refresh-alice", email="ra@example.com")
        call_command("rebac", "sync", verbosity=0)
        credential = _expiring_oauth_credential(
            user,
            slug="refreshprov",
            material={"access_token": "old-access", "refresh_token": "old-refresh", "expires_in": 3600},
        )

        def fake_refresh(self: Any, *, refresh_token: str) -> dict[str, Any]:
            assert refresh_token == "old-refresh"
            return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 7200}

        monkeypatch.setattr(OAuthClientProtocol, "refresh_token", fake_refresh)

        with system_context(reason="test refresh run"):
            credential.ensure_fresh()

        assert credential.secret_value() == "new-access"
        assert credential.reveal()["refresh_token"] == "new-refresh"
        assert credential.last_refresh_status == "ok"
        assert credential.expires_at is not None and credential.expires_at > timezone.now()
        reloaded = Credential.objects.sudo(reason="test reload").get(pk=credential.pk)
        assert reloaded.reveal()["access_token"] == "new-access"  # persisted, not just in-memory
    finally:
        _drop_models(created_models)


@pytest.mark.django_db(transaction=True)
def test_ensure_fresh_is_a_noop_for_a_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ensure_fresh` does not call the provider when the token is comfortably in date."""

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(username="refresh-val", email="rv@example.com")
        call_command("rebac", "sync", verbosity=0)
        with system_context(reason="test valid setup"):
            oauth_client = OAuthClient.objects.create(
                slug="validprov",
                display_name="Valid prod",
                client_id="validprov-client",
                token_endpoint="https://idp.example/token",
                supports_refresh=True,
            )
            credential = Credential.objects.upsert_for_user(
                user,
                oauth_client,
                CredentialKind.OAUTH,
                {"access_token": "valid-access", "refresh_token": "r", "expires_in": 3600},
            )

        monkeypatch.setattr(
            OAuthClientProtocol,
            "refresh_token",
            lambda *args, **kwargs: pytest.fail("must not refresh a still-valid token"),
        )
        with system_context(reason="test valid run"):
            credential.ensure_fresh()

        assert credential.secret_value() == "valid-access"
    finally:
        _drop_models(created_models)


@pytest.mark.django_db(transaction=True)
def test_ensure_fresh_records_failure_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider rejecting the refresh is recorded (``last_refresh_status``), not raised."""

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(username="refresh-fail", email="rf@example.com")
        call_command("rebac", "sync", verbosity=0)
        credential = _expiring_oauth_credential(
            user,
            slug="failprov",
            material={"access_token": "stale-access", "refresh_token": "revoked", "expires_in": 3600},
        )

        def boom(self: Any, *, refresh_token: str) -> dict[str, Any]:
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400)

        monkeypatch.setattr(OAuthClientProtocol, "refresh_token", boom)

        with system_context(reason="test fail run"):
            credential.ensure_fresh()  # must not raise

        assert credential.last_refresh_status == "failed"
        assert credential.secret_value() == "stale-access"  # the old token is retained
        reloaded = Credential.objects.sudo(reason="test reload").get(pk=credential.pk)
        assert reloaded.last_refresh_status == "failed"
        assert reloaded.reveal()["access_token"] == "stale-access"
    finally:
        _drop_models(created_models)


@pytest.mark.django_db(transaction=True)
def test_oauth_refresh_without_expires_in_clears_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refresh response with no ``expires_in`` clears ``expires_at`` (no permanent-expired loop)."""

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(username="refresh-noexp", email="rn@example.com")
        call_command("rebac", "sync", verbosity=0)
        credential = _expiring_oauth_credential(
            user,
            slug="noexpprov",
            material={"access_token": "old", "refresh_token": "old-refresh", "expires_in": 3600},
        )
        monkeypatch.setattr(
            OAuthClientProtocol,
            "refresh_token",
            lambda *args, **kwargs: {"access_token": "fresh-access", "refresh_token": "fresh-refresh"},
        )
        with system_context(reason="test noexp run"):
            credential.ensure_fresh()

        assert credential.secret_value() == "fresh-access"
        assert credential.expires_at is None
    finally:
        _drop_models(created_models)


@pytest.mark.django_db(transaction=True)
def test_refresh_now_forces_renewal_of_a_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """`refresh_now` renews even a still-valid token (the explicit, force path)."""

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(username="force-val", email="fv@example.com")
        call_command("rebac", "sync", verbosity=0)
        with system_context(reason="test force-valid setup"):
            oauth_client = OAuthClient.objects.create(
                slug="forcevalid",
                display_name="Force valid",
                client_id="forcevalid-client",
                token_endpoint="https://idp.example/token",
                supports_refresh=True,
            )
            credential = Credential.objects.upsert_for_user(
                user,
                oauth_client,
                CredentialKind.OAUTH,
                {"access_token": "valid-access", "refresh_token": "valid-refresh", "expires_in": 3600},
            )

        def fake_refresh(self: Any, *, refresh_token: str) -> dict[str, Any]:
            assert refresh_token == "valid-refresh"
            return {"access_token": "forced-access", "refresh_token": "forced-refresh", "expires_in": 3600}

        monkeypatch.setattr(OAuthClientProtocol, "refresh_token", fake_refresh)
        with system_context(reason="test force-valid run"):
            credential.refresh_now()

        assert credential.secret_value() == "forced-access"
        assert credential.last_refresh_status == "ok"
    finally:
        _drop_models(created_models)


@pytest.mark.django_db(transaction=True)
def test_refresh_now_records_and_raises_on_provider_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    """`refresh_now` re-raises a rejected grant (unlike the swallowing `ensure_fresh`)."""

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(username="force-fail", email="ff@example.com")
        call_command("rebac", "sync", verbosity=0)
        credential = _expiring_oauth_credential(
            user,
            slug="forcefail",
            material={"access_token": "stale-access", "refresh_token": "revoked", "expires_in": 3600},
        )

        def boom(self: Any, *, refresh_token: str) -> dict[str, Any]:
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400)

        monkeypatch.setattr(OAuthClientProtocol, "refresh_token", boom)
        with system_context(reason="test force-fail run"), pytest.raises(OAuthFlowError):
            credential.refresh_now()

        credential.refresh_from_db()
        assert credential.last_refresh_status == "failed"
        assert credential.secret_value() == "stale-access"  # the old token is retained
    finally:
        _drop_models(created_models)


@pytest.mark.django_db(transaction=True)
def test_refresh_now_raises_when_not_refreshable(monkeypatch: pytest.MonkeyPatch) -> None:
    """`refresh_now` refuses (without calling the provider) when no refresh is possible."""

    created_models = _create_missing_tables()
    try:
        user = get_user_model().objects.create_user(username="force-none", email="fn@example.com")
        call_command("rebac", "sync", verbosity=0)
        with system_context(reason="test force-none setup"):
            oauth_client = OAuthClient.objects.create(
                slug="forcenone",
                display_name="Force none",
                client_id="forcenone-client",
                token_endpoint="https://idp.example/token",
                supports_refresh=False,
            )
            credential = Credential.objects.upsert_for_user(
                user,
                oauth_client,
                CredentialKind.OAUTH,
                {"access_token": "static-access", "refresh_token": "unused", "expires_in": 3600},
            )

        monkeypatch.setattr(
            OAuthClientProtocol,
            "refresh_token",
            lambda *args, **kwargs: pytest.fail("must not call the provider when refresh is unsupported"),
        )
        with system_context(reason="test force-none run"), pytest.raises(ValueError):
            credential.refresh_now()
    finally:
        _drop_models(created_models)


def _expiring_oauth_credential(user: Any, *, slug: str, material: dict[str, Any]) -> Any:
    """Create a refresh-capable OAuth provider + an already-expired credential for ``user``."""

    with system_context(reason="test refresh setup"):
        oauth_client = OAuthClient.objects.create(
            slug=slug,
            display_name=f"{slug} prod",
            client_id=f"{slug}-client",
            token_endpoint="https://idp.example/token",
            supports_refresh=True,
        )
        credential = Credential.objects.upsert_for_user(user, oauth_client, CredentialKind.OAUTH, material)
        Credential.objects.sudo(reason="test expire").filter(pk=credential.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        credential.refresh_from_db()
    return credential


def _drop_models(created_models: list[Any]) -> None:
    """Drop the per-test concrete tables created by ``_create_missing_tables``."""

    if created_models:
        with connection.schema_editor() as schema_editor:
            for model in reversed(created_models):
                schema_editor.delete_model(model)


def _owner_tuple_exists(owner: Any, resource: Any) -> bool:
    """Return whether ``owner`` has the stored owner relation on ``resource``."""

    owner_ref = to_subject_ref(owner)
    resource_ref = to_object_ref(resource)
    return (
        active_relationship_model()
        .objects.filter(
            resource_type=resource_ref.resource_type,
            resource_id=resource_ref.resource_id,
            relation="owner",
            subject_type=owner_ref.subject_type,
            subject_id=owner_ref.subject_id,
            optional_subject_relation=owner_ref.optional_relation,
        )
        .exists()
    )
