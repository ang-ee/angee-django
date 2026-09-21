"""Closing D31 seams preserve explicit writers and independent revision stores."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
import reversion
from django.core.exceptions import ValidationError
from django.db import connection, connections, models, router, transaction

from angee.base.fields import FractionalRankField
from angee.base.mixins import RevisionMixin
from angee.graphql import deletion
from angee.graphql.deletion import delete_by_public_id


class ClosingRankedRow(models.Model):
    """A native Django writer with no model save override."""

    lane = models.CharField(max_length=16)
    rank = FractionalRankField()

    class Meta:
        app_label = "tests"
        constraints = [models.UniqueConstraint(fields=("lane", "rank"), name="closing_ranked_lane_rank")]


class ClosingRevisionRow(RevisionMixin, models.Model):
    body = models.TextField()
    revisioned_fields = ("body",)

    class Meta:
        app_label = "tests"


class ClosingRouter:
    """Reject all implicit reads and distinguish the model from revision storage."""

    def __init__(self, writer: str, *, store: str | None = None) -> None:
        self.writer = writer
        self.store = store
        self.writes: list[type[models.Model]] = []

    def db_for_read(self, model: type[models.Model], **hints: Any) -> str:
        raise AssertionError(f"Unexpected read routing for {model._meta.label}.")

    def db_for_write(self, model: type[models.Model], **hints: Any) -> str:
        self.writes.append(model)
        return self.store if model is reversion.models.Revision and self.store is not None else self.writer


@pytest.fixture
def closing_writer(
    transactional_db: None, database_alias: Callable[[str], AbstractContextManager[str]],
) -> Iterator[str]:
    del transactional_db
    with connection.schema_editor() as editor:
        editor.create_model(ClosingRankedRow)
    try:
        with database_alias("closing_writer") as alias:
            yield alias
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(ClosingRankedRow)


def test_explicit_rank_allocation_avoids_presave_alias_blindness(
    closing_writer: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native save and bulk_create use preallocated ranks without routing again."""

    ClosingRankedRow.objects.using(closing_writer).create(lane="one", rank=4096.0)
    row = ClosingRankedRow(lane="one")
    field = ClosingRankedRow._meta.get_field("rank")
    routing = ClosingRouter("wrong_writer")
    monkeypatch.setattr(router, "routers", [routing])

    def reject_default(*args: Any) -> None:
        raise AssertionError("An explicitly allocated rank touched the default connection.")

    with connection.execute_wrapper(reject_default):
        row.rank = field.get_append_rank_for_instance(row, using=closing_writer)
        row.save(using=closing_writer)
        second = ClosingRankedRow(lane="one")
        second.rank = field.get_append_rank_for_instance(second, using=closing_writer)
        ClosingRankedRow.objects.using(closing_writer).bulk_create([second])
    assert (row.rank, second.rank) == (5120.0, 6144.0)
    assert routing.writes == []


@pytest.mark.parametrize("entry", ["router", "queryset", "using"])
def test_delete_preview_and_confirmation_share_the_writer(
    closing_writer: str, monkeypatch: pytest.MonkeyPatch, entry: str,
) -> None:
    """Preview, delete, callback and transaction honor the same chosen database."""

    row = ClosingRankedRow.objects.using(closing_writer).create(lane="one", rank=1024.0)
    routing = ClosingRouter(closing_writer if entry == "router" else "wrong_writer")
    monkeypatch.setattr(router, "routers", [routing])
    committed: list[str] = []
    kwargs = {}
    if entry == "queryset":
        kwargs["queryset"] = ClosingRankedRow.objects.using(closing_writer).filter(lane="one")
    elif entry == "using":
        kwargs.update(using=closing_writer, queryset=ClosingRankedRow.objects.using("wrong_queryset"))

    def before_delete(instance: models.Model) -> None:
        assert instance._state.db == closing_writer
        assert connections[closing_writer].in_atomic_block
        transaction.on_commit(lambda: committed.append(closing_writer), using=closing_writer)
        assert committed == []

    def reject_default(*args: Any) -> None:
        raise AssertionError("Deletion touched the default connection.")

    with connection.execute_wrapper(reject_default):
        result = delete_by_public_id(
            ClosingRankedRow, str(row.pk), confirm=True, before_delete=before_delete, **kwargs,
        )
    assert not result.has_blockers
    assert result.total_deleted_count == 1
    assert committed == [closing_writer]
    assert not ClosingRankedRow.objects.using(closing_writer).exists()
    assert routing.writes == ([ClosingRankedRow] if entry == "router" else [])


def test_revision_revert_selects_the_store_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit model override must never become the reversion store alias."""

    row = ClosingRevisionRow(pk=1, body="current")
    row._state.adding = False
    row._state.db = "old_model_writer"
    routing = ClosingRouter("wrong_model_writer", store="revision_store")
    monkeypatch.setattr(router, "routers", [routing])
    entered: list[tuple[str, str]] = []
    saved: list[tuple[str, set[str], str]] = []

    @contextmanager
    def model_transaction(*, using: str) -> Iterator[None]:
        entered.append(("model", using))
        yield

    @contextmanager
    def revision_transaction(*, using: str) -> Iterator[None]:
        entered.append(("store", using))
        yield

    def save(instance: ClosingRevisionRow, *, using: str, update_fields: set[str]) -> None:
        saved.append((using, update_fields, instance.body))

    comments: list[str] = []
    monkeypatch.setattr(transaction, "atomic", model_transaction)
    monkeypatch.setattr(reversion, "create_revision", revision_transaction)
    monkeypatch.setattr(reversion, "set_comment", comments.append)
    monkeypatch.setattr(ClosingRevisionRow, "save", save)
    row.revert_to(SimpleNamespace(field_dict={"body": "restored"}, revision_id=9), using="model_override")

    assert entered == [("model", "model_override"), ("store", "revision_store")]
    assert saved == [("model_override", {"body"}, "restored")]
    assert comments == ["Reverted to revision 9."]
    assert routing.writes == [reversion.models.Revision]


def test_authorized_delete_rejects_unroutable_rebac_before_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """An alias-bound collector cannot bind REBAC's native pre-delete check."""

    monkeypatch.setattr(deletion, "model_resource_type", lambda model: "tests/ranked")

    def reject_lookup(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Unsupported authorization reached the row lookup.")

    monkeypatch.setattr(deletion, "require_instance_for_id", reject_lookup)
    with pytest.raises(ValidationError, match="default authorization database"):
        delete_by_public_id(ClosingRankedRow, "1", confirm=True, using="writer")


def test_revision_listing_binds_store_and_model_identity_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """The native version queryset receives the store binding and Version.db filter."""

    row = ClosingRevisionRow(pk=1)
    row._state.adding = False
    row._state.db = "model_writer"
    routing = ClosingRouter("wrong_model_writer", store="revision_store")
    monkeypatch.setattr(router, "routers", [routing])
    received: list[tuple[str, models.Model, str]] = []

    def get_for_object(queryset: Any, obj: models.Model, model_db: str | None = None) -> Any:
        received.append((queryset.db, obj, model_db))
        return queryset.filter(db=model_db, object_id=obj.pk)

    monkeypatch.setattr(reversion.models.VersionQuerySet, "get_for_object", get_for_object)
    versions = row.revisions
    assert versions.db == "revision_store"
    assert received == [("revision_store", row, "model_writer")]
    assert routing.writes == [reversion.models.Revision]
