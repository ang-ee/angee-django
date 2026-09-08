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
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.models.signals import post_save
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from rebac import app_settings, system_context
from rebac.roles import grant

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate import queue as integrate_queue
from angee.integrate.credentials import CredentialKind
from angee.integrate.events import EventKind
from angee.integrate.webhooks import WebhookDeliveryError
from tests.conftest import (
    POSTS_TEST_MODELS,
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
from tests.test_agents import InferenceProvider
from tests.test_agents_graphql import AGENTS_GRAPHQL_MODELS
from tests.test_messaging import MESSAGING_TEST_MODELS

User = get_user_model()
iam_schema = importlib.import_module("angee.iam.schema")
integrate_schema = importlib.import_module("angee.integrate.schema")
integrate_vcs_schema = importlib.import_module("angee.integrate_vcs.schema")
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
                lifecycle
                runtime_status
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
        "lifecycle": "CONNECTED",
        "runtime_status": "OK",
        "vendor": {"slug": "conn-node"},
        # ``make_integration`` builds the OAuth client with ``display_name=slug.title()``,
        # and an OAuth credential's label is its provider's display name (set on create by
        # ``CredentialManager._oauth_credential_name``).
        "credential": {"display_name": "Conn-Node"},
        "owner": {"username": "conn-node-owner"},
        "account": None,
    }


def test_integration_capabilities_are_native_creatable_children(
    integrate_console_tables: None,
) -> None:
    """Runtime capabilities expose only installed children with a real create ingress."""

    admin = _platform_admin("integration-capabilities-admin")
    rows = _data(
        _execute(
            _schema(),
            "query { integration_capabilities { resource label icon create_mode } }",
            user=admin,
        )
    )["integration_capabilities"]
    modes = {row["resource"]: row["create_mode"] for row in rows}
    assert modes["integrate_vcs.VcsBridge"] == "FORM"
    assert "agents.InferenceProvider" not in modes
    assert "messaging.Channel" not in modes
    assert "posts.Feed" not in modes


def test_integration_concrete_target_keeps_parent_only_rows_unavailable(
    integrate_console_tables: None,
) -> None:
    """Parent-only legacy rows remain visible without a guessed child target."""

    admin = _platform_admin("integration-target-admin")
    parent = make_integration("target-parent")
    child = make_integration("target-vcs", model=VcsBridge)
    rows = _data(
        _execute(
            _schema(),
            "query { integrations(limit: 20) { id concrete_target { state resource id } } }",
            user=admin,
        )
    )["integrations"]
    targets = {row["id"]: row["concrete_target"] for row in rows}
    assert targets[_public_id(parent)] == {"state": "UNAVAILABLE", "resource": None, "id": None}
    assert targets[_public_id(child)] == {
        "state": "AVAILABLE",
        "resource": "integrate_vcs.VcsBridge",
        "id": _public_id(child),
    }


def test_concrete_target_fails_closed_for_unexposed_and_ambiguous_children(
    integrate_console_tables: None,
) -> None:
    """Real hidden and sibling child rows never disclose an arbitrary target."""

    admin = _platform_admin("integration-target-closed-admin")
    hidden = make_integration("target-hidden", model=InferenceProvider, backend_class="manual")
    sibling_parent = make_integration("target-sibling", model=InferenceProvider, backend_class="manual")
    VcsBridge(integration_ptr_id=sibling_parent.pk, backend_class="local").save_base(raw=True, force_insert=True)
    rows = _data(
        _execute(
            _schema(),
            "query { integrations(limit: 20) { id concrete_target { state resource id } } }",
            user=admin,
        )
    )["integrations"]
    targets = {row["id"]: row["concrete_target"] for row in rows}
    assert targets[_public_id(hidden)] == {"state": "UNAVAILABLE", "resource": None, "id": None}
    # One exposed and one hidden sibling collapses to UNAVAILABLE so the hidden
    # capability is not revealed and the exposed sibling is never chosen.
    assert targets[_public_id(sibling_parent)] == {"state": "UNAVAILABLE", "resource": None, "id": None}


