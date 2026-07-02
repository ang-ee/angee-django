"""Tests for the integrate console GraphQL resource and action surfaces.

The integrate console references iam types (``IntegrationType.credential`` /
``account`` / ``owner``), so these tests build one ``console`` schema folding both
the iam and integrate addon ``console`` parts — the same shape the composer
assembles at runtime — and run over the concrete iam + integrate test tables.

Harness note: source-addon tests use the concrete IAM user model, so ``owner:
ID`` carries the same sqid public id runtime projects pass. Webhook ``secret``
remains write-only: accepted by the Hasura input and absent from the output
projection.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import IntegrityError
from django.test import RequestFactory
from rebac import app_settings, system_context
from rebac.roles import grant

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.credentials import CredentialKind
from angee.integrate.events import EventKind
from angee.integrate.webhooks import WebhookDeliveryError
from tests.conftest import (
    SOCIAL_TEST_MODELS,
    Credential,
    Integration,
    OAuthClient,
    SchemaAddon,
    VcsBridge,
    Vendor,
    WebhookSubscription,
    _clear_model_tables,
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
            query Integration($id: String!) {
              integrations_by_pk(id: $id) {
                status
                vendor { slug }
                credential { display_name }
                owner { username }
                account { external_id }
              }
            }
            """,
            {"id": _public_id(conn)},
            user=admin,
        )
    )["integrations_by_pk"]
    assert resolved == {
        "status": "ACTIVE",
        "vendor": {"slug": "conn-node"},
        # ``make_integration`` builds the OAuth client with ``display_name=slug.title()``,
        # and an OAuth credential's label is its provider's display name (set on create by
        # ``CredentialManager._oauth_credential_name``).
        "credential": {"display_name": "Conn-Node"},
        "owner": {"username": "conn-node-owner"},
        "account": None,
    }


def test_integration_groups_aggregate_runs_with_rebac_scope(
    integrate_console_tables: None,
) -> None:
    """The integration aggregate root executes through the Angee aggregate queryset seam."""

    admin = _platform_admin("conn-groups-admin")
    integration = make_integration("conn-groups")
    vendor_pk = str(integration.vendor_id)
    console_schema = _schema()

    grouped = _data(
        _execute(
            console_schema,
            """
            query IntegrationGroups($groupBy: [IntegrationTypeGroupBySpec!]!) {
              integrations_groups(group_by: $groupBy, limit: 10) {
                key { vendor_id vendor__display_name kind impl_class }
                aggregate { count }
              }
            }
            """,
            {
                "groupBy": [
                    {"field": "VENDOR"},
                    {"field": "VENDOR__DISPLAY_NAME"},
                    {"field": "KIND"},
                    {"field": "IMPL_CLASS"},
                ],
            },
            user=admin,
        )
    )["integrations_groups"]
    assert grouped == [
        {
            "key": {
                "vendor_id": vendor_pk,
                "vendor__display_name": "Conn-Groups",
                "kind": "Integration",
                "impl_class": "NONE",
            },
            "aggregate": {"count": 1},
        }
    ]


