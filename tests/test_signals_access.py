"""Tests for change publishers, revision registration, and read gates."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any

import pytest
import reversion
from django.contrib.auth.models import Group
from django.db import models
from rebac import anonymous_actor

from angee.base import access, signals
from angee.base.access import ChangeReadGate
from angee.base.mixins import RevisionMixin


class RevisionRegistered(RevisionMixin, models.Model):
    """Concrete model used to test reversion registration."""

    revisioned_fields = ("body",)

    body = models.TextField()

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


def test_register_revision_models_registers_declared_fields() -> None:
    """Models declaring revisioned fields are registered with reversion."""

    if reversion.is_registered(RevisionRegistered):
        reversion.unregister(RevisionRegistered)
    try:
        signals.register_revision_models()

        assert reversion.is_registered(RevisionRegistered)
    finally:
        if reversion.is_registered(RevisionRegistered):
            reversion.unregister(RevisionRegistered)


def test_connect_publishers_is_idempotent() -> None:
    """A model is connected to change publishers once."""

    signals._connected.discard(Group)

    signals.connect_publishers(Group)
    signals.connect_publishers(Group)

    assert Group in signals._connected


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

    monkeypatch.setattr(signals, "_broadcast", broadcast)
    monkeypatch.setattr(
        signals.transaction,
        "on_commit",
        lambda callback: callback(),
    )
    group = Group(id=7, name="editors")

    signals._publish(group, action="update", update_fields=("name",))

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


def test_json_safe_normalizes_nested_values() -> None:
    """Change payload values are converted to JSON-safe primitives."""

    when = datetime.datetime(2026, 5, 31, 12, 0, 0)

    assert signals._json_safe({"when": [when]}) == {
        "when": ["2026-05-31T12:00:00"]
    }


def test_change_read_gate_passes_non_rebac_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Models without a REBAC resource type stream unchanged."""

    monkeypatch.setattr(access, "model_resource_type", lambda model: "")

    gate = ChangeReadGate(Group, anonymous_actor())

    assert gate.filter(payload()) == payload()


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
    assert event["changed_fields"] == ["name"]
    assert event["changed_values"] == {"name": "editors"}