def test_concrete_target_list_query_cost_is_bounded_by_child_types(
    integrate_console_tables: None,
) -> None:
    """Adding parent rows does not add one concrete-child query per row."""

    admin = _platform_admin("integration-target-budget-admin")
    parent = make_integration("target-budget-parent")
    make_integration("target-budget-one", model=VcsBridge)
    hidden = make_integration("target-budget-hidden", model=InferenceProvider, backend_class="manual")
    schema = _schema()
    query = "query { integrations(limit: 20) { id concrete_target { state resource id } } }"
    with CaptureQueriesContext(connection) as one:
        first = _data(_execute(schema, query, user=admin))["integrations"]
    first_targets = {row["id"]: row["concrete_target"]["state"] for row in first}
    assert first_targets[_public_id(parent)] == "UNAVAILABLE"
    assert first_targets[_public_id(hidden)] == "UNAVAILABLE"
    assert "AVAILABLE" in first_targets.values()
    for index in range(4):
        make_integration(f"target-budget-more-{index}", model=VcsBridge)
    with CaptureQueriesContext(connection) as many:
        many_rows = _data(_execute(schema, query, user=admin))["integrations"]
    assert sum(row["concrete_target"]["state"] == "AVAILABLE" for row in many_rows) == 5
    assert len(many) <= len(one) + 1


def test_integration_groups_aggregate_runs_with_rebac_scope(
    integrate_console_tables: None,
) -> None:
    """The integration aggregate root executes through the Angee aggregate queryset seam."""

    admin = _platform_admin("conn-groups-admin")
    integration = make_integration("conn-groups")
    vendor_id = str(integration.vendor.sqid)
    console_schema = _schema()
    resources = {item.model_label: item for item in console_schema.angee_resources}
    group_by_type = resources["integrate.Integration"].type_names.group_by_spec
    assert group_by_type is not None

    grouped = _data(
        _execute(
            console_schema,
            """
            query IntegrationGroups($groupBy: [GROUP_BY_SPEC!]!) {
              integrations_groups(group_by: $groupBy, limit: 10) {
                key { vendor_id vendor__display_name kind }
                aggregate { count }
              }
            }
            """.replace("GROUP_BY_SPEC", group_by_type),
            {
                "groupBy": [
                    {"field": "VENDOR"},
                    {"field": "VENDOR__DISPLAY_NAME"},
                    {"field": "KIND"},
                ],
            },
            user=admin,
        )
    )["integrations_groups"]
    assert grouped == [
        {
            "key": {
                "vendor_id": vendor_id,
                "vendor__display_name": "Conn-Groups",
                "kind": "Integration",
            },
            "aggregate": {"count": 1},
        }
    ]


