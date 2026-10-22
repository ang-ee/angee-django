"""The generic driver commits domain rows with their opaque stream positions."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo
from threading import Barrier, Lock
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from django.db import (
    DatabaseError,
    DataError,
    OperationalError,
    close_old_connections,
    connection,
    connections,
    models,
    transaction,
)
from django.utils import timezone
from rebac import system_context

from angee.integrate import streams as stream_driver
from angee.integrate.errors import IntegrationError
from angee.integrate.impl import AdapterContractError, BridgeImpl
from angee.integrate.models import merge_json_state
from angee.integrate.states import (
    LinkStatus,
    StreamDefinition,
    StreamDirection,
    StreamKind,
    StreamPhase,
    SyncFailureKind,
    SyncFailureStatus,
)
from angee.integrate.streams import (
    ApplyResult,
    BridgeSyncError,
    CursorInvalid,
    LocalChange,
    PartitionFailure,
    RecordChange,
    SemanticError,
    StreamPage,
    WriteBackResult,
    advance_stream,
    begin_stream_cycle,
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


class AppliedRecord(models.Model):
    """Domain-owned idempotent sink independent of the record protocol tables."""

    key = models.CharField(max_length=80, unique=True)
    payload = models.JSONField(default=dict)

    class Meta:
        app_label = "integrate"
        db_table = "test_integrate_applied_record"


@pytest.fixture
def stream_bridge(composed_tables: None) -> Iterator[Channel]:
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
            mapping_version=record.mapping_version if isinstance(record, RecordChange) else 1,
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
    monkeypatch.setattr(type(stream_bridge), "capability_impl", property(lambda self: adapter))

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
    adapter.pages = [StreamPage((replace(record, **changed_evidence, projection={"name": "Ada"}),), {"page": 2})]
    assert advance_stream(stream, adapter).count == 1
    assert adapter.applied == ["person:1", "person:1"]
    assert applied_history().count() == 2
    revision = applied_history().order_by("-history_date").first()
    for name, expected in changed_evidence.items():
        assert getattr(revision, name) == expected


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
        resolve_applied = stream_driver._resolve_applied

        def fail_settlement(stream: Any, record: Any) -> None:
            key = record.external_key if isinstance(record, RecordChange) else record
            if key == "poison":
                transaction.set_rollback(True)
                raise DataError("private record payload and database details")
            resolve_applied(stream, record)

        monkeypatch.setattr(stream_driver, "_resolve_applied", fail_settlement)

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


def test_changed_remote_failure_defers_older_quarantine_for_the_same_identity(stream_bridge: Channel) -> None:
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
    assert all(row.status == SyncFailureStatus.OPEN and row.retry_at > timezone.now() for row in failures)
    assert failures[0].retry_at == failures[1].retry_at
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
    from django.core.exceptions import ValidationError

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
def test_seed_rejects_falsy_non_object_cursors(stream_bridge: Channel, cursor: Any) -> None:
    class InvalidSeedAdapter(MemoryAdapter):
        def seed_cursor(self, stream: Any, legacy_cursor: dict[str, Any]) -> Any:
            return cursor

    stream_bridge.cursor = {"legacy": 1}
    stream_bridge.save(update_fields=["cursor"])
    adapter = InvalidSeedAdapter()
    adapter.integration = stream_bridge

    with pytest.raises(AdapterContractError, match="plain finite JSON"):
        open_stream(stream_bridge, "records", "", adapter)

    stream = SyncStream.objects.current_for_bridge(stream_bridge, "records").get()
    assert stream.cursor == {}
    assert stream.last_advanced_at is None


def test_seed_cutover_locks_bridge_before_stream(stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Bridge cutover row-lock ordering requires PostgreSQL.")
    stream_bridge.cursor = {"legacy": 1}
    stream_bridge.save(update_fields=["cursor"])
    current_stream(stream_bridge, "records")
    manager_class = type(SyncStream.objects)
    lock_current = manager_class.lock_current

    def try_bridge_lock() -> None:
        close_old_connections()
        try:
            with (
                system_context(reason="test competing bridge cutover"),
                pytest.raises(DatabaseError) as denied,
                transaction.atomic(),
            ):
                Channel.objects.select_for_update(nowait=True).get(pk=stream_bridge.pk)
            assert denied.value.__cause__.sqlstate == "55P03"
        finally:
            connections.close_all()

    def verify_bridge_locked(manager: Any, stream: Any) -> Any:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(try_bridge_lock).result(timeout=10)
        return lock_current(manager, stream)

    monkeypatch.setattr(manager_class, "lock_current", verify_bridge_locked)
    adapter = MemoryAdapter()
    adapter.integration = stream_bridge
    stream = open_stream(stream_bridge, "records", "", adapter)
    assert stream.last_advanced_at is not None


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

    if execution == "pool":
        # Exercise the future collector deterministically on SQLite. Native
        # threaded database behavior is covered by the PostgreSQL test above.
        class InlinePool:
            def __init__(self, **kwargs: Any) -> None:
                pass

            def __enter__(self) -> InlinePool:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

            def submit(self, fn: Any, *args: Any) -> Future:
                future: Future = Future()
                try:
                    future.set_result(fn(*args))
                except Exception as error:
                    future.set_exception(error)
                return future

        monkeypatch.setattr(stream_driver, "connection", SimpleNamespace(vendor="postgresql", in_atomic_block=False))
        monkeypatch.setattr(stream_driver, "ThreadPoolExecutor", InlinePool)
        monkeypatch.setattr(
            stream_driver,
            "_drain_partition",
            lambda bridge, definition, deadline: stream_driver._drain(
                bridge, bridge.capability_impl, definition, deadline
            ),
        )

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
    assert stream_bridge.sync_progress["stage"] == stream_bridge.SyncStage.FAILED
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
