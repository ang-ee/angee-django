"""Tests for the integrate console GraphQL CRUD surfaces.

The integrate console references iam types (``IntegrationType.credential`` /
``account`` / ``owner``), so these tests build one ``console`` schema folding both
the iam and integrate addon ``console`` parts — the same shape the composer
assembles at runtime — and run over the concrete iam + integrate test tables.

Harness note: source-addon tests stand in Django's default auth user for the
swappable iam ``User`` (it has no ``sqid``). The create mutations take an
``owner: ID`` that Angee resolves through ``UserType``'s public id, which the
stand-in user lacks — so the owner-bearing *create*
path is exercised by building rows through the model managers (as ``runtime``
would resolve them) and asserting CRUD over those rows. ``createWebhookSubscription``'s
write-only ``secret`` is covered by the SDL invariant plus the read-back row.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory
from rebac import app_settings, system_context
from rebac.roles import grant

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.credentials import CredentialKind
from angee.integrate.events import EventKind
from angee.integrate.webhooks import WebhookDeliveryError
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    Credential,
    Integration,
    OAuthClient,
    SchemaAddon,
    VcsBridge,
    Vendor,
    WebhookSubscription,
    execute_schema,
    make_integration,
)
from tests.conftest import (
    _create_missing_tables as _create_connection_tables,
)
from tests.conftest import (
    result_data as _data,
)

User = get_user_model()
iam_schema = importlib.import_module("angee.iam.schema")
integrate_schema = importlib.import_module("angee.integrate.schema")
_BRIDGE_SYNCED = str(EventKind.BRIDGE_SYNCED)
"""Raw stored value of one integration event kind (``str`` for clean typing)."""


def test_integration_node_resolves_nested_relations(
    integrate_console_tables: None,
) -> None:
    """An integration's nested vendor/credential/owner/account relations resolve for an admin."""

    admin = _platform_admin("conn-node-admin")
    conn = make_integration("conn-node")
    console_schema = _schema()

    resolved = _data(
        _execute(
            console_schema,
            """
            query Integration($id: ID!) {
              integration(id: $id) {
                status
                vendor { slug }
                credential { displayName }
                owner { username }
                account { externalId }
              }
            }
            """,
            {"id": _public_id(conn)},
            user=admin,
        )
    )["integration"]
    assert resolved == {
        "status": "ACTIVE",
        "vendor": {"slug": "conn-node"},
        # ``make_integration`` builds the OAuth client with ``display_name=slug.title()``,
        # and an OAuth credential's label is its provider's display name (set on create by
        # ``CredentialManager._oauth_credential_name``).
        "credential": {"displayName": "Conn-Node"},
        "owner": {"username": "conn-node-owner"},
        "account": None,
    }


def test_integration_groups_aggregate_runs_with_rebac_scope(
    integrate_console_tables: None,
) -> None:
    """The integration aggregate root executes through the Angee aggregate queryset seam."""

    admin = _platform_admin("conn-groups-admin")
    make_integration("conn-groups")
    console_schema = _schema()

    grouped = _data(
        _execute(
            console_schema,
            """
            query IntegrationGroups($groupBy: [IntegrationAggregateGroupBySpec!]!) {
              integrationGroups(groupBy: $groupBy, pagination: {offset: 0, limit: 10}) {
                totalCount
                results {
                  key { implClass }
                  count
                }
              }
            }
            """,
            {"groupBy": [{"field": "IMPL_CLASS"}]},
            user=admin,
        )
    )["integrationGroups"]
    assert grouped["totalCount"] == 1
    assert grouped["results"] == [{"key": {"implClass": "NONE"}, "count": 1}]


def test_impl_choices_are_admin_only(integrate_console_tables: None) -> None:
    """Impl choice metadata is console data, so it is platform-admin gated."""

    console_schema = _schema()
    plain = User.objects.create_user(username="impl-choices-plain", email="plain@example.com")
    admin = _platform_admin("impl-choices-admin")
    query = """
        query {
          implChoices(model: "integrate.Integration", field: "implClass") {
            key
          }
        }
    """

    assert _execute(console_schema, query, user=plain).errors is not None
    result = _data(_execute(console_schema, query, user=admin))["implChoices"]
    assert {"key": "none"} in result

    vcs_result = _data(
        _execute(
            console_schema,
            """
            query {
              implChoices(model: "integrate.VcsBridge", field: "backendClass") {
                key
              }
            }
            """,
            user=admin,
        )
    )["implChoices"]
    assert {"key": "stub"} in vcs_result


