"""The generic driver commits domain rows with their opaque stream positions."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from threading import Barrier, Lock
from typing import Any

import pytest
from django.db import OperationalError, close_old_connections, connection, connections, models
from django.utils import timezone
from rebac import system_context

from angee.integrate.models import merge_json_state
from angee.integrate.records import (
    DiscrepancyKind,
    DiscrepancyStatus,
    LinkStatus,
    StreamDirection,
    StreamKind,
    StreamPhase,
)
from angee.integrate.streams import (
    ApplyResult,
    ChangeKind,
    CursorInvalid,
    LocalChange,
    RecordChange,
    SemanticError,
    StreamDefinition,
    StreamPage,
    WriteBackResult,
    advance_stream,
    begin_stream_cycle,
    classify_change,
    push_stream,
    reconcile_stream,
    sync_bridge,
)
from tests.conftest import _create_missing_tables, make_integration
from tests.integrate_models import RecordLink, RecordRevision, SyncDiscrepancy, SyncStream
from tests.messaging_models import Channel


class AppliedRecord(models.Model):
    """Domain-owned idempotent sink independent of the record protocol tables."""

    key = models.CharField(max_length=80, unique=True)
    payload = models.JSONField(default=dict)

    class Meta:
        app_label = "integrate"
        db_table = "test_integrate_applied_record"


@pytest.fixture
def stream_bridge(record_sync_tables: None) -> Iterator[Channel]:
    """Run transport tests outside any enclosing Django transaction."""

    created = _create_missing_tables((AppliedRecord,))
    try:
        with system_context(reason="test stream protocol"):
            yield make_integration("stream-protocol", model=Channel)
    finally:
        with connection.schema_editor() as editor:
            for model in reversed(created):
                editor.delete_model(model)


@dataclass
class MemoryAdapter:
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
    sync_deadline: float | None = None

    def streams(self, *, using: str | None = None) -> Iterable[StreamDefinition]:
        return (StreamDefinition("records"),)

    def extract(self, stream: Any, page_bound: int, *, using: str | None = None) -> StreamPage:
        assert not connections[using].in_atomic_block
        assert page_bound > 0
        self.extracted += 1
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    def apply(self, stream: Any, page: StreamPage, *, using: str | None = None) -> Iterable[ApplyResult]:
        assert connections[using].in_atomic_block
        record = page.records[0]
        key = record.external_key if isinstance(record, RecordChange) else record
        payload = record.source_payload if isinstance(record, RecordChange) else {"event": key}
        row, _ = AppliedRecord.objects.db_manager(using).update_or_create(key=key, defaults={"payload": payload})
        self.applied.append(key)
        if key == self.semantic_key:
            raise SemanticError("invalid_name", details={"key": key})
        if key == self.infrastructure_key:
            raise OperationalError("database unavailable")
        return (
            ApplyResult(
                external_key=key,
                target=row,
                local_hash=record.source_hash if isinstance(record, RecordChange) else "",
                mapped_payload=payload,
            ),
        )

    def enumerate_keys(self, stream: Any, *, using: str | None = None) -> Iterable[str]:
        assert not connections[using].in_atomic_block
        return self.inventory

    def finish_page(self, stream: Any, page: StreamPage, outcomes: Any, *, using: str | None = None) -> None:
        assert connections[using].in_atomic_block

    def local_changes(self, stream: Any, *, using: str | None = None) -> Iterable[LocalChange]:
        assert not connections[using].in_atomic_block
        return self.candidates

    def write_back(
        self,
        link: Any,
        projection: Any,
        *,
        expected_version: str,
        using: str | None = None,
    ) -> WriteBackResult:
        assert not connections[using].in_atomic_block
        self.written.append((link.external_key, projection, expected_version))
        return WriteBackResult("v2", "changed", projection)

    def close(self) -> None:
        self.closed += 1


@dataclass
class ReadKeysAdapter(MemoryAdapter):
    """Optional identity transport; the base adapter deliberately omits it."""

    remote: dict[str, RecordChange] = field(default_factory=dict)
    reads: list[tuple[str, ...]] = field(default_factory=list)

    def read_keys(self, stream: Any, keys: Sequence[str], *, using: str | None = None) -> Iterable[RecordChange]:
        assert not connections[using].in_atomic_block
        self.reads.append(tuple(keys))
        return tuple(self.remote[key] for key in sorted(keys))


@pytest.mark.parametrize(
    "remote,local,remote_base,local_base,tombstone,expected",
    [
        ("r", "l", "r", "l", False, ChangeKind.UNCHANGED),
        ("r2", "l", "r", "l", False, ChangeKind.APPLY),
        ("r", "l2", "r", "l", False, ChangeKind.WRITE_BACK),
        ("r2", "l2", "r", "l", False, ChangeKind.CONFLICT),
        ("new", "", "", "", False, ChangeKind.APPLY),
        ("", "new", "", "", False, ChangeKind.WRITE_BACK),
        ("", "l", "r", "l", True, ChangeKind.APPLY),
        ("", "edited", "r", "l", True, ChangeKind.CONFLICT),
    ],
)
def test_three_way_classification(
    remote: str,
    local: str,
    remote_base: str,
    local_base: str,
    tombstone: bool,
    expected: ChangeKind,
) -> None:
    assert (
        classify_change(
            remote_hash=remote,
            local_hash=local,
            remote_base_hash=remote_base,
            local_base_hash=local_base,
            tombstone=tombstone,
        )
        == expected
    )


@pytest.mark.parametrize("trigger", ["invalid_cursor", "stream_flag", "page_flag"])
def test_epoch_reset_retains_identity_and_reverifies_baseline(stream_bridge: Channel, trigger: str) -> None:
    stream = SyncStream.objects.current(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    link = RecordLink.objects.observe(stream, "person:1")
    RecordLink.objects.promote(
        link, source_payload={"name": "Ada"}, source_hash="base", mapped_payload={"name": "Ada"}, local_hash="base"
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
    adapter.pages = [StreamPage((RecordChange("person:1", {"name": "Ada"}, "base", "base"),), {"done": True})]
    advance_stream(reset.stream, adapter)
    link.refresh_from_db()
    assert link.last_verified_generation == reset.stream.generation
    assert RecordLink.objects.count() == RecordRevision.objects.count() == 1


def test_sweep_counts_absence_then_retains_confirmed_tombstone(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        reconcile_interval=timedelta(0),
        absence_threshold=2,
    )
    missing = RecordLink.objects.observe(stream, "missing")
    present = RecordLink.objects.observe(stream, "present")
    adapter = MemoryAdapter(inventory=("present",))
    assert reconcile_stream(stream, adapter) == 1
    missing.refresh_from_db()
    assert (missing.absence_count, missing.status) == (1, LinkStatus.UNAVAILABLE)
    assert reconcile_stream(stream, adapter) == 1
    missing.refresh_from_db()
    present.refresh_from_db()
    assert (missing.absence_count, missing.status) == (2, LinkStatus.TOMBSTONE)
    assert missing.tombstoned_at is not None
    assert present.absence_count == 0
    assert RecordLink.objects.count() == 2


def test_stale_peer_beyond_tombstone_retention_requires_baseline(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(
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
    stream = SyncStream.objects.current(stream_bridge, "events", cursor={"offset": 0})
    adapter = MemoryAdapter(
        pages=[StreamPage(("first", "broken"), {"offset": 2})],
        infrastructure_key="broken",
    )
    with pytest.raises(OperationalError, match="database unavailable"):
        advance_stream(stream, adapter)
    stream.refresh_from_db()
    assert stream.cursor == {"offset": 0}
    assert not AppliedRecord.objects.exists()
    assert not SyncDiscrepancy.objects.exists()


def test_semantic_failure_quarantines_one_row_and_continues_page(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(stream_bridge, "events")
    adapter = MemoryAdapter(pages=[StreamPage(("poison", "healthy"), {"offset": 2})], semantic_key="poison")
    result = advance_stream(stream, adapter)
    stream.refresh_from_db()
    assert result.count == 1
    assert stream.cursor == {"offset": 2}
    assert list(AppliedRecord.objects.values_list("key", flat=True)) == ["healthy"]
    discrepancy = SyncDiscrepancy.objects.get(pk=result.discrepancy_ids[0])
    assert (discrepancy.kind, discrepancy.code) == (DiscrepancyKind.SEMANTIC, "invalid_name")


@pytest.mark.parametrize("status", [DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY])
@pytest.mark.parametrize("tombstone", [False, True])
def test_identity_rescan_resolves_current_remote_state_without_advancing_stream(
    stream_bridge: Channel, status: str, tombstone: bool
) -> None:
    stream = SyncStream.objects.current(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    adapter = ReadKeysAdapter(
        pages=[
            StreamPage(
                (
                    RecordChange("person:1", {"name": "base"}, "base"),
                    RecordChange("healthy", {"name": "healthy"}, "healthy"),
                ),
                {"page": 1},
            )
        ]
    )
    advance_stream(stream, adapter)
    adapter.semantic_key = "person:1"
    adapter.pages = [
        StreamPage((RecordChange("person:1", {"name": "invalid"}, "invalid", "base"),), {"page": 2})
    ]
    failure = advance_stream(stream, adapter)
    discrepancy = SyncDiscrepancy.objects.get(pk=failure.discrepancy_ids[0])
    SyncDiscrepancy.objects.filter(pk=discrepancy.pk).update(status=status, retry_at=timezone.now())
    future = RecordLink.objects.observe(stream, "future")
    SyncDiscrepancy.objects.record(
        stream,
        link=future,
        kind=DiscrepancyKind.SEMANTIC,
        code="invalid_name",
        source_hash="future",
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
            "person:1", payload, "" if tombstone else "corrected", "base", tombstone=tombstone
        ),
    }
    begin_stream_cycle(stream, adapter)
    discrepancy.refresh_from_db()
    link = RecordLink.objects.get(stream=stream, external_key="person:1")
    assert adapter.reads == [("person:1",)]
    assert adapter.extracted == 2
    assert adapter.applied == ["person:1"]
    assert not adapter.written
    assert AppliedRecord.objects.get(key="person:1").payload == payload
    assert discrepancy.status == DiscrepancyStatus.RESOLVED
    assert discrepancy.retry_at is None
    assert link.status == (LinkStatus.TOMBSTONE if tombstone else LinkStatus.CURRENT)
    assert link.remote_base_hash == ("" if tombstone else "corrected")
    assert RecordRevision.objects.filter(link=link).count() == 2
    assert SyncStream.objects.filter(pk=stream.pk).values().get() == before
    assert SyncStream.objects.count() == 1


def test_changed_remote_failure_defers_older_quarantine_for_the_same_identity(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    adapter = ReadKeysAdapter(
        pages=[StreamPage((RecordChange("poison", {}, "old"),), {"page": 1})],
        semantic_key="poison",
        remote={"poison": RecordChange("poison", {}, "new")},
    )
    result = advance_stream(stream, adapter)
    SyncDiscrepancy.objects.filter(pk=result.discrepancy_ids[0]).update(retry_at=timezone.now())
    begin_stream_cycle(stream, adapter)
    failures = list(SyncDiscrepancy.objects.order_by("pk"))
    assert {row.source_hash for row in failures} == {"old", "new"}
    assert all(row.status == DiscrepancyStatus.OPEN and row.retry_at > timezone.now() for row in failures)
    assert failures[0].retry_at == failures[1].retry_at
    begin_stream_cycle(stream, adapter)
    assert adapter.reads == [("poison",)]
    assert not AppliedRecord.objects.exists()
    assert SyncStream.objects.count() == 1


def test_identity_rescan_rolls_back_all_records_on_infrastructure_failure(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(
        stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, cursor={"page": 8}
    )
    for key in ("first", "z-broken"):
        link = RecordLink.objects.observe(stream, key)
        SyncDiscrepancy.objects.record(
            stream, link=link, kind=DiscrepancyKind.SEMANTIC, code="invalid_name", source_hash=key
        )
    adapter = ReadKeysAdapter(
        infrastructure_key="z-broken",
        remote={key: RecordChange(key, {"name": key}, key) for key in ("first", "z-broken")},
    )
    with pytest.raises(OperationalError, match="database unavailable"):
        begin_stream_cycle(stream, adapter)
    stream.refresh_from_db()
    assert stream.cursor == {"page": 8}
    assert not AppliedRecord.objects.exists()
    assert adapter.applied == ["first", "z-broken"]
    assert not RecordRevision.objects.exists()
    assert SyncDiscrepancy.objects.filter(status=DiscrepancyStatus.OPEN).count() == 2


def test_identity_rescan_without_read_keys_requests_and_records_baseline(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    record = RecordChange("poison", {"name": "invalid"}, "invalid")
    adapter = MemoryAdapter(pages=[StreamPage((record,), {"page": 1})], semantic_key="poison")
    failure = advance_stream(stream, adapter)
    discrepancy = SyncDiscrepancy.objects.get(pk=failure.discrepancy_ids[0])
    SyncDiscrepancy.objects.filter(pk=discrepancy.pk).update(retry_at=timezone.now())
    begin_stream_cycle(stream, adapter)
    stream.refresh_from_db()
    discrepancy.refresh_from_db()
    assert stream.resync_required
    assert stream.cursor == {"page": 1}
    assert discrepancy.details == {
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
    discrepancy.refresh_from_db()
    assert discrepancy.attempts == 2
    assert discrepancy.retry_at > timezone.now()
    assert reset.stream.generation == 2
    assert not reset.stream.resync_required


def test_event_feed_discrepancies_never_trigger_rescan_or_baseline(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(stream_bridge, "events")
    adapter = ReadKeysAdapter(pages=[StreamPage(("poison",), {"offset": 1})], semantic_key="poison")
    failure = advance_stream(stream, adapter)
    SyncDiscrepancy.objects.filter(pk=failure.discrepancy_ids[0]).update(retry_at=timezone.now())
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
    stream = SyncStream.objects.current(
        stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, reconcile_interval=interval
    )
    record = RecordChange("poison", {"name": "invalid"}, "invalid")
    adapter = ReadKeysAdapter(
        pages=[StreamPage((record,), {"page": 1})], semantic_key="poison", remote={"poison": record}
    )
    failure = advance_stream(stream, adapter)
    discrepancy = SyncDiscrepancy.objects.get(pk=failure.discrepancy_ids[0])
    expected_attempt = 1
    for _ in range(4):
        discrepancy.refresh_from_db()
        assert discrepancy.attempts == expected_attempt
        delay = min(60 * 2 ** (expected_attempt - 1), cap)
        assert discrepancy.retry_at == now + timedelta(seconds=delay)
        reads = len(adapter.reads)
        begin_stream_cycle(stream, adapter)
        assert len(adapter.reads) == reads
        now = discrepancy.retry_at
        begin_stream_cycle(stream, adapter)
        assert len(adapter.reads) == reads + 1
        expected_attempt += 1
    SyncDiscrepancy.objects.filter(pk=discrepancy.pk).update(attempts=20, retry_at=now)
    begin_stream_cycle(stream, adapter)
    discrepancy.refresh_from_db()
    stream.refresh_from_db()
    assert discrepancy.attempts == 21
    assert discrepancy.retry_at == now + timedelta(seconds=cap)
    assert discrepancy.status == DiscrepancyStatus.OPEN
    assert not AppliedRecord.objects.exists()
    assert stream.cursor == {"page": 1}
    assert stream.generation == 1
    assert not stream.resync_required
    assert SyncStream.objects.count() == SyncDiscrepancy.objects.count() == 1


@pytest.mark.parametrize("status", [DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY])
def test_both_changed_conflict_requires_resolution_before_pull_or_push(stream_bridge: Channel, status: str) -> None:
    stream = SyncStream.objects.current(
        stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    adapter = ReadKeysAdapter(
        pages=[StreamPage((RecordChange("person:1", {"name": "base"}, "base"),), {"page": 1})]
    )
    advance_stream(stream, adapter)
    AppliedRecord.objects.filter(key="person:1").update(payload={"name": "local edit"})
    record = RecordChange("person:1", {"name": "remote edit"}, "remote", "local")
    adapter.applied.clear()
    adapter.remote = {"person:1": record}
    adapter.pages = [StreamPage((record,), {"page": 2})]
    result = advance_stream(stream, adapter)
    discrepancy = SyncDiscrepancy.objects.get(pk=result.discrepancy_ids[0])
    assert result.count == 0
    assert (discrepancy.kind, discrepancy.code, discrepancy.retry_at) == (
        DiscrepancyKind.CONFLICT,
        "both_changed",
        None,
    )
    SyncDiscrepancy.objects.filter(pk=discrepancy.pk).update(status=status)
    begin_stream_cycle(stream, adapter)
    # Even if a later remote observation matches the old base, an unresolved
    # conflict must not silently authorize the pending local write.
    adapter.pages = [
        StreamPage((RecordChange("person:1", {"name": "base"}, "base", "local"),), {"page": 3})
    ]
    assert advance_stream(stream, adapter).count == 0
    adapter.candidates = (LocalChange("person:1", {"name": "local edit"}, "local"),)
    assert push_stream(stream, adapter).count == 0
    stream.refresh_from_db()
    discrepancy.refresh_from_db()
    assert discrepancy.status == status
    assert discrepancy.retry_at is None
    assert not adapter.reads
    assert not adapter.applied
    assert not adapter.written
    assert not stream.resync_required
    assert stream.generation == 1
    assert AppliedRecord.objects.get(key="person:1").payload == {"name": "local edit"}
    assert RecordRevision.objects.count() == 1
    SyncDiscrepancy.objects.resolve(discrepancy)
    assert push_stream(stream, adapter).count == 1
    assert adapter.written == [("person:1", {"name": "local edit"}, "")]
    assert RecordRevision.objects.count() == 2


def test_write_back_origin_is_recognized_on_next_pull(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(
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
        local_hash="base",
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
                        "changed",
                        "v1",
                        {"name": "after"},
                    ),
                ),
                {"page": 1},
            )
        ]
    )
    assert advance_stream(stream, adapter).count == 1
    link.refresh_from_db()
    assert (link.origin, link.remote_version, link.remote_base_hash, link.local_base_hash) == (
        "local",
        "v2",
        "changed",
        "changed",
    )
    assert adapter.written == [("person:1", {"name": "after"}, "v1")]
    adapter.pages = [
        StreamPage((RecordChange("person:1", {"name": "after"}, "changed", "changed", "v2"),), {"page": 2})
    ]
    assert advance_stream(stream, adapter).count == 0
    assert adapter.applied == []
    assert RecordRevision.objects.filter(link=link).count() == 2


def test_event_feeds_apply_without_replica_links(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(stream_bridge, "events")
    adapter = MemoryAdapter(pages=[StreamPage(("message:1", "message:2"), {"next": "end"})])
    assert advance_stream(stream, adapter).count == 2
    assert AppliedRecord.objects.count() == 2
    assert not RecordLink.objects.exists()
    assert not RecordRevision.objects.exists()


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
        def streams(self, *, using: str | None = None) -> Iterable[StreamDefinition]:
            return tuple(StreamDefinition("messages", partition) for partition in ("inbox", "sent"))

        def extract(self, stream: Any, page_bound: int, *, using: str | None = None) -> StreamPage:
            assert not connections[using].in_atomic_block
            ready.wait(timeout=10)
            if stream.partition == failed_partition:
                raise OperationalError("partition unavailable")
            return StreamPage((stream.partition,), {"done": True})

    def backend(bridge: Channel) -> PartitionAdapter:
        adapter = PartitionAdapter(sync_parallelism=2)
        with adapters_lock:
            adapters.append(adapter)
        return adapter

    monkeypatch.setattr(Channel, "backend", property(backend))
    stream_bridge.config = {**stream_bridge.config, "sync_parallelism": 2, "sync_time_budget": 60}
    stream_bridge.save(update_fields=["config"])
    if failed_partition:
        with pytest.raises(RuntimeError, match="one or more partitions"):
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
                stale = Channel.objects.db_manager("default").get(pk=stream_bridge.pk)
                ready.wait(timeout=10)
                merge_json_state(
                    stale, "subscription_state", {"uid": uid}, path=("mailboxes", partition), using="default"
                )
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