def test_console_resource_metadata_declares_integration_surface() -> None:
    """The composed console schema reports Integration's Hasura resource contract."""

    schemas = _schemas()
    console_schema = schemas.build("console")
    metadata = {item.model_label: item for item in console_schema.angee_resources}["integrate.Integration"]

    assert schemas.resources("console") == console_schema.angee_resources
    assert metadata.roots.list_name == "integrations"
    assert metadata.roots.detail_name == "integrations_by_pk"
    assert metadata.roots.aggregate_name == "integrations_aggregate"
    assert metadata.roots.group_name == "integrations_groups"
    assert metadata.roots.create_name is None
    assert metadata.roots.update_name == "update_integrations_by_pk"
    assert metadata.roots.delete_name == "delete_integrations_by_pk"
    assert {name for name, field in metadata.query.fields.items() if field.filter} == {
        "display_name",
        "runtime_status",
        "lifecycle",
        "id",
        "kind",
        "vendor",
        "updated_at",
    }
    assert {name for name, field in metadata.query.fields.items() if field.sort} == {
        "display_name",
        "runtime_status",
        "lifecycle",
        "created_at",
        "kind",
        "vendor",
        "updated_at",
    }
    assert metadata.aggregate_fields == ("id",)
    assert set(metadata.query.axes) == {"runtime_status", "lifecycle", "vendor", "kind"}
    assert {
        dimension.field: (dimension.server.input, dimension.server.key, dimension.kind)
        for dimension in metadata.query.axes.values()
    } == {
        "kind": ("KIND", "kind", "column"),
        "vendor": ("VENDOR", "vendor_id", "relation"),
        "lifecycle": ("LIFECYCLE", "lifecycle", "column"),
        "runtime_status": ("RUNTIME_STATUS", "runtime_status", "column"),
    }
    assert metadata.default_measures[0].op == "count"
    assert metadata.aggregate_measures == ()
    assert metadata.capabilities == ("list", "detail", "aggregate", "groups", "update", "delete", "changes")
    assert metadata.query.axes["vendor"].field == "vendor"
    assert metadata.query.fields["vendor"].relation.model == "integrate.Vendor"
    assert metadata.query.fields["vendor"].relation.identity_path == "vendor.id"
    assert metadata.query.axes["vendor"].server.label_key == "vendor__display_name"
    assert metadata.query.identity.field == "id"
    assert not hasattr(metadata, "group_aliases")
    serialized = console_schema._schema.extensions["angee"]["resources"]
    integration = {item["modelLabel"]: item for item in serialized}["integrate.Integration"]
    assert integration["schemaName"] == "console"
    assert integration["query"]["identity"]["field"] == "id"
    assert integration["roots"]["list"] == "integrations"
    assert integration["roots"]["detail"] == "integrations_by_pk"
    assert integration["roots"]["aggregate"] == "integrations_aggregate"
    assert integration["roots"]["groups"] == "integrations_groups"
    assert integration["roots"]["groupsCount"] == "integrations_groups_count"
    assert integration["roots"]["create"] is None
    assert integration["roots"]["update"] == "update_integrations_by_pk"
    assert integration["roots"]["delete"] == "delete_integrations_by_pk"
    assert integration["roots"]["changes"] == "integrationChanged"
    assert integration["capabilities"] == [
        "list",
        "detail",
        "aggregate",
        "groups",
        "update",
        "delete",
        "changes",
    ]
    assert list(integration["query"]["axes"]) == [
        "kind",
        "vendor",
        "lifecycle",
        "runtime_status",
    ]
    assert {
        dimension["field"]: (
            dimension["server"]["input"],
            dimension["server"]["key"],
            dimension["kind"],
        )
        for dimension in integration["query"]["axes"].values()
    } == {
        "kind": ("KIND", "kind", "column"),
        "vendor": ("VENDOR", "vendor_id", "relation"),
        "lifecycle": ("LIFECYCLE", "lifecycle", "column"),
        "runtime_status": ("RUNTIME_STATUS", "runtime_status", "column"),
    }
    assert integration["defaultMeasures"] == [{"op": "count", "field": None, "input": None}]
    assert integration["aggregateMeasures"] == []
    assert integration["query"]["fields"]["vendor"]["relation"] == {
        "model": "integrate.Vendor",
        "identityPath": "vendor.id",
        "labelPath": "vendor.display_name",
    }
    assert "groupAliases" not in integration
    assert integration["updateFields"] == ["vendor", "credential", "account", "owner"]
    kind_field = {field["name"]: field for field in integration["fields"]}["kind"]
    assert kind_field["kind"] == "scalar"
    assert integration["query"]["fields"]["kind"]["filter"] is not None
    assert integration["query"]["fields"]["kind"]["sort"] is not None
    assert "kind" in integration["query"]["axes"]
    assert kind_field["updatable"] is False
    lifecycle_field = {field["name"]: field for field in integration["fields"]}["lifecycle"]
    assert lifecycle_field["kind"] == "enum"
    assert lifecycle_field["widget"] == "select"
    assert lifecycle_field["readable"] is True
    assert integration["query"]["fields"]["lifecycle"]["filter"] is not None
    assert integration["query"]["fields"]["lifecycle"]["sort"] is not None
    assert "lifecycle" in integration["query"]["axes"]
    assert lifecycle_field["updatable"] is False
    runtime_status_field = {field["name"]: field for field in integration["fields"]}["runtime_status"]
    assert runtime_status_field["kind"] == "enum"
    assert runtime_status_field["readable"] is True
    assert integration["query"]["fields"]["runtime_status"]["filter"] is not None
    assert integration["query"]["fields"]["runtime_status"]["sort"] is not None
    assert "runtime_status" in integration["query"]["axes"]
    assert runtime_status_field["updatable"] is False


def test_resource_metadata_names_the_impl_columns_it_projects() -> None:
    """A resource names its readable ``ImplClassField`` columns; one without names none.

    The impl key a row stores is the fact a console contribution varies on per
    row, so the artifact names the column that carries it and the console never
    hardcodes a model's impl column.
    """

    schema = _schema()
    resources = {item.model_label: item for item in schema.angee_resources}
    assert resources["integrate.Integration"].impl_fields == ()
    assert resources["integrate_vcs.VcsBridge"].impl_fields == ("backend_class",)
    assert resources["integrate.Vendor"].impl_fields == ()

    wire = {item["modelLabel"]: item for item in schema._schema.extensions["angee"]["resources"]}
    assert wire["integrate.Integration"]["implFields"] == []
    assert wire["integrate.Vendor"]["implFields"] == []