def test_update_integration_rejects_impl_class_patch(integrate_console_tables: None) -> None:
    """The implementation discriminator is create-time only."""

    admin = _platform_admin("impl-patch-admin")
    conn = make_integration("impl-patch")
    console_schema = _schema()

    result = _execute(
        console_schema,
        """
        mutation UpdateIntegration($id: ID!) {
          updateIntegration(data: {id: $id, implClass: "stub"}) {
            status
          }
        }
        """,
        {"id": _public_id(conn)},
        user=admin,
    )

    assert result.errors is not None
    assert "implClass" in result.errors[0].message


def test_create_integration_rejects_child_backend_key(integrate_console_tables: None) -> None:
    """Child backend keys are not valid parent integration implementation keys."""

    console_schema = _schema()
    admin = _platform_admin("vcs-parent-create-admin")
    owner = User.objects.create_user(username="vcs-parent-create-owner", email="owner@example.com")
    with system_context(reason="test.integrate.vcs_parent_create.seed"):
        vendor = Vendor.objects.create(slug="vcs-parent-create", display_name="VCS Parent Create")

    result = _execute(
        console_schema,
        """
        mutation CreateIntegration($vendor: ID!, $owner: ID!) {
          createIntegration(data: {vendor: $vendor, owner: $owner, implClass: "stub"}) {
            id
          }
        }
        """,
        {
            "vendor": _public_id(vendor.sqid),
            "owner": str(owner.pk),
        },
        user=admin,
    )

    assert result.errors is not None
    assert "ANGEE_INTEGRATION_IMPLS" in result.errors[0].message
    with system_context(reason="test.integrate.vcs_parent_create.verify"):
        assert not Integration.objects.filter(owner=owner, vendor=vendor).exists()


def test_vcs_bridge_child_creation_creates_parent_identity(integrate_console_tables: None) -> None:
    """Creating an MTI child creates the Integration parent identity row."""

    user = User.objects.create_user(username="impl-factory-owner", email="impl-factory@example.com")
    with system_context(reason="test.integrate.vcs_child.seed"):
        oauth_client = OAuthClient.objects.create(
            slug="vcs-child",
            display_name="VCS Child",
            client_id="vcs-child-client",
        )
        credential = Credential.objects.upsert_for_user(
            user,
            oauth_client,
            CredentialKind.STATIC_TOKEN,
            {"api_key": "x"},
        )
        vendor = Vendor.objects.create(slug="vcs-child", display_name="VCS Child")
        assert VcsBridge.impl_key_for("backend_class", "STUB", default="local") == "stub"
        bridge = VcsBridge.objects.create(
            vendor=vendor,
            credential=credential,
            owner=user,
            backend_class="stub",
            status="draft",
            webhook_secret="created-secret",
        )
        integration = Integration.objects.get(pk=bridge.pk)

        assert integration.impl_class == "none"
        assert bridge.backend_class == "stub"
        assert str(integration.status) == "draft"
        assert bridge.pk == integration.pk
        assert bridge.owner_id == integration.owner_id
        assert bridge.vendor_id == integration.vendor_id
        assert bridge.credential_id == integration.credential_id
        assert str(bridge.webhook_secret) == "created-secret"


