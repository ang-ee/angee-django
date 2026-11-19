"""The generic driver commits domain rows with their opaque stream positions."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo
from threading import Barrier, Lock
from typing import Any, ClassVar

import pytest
from django.core.exceptions import ValidationError
from django.db import (
    DataError,
    OperationalError,
    close_old_connections,
    connection,
    connections,
    models,
    transaction,
)
from django.db.models.signals import pre_delete
from django.utils import timezone
from hypothesis import example, given, settings
from hypothesis import strategies as st
from rebac import system_context

from angee.base.models.merge_references import has_live_references
from angee.base.serialization import canonical_json
from angee.integrate import streams as stream_driver
from angee.integrate.errors import IntegrationError
from angee.integrate.impl import AdapterContractError, BridgeImpl
from angee.integrate.models import merge_json_state
from angee.integrate.states import (
    EXTERNAL_KEY_LENGTH,
    LinkStatus,
    StreamDefinition,
    StreamDirection,
    StreamKind,
    StreamPhase,
    StreamState,
    SyncFailureKind,
    SyncFailureStatus,
)
from angee.integrate.streams import (
    ApplyResult,
    BridgeSyncError,
    ChangeKind,
    CursorInvalid,
    LocalChange,
    PartitionFailure,
    RecordChange,
    SemanticError,
    StreamPage,
    WriteBackResult,
    advance_stream,
    begin_stream_cycle,
    classify_change,
    open_stream,
    push_stream,
    reconcile_stream,
    reset_stream,
    sync_bridge,
)
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.messaging.backends import ChannelBackend, ParsedMessage, ParsedPart
from angee.messaging.testing.models import Channel, Message
from tests.conftest import current_stream, make_integration
from tests.stream_adapters import applied_history, failure_state

pytestmark = pytest.mark.django_db(transaction=True)


class AppliedRecord(models.Model):
    """Domain-owned idempotent sink independent of the record protocol tables."""

    key = models.CharField(max_length=80, unique=True)
    payload = models.JSONField(default=dict)

    class Meta:
        app_label = "integrate"
        db_table = "test_integrate_applied_record"


@pytest.fixture
def stream_bridge(db: None) -> Iterator[Channel]:
    """Run transport tests outside any enclosing Django transaction."""

    with system_context(reason="test stream protocol"):
        yield make_integration("stream-protocol", model=Channel)


@dataclass
class MemoryAdapter(BridgeImpl):
    """Deterministic transport and native idempotent domain sink for driver tests."""

    pages: list[StreamPage | Exception] = field(default_factory=list)
    semantic_key: str = ""
    infrastructure_key: str = ""
    inventory: tuple[str, ...] = ()
    candidates: tuple[LocalChange, ...] = ()
    applied: list[str] = field(default_factory=list)
    written: list[tuple[str, Any, str]] = field(default_factory=list)
    extracted: int = 0
    closed: int = 0
    sync_parallelism: int | None = 1

    def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
        return (StreamDefinition("records"),)

    def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        assert not connection.in_atomic_block
        assert page_bound > 0
        self.extracted += 1
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    def apply_record(self, stream: Any, record: Any) -> ApplyResult:
        assert connection.in_atomic_block
        key = record.external_key if isinstance(record, RecordChange) else record
        payload = record.source_payload if isinstance(record, RecordChange) else {"event": key}
        row, _ = AppliedRecord.objects.update_or_create(key=key, defaults={"payload": payload})
        self.applied.append(key)
        if key == self.semantic_key:
            raise SemanticError("invalid_name", details={"key": key})
        if key == self.infrastructure_key:
            raise OperationalError("database unavailable")
        return ApplyResult(
            target=row,
            mapped_payload=payload,
            dependency_digest=record.dependency_digest if isinstance(record, RecordChange) else "",
        )

    def enumerate_keys(self, stream: Any, *, after: str | None = None) -> Iterable[str]:
        assert not connection.in_atomic_block
        start = 0 if after is None else self.inventory.index(after) + 1
        yield from self.inventory[start:]

    def finish_page(self, stream: Any, page: StreamPage, outcomes: Any) -> None:
        assert connection.in_atomic_block

    def local_changes(self, stream: Any, *, keys: frozenset[str] | None = None) -> Iterable[LocalChange]:
        assert not connection.in_atomic_block
        return [candidate for candidate in self.candidates if keys is None or candidate.external_key in keys]

    def write_back(
        self,
        link: Any,
        projection: Any,
        *,
        expected_version: str,
    ) -> WriteBackResult:
        assert not connection.in_atomic_block
        self.written.append((link.external_key, projection, expected_version))
        return WriteBackResult("v2", "changed", projection)

    def close(self) -> None:
        self.closed += 1


@dataclass
class ReadKeysAdapter(MemoryAdapter):
    """Optional identity transport; the base adapter deliberately omits it."""

    supports_identity_reads: ClassVar[bool] = True
    remote: dict[str, RecordChange] = field(default_factory=dict)
    reads: list[tuple[str, ...]] = field(default_factory=list)

    def read_keys(self, stream: Any, keys: Sequence[str]) -> Iterable[RecordChange]:
        assert not connection.in_atomic_block
        self.reads.append(tuple(keys))
        return tuple(self.remote[key] for key in sorted(keys))


def finish_sweep(stream: SyncStream, adapter: MemoryAdapter, *, page_bound: int = 100) -> int:
    """Drain bounded pulses for tests that assert a completed inventory sweep."""

    changed = 0
    for _ in range(30):
        changed += reconcile_stream(stream, adapter, page_bound=page_bound)
        stream.refresh_from_db()
        if not stream.reconcile_state:
            return changed
    raise AssertionError("The bounded sweep did not finish.")


_JSON = st.recursive(
    st.none() | st.booleans() | st.integers(-2, 2) | st.text("ab", max_size=2),
    lambda values: st.lists(values, max_size=3) | st.dictionaries(st.text("ab", max_size=2), values, max_size=3),
    max_leaves=6,
)


SIDES = ("base", "edit", "other")


def _as_tuples(value: Any) -> Any:
    """Spell every array of a JSON value as a tuple, as a Python projection may."""
    if isinstance(value, dict):
        return {key: _as_tuples(item) for key, item in value.items()}
    if isinstance(value, list):
        return tuple(_as_tuples(item) for item in value)
    return value


@pytest.mark.django_db
@settings(max_examples=50, deadline=None)
@given(
    values=st.tuples(_JSON, _JSON, _JSON), local=st.sampled_from(SIDES), remote=st.sampled_from(SIDES),
    tombstone=st.booleans(), mapping_changed=st.booleans(),
)
@example(values=([1], [2], [3]), local="edit", remote="edit", tombstone=False, mapping_changed=False)  # same edit
@example(values=([1], [2], [3]), local="edit", remote="edit", tombstone=False, mapping_changed=True)  # remapped
@example(values=([1], [2], None), local="edit", remote="other", tombstone=True, mapping_changed=False)  # deleted
@example(values=([1], [2], [3]), local="edit", remote="other", tombstone=False, mapping_changed=False)  # diverged
@example(values=([1], [2], [3]), local="edit", remote="base", tombstone=False, mapping_changed=False)  # local only
@example(values=([1], [2], [3]), local="base", remote="base", tombstone=False, mapping_changed=True)  # remap only
@example(values=([1], [2], [3]), local="base", remote="base", tombstone=True, mapping_changed=False)  # known delete
def test_classify_change_decides_by_which_side_moved_from_the_base(
    values: tuple[Any, Any, Any], local: str, remote: str, tombstone: bool, mapping_changed: bool
) -> None:
    """A side moved when its canonical JSON differs from the base, however a projection spells it.

    A mapping change is a remote move, so it never leaves a record unchanged. A
    local edit beside a tombstone or a mapping change always needs a choice, even
    when the values agree; the same plain edit on both sides applies. A tombstone
    the base already holds, with no local edit, changes nothing.
    """
    side = dict(zip(SIDES, values))
    spelled = {name: canonical_json(value) for name, value in side.items()}
    flags = {"tombstone": tombstone, "mapping_changed": mapping_changed}
    kind = classify_change(base=side["base"], local=side[local], remote=side[remote], **flags)
    local_moved, remote_moved = (spelled[name] != spelled["base"] for name in (local, remote))
    if not local_moved:
        expected = ChangeKind.APPLY if remote_moved or mapping_changed else ChangeKind.UNCHANGED
    elif tombstone or mapping_changed or remote_moved and spelled[local] != spelled[remote]:
        expected = ChangeKind.CONFLICT
    else:
        expected = ChangeKind.APPLY if remote_moved else ChangeKind.WRITE_BACK
    assert kind is expected
    respelled = {"local": _as_tuples(side[local]), "remote": _as_tuples(side[remote])}
    assert classify_change(base=side["base"], **respelled, **flags) is kind


@pytest.mark.parametrize(
    "completed,prior_revision,tombstone,direction,adopted",
    [
        (False, False, False, StreamDirection.BIDIRECTIONAL, True),
        (True, False, False, StreamDirection.BIDIRECTIONAL, True),
        (False, True, False, StreamDirection.BIDIRECTIONAL, False),
        (False, False, True, StreamDirection.BIDIRECTIONAL, False),
        (False, False, False, StreamDirection.PULL, False),
    ],
)
def test_baseline_adoption_preserves_established_bases_and_delete_conflicts(
    stream_bridge: Channel,
    completed: bool,
    prior_revision: bool,
    tombstone: bool,
    direction: StreamDirection,
    adopted: bool,
) -> None:
    stream = current_stream(stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, direction=direction)
    row = AppliedRecord.objects.create(key="one", payload={"name": "local"})
    if prior_revision:
        link = RecordLink.objects.observe(stream, "one", target=row)
        RecordLink.objects.promote(link, source_payload={}, source_hash="base", mapped_payload={}, remote_version="v1")
    if completed:
        SyncStream.objects.advance(stream, {}, exhausted=True)
    stream = reset_stream(stream, cursor={}).stream
    record = RecordChange(
        "one",
        {"name": "remote"},
        "" if tombstone else "remote",
        "v2",
        target=row,
        tombstone=tombstone,
        mapped_payload={"name": "remote"},
        projection=row.payload,
    )
    adapter = MemoryAdapter(pages=[StreamPage((record,), {})])

    result = advance_stream(stream, adapter)

    row.refresh_from_db()
    assert result.count == int(adopted)
    assert row.payload == {"name": "remote" if adopted else "local"}
    assert not adapter.written
    if adopted:
        assert not result.failure_ids
        link = RecordLink.objects.get(stream=stream, external_key="one")
        assert (link.applied_base.mapped_payload, link.applied_base.source_hash, link.remote_version) == (
            {"name": "remote"},
            "remote",
            "v2",
        )
    else:
        failure = next(
            (
                row
                for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
                if row.id == result.failure_ids[0]
            )
        )
        assert (failure.kind, failure.code) == (SyncFailureKind.CONFLICT, "both_changed")


@pytest.mark.parametrize("tombstone", [False, True])
def test_repeated_page_identity_uses_earlier_revision_or_conflict(stream_bridge: Channel, tombstone: bool) -> None:
    stream = current_stream(
        stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    row = AppliedRecord.objects.create(key="one", payload={"name": "local"})
    first = RecordChange(
        "one",
        {"name": "remote"},
        "" if tombstone else "remote",
        target=row,
        tombstone=tombstone,
        mapped_payload={"name": "remote"},
        projection=row.payload,
    )
    second = RecordChange(
        "one", {"name": "newer"}, "newer", target=row, mapped_payload={"name": "newer"}, projection=row.payload
    )
    adapter = MemoryAdapter(pages=[StreamPage((first, second), {})])

    result = advance_stream(stream, adapter)

    row.refresh_from_db()
    assert result.count == int(not tombstone)
    assert row.payload == {"name": "local" if tombstone else "remote"}
    assert applied_history().count() == int(not tombstone)
    conflict = next(
        (
            row
            for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
            if row.kind == SyncFailureKind.CONFLICT
        )
    )
    assert conflict.attempts == 1
    assert set(result.failure_ids) == {conflict.pk}
    assert not adapter.written


def test_baseline_completion_during_extraction_retries_without_adopting(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = current_stream(
        stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    row = AppliedRecord.objects.create(key="one", payload={"name": "local"})
    record = RecordChange(
        "one",
        {"name": "remote"},
        "remote",
        "v1",
        target=row,
        mapped_payload={"name": "remote"},
        projection=row.payload,
    )
    page = StreamPage((record,), {})
    adapter = MemoryAdapter(pages=[page, page])
    has_completed_baseline = stream.has_completed_baseline

    def complete_on_another_handle() -> bool:
        completed = has_completed_baseline()
        peer = SyncStream.objects.get(pk=stream.pk)
        SyncStream.objects.advance(peer, peer.cursor, exhausted=True)
        return completed

    with monkeypatch.context() as patch:
        patch.setattr(stream, "has_completed_baseline", complete_on_another_handle)
        with pytest.raises(RuntimeError, match="Stream state changed during external work; retry the operation"):
            advance_stream(stream, adapter)

    row.refresh_from_db()
    assert row.payload == {"name": "local"}
    assert not adapter.applied and not adapter.written
    assert not RecordLink.objects.exists()
    assert not applied_history().exists()
    assert not bool(SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False))
    stream.refresh_from_db()
    assert stream.phase == StreamPhase.DELTA
    assert stream.cursor == {}

    result = advance_stream(stream, adapter)

    assert result.count == 0
    assert not adapter.applied and not adapter.written
    conflict = next(
        (
            row
            for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
            if row.id == result.failure_ids[0]
        )
    )
    assert (conflict.kind, conflict.code) == (SyncFailureKind.CONFLICT, "both_changed")


@pytest.mark.parametrize("reconcile", [False, True])
@pytest.mark.parametrize(
    "page,message",
    [
        (None, "extract must return StreamPage"),
        (StreamPage(None, {}), "records must be a sequence"),
        (StreamPage(("one", "two"), {}), "extract exceeded page_bound"),
    ],
)
def test_extraction_contract_is_checked_before_page_application(
    stream_bridge: Channel, reconcile: bool, page: Any, message: str
) -> None:
    stream = current_stream(stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, reconcile_interval=timedelta(0))
    adapter = MemoryAdapter(pages=[page])

    with pytest.raises(AdapterContractError, match=message):
        if reconcile:
            reconcile_stream(stream, adapter, page_bound=1)
        else:
            advance_stream(stream, adapter, page_bound=1)

    stream.refresh_from_db()
    assert stream.cursor == {}
    assert not RecordLink.objects.filter(stream=stream).exists()


@pytest.mark.parametrize("push", [False, True])
def test_invalid_write_result_never_promotes_record_bases(stream_bridge: Channel, push: bool) -> None:
    class InvalidWriteAdapter(MemoryAdapter):
        def write_back(self, link: Any, projection: Any, *, expected_version: str) -> Any:
            return None

    stream = current_stream(
        stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    link = RecordLink.objects.observe(stream, "person:1")
    RecordLink.objects.promote(link, source_payload={}, source_hash="base", mapped_payload={}, remote_version="v1")
    SyncStream.objects.advance(stream, {}, exhausted=True)
    adapter = InvalidWriteAdapter(
        pages=[
            StreamPage(
                (RecordChange("person:1", {}, "base", "v1", mapped_payload={}, projection={"value": "changed"}),),
                {"offset": 1},
            )
        ],
        candidates=(LocalChange("person:1", {"value": "changed"}),),
    )

    with pytest.raises(AdapterContractError, match="write_back must return WriteBackResult"):
        if push:
            push_stream(stream, adapter)
        else:
            advance_stream(stream, adapter)

    stream.refresh_from_db()
    link.refresh_from_db()
    assert stream.cursor == {}
    assert (link.applied_base.source_hash, link.applied_base.mapped_payload, link.remote_version) == ("base", {}, "v1")


@pytest.mark.parametrize("trigger", ["invalid_cursor", "stream_flag", "page_flag"])
def test_epoch_reset_retains_identity_and_reverifies_baseline(stream_bridge: Channel, trigger: str) -> None:
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    link = RecordLink.objects.observe(stream, "person:1")
    RecordLink.objects.promote(
        link,
        source_payload={"name": "Ada"},
        source_hash="base",
        mapped_payload={"name": "Ada"},
    )
    adapter = MemoryAdapter()
    if trigger == "invalid_cursor":
        adapter.pages = [CursorInvalid()]
    elif trigger == "page_flag":
        adapter.pages = [StreamPage((), {}, resync_required=True)]
    else:
        SyncStream.objects.filter(pk=stream.pk).update(resync_required=True)
    reset = advance_stream(stream, adapter)
    assert reset.reset
    assert reset.stream.generation == stream.generation + 1
    assert reset.stream.phase == StreamPhase.BASELINE
    assert reset.stream.cursor == {}
    assert adapter.extracted == (0 if trigger == "stream_flag" else 1)
    link.refresh_from_db()
    assert link.stream_id == reset.stream.pk
    assert link.last_verified_generation == stream.generation
    adapter.pages = [
        StreamPage(
            (
                RecordChange(
                    "person:1",
                    {"name": "Ada"},
                    "base",
                    mapped_payload={"name": "Ada"},
                    projection={"name": "Ada"},
                ),
            ),
            {"done": True},
        )
    ]
    advance_stream(reset.stream, adapter)
    link.refresh_from_db()
    assert link.last_verified_generation == reset.stream.generation
    assert RecordLink.objects.count() == applied_history().count() == 1


@pytest.mark.parametrize("fault", ["stalled", "reset"])
def test_native_sync_rejects_stalled_pages_and_repeated_resets(
    stream_bridge: Channel,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """The native drain shares PageResult's bounded continuation contract."""
    adapter = MemoryAdapter(
        pages=[CursorInvalid(), CursorInvalid()]
        if fault == "reset"
        else [
            StreamPage((), {}, exhausted=False),
            StreamPage((), {}, exhausted=False),
        ]
    )
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))

    with pytest.raises(BridgeSyncError, match="records: Integration operation failed") as refused:
        sync_bridge(stream_bridge)

    cause = refused.value.__cause__
    assert isinstance(cause, RuntimeError if fault == "reset" else AdapterContractError)
    assert ("fresh baseline" if fault == "reset" else "repeated a page") in str(cause)
    assert adapter.extracted == 2 and adapter.closed == 1