def test_impl_choices_are_admin_only(integrate_console_tables: None) -> None:
    """Impl choice metadata is console data, so it is platform-admin gated."""

    console_schema = _schema()
    plain = User.objects.create_user(username="impl-choices-plain", email="plain@example.com")
    admin = _platform_admin("impl-choices-admin")
    query = """
        query {
          impl_choices(model: "integrate_vcs.VcsBridge", field: "backendClass") {
            key
          }
        }
    """

    assert _execute(console_schema, query, user=plain).errors is not None
    result = _data(_execute(console_schema, query, user=admin))["impl_choices"]
    assert {"key": "stub"} in result

    vcs_result = _data(
        _execute(
            console_schema,
            """
            query {
              impl_choices(model: "integrate_vcs.VcsBridge", field: "backendClass") {
                key
                config_schema
              }
            }
            """,
            user=admin,
        )
    )["impl_choices"]
    assert {"key": "stub", "config_schema": None} in vcs_result
    local = next(choice for choice in vcs_result if choice["key"] == "local")
    assert local["config_schema"] == {
        "type": "object",
        "properties": {
            "local_root": {
                "type": "string",
                "omittable": True,
                "label": "Local Root",
                "description": "Path to the checkout root.",
                "defaultValue": "../..",
            },
            "local_name": {
                "type": "string",
                "omittable": True,
                "label": "Local Name",
                "description": "Repository name override.",
                "defaultValue": "",
            },
            "local_org": {
                "type": "string",
                "omittable": True,
                "label": "Local Org",
                "description": "Repository organization label.",
                "defaultValue": "local",
            },
            "local_default_branch": {
                "type": "string",
                "omittable": True,
                "label": "Local Default Branch",
                "description": "Default branch label.",
                "defaultValue": "main",
            },
        },
        "required": [],
    }


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
            lifecycle="disconnected",
            webhook_secret="created-secret",
        )
        integration = Integration.objects.get(pk=bridge.pk)

        assert integration.kind == "VCS bridge"
        assert bridge.backend_class == "stub"
        assert str(integration.lifecycle) == "disconnected"
        assert bridge.pk == integration.pk
        assert bridge.owner_id == integration.owner_id
        assert bridge.vendor_id == integration.vendor_id
        assert bridge.credential_id == integration.credential_id
        assert str(bridge.webhook_secret) == "created-secret"


def test_vcs_bridge_create_maps_typed_config_errors_to_nested_field(
    integrate_console_tables: None,
) -> None:
    """Concrete GraphQL creation enforces backend config at the model save boundary."""

    seed = make_integration("typed-config-seed", backend_class="stub", model=VcsBridge)
    result = _execute(
        _schema(),
        """
        mutation InvalidConfig($vendor: ID!, $owner: ID!) {
          create_vcs_bridge(data: {
            vendor: $vendor,
            owner: $owner,
            backend_class: "local",
            config: {unknown_local_option: true}
          }) { id }
        }
        """,
        {"vendor": _public_id(seed.vendor.sqid), "owner": str(seed.owner.sqid)},
        user=_platform_admin("typed-config-admin"),
    )

    assert result.errors is not None
    assert result.errors[0].extensions == {
        "code": "VALIDATION",
        "validationErrors": {"config.unknown_local_option": ["Extra inputs are not permitted"]},
        "formErrors": [],
    }


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

    integration_id = _public_id(conn)
    update_integration = """
        mutation UpdateIntegration($id: String!) {
          update_integrations_by_pk(pk_columns: {id: $id}, _set: {account: null}) {
            account { external_id }
            vendor { slug }
          }
        }
    """

    assert _execute(console_schema, update_integration, {"id": integration_id}, user=plain).errors is not None

    updated = _data(_execute(console_schema, update_integration, {"id": integration_id}, user=admin))[
        "update_integrations_by_pk"
    ]
    assert updated == {"account": None, "vendor": {"slug": "conn-crud"}}

    delete_integration = """
        mutation DeleteIntegration($id: String!) {
          delete_integrations_by_pk(id: $id) {
            id
          }
        }
    """

    assert _execute(console_schema, delete_integration, {"id": integration_id}, user=plain).errors is not None

    deleted = _data(_execute(console_schema, delete_integration, {"id": integration_id}, user=admin))[
        "delete_integrations_by_pk"
    ]
    assert deleted["id"] == integration_id
    with system_context(reason="test.integrate.integration_crud.after_delete"):
        assert not Integration.objects.filter(pk=conn.pk).exists()


