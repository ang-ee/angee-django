"""Base write observation: the change hooks a model declares and the batches it publishes."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from django.db import models
from django.utils import timezone

from angee.base import signals
from angee.base.models.base import AngeeModel
from angee.workflows.testing.models import RunSubjectRecord
from tests.core_persistence import OwnedRow, TrashRow


@pytest.fixture
def observed() -> Iterator[list[tuple[str, tuple[str, ...] | None]]]:
    """Capture each published change of the probe models as ``(id, changed_fields)``."""

    payloads: list[tuple[str, tuple[str, ...] | None]] = []

    def receiver(sender: type[models.Model], payload: signals.ModelChange, **kwargs: object) -> None:
        payloads.append((payload.id, payload.changed_fields))

    for model in (OwnedRow, TrashRow):
        signals.change_published.connect(receiver, sender=model, dispatch_uid=f"tests.base_signals.{model.__name__}")
    try:
        yield payloads
    finally:
        for model in (OwnedRow, TrashRow):
            signals.change_published.disconnect(sender=model, dispatch_uid=f"tests.base_signals.{model.__name__}")


def test_angee_models_declare_the_change_hooks_with_neutral_defaults() -> None:
    """Every Angee row answers the hooks: no read anchor, no related records, no batch coalescing."""

    row = OwnedRow(pk=1)

    assert isinstance(row, AngeeModel)
    assert row.change_read_resource() is None
    assert row.change_related_records() == ()
    assert OwnedRow.change_batch_targets is None
    assert not issubclass(TrashRow, AngeeModel)


def test_bulk_publication_observes_the_batch_rows_unless_the_model_coalesces(
    monkeypatch: pytest.MonkeyPatch,
    observed: list[tuple[str, tuple[str, ...] | None]],
) -> None:
    """A batch publishes its own rows as partial updates; a declared coalescer publishes its targets in full."""

    signals.publish_bulk_update(TrashRow, instances=(TrashRow(pk=1),), update_fields=("title",))
    signals.publish_bulk_update(OwnedRow, instances=(OwnedRow(pk=2), OwnedRow(pk=3)), update_fields=("title",))
    assert observed == [("1", ("title",)), ("2", ("title",)), ("3", ("title",))]

    calls: list[tuple[tuple[int, ...], tuple[str, ...]]] = []

    def coalesce(cls: type[OwnedRow], instances: Any, *, update_fields: Any) -> tuple[OwnedRow, ...]:
        calls.append((tuple(row.pk for row in instances), tuple(update_fields)))
        return (OwnedRow(pk=9),)

    monkeypatch.setattr(OwnedRow, "change_batch_targets", classmethod(coalesce))
    observed.clear()
    signals.publish_bulk_update(OwnedRow, instances=(OwnedRow(pk=2), OwnedRow(pk=3)), update_fields=("title",))

    assert calls == [((2, 3), ("title",))]
    assert observed == [("9", None)]


def test_published_changes_copy_mutable_values_and_keep_immutable_ones() -> None:
    """A captured value never follows the row: mutable values are copied, immutable ones are kept as they are."""

    stamped = timezone.now()
    row = RunSubjectRecord(pk=1, settlements=[{"status": "done"}], updated_at=stamped)
    captured: list[Mapping[str, Any] | None] = []

    def receiver(sender: type[models.Model], payload: signals.ModelChange, **kwargs: object) -> None:
        captured.append(payload.changed_values)

    signals.change_published.connect(receiver, sender=RunSubjectRecord, dispatch_uid="tests.base_signals.values")
    try:
        signals.publish_change(row, action="update", update_fields=("settlements", "updated_at"))
    finally:
        signals.change_published.disconnect(sender=RunSubjectRecord, dispatch_uid="tests.base_signals.values")
    row.settlements[0]["status"] = "reopened"

    [values] = captured
    assert values is not None
    assert values["settlements"] == [{"status": "done"}]
    assert values["updated_at"] is stamped
