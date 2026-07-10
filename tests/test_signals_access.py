"""Tests for change publishers, audit stamping, and read gates."""

from __future__ import annotations

import datetime
import decimal
import importlib
import logging
import sys
import textwrap
from types import SimpleNamespace
from typing import Any

import pytest
import strawberry
import strawberry_django
from django.apps import AppConfig
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection, models
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import actor_context, anonymous_actor
from strawberry import auto
from strawberry_django.fields.types import field_type_map

from angee.addons import AddonContract
from angee.base.mixins import AuditMixin, TimestampMixin
from angee.base.serialization import json_safe
from angee.base.sync import sync_ingestion_context
from angee.graphql import access, publishing
from angee.graphql.access import ChangeReadGate
from angee.graphql.events import ChangePayload
from angee.graphql.field_types import register_field_type
from angee.graphql.schema import DEFAULT_SCHEMA_NAME, GraphQLSchemas
from angee.graphql.subscriptions import changes
from tests.conftest import SchemaAddon, _clear_model_tables, _create_missing_tables


class AuditStamped(TimestampMixin, AuditMixin, models.Model):
    """Concrete audit model used to test model-owned stamping."""

    id = models.CharField(max_length=32, primary_key=True)
    name = models.CharField(max_length=64)

    class Meta:
        """Django model options for the test model."""

        app_label = "auth"


def payload(**overrides: object) -> dict[str, object]:
    """Return a baseline change payload with optional overrides."""

    data: dict[str, object] = {
        "model": "auth.Group",
        "id": "1",
        "action": "update",
        "changed_fields": ["name"],
        "changed_values": {"name": "editors"},
    }
    data.update(overrides)
    return data


def _receiver_count(signal: Any, dispatch_uid: str) -> int:
    """Return receivers connected with ``dispatch_uid``."""

    return sum(1 for receiver in signal.receivers if receiver[0][0] == dispatch_uid)


@pytest.mark.django_db(transaction=True)
def test_audit_mixin_stamps_from_rebac_actor_inside_save() -> None:
    """Audit fields are stamped by the model save chain, including partial saves."""

    User = get_user_model()
    creator = User.objects.create_user(username="audit-creator")
    editor = User.objects.create_user(username="audit-editor")

    created_models = _create_missing_tables((AuditStamped,))
    try:
        with actor_context(creator):
            row = AuditStamped.objects.create(id="known", name="first")
        row.refresh_from_db()
        assert row.created_by_id == creator.pk
        assert row.updated_by_id == creator.pk
        previous_updated_at = timezone.now() - datetime.timedelta(days=1)
        AuditStamped.objects.filter(pk=row.pk).update(updated_at=previous_updated_at)
        row.refresh_from_db()

        with actor_context(editor):
            row.name = "second"
            row.save(update_fields={"name"})
        row.refresh_from_db()
        assert row.created_by_id == creator.pk
        assert row.updated_by_id == editor.pk
        assert row.updated_at > previous_updated_at
    finally:
        _clear_model_tables((AuditStamped,))
        if created_models:
            with connection.schema_editor() as editor_schema:
                editor_schema.delete_model(AuditStamped)


def test_connect_publishers_is_idempotent() -> None:
    """A model is connected to change publishers once."""

    publishing.connect_publishers(Group)
    publishing.connect_publishers(Group)

    assert _receiver_count(post_save, "angee-changes-auth.Group-save") == 1


def test_publish_uses_public_id_and_changed_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publishing builds JSON-safe payloads with stable public IDs."""

    sent: list[tuple[type[models.Model], dict[str, Any]]] = []

    def broadcast(
        model: type[models.Model],
        event: dict[str, Any],
    ) -> None:
        """Capture a broadcast payload."""

        sent.append((model, event))

    monkeypatch.setattr(publishing, "_broadcast", broadcast)
    monkeypatch.setattr(
        publishing.transaction,
        "on_commit",
        lambda callback: callback(),
    )
    publishing.connect_change_broadcast_receiver()
    group = Group(id=7, name="editors")

    publishing.publish_change(group, action="update", update_fields=("name",))

    assert sent == [
        (
            Group,
            {
                "model": "auth.Group",
                "id": "7",
                "action": "update",
                "changed_fields": ["name"],
                "changed_values": {"name": "editors"},
            },
        )
    ]


def test_change_signal_receiver_sees_broadcast_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dispatch-uid receiver sees the same payload the GraphQL broadcast emits."""

    payloads: list[ChangePayload] = []
    broadcasts: list[tuple[type[models.Model], dict[str, Any]]] = []

    def receiver(sender: type[models.Model], payload: ChangePayload, **kwargs: object) -> None:
        del kwargs
        assert sender is Group
        payloads.append(payload)

    monkeypatch.setattr(publishing, "_broadcast", lambda model, event: broadcasts.append((model, event)))
    monkeypatch.setattr(publishing.transaction, "on_commit", lambda callback: callback())
    publishing.connect_change_broadcast_receiver()
    publishing.change_published.connect(
        receiver,
        dispatch_uid="tests.change_signal_receiver_sees_broadcast_payload",
    )
    try:
        publishing.publish_change(Group(id=8, name="reviewers"), action="update", update_fields=("name",))
    finally:
        publishing.change_published.disconnect(dispatch_uid="tests.change_signal_receiver_sees_broadcast_payload")

    assert len(payloads) == 1
    assert broadcasts == [(Group, payloads[0].as_message())]