def test_integration_update_delete_are_admin_only(
    integrate_console_tables: None,
) -> None:
    """Updating then deleting an integration is platform-admin gated."""

    plain = User.objects.create_user(username="conn-crud-plain", email="plain@example.com")
    admin = _platform_admin("conn-crud-admin")
    conn = make_integration("conn-crud")
    console_schema = _schema()

    # ``createIntegration`` is admin gated: the permission check fires before any FK
    # input resolution, so a non-admin is denied regardless of the owner id supplied.
    # (The full create write path needs an iam ``User`` with a public id, which
    # the source-addon stand-in auth user lacks — see the module docstring.)
    owner_id = str(conn.owner.pk)
    assert _execute(
        console_schema,
        """
        mutation CreateIntegration($owner: ID!) {
          createIntegration(data: {owner: $owner, vendor: $owner, credential: $owner}) {
            status
          }
        }
        """,
        {"owner": owner_id},
        user=plain,
    ).errors is not None

    integration_id = _public_id(conn)
    update_integration = """
        mutation UpdateIntegration($id: ID!) {
          updateIntegration(data: {id: $id, status: "disabled"}) {
            status
            vendor { slug }
          }
        }
    """

    assert _execute(console_schema, update_integration, {"id": integration_id}, user=plain).errors is not None

    updated = _data(
        _execute(console_schema, update_integration, {"id": integration_id}, user=admin)
    )["updateIntegration"]
    assert updated == {"status": "DISABLED", "vendor": {"slug": "conn-crud"}}

    delete_integration = """
        mutation DeleteIntegration($id: ID!) {
          deleteIntegration(id: $id, confirm: true) {
            hasBlockers
            totalDeletedCount
          }
        }
    """

    assert _execute(console_schema, delete_integration, {"id": integration_id}, user=plain).errors is not None

    deleted = _data(
        _execute(console_schema, delete_integration, {"id": integration_id}, user=admin)
    )["deleteIntegration"]
    assert deleted["hasBlockers"] is False
    assert deleted["totalDeletedCount"] >= 1
    with system_context(reason="test.integrate.integration_crud.after_delete"):
        assert not Integration.objects.filter(pk=conn.pk).exists()


def test_webhook_crud_secret_write_only(
    integrate_console_tables: None,
) -> None:
    """The webhook secret is a write-only input absent from the output type; delete is admin gated."""

    console_schema = _schema()
    console_sdl = console_schema.as_str()
    # ``secret`` is accepted on the input but never rendered on the output type.
    assert "secret" in _sdl_block(console_sdl, "input WebhookSubscriptionInput")
    assert "secret" not in _sdl_block(console_sdl, "type WebhookSubscriptionType")
    # The create mutation is contributed to the console mutation root.
    assert "createWebhookSubscription(" in _sdl_block(console_sdl, "type Mutation")

    plain = User.objects.create_user(username="webhook-plain", email="plain@example.com")
    admin = _platform_admin("webhook-admin")
    owner = User.objects.create_user(username="webhook-owner", email="owner@example.com")
    # ``createWebhookSubscription`` is admin gated before owner-id resolution.
    assert _execute(
        console_schema,
        """
        mutation CreateWebhook($owner: ID!) {
          createWebhookSubscription(data: {owner: $owner, targetUrl: "https://hooks.example/x", secret: "s"}) {
            targetUrl
          }
        }
        """,
        {"owner": str(owner.pk)},
        user=plain,
    ).errors is not None
    with system_context(reason="test.integrate.webhook_crud.create"):
        subscription = WebhookSubscription.objects.create(
            owner=owner,
            target_url="https://hooks.example.test/events",
            secret="top-secret",
            event_kinds=[_BRIDGE_SYNCED],
        )
    subscription_id = str(subscription.sqid)

    # The created row reads back without ever exposing the secret.
    read_back = _data(
        _execute(
            console_schema,
            """
            query Webhook($id: ID!) {
              webhookSubscription(id: $id) {
                targetUrl
                enabled
                eventKinds
                owner { username }
              }
            }
            """,
            {"id": subscription_id},
            user=admin,
        )
    )["webhookSubscription"]
    assert read_back == {
        "targetUrl": "https://hooks.example.test/events",
        "enabled": True,
        "eventKinds": [_BRIDGE_SYNCED],
        "owner": {"username": "webhook-owner"},
    }
    # Querying the absent ``secret`` field is a schema error, proving it is write-only.
    secret_query = _execute(
        console_schema,
        """
        query Webhook($id: ID!) {
          webhookSubscription(id: $id) { secret }
        }
        """,
        {"id": subscription_id},
        user=admin,
    )
    assert secret_query.errors is not None
    assert "secret" in secret_query.errors[0].message

    delete_webhook = """
        mutation DeleteWebhook($id: ID!) {
          deleteWebhookSubscription(id: $id, confirm: true) {
            hasBlockers
            totalDeletedCount
          }
        }
    """

    assert _execute(console_schema, delete_webhook, {"id": subscription_id}, user=plain).errors is not None

    deleted = _data(
        _execute(console_schema, delete_webhook, {"id": subscription_id}, user=admin)
    )["deleteWebhookSubscription"]
    assert deleted["hasBlockers"] is False
    assert deleted["totalDeletedCount"] >= 1
    with system_context(reason="test.integrate.webhook_crud.after_delete"):
        assert not WebhookSubscription.objects.filter(pk=subscription.pk).exists()