def test_sweep_counts_absence_then_retains_confirmed_tombstone(stream_bridge: Channel) -> None:
    stream = current_stream(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        reconcile_interval=timedelta(0),
        absence_threshold=2,
    )
    missing = RecordLink.objects.observe(stream, "missing")
    present = RecordLink.objects.observe(stream, "present")
    adapter = ReadKeysAdapter(
        inventory=("present",), remote={"present": RecordChange("present", {}, "present", mapped_payload={})}
    )
    assert finish_sweep(stream, adapter) == 1
    missing.refresh_from_db()
    assert (missing.absence_count, missing.status) == (1, LinkStatus.UNAVAILABLE)
    adapter.remote["present"] = replace(adapter.remote["present"], projection={})
    assert finish_sweep(stream, adapter) == 1
    missing.refresh_from_db()
    present.refresh_from_db()
    assert (missing.absence_count, missing.status) == (2, LinkStatus.TOMBSTONE)
    assert missing.tombstoned_at is not None
    assert present.absence_count == 0
    assert RecordLink.objects.count() == 2


@pytest.mark.parametrize("changed_evidence", [{"mapping_version": 2}, {"dependency_digest": "updated"}])
def test_mapping_evidence_change_reapplies_unchanged_source(
    stream_bridge: Channel, changed_evidence: dict[str, Any]
) -> None:
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    record = RecordChange("person:1", {"name": "Ada"}, "source", mapped_payload={"name": "Ada"})
    adapter = MemoryAdapter(pages=[StreamPage((record,), {"page": 1})])
    advance_stream(stream, adapter)
    changed_record = {name: value for name, value in changed_evidence.items() if name != "mapping_version"}
    if "mapping_version" in changed_evidence:
        stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, mapping_version=2)
    adapter.pages = [StreamPage((replace(record, **changed_record, projection={"name": "Ada"}),), {"page": 2})]
    assert advance_stream(stream, adapter).count == 1
    assert adapter.applied == ["person:1", "person:1"]
    assert applied_history().count() == 2
    revision = applied_history().order_by("-history_date").first()
    for name, expected in changed_evidence.items():
        assert getattr(revision, name) == expected


def test_driver_promotes_declared_mapping_without_apply_result_echo(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, mapping_version=7)
    record = RecordChange(
        "person:1",
        {"name": "Ada"},
        "source",
        mapped_payload={"name": "Ada"},
    )
    adapter = MemoryAdapter(pages=[StreamPage((record,), {"page": 1})])
    assert advance_stream(stream, adapter).count == 1
    link = RecordLink.objects.get(stream=stream)
    assert link.mapping_version == 7
    adapter.pages = [StreamPage((replace(record, projection={"name": "Ada"}),), {"page": 2})]
    assert advance_stream(stream, adapter).count == 0
    assert adapter.applied == ["person:1"]
    assert applied_history().count() == 1


@pytest.mark.parametrize("kind", [StreamKind.RECORD_REPLICA, StreamKind.EVENT_FEED])
def test_quarantine_stamps_stream_mapping_version(stream_bridge, kind):
    stream = current_stream(stream_bridge, "quarantine-version", kind=kind, mapping_version=7)
    record = RecordChange("poison", {}, "source", mapped_payload={})
    failure = stream_driver._quarantine(stream, record, SemanticError("unmappable"), record_key="poison")
    assert failure.mapping_version == 7


def test_mapping_rollback_during_extraction_refuses_stale_page(stream_bridge, monkeypatch):
    stream = current_stream(stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, mapping_version=2)
    record = RecordChange("one", {"name": "Ada"}, "source", mapped_payload={"name": "Ada"})
    adapter = MemoryAdapter(pages=[StreamPage((record,), {"done": True})])
    extract = adapter.extract

    def rollback(*args, **kwargs):
        page = extract(*args, **kwargs)
        current_stream(stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, mapping_version=1)
        return page

    monkeypatch.setattr(adapter, "extract", rollback)
    with pytest.raises(RuntimeError, match="Stream state changed during external work"):
        advance_stream(stream, adapter)
    assert not adapter.applied and not RecordLink.objects.exists()


@pytest.mark.parametrize("snapshot", [False, True])
def test_new_mapping_declaration_replays_applied_rows_once(stream_bridge: Channel, snapshot: bool) -> None:
    class MappingAdapter(MemoryAdapter):
        mapping_version = 1

        def streams(self, *, deadline=None):
            return (StreamDefinition(
                "records", kind=StreamKind.RECORD_REPLICA, snapshot=snapshot, mapping_version=self.mapping_version,
            ),)

        def extract(self, stream, page_bound, *, deadline=None):
            # A delta cursor never redelivers this unchanged remote row.
            target = AppliedRecord.objects.filter(key="person:1").first()
            records = () if stream.cursor else (RecordChange(
                "person:1", {"name": "Ada"}, "unchanged-source", mapped_payload={"name": "Ada"},
                target=target, projection=target.payload if target else None,
            ),)
            return StreamPage(records, {"done": True})

    adapter = MappingAdapter()
    adapter.integration = stream_bridge
    initial = open_stream(stream_bridge, "records", "", adapter)
    assert advance_stream(initial, adapter).count == 1
    link = RecordLink.objects.get(stream=initial)
    target_id = link.target_object_id

    adapter.mapping_version = 2
    upgraded = open_stream(stream_bridge, "records", "", adapter)
    assert upgraded.generation == initial.generation + 1
    assert upgraded.mapping_version == 2 and upgraded.cursor == {}
    assert upgraded.phase == StreamPhase.BASELINE
    assert open_stream(stream_bridge, "records", "", adapter).pk == upgraded.pk

    # A failed first page retains the requested generation for the next open.
    adapter.infrastructure_key = "person:1"
    with pytest.raises(OperationalError, match="database unavailable"):
        advance_stream(upgraded, adapter)
    retried = open_stream(stream_bridge, "records", "", adapter)
    assert retried.pk == upgraded.pk and retried.cursor == {}
    adapter.infrastructure_key = ""
    assert advance_stream(retried, adapter).count == 1
    link.refresh_from_db()
    assert link.stream_id == upgraded.pk and link.target_object_id == target_id
    assert link.last_verified_generation == upgraded.generation
    assert link.mapping_version == 2 and applied_history().count() == 2

    # Reopening the same declaration or an older deployment never resets progress.
    for version in (2, 1):
        adapter.mapping_version = version
        current = open_stream(stream_bridge, "records", "", adapter)
        assert (current.pk, current.mapping_version, current.cursor) == (upgraded.pk, version, {"done": True})
    assert SyncStream.objects.count() == 2 and RecordLink.objects.count() == 1

    # An explicit later reset carries the mapping, rather than triggering another upgrade.
    reset = SyncStream.objects.bump_generation(current)
    assert reset.mapping_version == 1
    assert open_stream(stream_bridge, "records", "", adapter).pk == reset.pk
    adapter.mapping_version = 2
    restored = open_stream(stream_bridge, "records", "", adapter)
    assert restored.generation == reset.generation + 1
    assert restored.mapping_version == 2 and restored.cursor == {}


@pytest.mark.parametrize("snapshot", [False, True])
@pytest.mark.parametrize("quarantined", [False, True])
@pytest.mark.parametrize("applied_versions", [(), (4, 4), (4, 3), (5, 5)])
def test_deploy_seeds_unknown_stream_mapping_before_deciding_to_replay(
    stream_bridge: Channel,
    snapshot: bool,
    applied_versions: tuple[int, ...],
    quarantined: bool,
) -> None:
    """Existing progress survives deployment unless at least one applied link is older."""

    definition = StreamDefinition("records", kind=StreamKind.RECORD_REPLICA, snapshot=snapshot, mapping_version=4)
    fields = definition.stream_fields()
    fields["mapping_version"] = 1  # Seed legacy application evidence before simulating deployment.
    initial = SyncStream.objects.create(integration_id=stream_bridge.pk, **fields)
    records = tuple(RecordChange(
        f"person:{index}", {"name": f"Person {index}"}, "unchanged-source",
        mapped_payload={"name": f"Person {index}"},
    ) for index, _version in enumerate(applied_versions))
    if quarantined:
        records += (RecordChange("poison", {"name": "Invalid"}, "refused-source", mapped_payload={"name": "Invalid"}),)
    advance_stream(initial, MemoryAdapter(pages=[StreamPage(records, {"done": True})], semantic_key="poison"))
    if quarantined:
        refused = RecordLink.objects.get(stream=initial, external_key="poison")
        assert refused.applied_at is None and refused.mapping_version == 1
        assert refused.status == LinkStatus.FAILED
    for index, version in enumerate(applied_versions):
        RecordLink.objects.filter(stream=initial, external_key=f"person:{index}").update(mapping_version=version)
    SyncStream.objects.filter(pk=initial.pk).update(mapping_version=None)
    initial.refresh_from_db()
    assert initial.mapping_version is None and initial.phase == StreamPhase.DELTA
    if snapshot:
        assert initial.state == StreamState.CLOSED

    current = SyncStream.objects.current(stream_bridge, definition)
    should_replay = bool(applied_versions and min(applied_versions) < definition.mapping_version)
    assert current.generation == initial.generation + int(should_replay)
    assert current.mapping_version == definition.mapping_version
    assert current.cursor == ({} if should_replay else {"done": True})
    assert current.phase == (StreamPhase.BASELINE if should_replay else StreamPhase.DELTA)
    assert current.state == (StreamState.OPEN if should_replay else initial.state)
    assert SyncStream.objects.current(stream_bridge, definition).pk == current.pk
    assert SyncStream.objects.count() == 1 + int(should_replay)
    assert set(RecordLink.objects.values_list("stream_id", flat=True)) == ({current.pk} if records else set())


@dataclass
class ComposedMappingAdapter(MemoryAdapter):
    """Declares a plain or composed mapping; a baseline redelivers two unchanged rows, a delta cursor none."""

    mapping_version: int = 1
    contributions: tuple[tuple[str, int], ...] = ()

    def streams(self, *, deadline=None):
        return (StreamDefinition(
            "records", kind=StreamKind.RECORD_REPLICA,
            mapping_version=self.mapping_version, mapping_contributions=self.contributions,
        ),)

    def extract(self, stream, page_bound, *, deadline=None):
        if stream.cursor:
            return StreamPage((), {"done": True})
        records = []
        for key in ("person:1", "person:2"):
            target = AppliedRecord.objects.filter(key=key).first()
            records.append(RecordChange(
                key, {"name": key}, "unchanged-source", mapped_payload={"name": key},
                target=target, projection=target.payload if target else None,
            ))
        return StreamPage(tuple(records), {"done": True})


def drain_records(bridge: Channel, adapter: ComposedMappingAdapter) -> tuple[Any, int]:
    """Open the declared stream and drain it; return its current generation and the records it applied."""

    stream = open_stream(bridge, "records", "", adapter)
    result = advance_stream(stream, adapter)
    assert result.exhausted
    return result.stream, result.count