def test_publish_change_suppresses_non_broadcasting_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows that opt out of generic broadcasts reach no signal receiver."""

    class SilentRow(models.Model):
        """Concrete row that opts out of generic change publication."""

        name = models.CharField(max_length=40)

        class Meta:
            app_label = "auth"

        def broadcasts_changes(self) -> bool:
            return False

    payloads: list[ChangePayload] = []

    def receiver(sender: type[models.Model], payload: ChangePayload, **kwargs: object) -> None:
        del sender, kwargs
        payloads.append(payload)

    monkeypatch.setattr(publishing.transaction, "on_commit", lambda callback: callback())
    publishing.change_published.connect(
        receiver,
        dispatch_uid="tests.publish_change_suppresses_non_broadcasting_rows",
    )
    try:
        row = SilentRow(id=1, name="hidden")
        publishing.publish_change(row, action="update", update_fields=("name",))
    finally:
        publishing.change_published.disconnect(
            dispatch_uid="tests.publish_change_suppresses_non_broadcasting_rows",
        )

    assert payloads == []


def test_publish_change_marks_sync_ingestion_and_still_broadcasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows saved inside sync ingestion still broadcast with an internal flag."""

    payloads: list[ChangePayload] = []
    broadcasts: list[dict[str, Any]] = []

    def receiver(sender: type[models.Model], payload: ChangePayload, **kwargs: object) -> None:
        del sender, kwargs
        payloads.append(payload)

    monkeypatch.setattr(publishing, "_broadcast", lambda model, event: broadcasts.append(event))
    monkeypatch.setattr(publishing.transaction, "on_commit", lambda callback: callback())
    publishing.connect_change_broadcast_receiver()
    publishing.change_published.connect(
        receiver,
        dispatch_uid="tests.publish_change_suppresses_sync_ingestion",
    )
    try:
        with sync_ingestion_context():
            publishing.publish_change(Group(id=9, name="backfill"), action="update", update_fields=("name",))
    finally:
        publishing.change_published.disconnect(dispatch_uid="tests.publish_change_suppresses_sync_ingestion")

    assert [payload.during_ingestion for payload in payloads] == [True]
    assert len(broadcasts) == 1
    assert "during_ingestion" not in broadcasts[0]