def test_integration_action_mutations_are_admin_only(
    integrate_console_tables: None,
) -> None:
    """sync/test/rotate action mutations are platform-admin gated."""

    console_schema = _schema()
    plain = User.objects.create_user(username="action-plain", email="action-plain@example.com")
    conn = make_integration("action-gate")
    conn_id = _public_id(conn)
    owner = User.objects.create_user(username="action-owner", email="action-owner@example.com")
    with system_context(reason="test.integrate.action_gate.seed"):
        subscription = WebhookSubscription.objects.create(
            owner=owner,
            target_url="https://hooks.example.test/events",
            secret="original-secret",
            event_kinds=[_BRIDGE_SYNCED],
        )
    sub_id = str(subscription.sqid)

    denied = [
        ("mutation($id: ID!){ syncIntegration(id: $id){ ok } }", {"id": conn_id}),
        ("mutation($id: ID!){ testConnection(id: $id){ ok } }", {"id": conn_id}),
        ("mutation($id: ID!){ rotateWebhookSecret(id: $id){ ok } }", {"id": sub_id}),
    ]
    for query, variables in denied:
        assert _execute(console_schema, query, variables, user=plain).errors is not None


def test_create_integration_from_credential_is_authenticated_user_owned(
    integrate_console_tables: None,
) -> None:
    """A signed-in user can create an integration only from their own credential."""

    console_schema = _schema()
    owner = User.objects.create_user(username="credential-owner", email="owner@example.com")
    other = User.objects.create_user(username="credential-other", email="other@example.com")
    with system_context(reason="test.integrate.credential_handoff.seed"):
        oauth_client = OAuthClient.objects.create(
            slug="anthropic",
            display_name="Anthropic",
            client_id="public-client",
        )
        credential = Credential.objects.upsert_for_user(
            owner,
            oauth_client,
            CredentialKind.OAUTH,
            {"access_token": "oauth-token"},
        )
        Vendor.objects.create(slug="anthropic", display_name="Anthropic")
    credential_id = str(credential.sqid)
    mutation = """
        mutation Connect($credential: ID!) {
          createIntegrationFromCredential(
            credential: $credential
            vendorSlug: "anthropic"
          ) {
            vendor { slug }
            owner { username }
            credential { displayName }
          }
        }
    """

    assert _execute(console_schema, mutation, {"credential": credential_id}, user=other).errors is not None

    created = _data(
        _execute(console_schema, mutation, {"credential": credential_id}, user=owner)
    )["createIntegrationFromCredential"]

    assert created["vendor"] == {"slug": "anthropic"}
    assert created["owner"] == {"username": "credential-owner"}
    # The OAuth credential is labelled from its provider's display name on create
    # (``CredentialManager._oauth_credential_name``).
    assert created["credential"]["displayName"] == "Anthropic"
    with system_context(reason="test.integrate.credential_handoff.verify"):
        integration = Integration.objects.get(owner=owner, credential=credential)
        assert integration.vendor.slug == "anthropic"