def composed_adapter(bridge: Channel, **facts: Any) -> ComposedMappingAdapter:
    adapter = ComposedMappingAdapter(**facts)
    adapter.integration = bridge
    return adapter


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param((("base", 1),), (("base", 1), ("extra", 1)), id="added"),
        pytest.param((("base", 1), ("extra", 1)), (("base", 1),), id="removed"),
        pytest.param((("base", 1), ("first", 1)), (("base", 1), ("second", 1)), id="exchanged-at-equal-sum"),
        pytest.param((("base", 1), ("extra", 1)), (("extra", 1), ("base", 1)), id="reordered"),
        pytest.param((("base", 1), ("extra", 2)), (("base", 1), ("extra", 1)), id="version-lowered"),
        pytest.param((("base", 1),), (("base", 2),), id="version-raised"),
    ],
)
def test_a_changed_contribution_list_replays_each_record_once(
    stream_bridge: Channel, before: tuple[tuple[str, int], ...], after: tuple[tuple[str, int], ...]
) -> None:
    adapter = composed_adapter(stream_bridge, contributions=before)
    initial, landed = drain_records(stream_bridge, adapter)
    assert landed == 2 and initial.mapping_version == sum(version for _key, version in before)
    assert initial.mapping_contributions == [list(item) for item in before]
    same, landed = drain_records(stream_bridge, adapter)
    assert (same.pk, landed) == (initial.pk, 0)

    adapter.contributions = after
    changed, landed = drain_records(stream_bridge, adapter)
    assert changed.generation == initial.generation + 1 and landed == 2
    assert changed.mapping_version == max(initial.mapping_version + 1, sum(version for _key, version in after))
    assert changed.mapping_contributions == [list(item) for item in after]
    assert set(RecordLink.objects.values_list("mapping_version", flat=True)) == {changed.mapping_version}
    again, landed = drain_records(stream_bridge, adapter)
    assert (again.pk, landed) == (changed.pk, 0)
    assert SyncStream.objects.count() == 2 and applied_history().count() == 4


def test_contribution_round_trips_open_a_baseline_each_time_and_never_lower_the_version(
    stream_bridge: Channel,
) -> None:
    """A, B, A replays every time; a plain version declared after composing reads as one contribution."""

    adapter = composed_adapter(stream_bridge)
    first, second = (("base", 1), ("first", 1)), (("base", 1), ("second", 1))
    declarations = [(first, 1), (second, 1), (first, 1), (first, 1), ((), 1), ((), 1), ((), 2), ((), 1)]
    seen = []
    for contributions, version in declarations:
        adapter.contributions, adapter.mapping_version = contributions, version
        stream, landed = drain_records(stream_bridge, adapter)
        seen.append((stream.generation, stream.mapping_version, landed))
    assert seen == [(1, 2, 2), (2, 3, 2), (3, 4, 2), (3, 4, 0), (4, 5, 2), (4, 5, 0), (5, 6, 2), (6, 7, 2)]
    assert SyncStream.objects.order_by("-generation").first().mapping_contributions == [["records", 1]]


def test_a_stream_written_before_retained_contributions_keeps_its_generation(stream_bridge: Channel) -> None:
    """The column's default, which ``AddField`` writes into existing rows, is a stream that never composed."""

    adapter = composed_adapter(stream_bridge, mapping_version=3)
    initial, landed = drain_records(stream_bridge, adapter)
    assert landed == 2
    default = SyncStream._meta.get_field("mapping_contributions").get_default()
    SyncStream.objects.filter(pk=initial.pk).update(mapping_contributions=default)

    upgraded, landed = drain_records(stream_bridge, adapter)
    assert (upgraded.pk, upgraded.generation, upgraded.mapping_version, upgraded.cursor, landed) == (
        initial.pk, initial.generation, 3, {"done": True}, 0,
    )
    assert upgraded.mapping_contributions == [] and applied_history().count() == 2

    # Starting to compose is a change of the list: one baseline, above the plain version.
    adapter.contributions, adapter.mapping_version = (("base", 1),), 1
    composed, landed = drain_records(stream_bridge, adapter)
    assert (composed.generation, composed.mapping_version, landed) == (initial.generation + 1, 4, 2)


@pytest.mark.parametrize("composed", [False, True])
def test_a_mapping_change_replays_records_refused_under_the_older_version(
    stream_bridge: Channel, composed: bool
) -> None:
    """A changed contribution list re-reads a refusal as a raised plain version does."""

    adapter = composed_adapter(stream_bridge, semantic_key="person:2", contributions=(("base", 1),) if composed else ())
    stream, landed = drain_records(stream_bridge, adapter)
    refused = RecordLink.objects.get(external_key="person:2")
    [failure] = refused.failures(open_only=True)
    assert landed == 1 and failure.mapping_version == stream.mapping_version
    adapter.semantic_key = ""
    same, landed = drain_records(stream_bridge, adapter)
    refused.refresh_from_db()
    assert (same.pk, landed) == (stream.pk, 0) and refused.failures(open_only=True)

    if composed:
        adapter.contributions = (("base", 1), ("extra", 1))
    else:
        adapter.mapping_version = 2
    changed, landed = drain_records(stream_bridge, adapter)
    refused.refresh_from_db()
    assert changed.generation == stream.generation + 1 and landed == 2
    assert refused.status == LinkStatus.CURRENT and not refused.failures(open_only=True)
    assert refused.mapping_version == changed.mapping_version


@pytest.mark.parametrize(
    ("facts", "message"),
    [
        pytest.param({"mapping_version": 2, "mapping_contributions": (("base", 1),)}, "not both", id="both"),
        pytest.param({"mapping_contributions": (("base", 1), ("base", 2))}, "distinct", id="repeated-key"),
        pytest.param({"mapping_contributions": (("", 1),)}, "nonempty", id="empty-key"),
        pytest.param({"mapping_contributions": (("base", 0),)}, "positive", id="zero-version"),
    ],
)
def test_contributions_are_declared_alone_with_distinct_keys_and_positive_versions(
    stream_bridge: Channel, facts: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        current_stream(stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, **facts)
    assert not SyncStream.objects.exists()


@pytest.mark.parametrize(("remote_version", "snapshots"), [("v1", 1), ("v2", 2)])
def test_unchanged_redelivery_snapshots_only_what_changed(
    stream_bridge: Channel, remote_version: str, snapshots: int
) -> None:
    """Unchanged facts keep the base: the observation is the delivery's snapshot, and a
    new remote version that observation marked OBSERVED is restored to CURRENT in a second."""
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    record = RecordChange("person:1", {"name": "Ada"}, "source", remote_version="v1", mapped_payload={"name": "Ada"})
    adapter = MemoryAdapter(pages=[StreamPage((record,), {"page": 1})])
    advance_stream(stream, adapter)
    link = RecordLink.objects.get(stream=stream)
    before = RecordLink.history.filter(id=link.pk).count()
    adapter.pages = [
        StreamPage((replace(record, remote_version=remote_version, projection={"name": "Ada"}),), {"page": 2})
    ]
    assert advance_stream(stream, adapter).count == 0
    assert adapter.applied == ["person:1"]
    assert RecordLink.history.filter(id=link.pk).count() == before + snapshots
    link.refresh_from_db()
    assert (link.status, link.remote_version) == (LinkStatus.CURRENT, remote_version)
    assert applied_history().count() == 1


def test_adapter_cannot_promote_before_the_driver(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)

    class PromotingAdapter(MemoryAdapter):
        def apply_record(self, stream: Any, record: Any) -> ApplyResult:
            outcome = super().apply_record(stream, record)
            link = RecordLink.objects.get(stream=stream, external_key=record.external_key)
            RecordLink.objects.promote(
                link,
                source_payload=record.source_payload,
                source_hash=record.source_hash,
                mapped_payload={"intermediate": True},
            )
            return outcome

    adapter = PromotingAdapter(
        pages=[StreamPage((RecordChange("person:1", {}, "source", mapped_payload={}),), {"page": 1})]
    )
    with pytest.raises(AdapterContractError, match="promot"):
        advance_stream(stream, adapter)
    stream.refresh_from_db()
    assert stream.cursor == {}
    assert not RecordLink.objects.exists()
    assert not applied_history().exists()
    assert not AppliedRecord.objects.exists()


def test_sweep_imports_unseen_keys_and_resumes_after_failed_page(stream_bridge: Channel) -> None:
    stream = current_stream(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        cursor={"incremental": 900},
        reconcile_interval=timedelta(0),
    )

    class ResumableAdapter(ReadKeysAdapter):
        fail = True

        def enumerate_keys(self, stream: Any, *, after: str | None = None) -> Iterable[str]:
            starts.append(after)
            emitted = 0
            for key in super().enumerate_keys(stream, after=after):
                emitted += 1
                assert emitted <= 3, "The driver consumed beyond one bounded page plus lookahead."
                yield key

        def read_keys(self, stream: Any, keys: Sequence[str]) -> Iterable[RecordChange]:
            assert len(keys) <= 2
            if "3" in keys and self.fail:
                raise OperationalError("inventory transport interrupted")
            return super().read_keys(stream, keys)

    starts: list[str | None] = []
    adapter = ResumableAdapter(
        inventory=("1", "2", "3", "4"),
        remote={key: RecordChange(key, {"id": key}, key, mapped_payload={"id": key}) for key in ("1", "2", "3", "4")},
    )
    reconcile_stream(stream, adapter, page_bound=2)
    stream.refresh_from_db()
    committed_cursor = dict(stream.cursor)
    assert committed_cursor["incremental"] == 900
    assert stream.reconcile_state["after"] == "2"
    assert set(AppliedRecord.objects.values_list("key", flat=True)) == {"1", "2"}
    with pytest.raises(OperationalError, match="inventory transport interrupted"):
        reconcile_stream(stream, adapter, page_bound=2)
    stream.refresh_from_db()
    assert stream.cursor == committed_cursor
    adapter.fail = False
    finish_sweep(stream, adapter, page_bound=2)
    assert starts[:3] == [None, "2", "2"]
    assert set(AppliedRecord.objects.values_list("key", flat=True)) == {"1", "2", "3", "4"}
    assert applied_history().count() == 4
    assert stream.cursor == {"incremental": 900}
    assert stream.last_reconciled_at is not None


def test_sweep_without_read_keys_resumes_bounded_baseline_without_moving_delta_cursor(stream_bridge: Channel) -> None:
    stream = current_stream(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        cursor={"incremental": 900},
        reconcile_interval=timedelta(0),
    )
    extracted_cursors: list[dict[str, Any]] = []

    class BaselineAdapter(MemoryAdapter):
        fail = True

        def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
            assert not connection.in_atomic_block
            assert page_bound == 1
            extracted_cursors.append(dict(stream.cursor))
            if not stream.cursor:
                return StreamPage(
                    (RecordChange("a", {"id": "a"}, "a", mapped_payload={"id": "a"}),),
                    {"baseline": "a"},
                    exhausted=False,
                )
            assert stream.cursor == {"baseline": "a"}
            if self.fail:
                raise OperationalError("baseline transport interrupted")
            return StreamPage((RecordChange("b", {"id": "b"}, "b", mapped_payload={"id": "b"}),), {"baseline": "b"})

    adapter = BaselineAdapter(inventory=("a", "b"))
    reconcile_stream(stream, adapter, page_bound=1)
    stream.refresh_from_db()
    committed_cursor = dict(stream.cursor)
    assert committed_cursor["incremental"] == 900
    assert stream.reconcile_state["cursor"] == {"baseline": "a"}
    assert AppliedRecord.objects.get().key == "a"
    with pytest.raises(OperationalError, match="baseline transport interrupted"):
        reconcile_stream(stream, adapter, page_bound=1)
    stream.refresh_from_db()
    assert stream.cursor == committed_cursor
    resumed_stream = SyncStream.objects.get(pk=stream.pk)
    resumed_adapter = BaselineAdapter(inventory=("a", "b"))
    resumed_adapter.fail = False
    assert finish_sweep(resumed_stream, resumed_adapter, page_bound=1) == 0
    assert extracted_cursors == [{}, {"baseline": "a"}, {"baseline": "a"}]
    assert set(AppliedRecord.objects.values_list("key", flat=True)) == {"a", "b"}
    assert applied_history().count() == 2
    assert resumed_stream.cursor == {"incremental": 900}
    assert resumed_stream.last_reconciled_at is not None


def test_visibility_hooks_run_in_transaction_once_per_status_transition(stream_bridge: Channel) -> None:
    stream = current_stream(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        reconcile_interval=timedelta(0),
        absence_threshold=3,
    )
    absent: list[tuple[str, str]] = []
    revalidated: list[tuple[str, str]] = []

    class VisibilityAdapter(ReadKeysAdapter):
        def on_absent(self, stream: Any, links: Sequence[Any]) -> None:
            assert connection.in_atomic_block
            for link in links:
                link.refresh_from_db()
                absent.append((link.external_key, link.status))

        def on_revalidated(self, stream: Any, links: Sequence[Any]) -> None:
            assert connection.in_atomic_block
            for link in links:
                link.refresh_from_db()
                revalidated.append((link.external_key, link.status))

    record = RecordChange("person:1", {}, "source", mapped_payload={})
    adapter = VisibilityAdapter(pages=[StreamPage((record,), {})])
    advance_stream(stream, adapter)
    for _ in range(4):
        finish_sweep(stream, adapter, page_bound=1)
    assert absent == [("person:1", LinkStatus.UNAVAILABLE), ("person:1", LinkStatus.TOMBSTONE)]
    adapter.pages = [StreamPage((replace(record, projection={}),), {"page": 2})]
    assert advance_stream(stream, adapter).count == 0
    assert revalidated == [("person:1", LinkStatus.CURRENT)]
    assert applied_history().count() == 1


def test_absence_hook_failure_rolls_back_transition_and_cursor_before_retry(stream_bridge: Channel) -> None:
    stream = current_stream(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        reconcile_interval=timedelta(days=1),
    )
    missing = RecordLink.objects.observe(stream, "missing")
    calls: list[tuple[str, int]] = []

    class FailingVisibilityAdapter(ReadKeysAdapter):
        fail = True

        def on_absent(self, stream: Any, links: Sequence[Any]) -> None:
            assert connection.in_atomic_block
            for link in links:
                calls.append((link.external_key, link.absence_count))
                AppliedRecord.objects.create(key=link.external_key, payload={"withdrawn": True})
            if self.fail:
                raise OperationalError("visibility persistence interrupted")

    adapter = FailingVisibilityAdapter()
    reconcile_stream(stream, adapter, page_bound=1)
    stream.refresh_from_db()
    committed_cursor = dict(stream.cursor)
    with pytest.raises(OperationalError, match="visibility persistence interrupted"):
        reconcile_stream(stream, adapter, page_bound=1)
    missing.refresh_from_db()
    stream.refresh_from_db()
    assert (missing.status, missing.absence_count) == (LinkStatus.OBSERVED, 0)
    assert stream.cursor == committed_cursor
    assert not AppliedRecord.objects.exists()
    adapter.fail = False
    assert finish_sweep(stream, adapter, page_bound=1) == 1
    missing.refresh_from_db()
    assert (missing.status, missing.absence_count) == (LinkStatus.UNAVAILABLE, 1)
    assert AppliedRecord.objects.get().payload == {"withdrawn": True}
    assert calls == [("missing", 1), ("missing", 1)]
    assert reconcile_stream(stream, adapter, page_bound=1) == 0
    assert len(calls) == 2


def test_sweep_children_follow_parent_presence_and_absence(stream_bridge: Channel) -> None:
    stream = current_stream(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        reconcile_interval=timedelta(0),
        absence_threshold=2,
    )
    parent = RecordLink.objects.observe(stream, "parent")
    child = RecordLink.objects.observe(stream, "child", parent=parent)
    RecordLink.objects.promote(
        child,
        source_payload={},
        source_hash="child",
        mapped_payload={},
    )
    adapter = ReadKeysAdapter(
        inventory=("parent",), remote={"parent": RecordChange("parent", {}, "parent", mapped_payload={})}
    )
    finish_sweep(stream, adapter, page_bound=1)
    child.refresh_from_db()
    assert child.status == LinkStatus.CURRENT
    assert child.absence_count == 0
    adapter.inventory = ()
    for expected in (LinkStatus.UNAVAILABLE, LinkStatus.TOMBSTONE):
        finish_sweep(stream, adapter, page_bound=1)
        parent.refresh_from_db()
        child.refresh_from_db()
        assert child.status == parent.status == expected
        assert child.absence_count == parent.absence_count


def test_child_failure_rescans_and_reapplies_parent_identity(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    record = RecordChange("parent", {}, "parent", mapped_payload={})
    adapter = ReadKeysAdapter(pages=[StreamPage((record,), {"page": 1})])
    advance_stream(stream, adapter)
    parent = RecordLink.objects.get(external_key="parent")
    child = RecordLink.objects.observe(stream, "child", parent=parent)
    failure = failure_state(
        child.record_failure(kind=SyncFailureKind.MISSING_DEPENDENCY, code="child_dependency", source_hash="child"),
        retry_at=timezone.now(),
    )
    adapter.remote = {"parent": replace(record, projection={})}
    begin_stream_cycle(stream, adapter, page_bound=1)
    assert adapter.reads == [("parent",)]
    assert adapter.applied == ["parent", "parent"]

    failure = failure_state(failure)
    assert failure.status == SyncFailureStatus.OPEN
    stream.refresh_from_db()
    assert stream.cursor == {"page": 1}


def test_prepare_page_runs_once_before_record_savepoints_and_transport_is_refused(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "events")
    events: list[str] = []
    prepared_depth = -1

    class PreparedAdapter(MemoryAdapter):
        def prepare_page(self, stream: Any, page: StreamPage) -> None:
            nonlocal prepared_depth
            assert connection.in_atomic_block
            prepared_depth = len(connection.savepoint_ids)
            assert page.records == ("first", "poison", "last")
            events.append("prepare")
            with pytest.raises(RuntimeError, match="outside a database transaction"):
                advance_stream(stream, self)

        def apply_record(self, stream: Any, record: Any) -> ApplyResult:
            assert len(connection.savepoint_ids) > prepared_depth
            events.append(record)
            return super().apply_record(stream, record)

    adapter = PreparedAdapter(pages=[StreamPage(("first", "poison", "last"), {})], semantic_key="poison")
    result = advance_stream(stream, adapter)
    assert result.count == 2
    assert events == ["prepare", "first", "poison", "last"]
    assert adapter.extracted == 1


def test_stale_peer_beyond_tombstone_retention_requires_baseline(stream_bridge: Channel) -> None:
    stream = current_stream(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        tombstone_retention=timedelta(days=7),
    )
    link = RecordLink.objects.observe(stream, "deleted")
    RecordLink.objects.tombstone(link)
    RecordLink.objects.filter(pk=link.pk).update(tombstoned_at=timezone.now() - timedelta(days=8))
    begin_stream_cycle(stream)
    stream.refresh_from_db()
    assert stream.resync_required
    assert advance_stream(stream, MemoryAdapter()).reset


def test_infrastructure_failure_rolls_back_domain_page_and_cursor(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "events", cursor={"offset": 0})
    adapter = MemoryAdapter(
        pages=[StreamPage(("first", "broken"), {"offset": 2})],
        infrastructure_key="broken",
    )
    with pytest.raises(OperationalError, match="database unavailable"):
        advance_stream(stream, adapter)
    stream.refresh_from_db()
    assert stream.cursor == {"offset": 0}
    assert not AppliedRecord.objects.exists()
    assert not bool(SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False))


def test_semantic_failure_quarantines_one_row_and_continues_page(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "events")
    adapter = MemoryAdapter(pages=[StreamPage(("poison", "healthy"), {"offset": 2})], semantic_key="poison")
    result = advance_stream(stream, adapter)
    stream.refresh_from_db()
    assert result.count == 1
    assert stream.cursor == {"offset": 2}
    assert list(AppliedRecord.objects.values_list("key", flat=True)) == ["healthy"]
    failure = next(
        (
            row
            for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
            if row.id == result.failure_ids[0]
        )
    )
    assert (failure.kind, failure.code) == (SyncFailureKind.SEMANTIC, "invalid_name")
    assert (failure.source_hash, failure.status) == ("", SyncFailureStatus.OPEN)


def test_keyed_event_refusals_with_one_code_stay_distinct(stream_bridge: Channel) -> None:
    """An event adapter's record key keeps same-code refusals from coalescing."""

    class KeyedAdapter(MemoryAdapter):
        def record_key(self, record: Any) -> str:
            return record

        def apply_record(self, stream: Any, record: Any) -> ApplyResult:
            if record.startswith("poison"):
                raise SemanticError("invalid_name", details={"reason": "refused"})
            return super().apply_record(stream, record)

    stream = current_stream(stream_bridge, "events")
    pages = [StreamPage(("poison-a", "poison-b"), {"offset": 2}), StreamPage(("poison-a",), {"offset": 3})]
    adapter = KeyedAdapter(pages=pages)
    first = advance_stream(stream, adapter)
    retained = {row.id: row for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)}
    rows = [retained[failure_id] for failure_id in first.failure_ids]
    assert [row.details["external_key"] for row in rows] == ["poison-a", "poison-b"]
    assert len({row.source_hash for row in rows}) == 2
    again = advance_stream(stream, adapter)
    assert again.failure_ids == (rows[0].id,)
    retried = {row.id: row for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)}
    assert retried[rows[0].id].attempts == 2