def test_console_resource_metadata_declares_integration_surface() -> None:
    """The composed console schema reports Integration's Hasura resource contract."""

    schemas = _schemas()
    console_schema = schemas.build("console")
    metadata = {
        item.model_label: item
        for item in console_schema.angee_resources
    }["integrate.Integration"]

    assert schemas.resources("console") == console_schema.angee_resources
    assert metadata.roots.list_name == "integrations"
    assert metadata.roots.detail_name == "integrations_by_pk"
    assert metadata.roots.aggregate_name == "integrations_aggregate"
    assert metadata.roots.group_name == "integrations_groups"
    assert metadata.roots.create_name == "insert_integrations_one"
    assert metadata.roots.update_name == "update_integrations_by_pk"
    assert metadata.roots.delete_name == "delete_integrations_by_pk"
    assert metadata.filter_fields == (
        "id", "display_name", "vendor", "kind", "impl_class", "status", "updated_at",
    )
    assert metadata.order_fields == (
        "display_name", "vendor", "kind", "impl_class", "status", "created_at", "updated_at",
    )
    assert metadata.aggregate_fields == ("id",)
    assert metadata.group_by_fields == ("kind", "impl_class", "vendor", "vendor__display_name", "status")
    assert {
        dimension.field: (dimension.input, dimension.key, dimension.kind, dimension.scalar)
        for dimension in metadata.group_dimensions
    } == {
        "kind": ("KIND", "kind", "column", None),
        "impl_class": ("IMPL_CLASS", "impl_class", "column", None),
        "vendor": ("VENDOR", "vendor_id", "relation", "ID"),
        "vendor__display_name": ("VENDOR__DISPLAY_NAME", "vendor__display_name", "column", None),
        "status": ("STATUS", "status", "column", None),
    }
    assert metadata.default_measures[0].op == "count"
    assert metadata.aggregate_measures == ()
    assert metadata.capabilities == ("list", "detail", "aggregate", "groups", "create", "update", "delete", "changes")
    assert metadata.relation_axes[0].field == "vendor"
    assert metadata.relation_axes[0].model_label == "integrate.Vendor"
    assert metadata.relation_axes[0].public_id_field == "sqid"
    assert metadata.relation_axes[0].label_axis == "vendor__display_name"
    assert metadata.group_aliases == ()
    serialized = console_schema._schema.extensions["angee"]["resources"]
    integration = {
        item["modelLabel"]: item
        for item in serialized
    }["integrate.Integration"]
    assert integration["schemaName"] == "console"
    assert integration["roots"]["list"] == "integrations"
    assert integration["roots"]["detail"] == "integrations_by_pk"
    assert integration["roots"]["aggregate"] == "integrations_aggregate"
    assert integration["roots"]["groups"] == "integrations_groups"
    assert integration["roots"]["create"] == "insert_integrations_one"
    assert integration["roots"]["update"] == "update_integrations_by_pk"
    assert integration["roots"]["delete"] == "delete_integrations_by_pk"
    assert integration["roots"]["changes"] == "integrationChanged"
    assert integration["capabilities"] == [
        "list",
        "detail",
        "aggregate",
        "groups",
        "create",
        "update",
        "delete",
        "changes",
    ]
    assert integration["groupByFields"] == ["kind", "impl_class", "vendor", "vendor__display_name", "status"]
    assert {
        dimension["field"]: (
            dimension["input"],
            dimension["key"],
            dimension["kind"],
            dimension["scalar"],
        )
        for dimension in integration["groupDimensions"]
    } == {
        "kind": ("KIND", "kind", "column", None),
        "impl_class": ("IMPL_CLASS", "impl_class", "column", None),
        "vendor": ("VENDOR", "vendor_id", "relation", "ID"),
        "vendor__display_name": ("VENDOR__DISPLAY_NAME", "vendor__display_name", "column", None),
        "status": ("STATUS", "status", "column", None),
    }
    assert integration["defaultMeasures"] == [{"op": "count", "field": None, "input": None}]
    assert integration["aggregateMeasures"] == []
    assert integration["relationAxes"] == [
        {
            "field": "vendor",
            "modelLabel": "integrate.Vendor",
            "publicIdField": "sqid",
            "labelAxis": "vendor__display_name",
        }
    ]
    assert integration["groupAliases"] == []
    assert integration["updateFields"] == ["vendor", "credential", "account", "owner", "status"]
    kind_field = {field["name"]: field for field in integration["fields"]}["kind"]
    assert kind_field["kind"] == "scalar"
    assert kind_field["filterable"] is True
    assert kind_field["sortable"] is True
    assert kind_field["groupable"] is True
    assert kind_field["updatable"] is False
    impl_field = {field["name"]: field for field in integration["fields"]}["impl_class"]
    assert impl_field["values"] == [{"value": "NONE", "description": "Draft"}]
    status_field = {field["name"]: field for field in integration["fields"]}["status"]
    assert status_field["kind"] == "enum"
    assert status_field["widget"] == "select"
    assert status_field["readable"] is True
    assert status_field["filterable"] is True
    assert status_field["sortable"] is True
    assert status_field["groupable"] is True
    assert status_field["updatable"] is True


def test_impl_choices_are_admin_only(integrate_console_tables: None) -> None:
    """Impl choice metadata is console data, so it is platform-admin gated."""

    console_schema = _schema()
    plain = User.objects.create_user(username="impl-choices-plain", email="plain@example.com")
    admin = _platform_admin("impl-choices-admin")
    query = """
        query {
          impl_choices(model: "integrate.Integration", field: "implClass") {
            key
          }
        }
    """

    assert _execute(console_schema, query, user=plain).errors is not None
    result = _data(_execute(console_schema, query, user=admin))["impl_choices"]
    assert {"key": "none"} in result

    vcs_result = _data(
        _execute(
            console_schema,
            """
            query {
              impl_choices(model: "integrate.VcsBridge", field: "backendClass") {
                key
              }
            }
            """,
            user=admin,
        )
    )["impl_choices"]
    assert {"key": "stub"} in vcs_result