def test_connect_integration_reuses_existing_row_with_enum_impl_class(
    integrate_console_tables: None,
) -> None:
    """The self-service connect mutation accepts read-side enum casing on retry."""

    console_schema = _schema()
    conn = make_integration(
        "connect-enum",
        kind=CredentialKind.OAUTH,
    )
    mutation = """
        mutation {
          connectIntegration(vendorSlug: "connect-enum", implClass: "NONE") {
            attached
            error
            integration {
              implClass
              vendor { slug }
            }
          }
        }
    """

    for _attempt in range(2):
        result = _data(_execute(console_schema, mutation, user=conn.owner))["connectIntegration"]
        assert result == {
            "attached": True,
            "error": None,
            "integration": {
                "implClass": "NONE",
                "vendor": {"slug": "connect-enum"},
            },
        }

    with system_context(reason="test.integrate.connect_enum.verify"):
        assert Integration.objects.filter(
            owner=conn.owner,
            vendor=conn.vendor,
            impl_class="none",
        ).count() == 1


def test_connect_integration_uses_shared_oauth_client_error_code(
    integrate_console_tables: None,
) -> None:
    """Integration connect reports the shared OAuth-client lookup error code."""

    console_schema = _schema()
    owner = User.objects.create_user(username="missing-oauth-owner", email="owner@example.com")
    with system_context(reason="test.integrate.missing_oauth.seed"):
        Vendor.objects.create(slug="missing-oauth", display_name="Missing OAuth")
    mutation = """
        mutation {
          connectIntegration(vendorSlug: "missing-oauth", implClass: "NONE") {
            attached
            error
            errorCode
          }
        }
    """

    result = _data(_execute(console_schema, mutation, user=owner))["connectIntegration"]

    assert result == {
        "attached": False,
        "error": "Integration has no enabled OAuth client.",
        "errorCode": "oauth_client_not_connectable",
    }


def test_connect_integration_rejects_child_backend_key(integrate_console_tables: None) -> None:
    """Self-service connect cannot use a child backend key as a parent implementation."""

    console_schema = _schema()
    owner = User.objects.create_user(username="vcs-parent-connect-owner", email="owner@example.com")
    with system_context(reason="test.integrate.vcs_parent_connect.seed"):
        vendor = Vendor.objects.create(slug="vcs-parent-connect", display_name="VCS Parent Connect")

    result = _execute(
        console_schema,
        """
        mutation {
          connectIntegration(vendorSlug: "vcs-parent-connect", implClass: "STUB") {
            attached
          }
        }
        """,
        user=owner,
    )

    assert result.errors is not None
    assert "ANGEE_INTEGRATION_IMPLS" in result.errors[0].message
    with system_context(reason="test.integrate.vcs_parent_connect.verify"):
        assert not Integration.objects.filter(owner=owner, vendor=vendor).exists()


def test_sync_integration_runs_for_an_admin(
    integrate_console_tables: None,
) -> None:
    """An admin can eagerly sync an integration; with no bridges it is a no-op."""

    console_schema = _schema()
    admin = _platform_admin("sync-admin")
    conn = make_integration("sync-run")
    result = _data(
        _execute(
            console_schema,
            "mutation($id: ID!){ syncIntegration(id: $id){ ok message } }",
            {"id": _public_id(conn)},
            user=admin,
        )
    )["syncIntegration"]
    # No bridge rows exist, so the eager sync finds nothing to run and reports success.
    assert result["ok"] is True
    assert "bridge" in result["message"].lower()


def test_rotate_webhook_secret_changes_the_stored_secret(
    integrate_console_tables: None,
) -> None:
    """Rotation returns a fresh secret once and persists it write-only."""

    console_schema = _schema()
    admin = _platform_admin("rotate-admin")
    owner = User.objects.create_user(username="rotate-owner", email="rotate-owner@example.com")
    with system_context(reason="test.integrate.rotate.seed"):
        subscription = WebhookSubscription.objects.create(
            owner=owner,
            target_url="https://hooks.example.test/events",
            secret="original-secret",
            event_kinds=[_BRIDGE_SYNCED],
        )
    sub_id = str(subscription.sqid)

    result = _data(
        _execute(
            console_schema,
            "mutation($id: ID!){ rotateWebhookSecret(id: $id){ ok secret } }",
            {"id": sub_id},
            user=admin,
        )
    )["rotateWebhookSecret"]
    assert result["ok"] is True
    assert result["secret"] and result["secret"] != "original-secret"
    with system_context(reason="test.integrate.rotate.verify"):
        stored = WebhookSubscription.objects.get(pk=subscription.pk)
        assert str(stored.secret) == result["secret"]