@pytest.mark.parametrize("replica", [False, True])
@pytest.mark.parametrize("stage", ["apply", "settle"])
def test_data_error_quarantines_record_after_savepoint_rollback(
    stream_bridge: Channel,
    monkeypatch: pytest.MonkeyPatch,
    replica: bool,
    stage: str,
) -> None:
    """A residual database data refusal rolls back one row and commits the page."""

    class DataAdapter(MemoryAdapter):
        def record_key(self, record: Any) -> str:
            return record

        def apply_record(self, stream: Any, record: Any) -> ApplyResult:
            result = super().apply_record(stream, record)
            if result.bound_target.key == "poison" and stage == "apply":
                # Match a PostgreSQL statement failure's broken savepoint state
                # while exercising the SQLite-only, isolated test database.
                transaction.set_rollback(True)
                raise DataError("private record payload and database details")
            return result

        def finish_page(self, stream: Any, page: StreamPage, outcomes: Sequence[ApplyResult]) -> None:
            assert [outcome.bound_target.key for outcome in outcomes] == ["first", "last"]

    if stage == "settle":
        resolve_applied = SyncStream.resolve_applied

        def fail_settlement(stream: Any, record: Any) -> None:
            key = record.external_key if isinstance(record, RecordChange) else record
            if key == "poison":
                transaction.set_rollback(True)
                raise DataError("private record payload and database details")
            resolve_applied(stream, record)

        monkeypatch.setattr(SyncStream, "resolve_applied", fail_settlement)

    stream = current_stream(
        stream_bridge,
        "records",
        "inbox",
        kind=StreamKind.RECORD_REPLICA if replica else StreamKind.EVENT_FEED,
    )
    records = tuple(
        RecordChange(key, {"name": key}, key, mapped_payload={"name": key}) if replica else key
        for key in ("first", "poison", "last")
    )
    result = advance_stream(stream, DataAdapter(pages=[StreamPage(records, {"offset": 3})]))

    assert result.count == 2
    assert set(AppliedRecord.objects.values_list("key", flat=True)) == {"first", "last"}
    stream.refresh_from_db()
    assert stream.cursor == {"offset": 3}
    failure = next(
        (
            row
            for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
            if row.id == result.failure_ids[0]
        )
    )
    assert failure.code == "invalid_record_data"
    assert failure.details == {
        "stream": "records",
        "partition": "inbox",
        "external_key": "poison",
        "fields": ["unknown"],
        "message": "Database rejected invalid or oversized record data.",
    }
    assert failure.is_open
    if replica:
        link = RecordLink.objects.get(stream=stream, external_key="poison")
        assert failure.link_pk == link.pk
        assert link.status == LinkStatus.FAILED
        assert not applied_history().filter(id=link.pk).exists()
    else:
        assert not RecordLink.objects.filter(stream=stream).exists()


def test_finish_page_runs_after_every_applied_link_is_promoted(stream_bridge: Channel) -> None:
    """The page hook sees promoted links and runs before the cursor advances."""

    seen: list[tuple[str, bool]] = []

    class FinishAdapter(MemoryAdapter):
        def finish_page(self, stream: Any, page: StreamPage, outcomes: Sequence[ApplyResult]) -> None:
            assert connection.in_atomic_block
            assert SyncStream.objects.get(pk=stream.pk).cursor == before
            seen.extend(
                (link.external_key, link.applied_base_id is not None)
                for link in RecordLink.objects.filter(stream=stream).order_by("external_key")
            )

    stream = current_stream(stream_bridge, "records", "inbox", kind=StreamKind.RECORD_REPLICA)
    before = stream.cursor
    records = tuple(RecordChange(key, {"name": key}, key, mapped_payload={"name": key}) for key in ("a", "b"))
    advance_stream(stream, FinishAdapter(pages=[StreamPage(records, {"offset": 2})]))

    assert seen == [("a", True), ("b", True)]
    stream.refresh_from_db()
    assert stream.cursor == {"offset": 2}


def test_message_metadata_refusal_quarantines_record_and_continues_page(stream_bridge: Channel) -> None:
    """The existing metadata budget is a field refusal, never a poison-page error."""

    class MessageAdapter(ChannelBackend):
        def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
            return StreamPage(
                (
                    ParsedMessage("first", "email", body=ParsedPart(text="First")),
                    ParsedMessage("poison", "email", metadata={"tags": ["x" * (512 * 1024)]}),
                    ParsedMessage("last", "email", body=ParsedPart(text="Last")),
                ),
                {"offset": 3},
            )

    stream = current_stream(stream_bridge, "messages", "inbox")
    result = advance_stream(stream, MessageAdapter(stream_bridge))

    assert result.count == 2
    assert set(Message._base_manager.values_list("external_id", flat=True)) == {"first", "last"}
    stream.refresh_from_db()
    assert stream.cursor == {"offset": 3}
    failure = next(
        (
            row
            for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
            if row.id == result.failure_ids[0]
        )
    )
    assert failure.code == "invalid_record"
    assert failure.details["external_key"] == "poison"
    assert failure.details["fields"] == ["metadata"]
    assert "tags" not in failure.details


@pytest.mark.parametrize("status", [SyncFailureStatus.OPEN, SyncFailureStatus.RETRY])
@pytest.mark.parametrize("tombstone", [False, True])
def test_identity_rescan_resolves_current_remote_state_without_advancing_stream(
    stream_bridge: Channel, status: str, tombstone: bool
) -> None:
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    adapter = ReadKeysAdapter(
        pages=[
            StreamPage(
                (
                    RecordChange("person:1", {"name": "base"}, "base", mapped_payload={"name": "base"}),
                    RecordChange("healthy", {"name": "healthy"}, "healthy", mapped_payload={"name": "healthy"}),
                ),
                {"page": 1},
            )
        ]
    )
    advance_stream(stream, adapter)
    adapter.semantic_key = "person:1"
    adapter.pages = [
        StreamPage(
            (
                RecordChange(
                    "person:1",
                    {"name": "invalid"},
                    "invalid",
                    mapped_payload={"name": "invalid"},
                    projection={"name": "base"},
                ),
            ),
            {"page": 2},
        )
    ]
    failure = advance_stream(stream, adapter)
    failure = next(
        (
            row
            for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
            if row.id == failure.failure_ids[0]
        )
    )
    for failure_row in [
        row for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False) if row.id == failure.pk
    ]:
        failure_state(failure_row, status=status, retry_at=timezone.now())
    future = RecordLink.objects.observe(stream, "future")
    failure_state(
        future.record_failure(kind=SyncFailureKind.SEMANTIC, code="invalid_name", source_hash="future"),
        retry_at=timezone.now() + timedelta(hours=1),
    )
    SyncStream.objects.filter(pk=stream.pk).update(
        cursor_expires_at=timezone.now() + timedelta(days=1),
        last_reconciled_at=timezone.now() - timedelta(hours=1),
    )
    before = SyncStream.objects.filter(pk=stream.pk).values().get()
    adapter.applied.clear()
    adapter.semantic_key = ""
    payload = {} if tombstone else {"name": "corrected"}
    adapter.remote = {
        "person:1": RecordChange(
            "person:1",
            payload,
            "" if tombstone else "corrected",
            tombstone=tombstone,
            mapped_payload=payload,
            projection={"name": "base"},
        ),
    }
    begin_stream_cycle(stream, adapter)

    failure = failure_state(failure)
    link = RecordLink.objects.get(stream=stream, external_key="person:1")
    assert adapter.reads == [("person:1",)]
    assert adapter.extracted == 2
    assert adapter.applied == ["person:1"]
    assert not adapter.written
    assert AppliedRecord.objects.get(key="person:1").payload == payload
    assert failure.status == SyncFailureStatus.RESOLVED
    assert failure.retry_at is None
    assert link.status == (LinkStatus.TOMBSTONE if tombstone else LinkStatus.CURRENT)
    assert link.applied_base.source_hash == ("" if tombstone else "corrected")
    assert applied_history().filter(id=link.pk).count() == 2
    assert SyncStream.objects.filter(pk=stream.pk).values().get() == before
    assert SyncStream.objects.count() == 1