def test_update_integration_rejects_impl_class_patch(integrate_console_tables: None) -> None:
    """The implementation discriminator is create-time only."""

    admin = _platform_admin("impl-patch-admin")
    conn = make_integration("impl-patch")
    console_schema = _schema()

    result = _execute(
        console_schema,
        """
        mutation UpdateIntegration($id: String!) {
          update_integrations_by_pk(pk_columns: {id: $id}, _set: {impl_class: "stub"}) {
            status
          }
        }
        """,
        {"id": _public_id(conn)},
        user=admin,
    )

    assert result.errors is not None
    assert "impl_class" in result.errors[0].message


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
          insert_integrations_one(object: {vendor: $vendor, owner: $owner, impl_class: "stub"}) {
            id
          }
        }
        """,
        {
            "vendor": _public_id(vendor.sqid),
            "owner": str(owner.sqid),
        },
        user=admin,
    )

    assert result.errors is not None
    assert "impl_class" in result.errors[0].message
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
        assert Integration.impl_key_for("impl_class", "", default="none") == "none"
        assert Integration.impl_key_for("impl_class", "   ", default="none") == "none"
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
        assert integration.kind == "VCS bridge"
        assert bridge.backend_class == "stub"
        assert str(integration.status) == "draft"
        assert bridge.pk == integration.pk
        assert bridge.owner_id == integration.owner_id
        assert bridge.vendor_id == integration.vendor_id
        assert bridge.credential_id == integration.credential_id
        assert str(bridge.webhook_secret) == "created-secret"


def test_integration_kind_backfill_recovers_child_rows(integrate_console_tables: None) -> None:
    """Existing parent rows recover their concrete integration kind after migration."""

    bridge = make_integration("kind-backfill", backend_class="stub", model=VcsBridge)

    with system_context(reason="test.integrate.kind_backfill"):
        Integration.objects.filter(pk=bridge.pk).update(kind="Integration")
        assert Integration.objects.get(pk=bridge.pk).kind == "Integration"
        assert Integration.objects.sync_kinds() == 1
        assert Integration.objects.get(pk=bridge.pk).kind == "VCS bridge"


def test_integration_update_delete_are_admin_only(
    integrate_console_tables: None,
) -> None:
    """Updating then deleting an integration is platform-admin gated."""

    plain = User.objects.create_user(username="conn-crud-plain", email="plain@example.com")
    admin = _platform_admin("conn-crud-admin")
    conn = make_integration("conn-crud")
    console_schema = _schema()

    # Hasura create is still REBAC-gated; the deliberately bogus relation ids
    # only need to prove a plain user cannot create through the generic root.
    owner_id = str(conn.owner.sqid)
    assert _execute(
        console_schema,
        """
        mutation CreateIntegration($owner: ID!) {
          insert_integrations_one(object: {owner: $owner, vendor: $owner, credential: $owner}) {
            status
          }
        }
        """,
        {"owner": owner_id},
        user=plain,
    ).errors is not None

    integration_id = _public_id(conn)
    update_integration = """
        mutation UpdateIntegration($id: String!) {
          update_integrations_by_pk(pk_columns: {id: $id}, _set: {status: "disabled"}) {
            status
            vendor { slug }
          }
        }
    """

    assert _execute(console_schema, update_integration, {"id": integration_id}, user=plain).errors is not None

    updated = _data(
        _execute(console_schema, update_integration, {"id": integration_id}, user=admin)
    )["update_integrations_by_pk"]
    assert updated == {"status": "DISABLED", "vendor": {"slug": "conn-crud"}}

    delete_integration = """
        mutation DeleteIntegration($id: String!) {
          delete_integrations_by_pk(id: $id) {
            id
          }
        }
    """

    assert _execute(console_schema, delete_integration, {"id": integration_id}, user=plain).errors is not None

    deleted = _data(
        _execute(console_schema, delete_integration, {"id": integration_id}, user=admin)
    )["delete_integrations_by_pk"]
    assert deleted["id"] == integration_id
    with system_context(reason="test.integrate.integration_crud.after_delete"):
        assert not Integration.objects.filter(pk=conn.pk).exists()


def test_webhook_crud_secret_write_only(
    integrate_console_tables: None,
) -> None:
    """The webhook secret is a write-only input absent from the output type; delete is admin gated."""

    console_schema = _schema()
    console_sdl = console_schema.as_str()
    # ``secret`` is accepted on the input but never rendered on the output type.
    assert "secret" in _sdl_block(console_sdl, "input webhook_subscriptions_insert_input")
    assert "secret" not in _sdl_block(console_sdl, "type WebhookSubscriptionType")
    # The create mutation is contributed to the console mutation root.
    assert "insert_webhook_subscriptions_one(" in _sdl_block(console_sdl, "type Mutation")

    plain = User.objects.create_user(username="webhook-plain", email="plain@example.com")
    admin = _platform_admin("webhook-admin")
    owner = User.objects.create_user(username="webhook-owner", email="owner@example.com")
    # ``createWebhookSubscription`` is admin gated before owner-id resolution.
    assert _execute(
        console_schema,
        """
        mutation CreateWebhook($owner: ID!) {
          insert_webhook_subscriptions_one(
            object: {owner: $owner, target_url: "https://hooks.example/x", secret: "s"}
          ) {
            target_url
          }
        }
        """,
        {"owner": str(owner.sqid)},
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
            query Webhook($id: String!) {
              webhook_subscriptions_by_pk(id: $id) {
                target_url
                enabled
                event_kinds
                owner { username }
              }
            }
            """,
            {"id": subscription_id},
            user=admin,
        )
    )["webhook_subscriptions_by_pk"]
    assert read_back == {
        "target_url": "https://hooks.example.test/events",
        "enabled": True,
        "event_kinds": [_BRIDGE_SYNCED],
        "owner": {"username": "webhook-owner"},
    }
    # Querying the absent ``secret`` field is a schema error, proving it is write-only.
    secret_query = _execute(
        console_schema,
        """
        query Webhook($id: String!) {
          webhook_subscriptions_by_pk(id: $id) { secret }
        }
        """,
        {"id": subscription_id},
        user=admin,
    )
    assert secret_query.errors is not None
    assert "secret" in secret_query.errors[0].message

    delete_webhook = """
        mutation DeleteWebhook($id: String!) {
          delete_webhook_subscriptions_by_pk(id: $id) {
            id
          }
        }
    """

    assert _execute(console_schema, delete_webhook, {"id": subscription_id}, user=plain).errors is not None

    deleted = _data(
        _execute(console_schema, delete_webhook, {"id": subscription_id}, user=admin)
    )["delete_webhook_subscriptions_by_pk"]
    assert deleted["id"] == subscription_id
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
        ("mutation($id: ID!){ sync_integration(id: $id){ ok } }", {"id": conn_id}),
        ("mutation($id: ID!){ test_connection(id: $id){ ok } }", {"id": conn_id}),
        ("mutation($id: ID!){ rotate_webhook_secret(id: $id){ ok } }", {"id": sub_id}),
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
          create_integration_from_credential(
            credential: $credential
            vendor_slug: "anthropic"
          ) {
            vendor { slug }
            owner { username }
            credential { display_name }
          }
        }
    """

    assert _execute(console_schema, mutation, {"credential": credential_id}, user=other).errors is not None

    created = _data(
        _execute(console_schema, mutation, {"credential": credential_id}, user=owner)
    )["create_integration_from_credential"]

    assert created["vendor"] == {"slug": "anthropic"}
    assert created["owner"] == {"username": "credential-owner"}
    # The OAuth credential is labelled from its provider's display name on create
    # (``CredentialManager._oauth_credential_name``).
    assert created["credential"]["display_name"] == "Anthropic"
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
          connect_integration(vendor_slug: "connect-enum", impl_class: "NONE") {
            attached
            error
            integration {
              impl_class
              vendor { slug }
            }
          }
        }
    """

    for _attempt in range(2):
        result = _data(_execute(console_schema, mutation, user=conn.owner))["connect_integration"]
        assert result == {
            "attached": True,
            "error": None,
            "integration": {
                "impl_class": "NONE",
                "vendor": {"slug": "connect-enum"},
            },
        }

    with system_context(reason="test.integrate.connect_enum.verify"):
        assert Integration.objects.filter(
            owner=conn.owner,
            vendor=conn.vendor,
            impl_class="none",
        ).count() == 1


