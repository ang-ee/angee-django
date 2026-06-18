"""Tests for the integrate console GraphQL CRUD surfaces.

The integrate console references iam types (``IntegrationType.credential`` /
``account`` / ``owner``), so these tests build one ``console`` schema folding both
the iam and integrate addon ``console`` parts — the same shape the composer
assembles at runtime — and run over the concrete iam + integrate test tables.

Harness note: source-addon tests stand in Django's default auth user for the
swappable iam ``User`` (it has no ``sqid``). The create mutations take an
``owner: GlobalID`` that strawberry-django resolves through ``UserType``'s
``sqid`` NodeID, which the stand-in user lacks — so the owner-bearing *create*
path is exercised by building rows through the model managers (as ``runtime``
would resolve them) and asserting CRUD over those rows. ``createWebhookSubscription``'s
write-only ``secret`` is covered by the SDL invariant plus the read-back row.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory
from rebac import app_settings, system_context
from rebac.roles import grant
from strawberry import relay

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.credentials import CredentialKind
from angee.integrate.events import EventKind
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    Credential,
    Integration,
    OAuthClient,
    SchemaAddon,
    StubVCSBackend,
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
            {"id": _integration_global_id(conn)},
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
    make_integration("conn-groups", impl_class="stub")
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
    assert grouped["results"] == [{"key": {"implClass": "STUB"}, "count": 1}]


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
        {"id": _integration_global_id(conn)},
        user=admin,
    )

    assert result.errors is not None
    assert "implClass" in result.errors[0].message


def test_create_with_impl_creates_related_row_and_rolls_back(
    integrate_console_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Integration manager owns related-row creation transactionally."""

    user = User.objects.create_user(username="impl-factory-owner", email="impl-factory@example.com")
    unset = object()
    with system_context(reason="test.integrate.create_with_impl.seed"):
        oauth_client = OAuthClient.objects.create(
            slug="impl-factory",
            display_name="Impl Factory",
            client_id="impl-factory-client",
        )
        credential = Credential.objects.upsert_for_user(
            user,
            oauth_client,
            CredentialKind.STATIC_TOKEN,
            {"api_key": "x"},
        )
        vendor = Vendor.objects.create(slug="impl-factory", display_name="Impl Factory")
        integration = Integration.objects.create_with_impl(
            vendor=vendor,
            credential=credential,
            owner=user,
            impl_class="stub",
            status="active",
            related_input=SimpleNamespace(webhook_secret="created-secret"),
            related_unset=unset,
        )

        bridge = VcsBridge.objects.get(integration=integration)
        assert str(bridge.webhook_secret) == "created-secret"

        rollback_vendor = Vendor.objects.create(slug="impl-factory-rollback", display_name="Rollback")

        def fail_create_related_row(cls: type[StubVCSBackend], integration: Any, values: dict[str, Any]) -> None:
            del cls, integration, values
            raise ValueError("related validation failed")

        monkeypatch.setattr(StubVCSBackend, "create_related_row", classmethod(fail_create_related_row))
        with pytest.raises(ValueError, match="related validation failed"):
            Integration.objects.create_with_impl(
                vendor=rollback_vendor,
                credential=credential,
                owner=user,
                impl_class="stub",
                status="active",
                related_input=SimpleNamespace(webhook_secret="bad-secret"),
                related_unset=unset,
            )

        assert not Integration.objects.filter(vendor=rollback_vendor, owner=user, impl_class="stub").exists()


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
    # (The full create write path needs an iam ``User`` with a ``sqid`` NodeID, which
    # the source-addon stand-in auth user lacks — see the module docstring.)
    owner_id = relay.to_base64("UserType", str(conn.owner.pk))
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

    # Seed a non-empty config so the update also proves unrelated JSON survives.
    with system_context(reason="test.integrate.integration_crud.seed"):
        conn.config = {"endpoint": "https://vendor.example"}
        conn.save(update_fields=["config", "updated_at"])
    integration_id = _integration_global_id(conn)
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
        {"owner": relay.to_base64("UserType", str(owner.pk))},
        user=plain,
    ).errors is not None
    with system_context(reason="test.integrate.webhook_crud.create"):
        subscription = WebhookSubscription.objects.create(
            owner=owner,
            target_url="https://hooks.example.test/events",
            secret="top-secret",
            event_kinds=[_BRIDGE_SYNCED],
        )
    subscription_id = relay.to_base64("WebhookSubscriptionType", subscription.sqid)

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
    conn_id = _integration_global_id(conn)
    owner = User.objects.create_user(username="action-owner", email="action-owner@example.com")
    with system_context(reason="test.integrate.action_gate.seed"):
        subscription = WebhookSubscription.objects.create(
            owner=owner,
            target_url="https://hooks.example.test/events",
            secret="original-secret",
            event_kinds=[_BRIDGE_SYNCED],
        )
    sub_id = relay.to_base64("WebhookSubscriptionType", subscription.sqid)

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
    credential_id = relay.to_base64("CredentialType", credential.sqid)
    mutation = """
        mutation Connect($credential: ID!) {
          createIntegrationFromCredential(
            credential: $credential
            vendorSlug: "anthropic"
            credentialEnv: "ANTHROPIC_OAUTH_TOKEN"
          ) {
            vendor { slug }
            owner { username }
            credential { displayName }
            config
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
    assert created["config"] == {"credential_env": "ANTHROPIC_OAUTH_TOKEN"}
    with system_context(reason="test.integrate.credential_handoff.verify"):
        integration = Integration.objects.get(owner=owner, credential=credential)
        assert integration.vendor.slug == "anthropic"
        assert integration.config["credential_env"] == "ANTHROPIC_OAUTH_TOKEN"


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
            {"id": _integration_global_id(conn)},
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
    sub_id = relay.to_base64("WebhookSubscriptionType", subscription.sqid)

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
            {"id": _integration_global_id(conn)},
            user=admin,
        )
    )["updateIntegration"]
    assert result["status"] == "DISABLED"
    with system_context(reason="test.integrate.status.verify"):
        conn.refresh_from_db()
        assert str(conn.status) == "disabled"


def test_create_vcs_integration_rejects_non_vcs_impl(
    integrate_console_tables: None,
) -> None:
    """A VCS bridge cannot point at a non-VCS integration implementation."""

    console_schema = _schema()
    admin = _platform_admin("vcs-create-admin")
    conn = make_integration("vcs-create-not-vcs")
    result = _execute(
        console_schema,
        """
        mutation CreateVcs($integration: ID!) {
          createVcsIntegration(data: {integration: $integration}) {
            id
          }
        }
        """,
        {"integration": _integration_global_id(conn)},
        user=admin,
    )

    assert result.errors is not None
    assert "does not use a VCS implementation" in result.errors[0].message


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


def _integration_global_id(conn: Any) -> str:
    """Return the relay global id ``IntegrationType`` mutations resolve for ``conn``."""

    with system_context(reason="test.integrate.integration_global_id"):
        return relay.to_base64("IntegrationType", conn.sqid)


def _sdl_block(sdl: str, header: str) -> str:
    """Return one SDL block by its header prefix."""

    start = sdl.index(header)
    body = sdl.index("{", start)
    end = sdl.index("\n}", body)
    return sdl[start:end]