def test_attach_integration_credential_targets_owned_concrete_child(
    integrate_console_tables: None,
) -> None:
    """Credential-first ingress preserves the concrete identity and rejects another owner."""

    bridge = make_integration("credential-target", backend_class="stub", model=VcsBridge)
    other = User.objects.create_user(username="credential-target-other", email="other@example.com")
    with system_context(reason="test.integrate.credential_target.seed"):
        replacement = Credential.objects.create_local_credential(
            bridge.owner,
            kind=CredentialKind.STATIC_TOKEN,
            name="replacement-token",
            material={"api_key": "replacement"},
        )
    mutation = """
        mutation Attach($resource: String!, $id: ID!, $credential: ID!) {
          attach_integration_credential(resource: $resource, id: $id, credential: $credential) {
            lifecycle
            credential { display_name }
          }
        }
    """
    variables = {
        "resource": "integrate_vcs.VcsBridge",
        "id": _public_id(bridge),
        "credential": _public_id(replacement),
    }

    denied = _execute(_schema(), mutation, variables, user=other)
    assert denied.errors is not None
    attached = _data(_execute(_schema(), mutation, variables, user=bridge.owner))["attach_integration_credential"]
    assert attached == {
        "lifecycle": "CONNECTED",
        "credential": {"display_name": "replacement-token"},
    }
    with system_context(reason="test.integrate.credential_target.verify"):
        assert VcsBridge.objects.get(pk=bridge.pk).credential_id == replacement.pk


def test_connect_integration_reuses_live_oauth_for_explicit_concrete_child(
    integrate_console_tables: None,
) -> None:
    """OAuth ingress carries the authorized child resource and public id through attach."""

    bridge = make_integration(
        "oauth-concrete-target",
        kind=CredentialKind.OAUTH,
        backend_class="stub",
        model=VcsBridge,
    )
    credential = bridge.credential
    with system_context(reason="test.integrate.oauth_concrete_target.disconnect"):
        VcsBridge.objects.filter(pk=bridge.pk).update(credential=None, lifecycle="disconnected")
    mutation = """
        mutation Connect($resource: String!, $id: ID!) {
          connect_integration(resource: $resource, id: $id) {
            attached
            integration { id lifecycle credential { display_name } }
            error_code
          }
        }
    """
    variables = {"resource": "integrate_vcs.VcsBridge", "id": _public_id(bridge)}

    result = _data(_execute(_schema(), mutation, variables, user=bridge.owner))["connect_integration"]
    assert result == {
        "attached": True,
        "integration": {
            "id": _public_id(bridge),
            "lifecycle": "CONNECTED",
            "credential": {"display_name": "Oauth-Concrete-Target"},
        },
        "error_code": None,
    }
    with system_context(reason="test.integrate.oauth_concrete_target.verify"):
        persisted = VcsBridge.objects.get(pk=bridge.pk)
        assert persisted.credential_id == credential.pk


def test_integration_lifecycle_action_mutations_pause_connect_and_disconnect(
    integrate_console_tables: None,
) -> None:
    """Admin action mutations move Integration lifecycle through guarded transitions."""

    plain = User.objects.create_user(username="conn-action-plain", email="plain@example.com")
    admin = _platform_admin("conn-action-admin")
    conn = make_integration("conn-action")
    console_schema = _schema()
    integration_id = _public_id(conn)

    pause = """
        mutation Pause($id: ID!) {
          pause_integration(id: $id) { ok message }
        }
    """
    assert _execute(console_schema, pause, {"id": integration_id}, user=plain).errors is not None
    paused = _data(_execute(console_schema, pause, {"id": integration_id}, user=admin))["pause_integration"]
    assert paused == {"ok": True, "message": "Paused integration."}

    with system_context(reason="test.integrate.integration_actions.seed_error"):
        conn.refresh_from_db()
        conn.report_status("error", "token expired")

    connected = _data(
        _execute(
            console_schema,
            """
            mutation Connect($id: ID!) {
              mark_integration_connected(id: $id) { ok message }
            }
            """,
            {"id": integration_id},
            user=admin,
        )
    )["mark_integration_connected"]
    assert connected == {"ok": True, "message": "Connected integration."}
    with system_context(reason="test.integrate.integration_actions.verify"):
        conn.refresh_from_db()
        assert str(conn.lifecycle) == "connected"
        assert str(conn.runtime_status) == "ok"
        assert conn.last_error == ""

    disconnected = _data(
        _execute(
            console_schema,
            """
            mutation Disconnect($id: ID!) {
              mark_integration_disconnected(id: $id) { ok message }
            }
            """,
            {"id": integration_id},
            user=admin,
        )
    )["mark_integration_disconnected"]
    assert disconnected == {"ok": True, "message": "Disconnected integration."}
    with system_context(reason="test.integrate.integration_actions.verify_disconnect"):
        conn.refresh_from_db()
        assert str(conn.lifecycle) == "disconnected"


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
    assert (
        _execute(
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
        ).errors
        is not None
    )
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

    deleted = _data(_execute(console_schema, delete_webhook, {"id": subscription_id}, user=admin))[
        "delete_webhook_subscriptions_by_pk"
    ]
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