def test_integration_draft_factory_is_database_unique(
    integrate_console_tables: None,
) -> None:
    """The Integration owner, not resolvers, owns parent draft uniqueness."""

    owner = User.objects.create_user(username="draft-unique", email="draft-unique@example.com")
    with system_context(reason="test.integrate.draft_unique.seed"):
        vendor = Vendor.objects.create(slug="draft-unique", display_name="Draft Unique")
        first = Integration.objects.draft_for(owner, vendor=vendor, impl_class="none")
        second = Integration.objects.draft_for(owner, vendor=vendor, impl_class="none")

    assert second.pk == first.pk
    with pytest.raises(IntegrityError), system_context(reason="test.integrate.draft_unique.duplicate"):
        Integration.objects.create(owner=owner, vendor=vendor, impl_class="none")


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
          connect_integration(vendor_slug: "missing-oauth", impl_class: "NONE") {
            attached
            error
            error_code
          }
        }
    """

    result = _data(_execute(console_schema, mutation, user=owner))["connect_integration"]

    assert result == {
        "attached": False,
        "error": "Integration has no enabled OAuth client.",
        "error_code": "oauth_client_not_connectable",
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
          connect_integration(vendor_slug: "vcs-parent-connect", impl_class: "STUB") {
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
            "mutation($id: ID!){ sync_integration(id: $id){ ok message } }",
            {"id": _public_id(conn)},
            user=admin,
        )
    )["sync_integration"]
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
            "mutation($id: ID!){ rotate_webhook_secret(id: $id){ ok secret } }",
            {"id": sub_id},
            user=admin,
        )
    )["rotate_webhook_secret"]
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
            "mutation($id: ID!){ test_webhook_delivery(id: $id){ ok message } }",
            {"id": str(subscription.sqid)},
            user=admin,
        )
    )["test_webhook_delivery"]

    subscription.refresh_from_db()
    assert result == {"ok": False, "message": "Delivery failed: WebhookDeliveryError: service unavailable"}
    assert subscription.last_delivery_status == "503"
    assert subscription.consecutive_failures == 1


def test_update_integration_status_accepts_the_lowercase_value(
    integrate_console_tables: None,
) -> None:
    """A `set`-action status patch sends the lowercase model value and reads back the enum.

    The console form's "Disable" action sends ``status: "disabled"`` through the
    generated ``update_integrations_by_pk``; this locks that the String patch persists the
    value and the output enum serializes it as the uppercase name.
    """

    console_schema = _schema()
    admin = _platform_admin("status-admin")
    conn = make_integration("status-set")
    result = _data(
        _execute(
            console_schema,
            """
            mutation($id: String!) {
              update_integrations_by_pk(pk_columns: {id: $id}, _set: {status: "disabled"}) { status }
            }
            """,
            {"id": _public_id(conn)},
            user=admin,
        )
    )["update_integrations_by_pk"]
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
            """
            mutation($id: String!) {
              update_integrations_by_pk(pk_columns: {id: $id}, _set: {status: "DRAFT"}) { status }
            }
            """,
            {"id": _public_id(conn)},
            user=admin,
        )
    )["update_integrations_by_pk"]
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
              create_vcs_bridge(
                data: {vendor: $vendor, owner: $owner, backend_class: "stub", config: {stub_repos: []}}
              ) {
                backend_class
                status
                config
              }
            }
            """,
            {
                "vendor": _public_id(seed.vendor.sqid),
                "owner": str(seed.owner.sqid),
            },
            user=admin,
        )
    )["create_vcs_bridge"]

    assert result == {"backend_class": "STUB", "status": "DRAFT", "config": {"stub_repos": []}}


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
              update_vcs_bridge(data: {id: $id, backend_class: "local"}) {
                backend_class
                config
              }
            }
            """,
            {"id": _public_id(bridge.sqid)},
            user=admin,
        )
    )["update_vcs_bridge"]

    assert result == {
        "backend_class": "LOCAL",
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
          update_vcs_bridge(data: {id: $id, backend_class: "none"}) {
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
    """The VCS patch exposes backend_class, not the parent impl_class."""

    console_schema = _schema()
    admin = _platform_admin("vcs-update-parent-impl-admin")
    bridge = make_integration("vcs-update-parent-impl", backend_class="stub", model=VcsBridge)
    result = _execute(
        console_schema,
        """
        mutation UpdateVcs($id: ID!) {
          update_vcs_bridge(data: {id: $id, impl_class: "none"}) {
            id
          }
        }
        """,
        {"id": _public_id(bridge.sqid)},
        user=admin,
    )

    assert result.errors is not None
    assert "impl_class" in result.errors[0].message


@pytest.fixture()
def integrate_console_tables(transactional_db: Any) -> Iterator[None]:
    """Create the iam + integrate (incl. webhook) console tables and sync REBAC."""

    del transactional_db
    from tests.test_messaging import MESSAGING_TEST_MODELS

    connection_models = MESSAGING_TEST_MODELS + SOCIAL_TEST_MODELS + (VcsBridge, WebhookSubscription)
    _create_connection_tables(connection_models)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(connection_models)


def _schema() -> Any:
    """Build the merged iam + integrate ``console`` schema for these tests."""

    return _schemas().build("console")


def _schemas() -> GraphQLSchemas:
    """Return the merged iam + integrate schema owner for these tests."""

    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in (iam_schema, integrate_schema)
    ]
    return GraphQLSchemas(addons)


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
