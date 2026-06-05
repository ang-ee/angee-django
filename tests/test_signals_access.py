"""Tests for change publishers, audit stamping, and read gates."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection, models
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, anonymous_actor

from angee.base.mixins import AuditMixin
from angee.base.serialization import json_safe
from angee.graphql import access, publishing
from angee.graphql.access import ChangeReadGate
from angee.graphql.events import ChangePayload


class AuditStamped(AuditMixin, models.Model):
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

    with connection.schema_editor() as editor_schema:
        editor_schema.create_model(AuditStamped)
    try:
        with actor_context(creator):
            row = AuditStamped.objects.create(id="known", name="first")
        row.refresh_from_db()
        assert row.created_by_id == creator.pk
        assert row.updated_by_id == creator.pk

        with actor_context(editor):
            row.name = "second"
            row.save(update_fields={"name"})
        row.refresh_from_db()
        assert row.created_by_id == creator.pk
        assert row.updated_by_id == editor.pk
    finally:
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
    group = Group(id=7, name="editors")

    publishing._publish(group, action="update", update_fields=("name",))

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
    """Field-gated values are removed when field reads are denied."""

    monkeypatch.setattr(access, "model_resource_type", lambda model: "group")
    monkeypatch.setattr(access, "gated_read_fields", lambda model: {"secret"})
    monkeypatch.setattr(access, "backend", lambda: object())

    def check(*args: object, **kwargs: object) -> SimpleNamespace:
        """Allow row reads and deny secret field reads."""

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