def test_sync_integration_runs_for_an_admin(
    integrate_console_tables: None,
) -> None:
    """An admin can queue an integration sync; with no bridges it is a no-op."""

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
    # No bridge rows exist, so the queue request finds nothing to run and reports success.
    assert result["ok"] is True
    assert "bridge" in result["message"].lower()


def test_sync_integration_queues_bridge_for_an_admin(
    integrate_console_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manual integration sync records queued state and defers bridge work."""

    console_schema = _schema()
    admin = _platform_admin("sync-queue-admin")
    bridge = make_integration("sync-queue", backend_class="stub", model=VcsBridge)
    queued: list[tuple[int, Any]] = []

    def fake_queue_bridge_sync(queued_bridge: VcsBridge, *, now: Any = None) -> None:
        queued.append((queued_bridge.pk, now))
        queued_bridge.sync_stage = queued_bridge.SyncStage.QUEUED
        queued_bridge.sync_error = ""
        queued_bridge.sync_progress = {"stage": queued_bridge.SyncStage.QUEUED, "queued_at": now.isoformat()}
        queued_bridge.save(update_fields=["sync_error", "sync_progress", "sync_stage", "updated_at"])

    monkeypatch.setattr(integrate_queue, "queue_bridge_sync", fake_queue_bridge_sync)
    monkeypatch.setattr("angee.integrate.schema.queue_bridge_sync", fake_queue_bridge_sync)
    monkeypatch.setattr("angee.integrate_vcs.schema.queue_bridge_sync", fake_queue_bridge_sync)

    result = _data(
        _execute(
            console_schema,
            "mutation($id: ID!){ sync_integration(id: $id){ ok message } }",
            {"id": _public_id(bridge)},
            user=admin,
        )
    )["sync_integration"]

    assert result["ok"] is True
    assert result["message"] == "Queued 1 bridge sync(s)."
    assert len(queued) == 1
    assert queued[0][0] == bridge.pk
    bridge.refresh_from_db()
    assert bridge.sync_stage == VcsBridge.SyncStage.QUEUED

    projected = _data(
        _execute(
            console_schema,
            """
            query BridgeSyncState($id: String!) {
              vcs_bridges_by_pk(id: $id) {
                sync_stage
                sync_error
                sync_progress
                last_sync_summary
                is_syncing
              }
            }
            """,
            {"id": _public_id(bridge)},
            user=admin,
        )
    )["vcs_bridges_by_pk"]
    assert projected == {
        "sync_stage": "queued",
        "sync_error": "",
        "sync_progress": bridge.sync_progress,
        "last_sync_summary": {},
        "is_syncing": False,
    }


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
    caplog: pytest.LogCaptureFixture,
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
        raise WebhookDeliveryError(
            "service unavailable at https://hooks.example/?token=canary-secret",
            status="503",
        )

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
    assert result == {"ok": False, "message": "Delivery failed: Webhook returned HTTP 503."}
    assert subscription.last_delivery_status == "503"
    assert subscription.last_error == "Webhook returned HTTP 503."
    assert subscription.consecutive_failures == 1
    assert "canary-secret" not in caplog.text


def test_update_vcs_bridge_lifecycle_accepts_the_lowercase_value(
    integrate_console_tables: None,
) -> None:
    """A VCS bridge lifecycle patch accepts the lowercase model value and reads back the enum."""

    console_schema = _schema()
    admin = _platform_admin("lifecycle-admin")
    bridge = make_integration("lifecycle-set", model=VcsBridge, backend_class="stub")
    result = _data(
        _execute(
            console_schema,
            """
            mutation($id: ID!) {
              update_vcs_bridge(data: {id: $id, lifecycle: "disconnected"}) { lifecycle }
            }
            """,
            {"id": _public_id(bridge)},
            user=admin,
        )
    )["update_vcs_bridge"]
    assert result["lifecycle"] == "DISCONNECTED"
    with system_context(reason="test.integrate.lifecycle.verify"):
        bridge.refresh_from_db()
        assert str(bridge.lifecycle) == "disconnected"


def test_update_vcs_bridge_lifecycle_only_emits_one_state_save(integrate_console_tables: None) -> None:
    """A guarded lifecycle patch saves in the transition hook, not again in the caller."""

    console_schema = _schema()
    admin = _platform_admin("lifecycle-save-admin")
    bridge = make_integration("lifecycle-save", model=VcsBridge, backend_class="stub")
    events: list[tuple[str, ...] | None] = []

    def capture_update_fields(sender: Any, instance: Any, update_fields: Any, **kwargs: Any) -> None:
        del sender, kwargs
        if instance.pk == bridge.pk:
            events.append(None if update_fields is None else tuple(sorted(update_fields)))

    post_save.connect(capture_update_fields, sender=VcsBridge, weak=False)
    try:
        result = _data(
            _execute(
                console_schema,
                """
                mutation($id: ID!) {
                  update_vcs_bridge(data: {id: $id, lifecycle: "paused"}) { lifecycle }
                }
                """,
                {"id": _public_id(bridge)},
                user=admin,
            )
        )["update_vcs_bridge"]
    finally:
        post_save.disconnect(capture_update_fields, sender=VcsBridge)

    assert result["lifecycle"] == "PAUSED"
    assert len(events) == 1
    assert events[0] is not None and "lifecycle" in events[0]


def test_update_vcs_bridge_lifecycle_accepts_the_graphql_enum_name(
    integrate_console_tables: None,
) -> None:
    """A lifecycle patch can echo the read-side GraphQL enum name back to the server."""

    console_schema = _schema()
    admin = _platform_admin("lifecycle-enum-admin")
    bridge = make_integration("lifecycle-enum", model=VcsBridge, backend_class="stub")
    result = _data(
        _execute(
            console_schema,
            """
            mutation($id: ID!) {
              update_vcs_bridge(data: {id: $id, lifecycle: "PAUSED"}) { lifecycle }
            }
            """,
            {"id": _public_id(bridge)},
            user=admin,
        )
    )["update_vcs_bridge"]
    assert result["lifecycle"] == "PAUSED"
    with system_context(reason="test.integrate.lifecycle_enum.verify"):
        bridge.refresh_from_db()
        assert str(bridge.lifecycle) == "paused"


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
                data: {
                  vendor: $vendor,
                  owner: $owner,
                  display_name: "Primary source host",
                  backend_class: "stub",
                  config: {stub_repos: []}
                }
              ) {
                display_name
                backend_class
                lifecycle
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

    assert result == {
        "display_name": "Primary source host",
        "backend_class": "STUB",
        "lifecycle": "DISCONNECTED",
        "config": {"stub_repos": []},
    }


@pytest.mark.parametrize("name, expected_name", [("Renamed", "Renamed"), (None, "")])
def test_update_vcs_bridge_merges_typed_config(
    integrate_console_tables: None,
    name: str | None,
    expected_name: str,
) -> None:
    """Unsent typed options survive patches; removing an option restores its default."""

    original = {
        "local_root": "../custom-checkout",
        "local_name": "Original",
        "local_org": "custom-org",
        "local_default_branch": "develop",
    }
    bridge = make_integration("vcs-config-patch", backend_class="local", model=VcsBridge, config=original)
    result = _data(
        _execute(
            _schema(),
            """
            mutation UpdateConfig($id: ID!, $config: JSON!) {
              update_vcs_bridge(data: {id: $id, config: $config}) { config }
            }
            """,
            {"id": _public_id(bridge.sqid), "config": {"local_name": name}},
            user=_platform_admin("vcs-config-patch-admin"),
        )
    )["update_vcs_bridge"]

    expected = {**original, "local_name": expected_name}
    assert result == {"config": expected}
    with system_context(reason="test.integrate.vcs_config_patch.verify"):
        bridge.refresh_from_db()
        assert bridge.config == expected


def test_update_vcs_bridge_rejects_unknown_config_key_after_merge(integrate_console_tables: None) -> None:
    """Patch merging still runs the typed config's unknown-key validation on save."""

    bridge = make_integration(
        "vcs-config-patch-invalid",
        backend_class="local",
        model=VcsBridge,
        config={"local_org": "kept"},
    )
    original = dict(bridge.config)
    result = _execute(
        _schema(),
        """
        mutation InvalidConfig($id: ID!) {
          update_vcs_bridge(data: {id: $id, config: {unknown_local_option: true}}) { id }
        }
        """,
        {"id": _public_id(bridge.sqid)},
        user=_platform_admin("vcs-config-patch-invalid-admin"),
    )

    assert result.errors is not None
    assert result.errors[0].extensions == {
        "code": "VALIDATION",
        "validationErrors": {"config.unknown_local_option": ["Extra inputs are not permitted"]},
        "formErrors": [],
    }
    with system_context(reason="test.integrate.vcs_config_patch_invalid.verify"):
        bridge.refresh_from_db()
        assert bridge.config == original


def test_update_vcs_bridge_rejects_backend_switch(
    integrate_console_tables: None,
) -> None:
    """A saved child cannot reinterpret its private config under another backend."""

    console_schema = _schema()
    admin = _platform_admin("vcs-update-admin")
    bridge = make_integration("vcs-update", backend_class="stub", model=VcsBridge)
    result = _execute(
        console_schema,
        """
            mutation UpdateVcs($id: ID!) {
              update_vcs_bridge(data: {id: $id, display_name: "Mutated", backend_class: "local"}) {
                id
              }
            }
            """,
        {"id": _public_id(bridge.sqid)},
        user=admin,
    )

    assert result.errors is not None
    assert result.errors[0].extensions["validationErrors"] == {
        "backendClass": ["Implementation selection is create-only."]
    }
    with system_context(reason="test.integrate.vcs_update_backend.verify"):
        bridge.refresh_from_db()
        assert bridge.display_name == ""
        assert bridge.backend_class == "stub"


def test_model_save_rejects_backend_switch_and_scopes_partial_config_validation(
    integrate_console_tables: None,
) -> None:
    """The model boundary guards direct writers without blocking unrelated legacy-row updates."""

    bridge = make_integration("vcs-model-impl-guard", backend_class="stub", model=VcsBridge)
    with system_context(reason="test.integrate.vcs_model_impl_guard.load"):
        loaded = VcsBridge.objects.get(pk=bridge.pk)
        loaded.backend_class = "local"
        with pytest.raises(ValidationError, match="Implementation selection is create-only"):
            loaded.save()
        with pytest.raises(ValidationError, match="Implementation selection is create-only"):
            VcsBridge.objects.update_or_create(pk=bridge.pk, defaults={"backend_class": "local"})

        reconstructed = VcsBridge(pk=bridge.pk, backend_class="local")
        with pytest.raises(ValidationError, match="Implementation selection is create-only"):
            reconstructed.save(using="default")

        unpersisted = VcsBridge.objects.get(pk=bridge.pk)
        unpersisted.backend_class = "local"
        unpersisted.display_name = "Only this field"
        unpersisted.save(update_fields={"display_name", "updated_at"})
        with pytest.raises(ValidationError, match="Implementation selection is create-only"):
            unpersisted.save()

        deferred_assignment = VcsBridge.objects.only("id").get(pk=bridge.pk)
        deferred_assignment.backend_class = "local"
        with pytest.raises(ValidationError, match="Implementation selection is create-only"):
            deferred_assignment.save(update_fields={"backend_class", "updated_at"})

        VcsBridge.objects.filter(pk=bridge.pk).update(
            backend_class="local",
            config={"unknown_legacy_key": True},
        )
        legacy = VcsBridge.objects.get(pk=bridge.pk)
        legacy.display_name = "Still writable"
        legacy.save(update_fields={"display_name", "updated_at"})
        legacy.config = {"unknown_legacy_key": False}
        with pytest.raises(ValidationError, match="config.unknown_legacy_key"):
            legacy.save(update_fields={"config", "updated_at"})

        deferred = VcsBridge.objects.only("display_name").get(pk=bridge.pk)
        deferred.display_name = "Deferred write"
        deferred.save(update_fields={"display_name", "updated_at"})
        assert deferred.backend_class == "local"
        deferred.backend_class = "github"
        with pytest.raises(ValidationError, match="Implementation selection is create-only"):
            deferred.save(update_fields={"backend_class", "updated_at"})

        refreshed = VcsBridge.objects.get(pk=bridge.pk)
        refreshed.backend_class = "github"
        refreshed.refresh_from_db(fields={"backend_class"})
        assert refreshed.backend_class == "local"
        refreshed.backend_class = "github"
        with pytest.raises(ValidationError, match="Implementation selection is create-only"):
            refreshed.save()


def test_non_create_only_impl_field_remains_editable(integrate_console_tables: None) -> None:
    """The integration invariant does not freeze unrelated implementation selectors."""

    provider_field = OAuthClient.impl_field("provider_type")
    first, second, *_rest = provider_field.registered_keys()
    with system_context(reason="test.integrate.mutable_impl_field"):
        client = OAuthClient.objects.create(
            slug="mutable-provider-type",
            display_name="Mutable provider type",
            provider_type=first,
        )
        client.provider_type = second
        client.save(update_fields={"provider_type", "updated_at"})
        client.refresh_from_db()
    assert client.provider_type == second


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
    assert result.errors[0].extensions == {"code": "INTERNAL"}
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
    connection_models = tuple(
        dict.fromkeys(
            MESSAGING_TEST_MODELS + POSTS_TEST_MODELS + (VcsBridge, WebhookSubscription) + AGENTS_GRAPHQL_MODELS
        )
    )
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
        for module in (iam_schema, integrate_schema, integrate_vcs_schema)
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
