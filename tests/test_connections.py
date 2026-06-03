"""Tests for IAM connection model managers."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.utils import timezone
from rebac import system_context, to_object_ref, to_subject_ref
from rebac.models import active_relationship_model

from angee.iam.credentials import CredentialKind, StaticTokenCredentialHandler
from angee.iam.models import AccountStatus
from tests.conftest import Credential, ExternalAccount, OAuthClient, Vendor, _create_missing_tables


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
            vendor = Vendor.objects.create(
                slug="example",
                display_name="Example",
                website_url="https://example.com",
            )
            oauth_client = OAuthClient.objects.create(
                vendor=vendor,
                display_name="Example prod",
                client_id="example-client",
                client_secret="secret",
            )

            first_account = ExternalAccount.objects.link(
                vendor,
                "ext-123",
                email="alice@example.com",
                display_name="Alice",
                status=AccountStatus.ERROR,
                identity_claims={"sub": "ext-123"},
                last_error="needs review",
                owner=user,
            )
            second_account = ExternalAccount.objects.link(
                vendor,
                "ext-123",
            )

            assert second_account.pk == first_account.pk
            assert ExternalAccount.objects.count() == 1
            second_account.refresh_from_db()
            assert second_account.identity_claims == {"sub": "ext-123"}
            assert second_account.status == AccountStatus.ERROR
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
            assert _owner_tuple_exists(user, second_credential)
            assert Credential.objects.with_actor(user).filter(pk=second_credential.pk).exists()
            assert not Credential.objects.with_actor(other_user).filter(pk=second_credential.pk).exists()

            second_credential.refresh_from_db()
            assert second_credential.external_account_id == second_account.pk
            assert second_credential.expires_at == expires_at
            assert second_credential.reveal() == {"api_key": "second-key"}
            assert isinstance(second_credential.handler, StaticTokenCredentialHandler)
            assert second_credential.auth_headers() == {"Authorization": "Bearer second-key"}

            with pytest.raises(ValueError, match="owned by upsert_for_user: kind"):
                Credential.objects.upsert_for_user(
                    user,
                    oauth_client,
                    CredentialKind.STATIC_TOKEN,
                    {"api_key": "third-key"},
                    **{"kind": CredentialKind.OAUTH},
                )
            with pytest.raises(ValueError, match="owned by upsert_for_user: material"):
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
        with system_context(reason="test setup"):
            vendor = Vendor.objects.create(slug="selfsuff", display_name="SelfSuff")
            oauth_client = OAuthClient.objects.create(
                vendor=vendor,
                display_name="SelfSuff prod",
                client_id="selfsuff-client",
                client_secret="secret",
            )

        # No ambient system_context here: the managers authorize their own writes.
        account = ExternalAccount.objects.link(
            vendor, "ext-self", owner=user, email="bob@example.com"
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
        assert _owner_tuple_exists(user, credential)
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
        with system_context(reason="test setup"):
            Vendor.objects.create(slug="google", display_name="Google")

        settings.ANGEE_IAM_OAUTH_CLIENTS = (
            {
                "vendor": "google",
                "environment": "prod",
                "display_name": "Google Login",
                "client_id": "google-client",
                "client_secret": "from-settings",
                "issuer": "https://accounts.google.com",
                "authorize_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_endpoint": "https://oauth2.googleapis.com/token",
                "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
                "is_oidc": True,
                "default_scopes": ["openid", "email"],
                "allowed_email_domains": ["example.com"],
            },
        )

        synced = OAuthClient.objects.sync_from_settings()

        assert len(synced) == 1
        with system_context(reason="test assertions"):
            oauth_client = OAuthClient.objects.get(vendor__slug="google", environment="prod")
        assert oauth_client.display_name == "Google Login"
        assert oauth_client.client_secret == "from-settings"
        assert oauth_client.is_oidc is True
        assert oauth_client.default_scopes == ["openid", "email"]

        settings.ANGEE_IAM_OAUTH_CLIENTS = (
            {
                "vendor": "google",
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
