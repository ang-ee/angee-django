"""Base write observation: the change hooks a model declares and the batches it publishes."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.db import models

from angee.base import signals
from angee.base.models.base import AngeeModel
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
