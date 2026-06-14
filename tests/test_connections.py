"""Tests for IAM connection model managers."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test.utils import override_settings
from django.utils import timezone
from rebac import system_context, to_object_ref, to_subject_ref
from rebac.models import active_relationship_model

from angee.iam.credentials import CredentialKind, StaticTokenCredentialHandler
from angee.iam.models import AccountStatus
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
    fixed = OAuthClient(slug="anthropic", client_id="a", manual_redirect_uri=manual)
    assert fixed.resolve_connect_redirect("http://localhost:5177/callback") == (
        "http://localhost:5177/callback",
        "auto",
    )
    # 127.0.0.1 is not the allow-listed loopback host, and a remote origin can't round-trip
    # the session — both fall back to the manual paste callback.
    assert fixed.resolve_connect_redirect("http://127.0.0.1:5177/callback") == (manual, "manual")
    assert fixed.resolve_connect_redirect("https://console.example/callback") == (manual, "manual")


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
        account = ExternalAccount.objects.link(
            oauth_client, "ext-self", owner=user, email="bob@example.com"
        )
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
                slug="example", display_name="Example prod", client_id="example-client"
            )
            account = ExternalAccount.objects.link(
                oauth_client, "ext-name", owner=user, email="picker@example.com"
            )
            credential = Credential.objects.upsert_for_user(
                user,
                oauth_client,
                CredentialKind.STATIC_TOKEN,
                {"api_key": "k"},
                external_account=account,
            )
            assert credential.name == "Example prod (picker@example.com)"

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
        settings.ANGEE_IAM_OAUTH_CLIENTS = (
            {
                "slug": "google",
                "environment": "prod",
                "display_name": "Google Login",
                "client_id": "google-client",
                "client_secret": "from-settings",
                "issuer": "https://accounts.google.com",
                "authorize_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_endpoint": "https://oauth2.googleapis.com/token",
                "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
                "token_request_format": "json",
                "is_oidc": True,
                "default_scopes": ["openid", "email"],
                "allowed_email_domains": ["example.com"],
            },
        )

        synced = OAuthClient.objects.sync_from_settings()

        assert len(synced) == 1
        with system_context(reason="test assertions"):
            oauth_client = OAuthClient.objects.get(slug="google", environment="prod")
        assert oauth_client.display_name == "Google Login"
        assert oauth_client.client_secret == "from-settings"
        assert oauth_client.is_oidc is True
        assert oauth_client.default_scopes == ["openid", "email"]
        assert oauth_client.token_request_format == "json"

        settings.ANGEE_IAM_OAUTH_CLIENTS = (
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
        settings.ANGEE_IAM_OAUTH_CLIENTS = (
            {
                "slug": "github",
                "display_name": "GitHub Login",
                "client_id": "gh-client",
                "client_secret": "gh-secret",
                "is_oidc": True,
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