def test_publish_change_robust_receivers_log_and_continue(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing receiver is logged and does not starve later receivers."""

    calls: list[str] = []

    def failing_receiver(sender: type[models.Model], payload: ChangePayload, **kwargs: object) -> None:
        del sender, payload, kwargs
        calls.append("failing")
        raise RuntimeError("receiver exploded")

    def later_receiver(sender: type[models.Model], payload: ChangePayload, **kwargs: object) -> None:
        del sender, payload, kwargs
        calls.append("later")

    monkeypatch.setattr(publishing.transaction, "on_commit", lambda callback: callback())
    publishing.change_published.connect(failing_receiver, dispatch_uid="tests.publish_change.failing")
    publishing.change_published.connect(later_receiver, dispatch_uid="tests.publish_change.later")
    try:
        with caplog.at_level(logging.ERROR, logger="angee.graphql.publishing"):
            publishing.publish_change(Group(id=10, name="ops"), action="update", update_fields=("name",))
    finally:
        publishing.change_published.disconnect(dispatch_uid="tests.publish_change.failing")
        publishing.change_published.disconnect(dispatch_uid="tests.publish_change.later")

    assert calls == ["failing", "later"]
    assert "change_published receiver" in caplog.text
    assert "receiver exploded" in caplog.text


@pytest.mark.django_db(transaction=True)
def test_graphql_ready_connects_publishers_without_schema_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """App ready wiring publishes changes without building a GraphQL schema."""

    class ReadyPublished(models.Model):
        """Concrete model exposed as a changes-capable resource by a fake discovery."""

        name = models.CharField(max_length=40)

        class Meta:
            app_label = "auth"

    class FakeSchemas:
        """Schema discovery stand-in that exposes metadata without building."""

        def connect_change_publishers(self) -> None:
            publishing.connect_publishers(ReadyPublished)

        def build(self, name: str) -> object:
            pytest.fail(f"schema build should not be called for {name}")

    payloads: list[ChangePayload] = []

    def receiver(sender: type[models.Model], payload: ChangePayload, **kwargs: object) -> None:
        del sender, kwargs
        payloads.append(payload)

    created_models = _create_missing_tables((ReadyPublished,))
    from angee.graphql.apps import GraphQLConfig

    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: FakeSchemas()))
    monkeypatch.setattr(publishing, "_broadcast", lambda model, event: None)
    monkeypatch.setattr(publishing.transaction, "on_commit", lambda callback: callback())
    publishing.disconnect_publishers(ReadyPublished)
    publishing.change_published.connect(
        receiver,
        dispatch_uid="tests.graphql_ready_connects_publishers_without_schema_build",
    )
    try:
        GraphQLConfig("graphql", importlib.import_module("angee.graphql")).ready()
        ReadyPublished.objects.create(name="ready")
    finally:
        publishing.disconnect_publishers(ReadyPublished)
        publishing.change_published.disconnect(
            dispatch_uid="tests.graphql_ready_connects_publishers_without_schema_build",
        )
        _clear_model_tables((ReadyPublished,))
        if created_models:
            with connection.schema_editor() as editor_schema:
                editor_schema.delete_model(ReadyPublished)

    assert [payload.model for payload in payloads] == ["auth.ReadyPublished"]
    assert [payload.action for payload in payloads] == ["create"]


def test_ready_publisher_discovery_does_not_require_value_field_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ready-time publisher discovery must not depend on late field-type registrations."""

    class LateRegisteredDecimalField(models.DecimalField):
        """Value field whose GraphQL type is registered after publisher discovery."""

    class LateValuePublished(models.Model):
        """Change-published model carrying a late-registered value field."""

        name = models.CharField(max_length=40)
        amount = LateRegisteredDecimalField(max_digits=8, decimal_places=2)

        class Meta:
            app_label = "auth"

    @strawberry_django.type(LateValuePublished)
    class LateValuePublishedType:
        id: auto
        name: auto
        amount: auto

    @strawberry.type
    class LateValueQuery:
        placeholder: str = "ok"

    schemas = GraphQLSchemas(
        [
            SchemaAddon(
                {
                    "public": {
                        "query": (LateValueQuery,),
                        "subscription": (changes(LateValuePublished, field="lateValuePublishedChanged"),),
                        "types": (LateValuePublishedType,),
                    }
                }
            )
        ]
    )
    prior = field_type_map.pop(LateRegisteredDecimalField, None)
    publishing.disconnect_publishers(LateValuePublished)
    try:
        monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
        from angee.graphql.apps import GraphQLConfig

        GraphQLConfig("graphql", importlib.import_module("angee.graphql")).ready()

        assert _receiver_count(post_save, "angee-changes-auth.LateValuePublished-save") == 1
        assert schemas.change_publisher_models() == (LateValuePublished,)

        register_field_type(LateRegisteredDecimalField, decimal.Decimal)
        sdl = schemas.build(DEFAULT_SCHEMA_NAME).as_str()
    finally:
        publishing.disconnect_publishers(LateValuePublished)
        if prior is None:
            field_type_map.pop(LateRegisteredDecimalField, None)
        else:
            field_type_map[LateRegisteredDecimalField] = prior

    assert "amount: Decimal!" in sdl


def test_alias_imported_changes_declaration_connects_publisher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Publisher derivation follows changes() metadata, not source syntax."""

    class AliasPublished(models.Model):
        """Change-published model declared through an aliased changes import."""

        name = models.CharField(max_length=40)

        class Meta:
            app_label = "auth"

    module_name = "alias_graphql_app"
    package_dir = tmp_path / module_name
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "schema.py").write_text(
        textwrap.dedent(
            """
            from __future__ import annotations

            from django.apps import apps

            from angee.graphql.subscriptions import changes as publish

            AliasPublished = apps.get_model("auth", "AliasPublished")

            schemas = {
                "public": {
                    "subscription": [publish(AliasPublished, field="aliasPublishedChanged")],
                },
            }
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module(module_name)
    addon = AppConfig(module_name, module)
    addon.path = str(package_dir)
    addon._addon_contract = AddonContract(name=module_name, schemas="schema.schemas")
    schemas = GraphQLSchemas((addon,))
    publishing.disconnect_publishers(AliasPublished)
    try:
        monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
        from angee.graphql.apps import GraphQLConfig

        GraphQLConfig("graphql", importlib.import_module("angee.graphql")).ready()

        assert _receiver_count(post_save, "angee-changes-auth.AliasPublished-save") == 1
        assert schemas.change_publisher_models() == (AliasPublished,)
    finally:
        publishing.disconnect_publishers(AliasPublished)
        sys.modules.pop(f"{module_name}.schema", None)
        sys.modules.pop(module_name, None)


@pytest.mark.django_db
def test_change_payload_reads_fk_ids_without_fetching_relations() -> None:
    """Changed relation fields publish raw local ids without loading objects."""

    class EventParent(models.Model):
        """Parent model for relation payload tests."""

        name = models.CharField(max_length=64)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    class EventChild(models.Model):
        """Child model whose FK update should stay query-free."""

        parent = models.ForeignKey(EventParent, on_delete=models.CASCADE)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    child = EventChild(id=1, parent_id=42)

    with CaptureQueriesContext(connection) as captured:
        payload = ChangePayload.from_instance(
            child,
            action="update",
            update_fields=("parent",),
        )

    assert len(captured) == 0
    assert payload.changed_fields == ("parent",)
    assert payload.changed_values == {"parent": 42}


def test_json_safe_normalizes_nested_values() -> None:
    """Change payload values are converted to JSON-safe primitives."""

    when = datetime.datetime(2026, 5, 31, 12, 0, 0)

    assert json_safe({"when": [when]}) == {"when": ["2026-05-31T12:00:00"]}


def test_change_read_gate_passes_non_rebac_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Models without a REBAC resource type stream unchanged."""

    monkeypatch.setattr(access, "model_resource_type", lambda model: "")

    gate = ChangeReadGate(Group, anonymous_actor())

    event = gate.filter(payload())

    assert event is not None
    assert event.action == "update"
    assert event.changed_fields == ["name"]


def test_change_read_gate_uses_payload_resource_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subscription gating uses the REBAC id while exposing the public id."""

    checked: list[str] = []

    monkeypatch.setattr(access, "model_resource_type", lambda model: "group")
    monkeypatch.setattr(access, "gated_read_fields", lambda model: frozenset())
    monkeypatch.setattr(access, "backend", lambda: object())

    def check(*args: object, **kwargs: object) -> SimpleNamespace:
        checked.append(kwargs["resource"].resource_id)
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(access, "check_field_access", check)

    gate = ChangeReadGate(Group, anonymous_actor())
    event = gate.filter(payload(id="public-id", resource_id="rebac-id"))

    assert event is not None
    assert event.id == "public-id"
    assert checked == ["rebac-id"]


def test_change_read_gate_drops_unreadable_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows the actor cannot read are hidden."""

    monkeypatch.setattr(access, "model_resource_type", lambda model: "group")
    monkeypatch.setattr(access, "backend", lambda: object())
    monkeypatch.setattr(
        access,
        "check_field_access",
        lambda *args, **kwargs: SimpleNamespace(allowed=False),
    )

    gate = ChangeReadGate(Group, anonymous_actor())

    assert gate.filter(payload()) is None


def test_change_read_gate_redacts_denied_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Field-gated values are redacted when their field action denies."""

    monkeypatch.setattr(access, "model_resource_type", lambda model: "group")
    monkeypatch.setattr(access, "gated_read_fields", lambda model: {"secret"})
    monkeypatch.setattr(access, "backend", lambda: object())
    actions: list[str] = []

    def check(*args: object, **kwargs: object) -> SimpleNamespace:
        """Record the REBAC checks used by the subscription gate."""

        actions.append(kwargs["action"])
        return SimpleNamespace(allowed=kwargs["action"] != "read__secret")

    monkeypatch.setattr(access, "check_field_access", check)
    gate = ChangeReadGate(Group, anonymous_actor())

    event = gate.filter(
        payload(
            changed_fields=["name", "secret"],
            changed_values={"name": "editors", "secret": "hidden"},
        )
    )

    assert event is not None
    assert event.changed_fields == ["name"]
    assert event.changed_values == {"name": "editors"}
    assert actions == ["read", "read__secret"]


def test_change_read_gate_keeps_allowed_field_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Field-gated values remain in live payloads when the actor may read them."""

    monkeypatch.setattr(access, "model_resource_type", lambda model: "group")
    monkeypatch.setattr(access, "gated_read_fields", lambda model: {"secret"})
    monkeypatch.setattr(access, "backend", lambda: object())
    actions: list[str] = []

    def check(*args: object, **kwargs: object) -> SimpleNamespace:
        """Record the resource and field checks used by the subscription gate."""

        actions.append(kwargs["action"])
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(access, "check_field_access", check)
    gate = ChangeReadGate(Group, anonymous_actor())

    event = gate.filter(
        payload(
            changed_fields=["name", "secret"],
            changed_values={"name": "editors", "secret": "visible"},
        )
    )

    assert event is not None
    assert event.changed_fields == ["name", "secret"]
    assert event.changed_values == {"name": "editors", "secret": "visible"}
    assert actions == ["read", "read__secret"]