def test_test_webhook_delivery_records_failure_status(
    integrate_console_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed test delivery records the HTTP status classified by the model owner."""

    console_schema = _schema()
    admin = _platform_admin("test-webhook-admin")
    owner = User.objects.create_user(username="test-webhook-owner", email="test-webhook-owner@example.com")
    with system_context(reason="test.integrate.webhook_test.seed"):
        subscription = WebhookSubscription.objects.create(
            owner=owner,
            target_url="https://hooks.example.test/events",
            secret="original-secret",
            event_kinds=[_BRIDGE_SYNCED],
        )

    def fail_delivery(self: WebhookSubscription, body: bytes) -> str:
        assert b'"type":"test"' in body
        raise WebhookDeliveryError("service unavailable", status="503")

    monkeypatch.setattr(WebhookSubscription, "deliver", fail_delivery)

    result = _data(
        _execute(
            console_schema,
            "mutation($id: ID!){ testWebhookDelivery(id: $id){ ok message } }",
            {"id": str(subscription.sqid)},
            user=admin,
        )
    )["testWebhookDelivery"]

    subscription.refresh_from_db()
    assert result == {"ok": False, "message": "Delivery failed: WebhookDeliveryError: service unavailable"}
    assert subscription.last_delivery_status == "503"
    assert subscription.consecutive_failures == 1


def test_update_integration_status_accepts_the_lowercase_value(
    integrate_console_tables: None,
) -> None:
    """A `set`-action status patch sends the lowercase model value and reads back the enum.

    The console form's "Disable" action sends ``status: "disabled"`` through the
    generated ``updateIntegration``; this locks that the String patch persists the
    value and the output enum serializes it as the uppercase name.
    """

    console_schema = _schema()
    admin = _platform_admin("status-admin")
    conn = make_integration("status-set")
    result = _data(
        _execute(
            console_schema,
            'mutation($id: ID!){ updateIntegration(data: {id: $id, status: "disabled"}){ status } }',
            {"id": _public_id(conn)},
            user=admin,
        )
    )["updateIntegration"]
    assert result["status"] == "DISABLED"
    with system_context(reason="test.integrate.status.verify"):
        conn.refresh_from_db()
        assert str(conn.status) == "disabled"


def test_update_integration_status_accepts_the_graphql_enum_name(
    integrate_console_tables: None,
) -> None:
    """A status patch can echo the read-side GraphQL enum name back to the server."""

    console_schema = _schema()
    admin = _platform_admin("status-enum-admin")
    conn = make_integration("status-enum")
    result = _data(
        _execute(
            console_schema,
            'mutation($id: ID!){ updateIntegration(data: {id: $id, status: "DRAFT"}){ status } }',
            {"id": _public_id(conn)},
            user=admin,
        )
    )["updateIntegration"]
    assert result["status"] == "DRAFT"
    with system_context(reason="test.integrate.status_enum.verify"):
        conn.refresh_from_db()
        assert str(conn.status) == "draft"


def test_create_vcs_bridge_creates_child_row(
    integrate_console_tables: None,
) -> None:
    """VCS bridge create writes the child row directly."""

    console_schema = _schema()
    admin = _platform_admin("vcs-create-admin")
    seed = make_integration("vcs-create")
    result = _data(
        _execute(
            console_schema,
            """
            mutation CreateVcs($vendor: ID!, $owner: ID!) {
              createVcsBridge(
                data: {vendor: $vendor, owner: $owner, backendClass: "stub", config: {stub_repos: []}}
              ) {
                backendClass
                status
                config
              }
            }
            """,
            {
                "vendor": _public_id(seed.vendor.sqid),
                "owner": str(seed.owner.pk),
            },
            user=admin,
        )
    )["createVcsBridge"]

    assert result == {"backendClass": "STUB", "status": "DRAFT", "config": {"stub_repos": []}}


def test_update_vcs_bridge_accepts_backend_class(
    integrate_console_tables: None,
) -> None:
    """A saved VCS child can switch backend and materialize backend defaults."""

    console_schema = _schema()
    admin = _platform_admin("vcs-update-admin")
    bridge = make_integration("vcs-update", backend_class="stub", model=VcsBridge)
    result = _data(
        _execute(
            console_schema,
            """
            mutation UpdateVcs($id: ID!) {
              updateVcsBridge(data: {id: $id, backendClass: "local"}) {
                backendClass
                config
              }
            }
            """,
            {"id": _public_id(bridge.sqid)},
            user=admin,
        )
    )["updateVcsBridge"]

    assert result == {
        "backendClass": "LOCAL",
        "config": {
            "local_default_branch": "main",
            "local_org": "local",
            "local_root": "../..",
        },
    }
    with system_context(reason="test.integrate.vcs_update_backend.verify"):
        bridge.refresh_from_db()
        assert bridge.backend_class == "local"
        assert bridge.config == {
            "local_default_branch": "main",
            "local_org": "local",
            "local_root": "../..",
        }


def test_update_vcs_bridge_rejects_unknown_backend_class(
    integrate_console_tables: None,
) -> None:
    """VCS backend updates validate through the VCS backend registry."""

    console_schema = _schema()
    admin = _platform_admin("vcs-update-invalid-backend-admin")
    bridge = make_integration("vcs-update-invalid-backend", backend_class="stub", model=VcsBridge)
    result = _execute(
        console_schema,
        """
        mutation UpdateVcs($id: ID!) {
          updateVcsBridge(data: {id: $id, backendClass: "none"}) {
            id
          }
        }
        """,
        {"id": _public_id(bridge.sqid)},
        user=admin,
    )

    assert result.errors is not None
    assert "none" in result.errors[0].message
    with system_context(reason="test.integrate.vcs_update_invalid_backend.verify"):
        bridge.refresh_from_db()
        assert bridge.backend_class == "stub"


def test_update_vcs_bridge_rejects_parent_impl_class(
    integrate_console_tables: None,
) -> None:
    """The VCS patch exposes backendClass, not the parent implClass."""

    console_schema = _schema()
    admin = _platform_admin("vcs-update-parent-impl-admin")
    bridge = make_integration("vcs-update-parent-impl", backend_class="stub", model=VcsBridge)
    result = _execute(
        console_schema,
        """
        mutation UpdateVcs($id: ID!) {
          updateVcsBridge(data: {id: $id, implClass: "none"}) {
            id
          }
        }
        """,
        {"id": _public_id(bridge.sqid)},
        user=admin,
    )

    assert result.errors is not None
    assert "implClass" in result.errors[0].message


@pytest.fixture()
def integrate_console_tables(transactional_db: Any) -> Iterator[None]:
    """Create the iam + integrate (incl. webhook) console tables and sync REBAC."""

    del transactional_db
    created_models = _create_connection_tables(IAM_CONNECTION_TEST_MODELS + INTEGRATE_TEST_MODELS + (VcsBridge,))
    webhook_created = False
    if WebhookSubscription._meta.db_table not in connection.introspection.table_names():
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(WebhookSubscription)
        webhook_created = True
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if webhook_created:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(WebhookSubscription)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _schema() -> Any:
    """Build the merged iam + integrate ``console`` schema for these tests."""

    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in (iam_schema, integrate_schema)
    ]
    return GraphQLSchemas(addons).build("console")


def _execute(
    schema: Any,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    user: Any | None = None,
) -> Any:
    """Execute one GraphQL operation against the merged console schema."""

    return execute_schema(schema, query, variables, request=_request(user or AnonymousUser()))


def _request(user: Any) -> Any:
    """Return a console-shaped POST request bound to ``user``."""

    request = RequestFactory().post("/graphql/console/")
    request.user = user
    return request


def _platform_admin(username: str) -> Any:
    """Create a superuser holding the platform-admin role tuple."""

    admin = User.objects.create_superuser(
        username=username,
        email=f"{username}@example.com",
        password="admin",
    )
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    return admin


def _public_id(value: Any) -> str:
    """Return the public id mutations resolve for ``value``."""

    return str(getattr(value, "sqid", value))


def _sdl_block(sdl: str, header: str) -> str:
    """Return one SDL block by its header prefix."""

    start = sdl.index(header)
    body = sdl.index("{", start)
    end = sdl.index("\n}", body)
    return sdl[start:end]