def test_identity_rescan_skips_an_identity_another_attempt_healed_while_it_read(stream_bridge: Channel) -> None:
    """A rescan's read never meets a base that moved after it selected the identity."""

    class RacingAdapter(ReadKeysAdapter):
        def read_keys(self, stream: Any, keys: Sequence[str]) -> Iterable[RecordChange]:
            records = tuple(super().read_keys(stream, keys))
            if len(self.reads) == 1:
                # An earlier attempt, still running, applies the same identity first.
                begin_stream_cycle(stream, self)
            return records

    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    base = RecordChange("person:1", {"name": "base"}, "base", mapped_payload={"name": "base"})
    adapter = RacingAdapter(pages=[StreamPage((base,), {"page": 1})])
    advance_stream(stream, adapter)
    # The local projection is the applied base the adapter read, as a pull-only replica's is.
    changed = RecordChange(
        "person:1",
        {"name": "v2"},
        "v2",
        "base",
        mapped_payload={"name": "v2"},
        projection={"name": "base"},
    )
    adapter.semantic_key = "person:1"
    adapter.pages = [StreamPage((changed,), {"page": 2})]
    result = advance_stream(stream, adapter)
    failure = next(
        row for row in SyncStream.objects.get(pk=stream.pk).sync_failures() if row.id == result.failure_ids[0]
    )
    failure_state(failure, retry_at=timezone.now())
    adapter.semantic_key = ""
    adapter.applied.clear()
    adapter.remote = {"person:1": changed}

    begin_stream_cycle(stream, adapter)

    link = RecordLink.objects.get(stream=stream, external_key="person:1")
    assert adapter.reads == [("person:1",), ("person:1",)]
    assert adapter.applied == ["person:1"]
    assert link.status == LinkStatus.CURRENT
    assert link.applied_base.source_hash == "v2"
    assert applied_history().filter(id=link.pk).count() == 2
    assert not SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=True)
    assert failure_state(failure).status == SyncFailureStatus.RESOLVED


def test_changed_remote_failure_keeps_one_current_problem_and_older_quarantine_history(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    adapter = ReadKeysAdapter(
        pages=[StreamPage((RecordChange("poison", {}, "old", mapped_payload={}),), {"page": 1})],
        semantic_key="poison",
        remote={"poison": RecordChange("poison", {}, "new", mapped_payload={})},
    )
    result = advance_stream(stream, adapter)
    for failure_row in [
        row
        for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
        if row.id == result.failure_ids[0]
    ]:
        failure_state(failure_row, retry_at=timezone.now())
    begin_stream_cycle(stream, adapter)
    failures = list(
        sorted(
            [row for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)],
            key=lambda row: row.created_at,
        )
    )
    assert {row.source_hash for row in failures} == {"old", "new"}
    old, current = failures
    assert old.status == SyncFailureStatus.RESOLVED and old.resolved_at is not None and old.retry_at is None
    assert current.status == SyncFailureStatus.OPEN and current.retry_at > timezone.now()
    assert [row.pk for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=True)] == [current.pk]
    assert not RecordLink.objects.filter(stream=stream).rescan(limit=100)
    begin_stream_cycle(stream, adapter)
    assert adapter.reads == [("poison",)]
    assert not AppliedRecord.objects.exists()
    assert SyncStream.objects.count() == 1


def test_identity_rescan_rolls_back_all_records_on_infrastructure_failure(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, cursor={"page": 8})
    for key in ("first", "z-broken"):
        link = RecordLink.objects.observe(stream, key)
        failure_state(
            link.record_failure(kind=SyncFailureKind.SEMANTIC, code="invalid_name", source_hash=key),
            retry_at=timezone.now(),
        )
    adapter = ReadKeysAdapter(
        infrastructure_key="z-broken",
        remote={
            key: RecordChange(key, {"name": key}, key, mapped_payload={"name": key}) for key in ("first", "z-broken")
        },
    )
    with pytest.raises(OperationalError, match="database unavailable"):
        begin_stream_cycle(stream, adapter)
    stream.refresh_from_db()
    assert stream.cursor == {"page": 8}
    assert not AppliedRecord.objects.exists()
    assert adapter.applied == ["first", "z-broken"]
    assert not applied_history().exists()
    assert (
        len(
            [
                row
                for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
                if row.status == SyncFailureStatus.OPEN
            ]
        )
        == 2
    )


def test_identity_rescan_without_read_keys_requests_and_records_baseline(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    record = RecordChange("poison", {"name": "invalid"}, "invalid", mapped_payload={"name": "invalid"})
    adapter = MemoryAdapter(pages=[StreamPage((record,), {"page": 1})], semantic_key="poison")
    failure = advance_stream(stream, adapter)
    failure = next(
        (
            row
            for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
            if row.id == failure.failure_ids[0]
        )
    )
    for failure_row in [
        row for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False) if row.id == failure.pk
    ]:
        failure_state(failure_row, retry_at=timezone.now())
    begin_stream_cycle(stream, adapter)
    stream.refresh_from_db()

    failure = failure_state(failure)
    assert stream.resync_required
    assert stream.cursor == {"page": 1}
    assert failure.details == {
        "key": "poison",
        "external_key": "poison",
        "rescan": "baseline",
        "reason": "read_keys_unavailable",
    }
    reset = advance_stream(stream, adapter)
    assert reset.reset
    adapter.pages = [StreamPage((record,), {"page": 1})]
    advance_stream(reset.stream, adapter)
    begin_stream_cycle(reset.stream, adapter)
    reset.stream.refresh_from_db()

    failure = failure_state(failure)
    assert failure.attempts == 2
    assert failure.retry_at > timezone.now()
    assert reset.stream.generation == 2
    assert not reset.stream.resync_required


def test_event_feed_failures_never_trigger_rescan_or_baseline(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "events")
    adapter = ReadKeysAdapter(pages=[StreamPage(("poison",), {"offset": 1})], semantic_key="poison")
    failure = advance_stream(stream, adapter)
    for failure_row in [
        row
        for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
        if row.id == failure.failure_ids[0]
    ]:
        failure_state(failure_row, retry_at=timezone.now())
    before = SyncStream.objects.filter(pk=stream.pk).values().get()
    for _ in range(3):
        begin_stream_cycle(stream, adapter)
    assert not adapter.reads
    assert adapter.extracted == 1
    assert not RecordLink.objects.exists()
    assert SyncStream.objects.filter(pk=stream.pk).values().get() == before


@pytest.mark.parametrize(
    "interval,cap",
    [
        (timedelta(minutes=3), 180),
        (timedelta(days=2), 86400),
        (None, 86400),
        (timedelta(0), 86400),
    ],
)
def test_quarantine_backoff_grows_and_caps_without_generation_inflation(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch, interval: timedelta | None, cap: int
) -> None:
    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, reconcile_interval=interval)
    record = RecordChange("poison", {"name": "invalid"}, "invalid", mapped_payload={"name": "invalid"})
    adapter = ReadKeysAdapter(
        pages=[StreamPage((record,), {"page": 1})], semantic_key="poison", remote={"poison": record}
    )
    failure = advance_stream(stream, adapter)
    failure = next(
        (
            row
            for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
            if row.id == failure.failure_ids[0]
        )
    )
    expected_attempt = 1
    for _ in range(4):
        failure = failure_state(failure)
        assert failure.attempts == expected_attempt
        delay = min(60 * 2 ** (expected_attempt - 1), cap)
        assert failure.retry_at == now + timedelta(seconds=delay)
        reads = len(adapter.reads)
        begin_stream_cycle(stream, adapter)
        assert len(adapter.reads) == reads
        now = failure.retry_at
        begin_stream_cycle(stream, adapter)
        assert len(adapter.reads) == reads + 1
        expected_attempt += 1
    for failure_row in [
        row for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False) if row.id == failure.pk
    ]:
        failure_state(failure_row, attempts=20, retry_at=now)
    begin_stream_cycle(stream, adapter)

    failure = failure_state(failure)
    stream.refresh_from_db()
    assert failure.attempts == 21
    assert failure.retry_at == now + timedelta(seconds=cap)
    assert failure.status == SyncFailureStatus.OPEN
    assert not AppliedRecord.objects.exists()
    assert stream.cursor == {"page": 1}
    assert stream.generation == 1
    assert not stream.resync_required
    assert SyncStream.objects.count() == len(SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)) == 1


@pytest.mark.parametrize("status", [SyncFailureStatus.OPEN, SyncFailureStatus.RETRY])
def test_both_changed_conflict_requires_resolution_before_pull_or_push(stream_bridge: Channel, status: str) -> None:
    stream = current_stream(
        stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    adapter = ReadKeysAdapter(
        pages=[
            StreamPage(
                (RecordChange("person:1", {"name": "base"}, "base", mapped_payload={"name": "base"}),), {"page": 1}
            )
        ]
    )
    advance_stream(stream, adapter)
    AppliedRecord.objects.filter(key="person:1").update(payload={"name": "local edit"})
    record = RecordChange(
        "person:1",
        {"name": "remote edit"},
        "remote",
        mapped_payload={"name": "remote edit"},
        projection={"value": "local"},
    )
    adapter.applied.clear()
    adapter.remote = {"person:1": record}
    adapter.pages = [StreamPage((record,), {"page": 2})]
    result = advance_stream(stream, adapter)
    failure = next(
        (
            row
            for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
            if row.id == result.failure_ids[0]
        )
    )
    assert result.count == 0
    assert (failure.kind, failure.code, failure.retry_at) == (
        SyncFailureKind.CONFLICT,
        "both_changed",
        None,
    )
    for failure_row in [
        row for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False) if row.id == failure.pk
    ]:
        failure_state(failure_row, status=status)
    begin_stream_cycle(stream, adapter)
    # Even if a later remote observation matches the old base, an unresolved
    # conflict must not silently authorize the pending local write.
    adapter.pages = [
        StreamPage(
            (
                RecordChange(
                    "person:1",
                    {"name": "base"},
                    "base",
                    mapped_payload={"name": "base"},
                    projection={"value": "local"},
                ),
            ),
            {"page": 3},
        )
    ]
    assert advance_stream(stream, adapter).count == 0
    adapter.candidates = (LocalChange("person:1", {"name": "local edit"}),)
    assert push_stream(stream, adapter).count == 0
    stream.refresh_from_db()

    failure = failure_state(failure)
    assert failure.status == status
    assert failure.retry_at is None
    assert not adapter.reads
    assert not adapter.applied
    assert not adapter.written
    assert not stream.resync_required
    assert stream.generation == 1
    assert AppliedRecord.objects.get(key="person:1").payload == {"name": "local edit"}
    assert applied_history().count() == 1

    with pytest.raises(ValidationError, match="requires keeping"):
        failure.resolve()


def test_page_reloads_conflicts_recorded_during_conditional_write(stream_bridge: Channel) -> None:
    stream = current_stream(
        stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    link = RecordLink.objects.observe(stream, "one")
    RecordLink.objects.promote(
        link,
        source_payload={},
        source_hash="base",
        mapped_payload={},
    )
    SyncStream.objects.advance(stream, {}, exhausted=True)

    class RacingAdapter(MemoryAdapter):
        def write_back(self, link: Any, projection: Any, *, expected_version: str) -> WriteBackResult:
            link.record_failure(kind=SyncFailureKind.CONFLICT, code="concurrent_conflict")
            return super().write_back(link, projection, expected_version=expected_version)

    adapter = RacingAdapter(
        pages=[StreamPage((RecordChange("one", {}, "base", mapped_payload={}, projection={"value": "local"}),), {})]
    )

    result = advance_stream(stream, adapter)

    assert result.count == 0
    assert len(adapter.written) == 1
    assert applied_history().count() == 1
    conflict = next(
        (
            row
            for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=False)
            if row.id == result.failure_ids[0]
        )
    )
    assert conflict.code == "concurrent_conflict"
    assert conflict.status == SyncFailureStatus.OPEN
    link.refresh_from_db()
    assert (link.applied_base.mapped_payload, link.applied_base.source_hash) == ({}, "base")


def test_push_writes_each_record_against_its_bases_as_they_are_now(stream_bridge: Channel) -> None:
    """A link moved since its batch loaded (here by a concurrent keep-local) is written against its new version."""

    stream = current_stream(
        stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    for key in ("one", "two"):
        link = RecordLink.objects.observe(stream, key)
        RecordLink.objects.promote(link, source_payload={}, source_hash="base", mapped_payload={}, remote_version="v1")
    SyncStream.objects.advance(stream, {}, exhausted=True)

    class RacingAdapter(MemoryAdapter):
        def write_back(self, link: Any, projection: Any, *, expected_version: str) -> WriteBackResult:
            if link.external_key == "one":
                two = RecordLink.objects.get(stream=stream, external_key="two")
                RecordLink.objects.promote(
                    two,
                    source_payload={},
                    source_hash="fresh",
                    mapped_payload={},
                    remote_version="v-fresh",
                )
            return super().write_back(link, projection, expected_version=expected_version)

    adapter = RacingAdapter(candidates=(LocalChange("one", {"n": 1}), LocalChange("two", {"n": 2})))

    assert push_stream(stream, adapter).count == 2
    assert [(key, version) for key, _projection, version in adapter.written] == [("one", "v1"), ("two", "v-fresh")]


def test_write_back_origin_is_recognized_on_next_pull(stream_bridge: Channel) -> None:
    stream = current_stream(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        direction=StreamDirection.BIDIRECTIONAL,
    )
    link = RecordLink.objects.observe(stream, "person:1")
    RecordLink.objects.promote(
        link,
        source_payload={"name": "before"},
        source_hash="base",
        mapped_payload={"name": "before"},
        remote_version="v1",
    )
    adapter = MemoryAdapter(
        pages=[
            StreamPage(
                (
                    RecordChange(
                        "person:1",
                        {"name": "before"},
                        "base",
                        "v1",
                        {"name": "after"},
                        mapped_payload={"name": "before"},
                    ),
                ),
                {"page": 1},
            )
        ]
    )
    assert advance_stream(stream, adapter).count == 1
    link.refresh_from_db()
    assert (link.origin, link.remote_version, link.applied_base.source_hash, link.applied_base.mapped_payload) == (
        "local",
        "v2",
        "changed",
        {"name": "after"},
    )
    assert adapter.written == [("person:1", {"name": "after"}, "v1")]
    adapter.pages = [
        StreamPage(
            (
                RecordChange(
                    "person:1",
                    {"name": "after"},
                    "changed",
                    "v2",
                    mapped_payload={"name": "after"},
                    projection={"name": "after"},
                ),
            ),
            {"page": 2},
        )
    ]
    assert advance_stream(stream, adapter).count == 0
    assert adapter.applied == []
    assert applied_history().filter(id=link.pk).count() == 2


def test_event_feeds_apply_without_replica_links(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "events")
    adapter = MemoryAdapter(pages=[StreamPage(("message:1", "message:2"), {"next": "end"})])
    assert advance_stream(stream, adapter).count == 2
    assert AppliedRecord.objects.count() == 2
    assert not RecordLink.objects.exists()
    assert not applied_history().exists()


def test_sync_bridge_deadline_partial_page_is_not_a_repeated_cursor(
    stream_bridge: Channel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]

    class BudgetAdapter(MemoryAdapter):
        def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
            page = super().extract(stream, page_bound, deadline=deadline)
            if self.extracted == 2:
                clock[0] = 2.0
            return page

    page = StreamPage(("partial-event",), {"next": "same"}, exhausted=False)
    adapter = BudgetAdapter(pages=[page, page])
    stream_bridge.config = {"sync_time_budget": 1}
    monkeypatch.setattr(stream_driver, "monotonic", lambda: clock[0])
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    with system_context(reason="test budget partial page"):
        assert sync_bridge(stream_bridge) == 2
    assert adapter.extracted == 2
    assert SyncStream.objects.get(integration=stream_bridge, key="records").cursor == {"next": "same"}


def test_event_feed_accepts_message_with_noncopyable_timezone(stream_bridge: Channel) -> None:
    class RequiredOffset(tzinfo):
        def __init__(self, minutes: int) -> None:
            self.offset = timedelta(minutes=minutes)

        def utcoffset(self, value: datetime | None) -> timedelta:
            return self.offset

        def dst(self, value: datetime | None) -> timedelta:
            return timedelta(0)

    message = ParsedMessage(
        external_id="message:noncopyable-timezone",
        platform="email",
        sent_at=datetime(2026, 9, 24, 12, tzinfo=RequiredOffset(120)),
    )

    class MessageAdapter(MemoryAdapter):
        def apply_record(self, stream: Any, record: Any) -> ApplyResult:
            assert connection.in_atomic_block
            assert record is message
            assert message.sent_at is not None
            row, _ = AppliedRecord.objects.update_or_create(
                key=message.external_id, defaults={"payload": {"sent_at": message.sent_at.isoformat()}}
            )
            return ApplyResult(target=row)

    stream = current_stream(stream_bridge, "messages", "INBOX")
    adapter = MessageAdapter(pages=[StreamPage((message,), {"uid": 1})])
    assert advance_stream(stream, adapter).count == 1
    stream.refresh_from_db()
    assert stream.cursor == {"uid": 1}
    assert AppliedRecord.objects.get().payload == {"sent_at": "2026-09-24T12:00:00+02:00"}
    assert not RecordLink.objects.exists()
    assert not applied_history().exists()


def test_opening_an_existing_partition_applies_its_changed_declaration(stream_bridge: Channel) -> None:
    """Without a supplied definition the adapter declares again: no caller can skip the reconciliation."""

    class DeclaringAdapter(MemoryAdapter):
        authoritative = False

        def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
            return (StreamDefinition("records", authoritative=self.authoritative),)

    adapter = DeclaringAdapter()
    adapter.integration = stream_bridge
    assert open_stream(stream_bridge, "records", "", adapter).authoritative is False

    adapter.authoritative = True
    stream = open_stream(stream_bridge, "records", "", adapter)

    assert stream.authoritative is True and stream.generation == 1
    assert SyncStream.objects.count() == 1


@pytest.mark.parametrize("cursor", [[], 0, ""])
def test_stream_declaration_rejects_falsy_non_object_cursors(stream_bridge: Channel, cursor: Any) -> None:
    class InvalidDeclarationAdapter(MemoryAdapter):
        def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
            return (StreamDefinition(key="records", cursor=cursor),)

    adapter = InvalidDeclarationAdapter()
    adapter.integration = stream_bridge
    with pytest.raises(AdapterContractError, match="plain finite JSON"):
        open_stream(stream_bridge, "records", "", adapter)
    assert not SyncStream.objects.current_for_bridge(stream_bridge, "records").exists()


@pytest.mark.parametrize("boundary", ["page", "reset", "definition"])
@pytest.mark.parametrize(
    "cursor",
    [
        {"nested": [ParsedMessage(external_id="message:1", platform="email")]},
        {"nested": [datetime(2026, 9, 24)]},
        {"tuple": (1, 2)},
        {"number": float("nan")},
        {1: "not a JSON object key"},
    ],
)
def test_stream_rejects_non_json_cursors_before_applying(
    stream_bridge: Channel, boundary: str, cursor: dict[Any, Any]
) -> None:
    stream = current_stream(stream_bridge, "events")
    with pytest.raises(AdapterContractError, match="plain finite JSON"):
        if boundary == "definition":

            class InvalidDefinitionAdapter(MemoryAdapter):
                def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
                    return (StreamDefinition("invalid", cursor=cursor),)

            adapter = InvalidDefinitionAdapter()
            open_stream(stream_bridge, "invalid", "", adapter)
        else:
            page = CursorInvalid(cursor=cursor) if boundary == "reset" else StreamPage(("message:1",), cursor)
            advance_stream(stream, MemoryAdapter(pages=[page]))
    stream.refresh_from_db()
    assert stream.cursor == {}
    assert SyncStream.objects.count() == 1
    assert not AppliedRecord.objects.exists()


@pytest.mark.parametrize("failed_partition", ["", "sent"])
def test_parallel_partitions_close_each_adapter_once_even_after_failure(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch, failed_partition: str
) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Parallel stream partitions require PostgreSQL.")
    adapters: list[MemoryAdapter] = []
    adapters_lock = Lock()
    ready = Barrier(2)

    class PartitionAdapter(MemoryAdapter):
        def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
            return tuple(StreamDefinition("messages", partition) for partition in ("inbox", "sent"))

        def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
            assert not connection.in_atomic_block
            ready.wait(timeout=10)
            if stream.partition == failed_partition:
                raise OperationalError("partition unavailable")
            return StreamPage((stream.partition,), {"done": True})

    def backend(bridge: Channel) -> PartitionAdapter:
        adapter = PartitionAdapter(sync_parallelism=2)
        with adapters_lock:
            adapters.append(adapter)
        return adapter

    monkeypatch.setattr(Channel, "capability_impl", property(backend))
    stream_bridge.config = {**stream_bridge.config, "sync_parallelism": 2, "sync_time_budget": 60}
    stream_bridge.save(update_fields=["config"])
    if failed_partition:
        with pytest.raises(BridgeSyncError, match="messages.*sent.*Integration operation failed"):
            sync_bridge(stream_bridge)
        assert list(AppliedRecord.objects.values_list("key", flat=True)) == ["inbox"]
    else:
        assert sync_bridge(stream_bridge) == 2
        assert set(AppliedRecord.objects.values_list("key", flat=True)) == {"inbox", "sent"}
    assert len(adapters) == 3
    assert [adapter.closed for adapter in adapters] == [1, 1, 1]
    assert SyncStream.objects.count() == 2
    assert SyncStream.objects.get(partition="inbox").cursor == {"done": True}
    assert SyncStream.objects.get(partition="sent").cursor == ({} if failed_partition else {"done": True})


@pytest.mark.parametrize("execution", ["serial", "pool", "single"])
@pytest.mark.parametrize("safe", [False, True])
@pytest.mark.parametrize("stage", ["pull", "push", "reconcile"])
def test_partition_failure_preserves_safe_cause_logs_once_and_replaces_healthy_progress(
    stream_bridge: Channel,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    execution: str,
    safe: bool,
    stage: str,
) -> None:
    if execution == "pool" and connection.vendor != "postgresql":
        pytest.skip("Parallel stream partitions require PostgreSQL.")
    failure = IntegrationError("The address book is unavailable.") if safe else RuntimeError("vendor payload")
    partition = "https://login:password@dav.example/books/a-failed/?token=private"

    class PartitionAdapter(MemoryAdapter):
        def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
            definitions = (StreamDefinition("contacts", partition), StreamDefinition("contacts", "z-healthy"))
            return definitions[:1] if execution == "single" else definitions

        def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
            if stream.partition == partition and stage == "pull":
                raise failure
            return StreamPage((stream.partition,), {"done": True})

    if stage != "pull":
        name = "push_stream" if stage == "push" else "reconcile_stream"
        original = getattr(stream_driver, name)

        def fail_operation(stream: Any, *args: Any, **kwargs: Any) -> Any:
            if stream.partition == partition:
                raise failure
            return original(stream, *args, **kwargs)

        monkeypatch.setattr(stream_driver, name, fail_operation)

    monkeypatch.setattr(Channel, "capability_impl", property(lambda row: PartitionAdapter(sync_parallelism=2)))
    stream_bridge.config = {"sync_parallelism": 2 if execution == "pool" else 1, "sync_time_budget": 60}
    stream_bridge.save(update_fields=["config"])
    with pytest.raises(BridgeSyncError) as refused:
        stream_bridge.run_sync(now=timezone.now())

    message = "The address book is unavailable." if safe else "Integration operation failed."
    expected = f"contacts (/books/a-failed/): {message}"
    assert refused.value.public_message == expected
    assert refused.value.__cause__ is failure
    stream_bridge.refresh_from_db()
    assert stream_bridge.sync_error == stream_bridge.last_error == expected
    assert stream_bridge.sync_stage == stream_bridge.SyncStage.FAILED
    assert stream_bridge.sync_progress["details"] == {
        "stream": "contacts",
        "partition": "/books/a-failed/",
        "stage": stage,
        "failures": [{"stream": "contacts", "partition": "/books/a-failed/", "stage": stage, "message": message}],
    }
    logs = [record for record in caplog.records if record.name == "angee.integrate.streams"]
    assert len(logs) == 1
    assert logs[0].exc_info and logs[0].exc_info[1] is failure and logs[0].exc_info[2] is not None
    assert str(stream_bridge.pk) in logs[0].message
    assert "contacts" in logs[0].message and "/books/a-failed/" in logs[0].message and stage in logs[0].message
    assert "password" not in caplog.text and "token=private" not in caplog.text
    if execution != "single":
        assert AppliedRecord.objects.filter(key="z-healthy").exists()
        assert SyncStream.objects.get(partition="z-healthy").cursor == {"done": True}


def test_partition_error_names_all_failures_in_order_and_bounds_the_total_message() -> None:
    failures = [
        PartitionFailure(StreamDefinition("contacts", "b"), "pull", RuntimeError("private payload")),
        PartitionFailure(StreamDefinition("contacts", "a"), "pull", IntegrationError("Login refused.")),
    ]
    assert BridgeSyncError(failures).public_message == (
        "contacts (a): Login refused.; contacts (b): Integration operation failed."
    )
    failures.append(PartitionFailure(StreamDefinition("contacts", "c"), "pull", IntegrationError("x" * 5000)))
    assert len(BridgeSyncError(failures).public_message) == 4096
    assert BridgeSyncError(failures).public_message.endswith("…")


def test_partition_data_error_persists_safe_stream_partition_and_reason(
    stream_bridge: Channel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Data errors outside record savepoints retain actionable, safe telemetry."""

    class DataAdapter(MemoryAdapter):
        def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
            return (StreamDefinition("messages", "INBOX"),)

        def finish_page(self, stream: Any, page: StreamPage, outcomes: Any) -> None:
            raise DataError("secret database payload")

    monkeypatch.setattr(Channel, "capability_impl", property(lambda row: DataAdapter(pages=[StreamPage(("one",), {})])))
    with pytest.raises(BridgeSyncError) as refused:
        stream_bridge.run_sync(now=timezone.now())

    expected = "messages (INBOX): Database rejected invalid or oversized record data."
    assert refused.value.public_message == expected
    stream_bridge.refresh_from_db()
    assert stream_bridge.sync_error == stream_bridge.last_error == expected
    assert stream_bridge.sync_progress["details"]["failures"] == [
        {
            "stream": "messages",
            "partition": "INBOX",
            "stage": "pull",
            "message": "Database rejected invalid or oversized record data.",
        }
    ]
    assert not AppliedRecord.objects.exists()
    assert SyncStream.objects.current_for_bridge(stream_bridge, "messages").get().cursor == {}


def test_bridge_reports_and_logs_each_failed_partition(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class RefusedAdapter(MemoryAdapter):
        def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
            return (StreamDefinition("contacts", "b"), StreamDefinition("contacts", "a"))

        def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
            if stream.partition == "a":
                raise IntegrationError("Login refused.")
            raise RuntimeError("private vendor payload")

    monkeypatch.setattr(Channel, "capability_impl", property(lambda row: RefusedAdapter()))
    with pytest.raises(BridgeSyncError) as refused:
        stream_bridge.run_sync(now=timezone.now())
    assert refused.value.public_message == ("contacts (a): Login refused.; contacts (b): Integration operation failed.")
    stream_bridge.refresh_from_db()
    assert stream_bridge.sync_error == refused.value.public_message
    assert [failure["partition"] for failure in stream_bridge.sync_progress["details"]["failures"]] == ["a", "b"]
    logs = [record for record in caplog.records if record.name == "angee.integrate.streams"]
    assert len(logs) == 2
    assert all(record.exc_info and record.exc_info[2] is not None for record in logs)


def test_json_merge_uses_fresh_locked_row_for_stale_partition_instances(stream_bridge: Channel) -> None:
    stream_bridge.subscription_state = {"health": "ok", "mailboxes": {"inbox": {"uid": 1}, "sent": {"uid": 2}}}
    stream_bridge.save(update_fields=["subscription_state"])
    inbox = Channel.objects.get(pk=stream_bridge.pk)
    sent = Channel.objects.get(pk=stream_bridge.pk)
    merge_json_state(inbox, "subscription_state", {"uid": 10}, path=("mailboxes", "inbox"))
    merge_json_state(sent, "subscription_state", {"uid": 20}, path=("mailboxes", "sent"))
    stream_bridge.refresh_from_db()
    assert stream_bridge.subscription_state == {
        "health": "ok",
        "mailboxes": {"inbox": {"uid": 10}, "sent": {"uid": 20}},
    }


def test_json_merge_preserves_concurrent_partition_writes(stream_bridge: Channel) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Concurrent row-lock contract requires PostgreSQL.")
    stream_bridge.subscription_state = {"health": "ok"}
    stream_bridge.save(update_fields=["subscription_state"])
    ready = Barrier(2)

    def merge_partition(partition: str, uid: int) -> None:
        close_old_connections()
        try:
            with system_context(reason="test concurrent stream partition"):
                stale = Channel.objects.get(pk=stream_bridge.pk)
                ready.wait(timeout=10)
                merge_json_state(stale, "subscription_state", {"uid": uid}, path=("mailboxes", partition))
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(merge_partition, "inbox", 10)
        second = pool.submit(merge_partition, "sent", 20)
        first.result(timeout=15)
        second.result(timeout=15)
    stream_bridge.refresh_from_db()
    assert stream_bridge.subscription_state == {
        "health": "ok",
        "mailboxes": {"inbox": {"uid": 10}, "sent": {"uid": 20}},
    }


def test_due_semantic_rescan_never_overwrites_concurrent_local_changes(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    adapter = ReadKeysAdapter(
        pages=[StreamPage((RecordChange("one", {"name": "base"}, "base", mapped_payload={"name": "base"}),), {})]
    )
    advance_stream(stream, adapter)
    link = RecordLink.objects.get(stream=stream)
    failure_state(link.record_failure(kind=SyncFailureKind.SEMANTIC, code="retry"), retry_at=timezone.now())
    adapter.remote = {
        "one": RecordChange(
            "one",
            {"name": "remote"},
            "remote",
            mapped_payload={"name": "remote"},
            projection={"value": "local"},
        )
    }
    adapter.applied.clear()
    begin_stream_cycle(stream, adapter)
    assert not adapter.applied
    assert AppliedRecord.objects.get(key="one").payload == {"name": "base"}
    assert any(
        (
            True
            for row in SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=True)
            if row.kind == SyncFailureKind.CONFLICT
        )
    )


@pytest.mark.parametrize("keep", ["remote", "local"])
def test_failed_conflict_reread_keeps_quarantine(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch, keep: str
) -> None:
    stream = current_stream(
        stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    SyncStream.objects.advance(stream, {}, exhausted=True)
    link = RecordLink.objects.observe(stream, "one")
    failure = link.record_failure(kind=SyncFailureKind.CONFLICT, code="both_changed")

    class UnavailableAdapter(ReadKeysAdapter):
        def read_keys(self, stream: Any, keys: Sequence[str]) -> Iterable[RecordChange]:
            raise OperationalError("remote offline")

    adapter = UnavailableAdapter()
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    with pytest.raises(OperationalError, match="remote offline"):
        failure.resolve_conflict(keep=keep)
    link.refresh_from_db()
    assert len(RecordLink.objects.get(pk=link.pk).failures(open_only=True)) == 1
    assert link.status == LinkStatus.FAILED
    assert adapter.closed == 1


@pytest.mark.parametrize("failure", ["missing_candidate", "new_remote_version", "baseline_required"])
def test_conflict_choice_requires_a_completed_apply_or_conditional_write(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from django.core.exceptions import ValidationError

    from angee.integrate.streams import RemoteRejected

    stream = current_stream(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        direction=StreamDirection.BIDIRECTIONAL,
        tombstone_retention=timedelta(days=1),
    )
    link = RecordLink.objects.observe(stream, "one")
    RecordLink.objects.promote(
        link,
        source_payload={"name": "base"},
        source_hash="base",
        mapped_payload={"name": "base"},
        remote_version="v1",
    )
    failure = link.record_failure(kind=SyncFailureKind.CONFLICT, code="both_changed", source_hash="base")
    SyncStream.objects.advance(stream, {}, exhausted=True)

    class RejectedAdapter(ReadKeysAdapter):
        def write_back(self, link: Any, projection: Any, *, expected_version: str) -> WriteBackResult:
            assert expected_version == "v2"
            raise RemoteRejected(details={"status": 412})

    adapter = RejectedAdapter(
        remote={
            "one": RecordChange(
                "one",
                {"name": "remote"},
                "remote",
                "v2",
                mapped_payload={"name": "remote"},
                projection={"value": "local"},
            )
        }
    )
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    keep = "local"
    if failure == "new_remote_version":
        adapter.candidates = (LocalChange("one", {"name": "local"}),)
    elif failure == "baseline_required":
        keep = "remote"
        old = RecordLink.objects.observe(stream, "expired")
        RecordLink.objects.tombstone(old)
        RecordLink.objects.filter(pk=old.pk).update(tombstoned_at=timezone.now() - timedelta(days=2))
        SyncStream.objects.filter(pk=stream.pk).update(last_advanced_at=timezone.now() - timedelta(days=2))
    with pytest.raises(ValidationError):
        failure.resolve_conflict(keep=keep)
    assert len(RecordLink.objects.get(pk=link.pk).failures(open_only=True)) == 1
    if failure == "new_remote_version":
        assert (
            next((row for row in RecordLink.objects.get(pk=link.pk).failures(open_only=True))).source_hash == "remote"
        )
    assert adapter.closed == 1


def test_stale_cursor_reset_cannot_overwrite_successor_progress(stream_bridge: Channel) -> None:
    retired = current_stream(stream_bridge, "events", cursor={"old": 1})
    first = reset_stream(retired, cursor={"new": 1}).stream
    SyncStream.objects.advance(first, {"new": 2})
    late = reset_stream(retired, cursor={"stale": 9}).stream
    late.refresh_from_db()
    assert late.pk == first.pk
    assert late.cursor == {"new": 2}
    assert SyncStream.objects.count() == 2


def test_reconcile_pulse_rejects_a_checkpoint_changed_during_transport(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, reconcile_interval=timedelta(0))

    class RacingAdapter(ReadKeysAdapter):
        def read_keys(self, stream: Any, keys: Sequence[str]) -> Iterable[RecordChange]:
            SyncStream.objects.filter(pk=stream.pk).update(reconcile_state={"newer": True})
            return super().read_keys(stream, keys)

    adapter = RacingAdapter(inventory=("one",), remote={"one": RecordChange("one", {}, "source", mapped_payload={})})
    with pytest.raises(RuntimeError, match="state changed"):
        reconcile_stream(stream, adapter)
    stream.refresh_from_db()
    assert stream.reconcile_state == {"newer": True}
    assert not RecordLink.objects.filter(stream=stream).exists()
    assert not AppliedRecord.objects.exists()


@dataclass
class ShelfAdapter(BridgeImpl):
    """A ``shelves`` stream whose links are the containers of one ``books`` stream each.

    A baseline re-reads the remote's complete state, a delta only what changed;
    the local side never changes, so a record's projection is its applied base.
    A missing record is a remote tombstone, or with ``listing`` a listing miss
    the driver counts against the absence threshold.
    """

    channel: Any = None
    shelves: dict[str, list[str]] = field(default_factory=dict)
    listing: bool = False
    extracted: list[tuple[str, str]] = field(default_factory=list)
    absent: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    declared: int = 0

    def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
        self.declared += 1
        yield StreamDefinition("shelves", kind=StreamKind.RECORD_REPLICA, holds_containers=True)
        for shelf in RecordLink.objects.containers_of(self.channel, "shelves"):
            yield StreamDefinition("books", shelf.external_key, kind=StreamKind.RECORD_REPLICA, container=shelf)

    def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        self.extracted.append((stream.key, stream.partition))
        links = {link.external_key: link for link in stream.links.with_sync_evidence()}
        bases = {key: link.applied_base.mapped_payload if link.applied_base else None for key, link in links.items()}
        if stream.key == "books":
            remote = {f"{stream.partition}/{book}": {"book": book} for book in self.shelves.get(stream.partition, ())}
        else:
            remote = {shelf: {"shelf": shelf} for shelf in self.shelves}
        baseline = stream.phase == StreamPhase.BASELINE
        records = [
            RecordChange(key, payload, key, mapped_payload=payload, projection=bases.get(key))
            for key, payload in sorted(remote.items())
            if baseline
            or key not in links
            or links[key].status == LinkStatus.TOMBSTONE
            or links[key].absence_count
            or bases[key] != payload
        ]
        missing = [
            key for key, link in sorted(links.items()) if key not in remote and link.status != LinkStatus.TOMBSTONE
        ]
        if self.listing:
            return StreamPage(records, {}, absent=missing)
        records += [RecordChange(key, {}, "", tombstone=True, projection=bases[key]) for key in missing]
        return StreamPage(records, {})

    def apply_record(self, stream: Any, record: Any) -> ApplyResult:
        if record.tombstone:
            return ApplyResult(mapped_payload=None)
        row, _ = AppliedRecord.objects.update_or_create(
            key=record.external_key, defaults={"payload": record.source_payload}
        )
        return ApplyResult(target=row, mapped_payload=record.mapped_payload)

    def on_absent(self, stream: Any, links: Sequence[Any]) -> None:
        self.absent.append((stream.partition, tuple(link.external_key for link in links)))


def _books(bridge: Any, shelf: str) -> Any:
    return SyncStream.objects.current_for_bridge(bridge, "books").get(partition=shelf)


def test_a_new_containers_streams_drain_in_the_cycle_that_created_it(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``books`` sorts before ``shelves``; only the container rules order the shelves first."""

    adapter = ShelfAdapter(stream_bridge, shelves={"a": ["x"], "b": ["y"]})
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    assert sync_bridge(stream_bridge) == 4
    assert adapter.extracted[0] == ("shelves", "") and sorted(adapter.extracted[1:]) == [("books", "a"), ("books", "b")]
    assert adapter.declared == 2  # Once at the start, once after the shelves appeared.
    held = SyncStream.objects.current_for_bridge(stream_bridge, "books")
    assert {(stream.partition, stream.container.stream.key) for stream in held} == {("a", "shelves"), ("b", "shelves")}

    adapter.extracted.clear()
    adapter.declared = 0
    adapter.shelves["a"].append("z")
    assert sync_bridge(stream_bridge) == 1  # One wave: the shelves first, then the books they already held.
    assert adapter.extracted[0] == ("shelves", "") and sorted(adapter.extracted[1:]) == [("books", "a"), ("books", "b")]
    assert adapter.declared == 1  # A new book is no container, so nothing is declared again.


def test_a_child_stream_names_one_live_container_of_its_own_bridge(stream_bridge: Channel) -> None:
    shelves = current_stream(stream_bridge, "shelves", kind=StreamKind.RECORD_REPLICA)
    shelf = RecordLink.objects.observe(shelves, "a", target=shelves)
    unbound = RecordLink.objects.observe(shelves, "c")
    other = make_integration("stream-protocol-other", model=Channel)
    refused = ((stream_bridge, "b", shelf), (other, "a", shelf), (stream_bridge, "c", unbound))
    for bridge, partition, container in refused:
        with pytest.raises(ValidationError, match="live container"):
            current_stream(bridge, "books", partition, kind=StreamKind.RECORD_REPLICA, container=container)

    legacy = current_stream(stream_bridge, "books", "a", kind=StreamKind.RECORD_REPLICA)
    books = current_stream(stream_bridge, "books", "a", kind=StreamKind.RECORD_REPLICA, container=shelf)
    assert books.pk == legacy.pk and SyncStream.objects.get(pk=books.pk).container_id == shelf.pk
    with pytest.raises(ValidationError, match="container cannot change"):
        current_stream(stream_bridge, "books", "a", kind=StreamKind.RECORD_REPLICA)
    books.container = RecordLink.objects.observe(shelves, "a2", target=shelves)
    with pytest.raises(ValidationError, match="immutable"):
        books.save(update_fields=["container", "updated_at"])


def test_streams_lock_roots_before_the_streams_their_containers_hold(stream_bridge: Channel) -> None:
    """``in_lock_order``: whatever their keys and primary keys, a root stream comes before a contained one."""

    shelves = current_stream(stream_bridge, "zz-shelves", kind=StreamKind.RECORD_REPLICA)
    shelf = RecordLink.objects.observe(shelves, "a", target=shelves)
    books = current_stream(stream_bridge, "books", "a", kind=StreamKind.RECORD_REPLICA, container=shelf)
    notes = current_stream(stream_bridge, "notes", kind=StreamKind.RECORD_REPLICA)

    ordered = SyncStream.objects.filter(integration_id=stream_bridge.pk).in_lock_order()
    assert [row.pk for row in ordered] == [notes.pk, shelves.pk, books.pk]


def test_a_contained_stream_cannot_hold_containers(stream_bridge: Channel) -> None:
    """Containers nest one level, so ``in_lock_order``'s roots before contained streams is parents before children."""

    shelves = current_stream(stream_bridge, "shelves", kind=StreamKind.RECORD_REPLICA, holds_containers=True)
    shelf = RecordLink.objects.observe(shelves, "a", target=shelves)
    with pytest.raises(ValidationError, match="A contained stream cannot hold containers"):
        current_stream(
            stream_bridge, "books", "a", kind=StreamKind.RECORD_REPLICA, container=shelf, holds_containers=True
        )
    assert not SyncStream.objects.filter(integration_id=stream_bridge.pk, key="books").exists()


def test_a_tombstoned_container_retires_its_streams_and_its_revival_reopens_them(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = ShelfAdapter(stream_bridge, shelves={"a": ["x", "y"], "b": ["w"]})
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    sync_bridge(stream_bridge)
    held = _books(stream_bridge, "a")
    removed = adapter.shelves.pop("a")
    adapter.extracted.clear()

    assert sync_bridge(stream_bridge) == 1
    held.refresh_from_db()
    assert (held.state, held.reconcile_state) == (StreamState.RETIRED, {})
    assert ("books", "a") not in adapter.extracted
    assert set(RecordLink.objects.filter(stream=held).values_list("status", flat=True)) == {LinkStatus.TOMBSTONE}
    assert adapter.absent == [("a", ("a/x", "a/y"))]
    assert AppliedRecord.objects.filter(key__in=("a/x", "a/y")).count() == 2
    assert _books(stream_bridge, "b").state == StreamState.OPEN
    assert SyncStream.objects.bump_generation(held).pk == held.pk  # No resync revives it while its shelf is gone.
    reset = reset_stream(held, cursor={})
    assert (reset.stream.pk, reset.exhausted, reset.reset) == (held.pk, True, False)
    assert advance_stream(held, adapter).exhausted and ("books", "a") not in adapter.extracted

    adapter.shelves["a"] = removed
    sync_bridge(stream_bridge)
    reopened = _books(stream_bridge, "a")
    held.refresh_from_db()
    assert (reopened.generation, reopened.state, reopened.container_id) == (
        held.generation + 1,
        StreamState.OPEN,
        held.container_id,
    )
    assert held.state == StreamState.RETIRED
    assert set(RecordLink.objects.filter(stream=reopened).values_list("status", "absence_count")) == {
        (LinkStatus.CURRENT, 0)
    }


def test_a_container_missing_from_a_listing_pauses_its_stream_until_its_absence_is_confirmed(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = ShelfAdapter(stream_bridge, shelves={"a": ["x"], "b": ["w"]}, listing=True)
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    sync_bridge(stream_bridge)
    held = _books(stream_bridge, "a")
    removed = adapter.shelves.pop("a")

    for cycle in ("missed", "back", "missed", "confirmed"):
        if cycle == "back":
            adapter.shelves["a"] = removed
        elif adapter.shelves.get("a"):
            adapter.shelves.pop("a")
        adapter.extracted.clear()
        sync_bridge(stream_bridge)
        held.refresh_from_db()
        shelf = RecordLink.objects.get(stream__key="shelves", external_key="a")
        if cycle == "missed":
            assert (shelf.status, shelf.absence_count, held.state) == (LinkStatus.UNAVAILABLE, 1, StreamState.OPEN)
            assert ("books", "a") not in adapter.extracted
            assert [item.partition for item in adapter.streams() if item.key == "books"] == ["b"]
        elif cycle == "back":
            assert (shelf.status, shelf.absence_count, held.state) == (LinkStatus.CURRENT, 0, StreamState.OPEN)
            assert ("books", "a") in adapter.extracted
    assert (shelf.status, held.state) == (LinkStatus.TOMBSTONE, StreamState.RETIRED)
    assert RecordLink.objects.get(stream=held).status == LinkStatus.TOMBSTONE


def test_a_container_that_returns_pauses_the_retirement_pass_until_its_stream_reopens(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = ShelfAdapter(stream_bridge, shelves={"a": ["x", "y"]})
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    sync_bridge(stream_bridge)
    shelves, held = SyncStream.objects.current_for_bridge(stream_bridge, "shelves").get(), _books(stream_bridge, "a")
    shelf = RecordLink.objects.get(stream=shelves, external_key="a")
    RecordLink.objects.tombstone(shelf)
    assert SyncStream.objects.retire_children(shelves) == 1
    assert list(SyncStream.objects.retiring(shelves)) == [held]
    with transaction.atomic():
        RecordLink.objects.revalidate(shelf)

    assert not SyncStream.objects.retiring(shelves).exists()
    pending = SyncStream.objects.get(pk=held.pk).reconcile_state
    assert pending and reconcile_stream(held, adapter) == 0
    held.refresh_from_db()
    assert (held.state, held.reconcile_state) == (StreamState.RETIRED, pending)  # Paused, not dropped.
    assert set(RecordLink.objects.filter(stream=held).values_list("status", "absence_count")) == {
        (LinkStatus.CURRENT, 0)
    }
    assert adapter.absent == []

    sync_bridge(stream_bridge)  # The live shelf declares its books again, which reopens them.
    held.refresh_from_db()
    assert held.reconcile_state == {} and _books(stream_bridge, "a").state == StreamState.OPEN


def test_retirement_closes_the_open_failures_of_the_links_it_tombstones(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = ShelfAdapter(stream_bridge, shelves={"a": ["x"]})
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    sync_bridge(stream_bridge)
    link = RecordLink.objects.get(stream__key="books", external_key="a/x")
    link.record_failure(kind=SyncFailureKind.CONFLICT, code="both_changed")
    link.refresh_from_db()
    assert link.status == LinkStatus.FAILED
    adapter.shelves.pop("a")

    sync_bridge(stream_bridge)
    link.refresh_from_db()
    assert (link.status, link.failures(open_only=True)) == (LinkStatus.TOMBSTONE, ())


@dataclass
class OrderedShelfAdapter(ShelfAdapter):
    """Records when each stream applies a page and when each starts extracting, across workers."""

    events: list[str] = field(default_factory=list)

    def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        self.events.append(f"extract {stream.key}")
        return super().extract(stream, page_bound, deadline=deadline)

    def finish_page(self, stream: Any, page: StreamPage, outcomes: Any) -> None:
        self.events.append(f"applied {stream.key}")

    def on_absent(self, stream: Any, links: Sequence[Any]) -> None:
        self.events.append(f"absent {stream.partition}")
        super().on_absent(stream, links)


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Parallel stream partitions require PostgreSQL.")
def test_a_parallel_wave_starts_a_held_stream_after_its_container_stream_finishes(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = OrderedShelfAdapter(stream_bridge, shelves={"a": ["x"], "b": ["y"], "c": ["z"]}, listing=True)
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    sync_bridge(stream_bridge)
    adapter.events.clear()
    adapter.extracted.clear()
    adapter.shelves.pop("a")

    sync_bridge(stream_bridge)
    applied = adapter.events.index("applied shelves")
    assert [index for index, event in enumerate(adapter.events) if event == "extract books"]
    assert all(index > applied for index, event in enumerate(adapter.events) if event == "extract books")
    assert ("books", "a") not in adapter.extracted
    assert _books(stream_bridge, "a").state == StreamState.OPEN


def test_an_identity_longer_than_a_link_key_is_refused_once_at_the_stream(stream_bridge: Channel) -> None:
    stream = current_stream(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    long_key = "k" * (EXTERNAL_KEY_LENGTH + 1)
    records = [
        RecordChange(key, {"name": key[:8]}, f"hash-{len(key)}", mapped_payload={"name": key[:8]})
        for key in (long_key, "person:1")
    ]
    again = [records[0], replace(records[1], projection=records[1].mapped_payload)]
    adapter = MemoryAdapter(pages=[StreamPage(records, {"page": 1}), StreamPage(again, {"page": 2})])
    for _cycle in range(2):
        result = advance_stream(stream, adapter)
        assert (result.count, len(result.failure_ids)) == (1 if _cycle == 0 else 0, 1)
        stream = result.stream
    assert list(RecordLink.objects.filter(stream=stream).values_list("external_key", flat=True)) == ["person:1"]
    (refusal,) = SyncStream.objects.get(pk=stream.pk).sync_failures(open_only=True)
    assert (refusal.code, refusal.link_id, refusal.attempts) == ("identity_too_long", None, 2)


def test_no_partition_is_opened_or_read_after_the_deadline(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = ShelfAdapter(stream_bridge, shelves={"a": ["x"]})
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    stream_bridge.config = {**stream_bridge.config, "sync_time_budget": 0}
    assert sync_bridge(stream_bridge) == 0
    assert adapter.extracted == []
    assert not SyncStream.objects.filter(integration_id=stream_bridge.pk).exists()


def test_a_retirement_backlog_runs_after_the_sibling_streams_drain(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = OrderedShelfAdapter(stream_bridge, shelves={"a": [f"x{index}" for index in range(6)], "b": ["y"]})
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    sync_bridge(stream_bridge)
    adapter.events.clear()
    adapter.shelves.pop("a")

    sync_bridge(stream_bridge)
    assert adapter.events.index("extract books") < adapter.events.index("absent a")
    assert adapter.absent == [("a", tuple(f"a/x{index}" for index in range(6)))]
    assert _books(stream_bridge, "a").reconcile_state == {}


def test_a_self_held_declaration_fails_fast(stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch) -> None:
    books = current_stream(stream_bridge, "books", "a", kind=StreamKind.RECORD_REPLICA)
    own = RecordLink.objects.observe(books, "a", target=books)

    @dataclass
    class SelfHeld(ShelfAdapter):
        def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
            yield StreamDefinition("books", "a", kind=StreamKind.RECORD_REPLICA, container=own)

    adapter = SelfHeld(stream_bridge)
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    with pytest.raises(AdapterContractError, match="cycle"):
        sync_bridge(stream_bridge)
    assert adapter.extracted == []



@pytest.mark.parametrize("direction", [StreamDirection.PUSH, StreamDirection.PULL, StreamDirection.BIDIRECTIONAL])
def test_only_push_links_let_their_row_go_and_the_push_writes_its_removal(
    stream_bridge: Channel, direction: StreamDirection
) -> None:
    from angee.messaging.testing.models import Person

    stream = current_stream(stream_bridge, "people", kind=StreamKind.RECORD_REPLICA, direction=direction)
    person = Person.objects.create(display_name="Ada")
    link = RecordLink.objects.observe(stream, "ada", target=person)
    RecordLink.objects.promote(link, source_payload={}, source_hash="v1", mapped_payload={"n": 1}, remote_version="v1")
    if direction != StreamDirection.PUSH:
        # A pulled or two-way record is the source's: its link keeps it, refused as every protecting edge refuses.
        with pytest.raises(models.ProtectedError, match=f"{RecordLink._meta.label} rows still refer to "):
            Person.objects.get(pk=person.pk).delete()
        assert RecordLink.objects.get(pk=link.pk).target_object_id == str(person.pk)
        return

    Person.objects.get(pk=person.pk).delete()

    assert RecordLink.objects.get(pk=link.pk).target_object_id is None

    class RemovingAdapter(MemoryAdapter):
        def write_back(self, link: Any, projection: Any, *, expected_version: str) -> WriteBackResult:
            result = super().write_back(link, projection, expected_version=expected_version)
            return replace(result, tombstone=projection is None)

    adapter = RemovingAdapter()
    assert push_stream(stream, adapter).count == 1
    assert adapter.written == [("ada", None, "v1")]
    assert RecordLink.objects.get(pk=link.pk).status == LinkStatus.TOMBSTONE
    assert push_stream(stream, adapter).count == 0


def test_a_push_link_naming_a_row_deleted_without_releasing_it_writes_the_rows_removal(stream_bridge: Channel) -> None:
    """A delete that locked nothing (a cascade) read no link, then a first push bound one before the row went.

    The link still names the missing row; the push reads it as released and writes its removal.
    """

    from angee.messaging.testing.models import Person

    stream = current_stream(stream_bridge, "people", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.PUSH)
    person = Person.objects.create(display_name="Ada")
    link = RecordLink.objects.observe(stream, "ada")

    def bind_as_the_push_does(sender: Any, **kwargs: Any) -> None:
        # After the delete's last read of the links naming its row (none yet), the push binds the row it wrote.
        RecordLink.objects.promote(
            link, source_payload={}, source_hash="v1", mapped_payload={"n": 1}, remote_version="v1", target=person
        )

    # The delete reads those links at each of the row's tables: the person's, then its party parent's.
    (party,) = Person._meta.parents
    pre_delete.connect(bind_as_the_push_does, sender=party, dispatch_uid="tests.integrate.bind_during_delete")
    try:
        Person.objects.get(pk=person.pk).delete()
    finally:
        pre_delete.disconnect(sender=party, dispatch_uid="tests.integrate.bind_during_delete")
    assert RecordLink.objects.get(pk=link.pk).target_object_id == str(person.pk)

    class RemovingAdapter(MemoryAdapter):
        def write_back(self, link: Any, projection: Any, *, expected_version: str) -> WriteBackResult:
            result = super().write_back(link, projection, expected_version=expected_version)
            return replace(result, tombstone=projection is None)

    adapter = RemovingAdapter()
    assert push_stream(stream, adapter).count == 1
    assert adapter.written == [("ada", None, "v1")]
    written = RecordLink.objects.get(pk=link.pk)
    assert (written.status, written.target_object_id) == (LinkStatus.TOMBSTONE, None)
    assert push_stream(stream, adapter).count == 0


@pytest.mark.parametrize("direction", [StreamDirection.PUSH, StreamDirection.PULL])
def test_creation_undo_and_deletion_judge_a_link_by_one_live_filter(
    stream_bridge: Channel, direction: StreamDirection
) -> None:
    """Creation undo's structural check (``has_live_references``) and the delete receiver agree on push links."""

    from angee.messaging.testing.models import Person

    stream = current_stream(stream_bridge, "people", kind=StreamKind.RECORD_REPLICA, direction=direction)
    person = Person.objects.create(display_name="Ada")
    link = RecordLink.objects.observe(stream, "ada", target=person)
    RecordLink.objects.promote(link, source_payload={}, source_hash="v1", mapped_payload={"n": 1}, remote_version="v1")

    held = direction != StreamDirection.PUSH
    assert has_live_references(Person.objects.get(pk=person.pk)) is held
    if held:
        with pytest.raises(models.ProtectedError):
            Person.objects.get(pk=person.pk).delete()
    else:
        Person.objects.get(pk=person.pk).delete()
    assert Person.objects.filter(pk=person.pk).exists() is held


def test_a_record_link_teardown_releases_push_links_only_and_never_raises(stream_bridge: Channel) -> None:
    """The receiver refuses on live links first; the teardown itself lets push links go and leaves the rest."""

    from angee.messaging.testing.models import Person

    person = Person.objects.create(display_name="Ada")
    pulled, pushed = (
        RecordLink.objects.observe(
            current_stream(stream_bridge, key, kind=StreamKind.RECORD_REPLICA, direction=direction), "ada",
            target=person,
        )
        for key, direction in (("pulled", StreamDirection.PULL), ("pushed", StreamDirection.PUSH))
    )

    # Base runs the teardown inside the deleting transaction; a direct call opens it.
    with transaction.atomic():
        RecordLink.teardown_for_target(person)

    assert RecordLink.objects.get(pk=pulled.pk).target_object_id == str(person.pk)
    assert RecordLink.objects.get(pk=pushed.pk).target_object_id is None


@pytest.mark.parametrize("bound", [True, False], ids=["written-before", "new"])
def test_a_row_deleted_during_its_push_is_bound_by_nothing_and_its_removal_is_the_next_write(
    stream_bridge: Channel, bound: bool
) -> None:
    from angee.messaging.testing.models import Person

    stream = current_stream(stream_bridge, "people", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.PUSH)
    SyncStream.objects.advance(stream, {}, exhausted=True)
    person = Person.objects.create(display_name="Ada")
    if bound:
        link = RecordLink.objects.observe(stream, "ada", target=person)
        RecordLink.objects.promote(
            link, source_payload={}, source_hash="v1", mapped_payload={"n": 1}, remote_version="v1"
        )

    class DeletingAdapter(MemoryAdapter):
        def write_back(self, link: Any, projection: Any, *, expected_version: str) -> WriteBackResult:
            result = super().write_back(link, projection, expected_version=expected_version)
            if projection is not None:
                # The row goes while its write is in flight, after the push read it.
                Person.objects.get(pk=person.pk).delete()
            return replace(result, remote_version=f"v{len(self.written) + 1}", tombstone=projection is None)

    adapter = DeletingAdapter(candidates=(LocalChange("ada", {"n": 2}, target=person),))
    assert push_stream(stream, adapter).count == 1
    link = RecordLink.objects.get(stream=stream, external_key="ada")
    assert (link.target_object_id, link.remote_version) == (None, "v2")

    adapter.candidates = ()
    assert push_stream(stream, adapter).count == 1
    # The removal is written over the version the raced write left.
    assert adapter.written[-1] == ("ada", None, "v2")
    assert RecordLink.objects.get(pk=link.pk).status == LinkStatus.TOMBSTONE


def test_keeping_the_remote_of_a_push_stream_accepts_its_version_until_the_next_local_change(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    from angee.integrate.states import ConflictKeep
    from angee.integrate.streams import RemoteRejected

    stream = current_stream(stream_bridge, "outbox", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.PUSH)
    link = RecordLink.objects.observe(stream, "one")
    RecordLink.objects.promote(link, source_payload={}, source_hash="v1", mapped_payload={"n": 0}, remote_version="v1")
    SyncStream.objects.advance(stream, {}, exhausted=True)

    class RefusingAdapter(ReadKeysAdapter):
        refuse: bool = True

        def write_back(self, link: Any, projection: Any, *, expected_version: str) -> WriteBackResult:
            if self.refuse:
                raise RemoteRejected()
            return super().write_back(link, projection, expected_version=expected_version)

    adapter = RefusingAdapter(
        candidates=(LocalChange("one", {"n": 1}),),
        remote={"one": RecordChange("one", {"id": 1}, "v2-hash", remote_version="v2", mapped_payload={"n": 5})},
    )
    assert push_stream(stream, adapter).failure_ids
    [conflict] = RecordLink.objects.get(pk=link.pk).failures(open_only=True)
    adapter.refuse = False

    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))
    conflict.resolve_conflict(keep=ConflictKeep.REMOTE)

    accepted = RecordLink.objects.with_sync_evidence().get(pk=link.pk)
    # The remote's version is the base, over the local projection: nothing applies, nothing pushes.
    assert (accepted.remote_version, accepted.applied_base.mapped_payload) == ("v2", {"n": 1})
    assert accepted.failures(open_only=True) == ()
    assert push_stream(stream, adapter).count == 0
    adapter.candidates = (LocalChange("one", {"n": 2}),)
    assert push_stream(stream, adapter).count == 1
    assert adapter.written[-1] == ("one", {"n": 2}, "v2")
