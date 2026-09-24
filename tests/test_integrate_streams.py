"""The generic driver commits domain rows with their opaque stream positions."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo
from threading import Barrier, Lock
from typing import Any, ClassVar

import pytest
from django.db import (
    DatabaseError,
    OperationalError,
    close_old_connections,
    connection,
    connections,
    models,
    transaction,
)
from django.utils import timezone
from rebac import system_context

from angee.integrate.impl import AdapterContractError, BridgeImpl
from angee.integrate.models import merge_json_state
from angee.integrate.states import (
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
    open_stream,
    push_stream,
    reconcile_stream,
    reset_stream,
    sync_bridge,
)
from angee.messaging.backends import ParsedMessage
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

    def streams(self, *, deadline: float | None = None, using: str | None = None) -> Iterable[StreamDefinition]:
        return (StreamDefinition("records"),)

    def extract(
        self, stream: Any, page_bound: int, *, deadline: float | None = None, using: str | None = None
    ) -> StreamPage:
        assert not connections[using].in_atomic_block
        assert page_bound > 0
        self.extracted += 1
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    def apply_record(self, stream: Any, record: Any, *, using: str | None = None) -> ApplyResult:
        assert connections[using].in_atomic_block
        key = record.external_key if isinstance(record, RecordChange) else record
        payload = record.source_payload if isinstance(record, RecordChange) else {"event": key}
        row, _ = AppliedRecord.objects.db_manager(using).update_or_create(key=key, defaults={"payload": payload})
        self.applied.append(key)
        if key == self.semantic_key:
            raise SemanticError("invalid_name", details={"key": key})
        if key == self.infrastructure_key:
            raise OperationalError("database unavailable")
        return ApplyResult(
            target=row,
            local_hash=record.source_hash if isinstance(record, RecordChange) else "",
            mapped_payload=payload,
            mapping_version=record.mapping_version if isinstance(record, RecordChange) else 1,
            dependency_digest=record.dependency_digest if isinstance(record, RecordChange) else "",
        )

    def enumerate_keys(self, stream: Any, *, after: str | None = None, using: str | None = None) -> Iterable[str]:
        assert not connections[using].in_atomic_block
        start = 0 if after is None else self.inventory.index(after) + 1
        yield from self.inventory[start:]

    def finish_page(self, stream: Any, page: StreamPage, outcomes: Any, *, using: str | None = None) -> None:
        assert connections[using].in_atomic_block

    def local_changes(
        self, stream: Any, *, keys: frozenset[str] | None = None, using: str | None = None
    ) -> Iterable[LocalChange]:
        assert not connections[using].in_atomic_block
        return [candidate for candidate in self.candidates if keys is None or candidate.external_key in keys]

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

    supports_identity_reads: ClassVar[bool] = True
    remote: dict[str, RecordChange] = field(default_factory=dict)
    reads: list[tuple[str, ...]] = field(default_factory=list)

    def read_keys(self, stream: Any, keys: Sequence[str], *, using: str | None = None) -> Iterable[RecordChange]:
        assert not connections[using].in_atomic_block
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


@pytest.mark.parametrize(
    "completed,prior_revision,tombstone,direction,adopted",
    [
        (False, False, False, StreamDirection.BIDIRECTIONAL, True),
        (True, False, False, StreamDirection.BIDIRECTIONAL, False),
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
    stream = SyncStream.objects.current(stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, direction=direction)
    row = AppliedRecord.objects.create(key="one", payload={"name": "local"})
    if prior_revision:
        link = RecordLink.objects.observe(stream, "one", target=row)
        RecordLink.objects.promote(
            link, source_payload={}, source_hash="base", mapped_payload={}, local_hash="base", remote_version="v1"
        )
    if completed:
        SyncStream.objects.advance(stream, {}, exhausted=True)
    stream = reset_stream(stream, cursor={}).stream
    record = RecordChange(
        "one", {"name": "remote"}, "" if tombstone else "remote", "local", "v2", target=row, tombstone=tombstone
    )
    adapter = MemoryAdapter(pages=[StreamPage((record,), {})])

    result = advance_stream(stream, adapter)

    row.refresh_from_db()
    assert result.count == int(adopted)
    assert row.payload == {"name": "remote" if adopted else "local"}
    assert not adapter.written
    if adopted:
        assert not result.discrepancy_ids
        link = RecordLink.objects.get(stream=stream, external_key="one")
        assert (link.local_base_hash, link.remote_base_hash, link.remote_version) == ("remote", "remote", "v2")
    else:
        discrepancy = SyncDiscrepancy.objects.get(pk=result.discrepancy_ids[0])
        assert (discrepancy.kind, discrepancy.code) == (DiscrepancyKind.CONFLICT, "both_changed")


@pytest.mark.parametrize("tombstone", [False, True])
def test_repeated_page_identity_uses_earlier_revision_or_conflict(stream_bridge: Channel, tombstone: bool) -> None:
    stream = SyncStream.objects.current(
        stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    row = AppliedRecord.objects.create(key="one", payload={"name": "local"})
    first = RecordChange(
        "one", {"name": "remote"}, "" if tombstone else "remote", "local", target=row, tombstone=tombstone
    )
    second = RecordChange("one", {"name": "newer"}, "newer", "local", target=row)
    adapter = MemoryAdapter(pages=[StreamPage((first, second), {})])

    result = advance_stream(stream, adapter)

    row.refresh_from_db()
    assert result.count == int(not tombstone)
    assert row.payload == {"name": "local" if tombstone else "remote"}
    assert RecordRevision.objects.count() == int(not tombstone)
    conflict = SyncDiscrepancy.objects.get(kind=DiscrepancyKind.CONFLICT)
    assert conflict.attempts == 1
    assert set(result.discrepancy_ids) == {conflict.pk}
    assert not adapter.written


def test_baseline_completion_during_extraction_retries_without_adopting(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = SyncStream.objects.current(
        stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    row = AppliedRecord.objects.create(key="one", payload={"name": "local"})
    record = RecordChange("one", {"name": "remote"}, "remote", "local", "v1", target=row)
    page = StreamPage((record,), {})
    adapter = MemoryAdapter(pages=[page, page])
    has_completed_baseline = stream.has_completed_baseline

    def complete_on_another_handle(*, using: str | None = None) -> bool:
        completed = has_completed_baseline(using=using)
        peer = SyncStream.objects.db_manager(using).get(pk=stream.pk)
        SyncStream.objects.advance(peer, peer.cursor, exhausted=True, using=using)
        return completed

    with monkeypatch.context() as patch:
        patch.setattr(stream, "has_completed_baseline", complete_on_another_handle)
        with pytest.raises(RuntimeError, match="Stream state changed during extraction; retry the page"):
            advance_stream(stream, adapter)

    row.refresh_from_db()
    assert row.payload == {"name": "local"}
    assert not adapter.applied and not adapter.written
    assert not RecordLink.objects.exists()
    assert not RecordRevision.objects.exists()
    assert not SyncDiscrepancy.objects.exists()
    stream.refresh_from_db()
    assert stream.phase == StreamPhase.DELTA
    assert stream.cursor == {}

    result = advance_stream(stream, adapter)

    assert result.count == 0
    assert not adapter.applied and not adapter.written
    conflict = SyncDiscrepancy.objects.get(pk=result.discrepancy_ids[0])
    assert (conflict.kind, conflict.code) == (DiscrepancyKind.CONFLICT, "both_changed")


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
    stream = SyncStream.objects.current(
        stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, reconcile_interval=timedelta(0)
    )
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
        def write_back(self, link: Any, projection: Any, *, expected_version: str, using: str | None = None) -> Any:
            return None

    stream = SyncStream.objects.current(
        stream_bridge, "records", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    link = RecordLink.objects.observe(stream, "person:1")
    RecordLink.objects.promote(
        link, source_payload={}, source_hash="base", mapped_payload={}, local_hash="base", remote_version="v1"
    )
    SyncStream.objects.advance(stream, {}, exhausted=True)
    adapter = InvalidWriteAdapter(
        pages=[StreamPage((RecordChange("person:1", {}, "base", "changed", "v1"),), {"offset": 1})],
        candidates=(LocalChange("person:1", {}, "changed"),),
    )

    with pytest.raises(AdapterContractError, match="write_back must return WriteBackResult"):
        if push:
            push_stream(stream, adapter)
        else:
            advance_stream(stream, adapter)

    stream.refresh_from_db()
    link.refresh_from_db()
    assert stream.cursor == {}
    assert (link.remote_base_hash, link.local_base_hash, link.remote_version) == ("base", "base", "v1")


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
    adapter = ReadKeysAdapter(inventory=("present",), remote={"present": RecordChange("present", {}, "present")})
    assert finish_sweep(stream, adapter) == 1
    missing.refresh_from_db()
    assert (missing.absence_count, missing.status) == (1, LinkStatus.UNAVAILABLE)
    adapter.remote["present"] = replace(adapter.remote["present"], local_hash="present")
    assert finish_sweep(stream, adapter) == 1
    missing.refresh_from_db()
    present.refresh_from_db()
    assert (missing.absence_count, missing.status) == (2, LinkStatus.TOMBSTONE)
    assert missing.tombstoned_at is not None
    assert present.absence_count == 0
    assert RecordLink.objects.count() == 2


def test_single_promotion_retains_applied_evidence_and_revalidation_reuses_it(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)

    class AppliedEvidenceAdapter(MemoryAdapter):
        def apply_record(self, stream: Any, record: Any, *, using: str | None = None) -> ApplyResult:
            return replace(
                super().apply_record(stream, record, using=using),
                mapped_payload={"normalized": True},
                dependency_digest="applied",
                mapping_version=7,
            )

    adapter = AppliedEvidenceAdapter(
        pages=[StreamPage((RecordChange("person:1", {"name": "Ada"}, "source", dependency_digest="old"),), {})]
    )
    assert advance_stream(stream, adapter).count == 1
    revision = RecordRevision.objects.get()
    assert (revision.mapping_version, revision.dependency_digest, revision.mapped_payload) == (
        7,
        "applied",
        {"normalized": True},
    )
    adapter.pages = [
        StreamPage(
            (
                RecordChange(
                    "person:1",
                    {"name": "Ada"},
                    "source",
                    "source",
                    projection={"different_observation_shape": True},
                    mapping_version=7,
                    dependency_digest="applied",
                ),
            ),
            {"page": 2},
        )
    ]
    assert advance_stream(stream, adapter).count == 0
    assert adapter.applied == ["person:1"]
    assert RecordRevision.objects.count() == 1


@pytest.mark.parametrize("changed_evidence", [{"mapping_version": 2}, {"dependency_digest": "updated"}])
def test_mapping_evidence_change_reapplies_unchanged_source(
    stream_bridge: Channel, changed_evidence: dict[str, Any]
) -> None:
    stream = SyncStream.objects.current(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    record = RecordChange("person:1", {"name": "Ada"}, "source")
    adapter = MemoryAdapter(pages=[StreamPage((record,), {"page": 1})])
    advance_stream(stream, adapter)
    adapter.pages = [StreamPage((replace(record, local_hash="source", **changed_evidence),), {"page": 2})]
    assert advance_stream(stream, adapter).count == 1
    assert adapter.applied == ["person:1", "person:1"]
    assert RecordRevision.objects.count() == 2
    revision = RecordRevision.objects.order_by("-number").first()
    for name, expected in changed_evidence.items():
        assert getattr(revision, name) == expected


def test_adapter_cannot_promote_before_the_driver(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)

    class PromotingAdapter(MemoryAdapter):
        def apply_record(self, stream: Any, record: Any, *, using: str | None = None) -> ApplyResult:
            outcome = super().apply_record(stream, record, using=using)
            link = RecordLink.objects.db_manager(using).get(stream=stream, external_key=record.external_key)
            RecordLink.objects.promote(
                link,
                source_payload=record.source_payload,
                source_hash=record.source_hash,
                mapped_payload={"intermediate": True},
                local_hash=record.source_hash,
                using=using,
            )
            return outcome

    adapter = PromotingAdapter(pages=[StreamPage((RecordChange("person:1", {}, "source"),), {"page": 1})])
    with pytest.raises(AdapterContractError, match="promot"):
        advance_stream(stream, adapter)
    stream.refresh_from_db()
    assert stream.cursor == {}
    assert not RecordLink.objects.exists()
    assert not RecordRevision.objects.exists()
    assert not AppliedRecord.objects.exists()


def test_sweep_imports_unseen_keys_and_resumes_after_failed_page(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        cursor={"incremental": 900},
        reconcile_interval=timedelta(0),
    )

    class ResumableAdapter(ReadKeysAdapter):
        fail = True

        def enumerate_keys(self, stream: Any, *, after: str | None = None, using: str | None = None) -> Iterable[str]:
            starts.append(after)
            emitted = 0
            for key in super().enumerate_keys(stream, after=after, using=using):
                emitted += 1
                assert emitted <= 3, "The driver consumed beyond one bounded page plus lookahead."
                yield key

        def read_keys(self, stream: Any, keys: Sequence[str], *, using: str | None = None) -> Iterable[RecordChange]:
            assert len(keys) <= 2
            if "3" in keys and self.fail:
                raise OperationalError("inventory transport interrupted")
            return super().read_keys(stream, keys, using=using)

    starts: list[str | None] = []
    adapter = ResumableAdapter(
        inventory=("1", "2", "3", "4"),
        remote={key: RecordChange(key, {"id": key}, key) for key in ("1", "2", "3", "4")},
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
    assert RecordRevision.objects.count() == 4
    assert stream.cursor == {"incremental": 900}
    assert stream.last_reconciled_at is not None


def test_sweep_without_read_keys_resumes_bounded_baseline_without_moving_delta_cursor(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        cursor={"incremental": 900},
        reconcile_interval=timedelta(0),
    )
    extracted_cursors: list[dict[str, Any]] = []

    class BaselineAdapter(MemoryAdapter):
        fail = True

        def extract(
            self, stream: Any, page_bound: int, *, deadline: float | None = None, using: str | None = None
        ) -> StreamPage:
            assert not connections[using].in_atomic_block
            assert page_bound == 1
            extracted_cursors.append(dict(stream.cursor))
            if not stream.cursor:
                return StreamPage((RecordChange("a", {"id": "a"}, "a"),), {"baseline": "a"}, exhausted=False)
            assert stream.cursor == {"baseline": "a"}
            if self.fail:
                raise OperationalError("baseline transport interrupted")
            return StreamPage((RecordChange("b", {"id": "b"}, "b"),), {"baseline": "b"})

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
    assert RecordRevision.objects.count() == 2
    assert resumed_stream.cursor == {"incremental": 900}
    assert resumed_stream.last_reconciled_at is not None


def test_visibility_hooks_run_in_transaction_once_per_status_transition(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        reconcile_interval=timedelta(0),
        absence_threshold=3,
    )
    absent: list[tuple[str, str]] = []
    revalidated: list[tuple[str, str]] = []

    class VisibilityAdapter(ReadKeysAdapter):
        def on_absent(self, stream: Any, links: Sequence[Any], *, using: str | None = None) -> None:
            assert connections[using].in_atomic_block
            for link in links:
                link.refresh_from_db(using=using)
                absent.append((link.external_key, link.status))

        def on_revalidated(self, stream: Any, links: Sequence[Any], *, using: str | None = None) -> None:
            assert connections[using].in_atomic_block
            for link in links:
                link.refresh_from_db(using=using)
                revalidated.append((link.external_key, link.status))

    record = RecordChange("person:1", {}, "source")
    adapter = VisibilityAdapter(pages=[StreamPage((record,), {})])
    advance_stream(stream, adapter)
    for _ in range(4):
        finish_sweep(stream, adapter, page_bound=1)
    assert absent == [("person:1", LinkStatus.UNAVAILABLE), ("person:1", LinkStatus.TOMBSTONE)]
    adapter.pages = [StreamPage((replace(record, local_hash="source"),), {"page": 2})]
    assert advance_stream(stream, adapter).count == 0
    assert revalidated == [("person:1", LinkStatus.CURRENT)]
    assert RecordRevision.objects.count() == 1


def test_absence_hook_failure_rolls_back_transition_and_cursor_before_retry(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        reconcile_interval=timedelta(days=1),
    )
    missing = RecordLink.objects.observe(stream, "missing")
    calls: list[tuple[str, int]] = []

    class FailingVisibilityAdapter(ReadKeysAdapter):
        fail = True

        def on_absent(self, stream: Any, links: Sequence[Any], *, using: str | None = None) -> None:
            assert connections[using].in_atomic_block
            for link in links:
                calls.append((link.external_key, link.absence_count))
                AppliedRecord.objects.db_manager(using).create(key=link.external_key, payload={"withdrawn": True})
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
    stream = SyncStream.objects.current(
        stream_bridge,
        "contacts",
        kind=StreamKind.RECORD_REPLICA,
        reconcile_interval=timedelta(0),
        absence_threshold=2,
    )
    parent = RecordLink.objects.observe(stream, "parent")
    child = RecordLink.objects.observe(stream, "child", parent=parent)
    RecordLink.objects.promote(child, source_payload={}, source_hash="child", mapped_payload={}, local_hash="child")
    adapter = ReadKeysAdapter(inventory=("parent",), remote={"parent": RecordChange("parent", {}, "parent")})
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


def test_child_discrepancy_rescans_and_reapplies_parent_identity(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    record = RecordChange("parent", {}, "parent")
    adapter = ReadKeysAdapter(pages=[StreamPage((record,), {"page": 1})])
    advance_stream(stream, adapter)
    parent = RecordLink.objects.get(external_key="parent")
    child = RecordLink.objects.observe(stream, "child", parent=parent)
    discrepancy = SyncDiscrepancy.objects.record(
        stream,
        link=child,
        kind=DiscrepancyKind.MISSING_DEPENDENCY,
        code="child_dependency",
        source_hash="child",
    )
    adapter.remote = {"parent": replace(record, local_hash="parent")}
    begin_stream_cycle(stream, adapter, page_bound=1)
    assert adapter.reads == [("parent",)]
    assert adapter.applied == ["parent", "parent"]
    discrepancy.refresh_from_db()
    assert discrepancy.status == DiscrepancyStatus.OPEN
    stream.refresh_from_db()
    assert stream.cursor == {"page": 1}


def test_prepare_page_runs_once_before_record_savepoints_and_transport_is_refused(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(stream_bridge, "events")
    events: list[str] = []
    prepared_depth = -1

    class PreparedAdapter(MemoryAdapter):
        def prepare_page(self, stream: Any, page: StreamPage, *, using: str | None = None) -> None:
            nonlocal prepared_depth
            assert connections[using].in_atomic_block
            prepared_depth = len(connections[using].savepoint_ids)
            assert page.records == ("first", "poison", "last")
            events.append("prepare")
            with pytest.raises(RuntimeError, match="outside a database transaction"):
                advance_stream(stream, self, using=using)

        def apply_record(self, stream: Any, record: Any, *, using: str | None = None) -> ApplyResult:
            assert len(connections[using].savepoint_ids) > prepared_depth
            events.append(record)
            return super().apply_record(stream, record, using=using)

    adapter = PreparedAdapter(pages=[StreamPage(("first", "poison", "last"), {})], semantic_key="poison")
    result = advance_stream(stream, adapter)
    assert result.count == 2
    assert events == ["prepare", "first", "poison", "last"]
    assert adapter.extracted == 1


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
    assert (discrepancy.source_hash, discrepancy.status) == ("", DiscrepancyStatus.OPEN)


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
    adapter.pages = [StreamPage((RecordChange("person:1", {"name": "invalid"}, "invalid", "base"),), {"page": 2})]
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
        "person:1": RecordChange("person:1", payload, "" if tombstone else "corrected", "base", tombstone=tombstone),
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
    stream = SyncStream.objects.current(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, cursor={"page": 8})
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
    adapter = ReadKeysAdapter(pages=[StreamPage((RecordChange("person:1", {"name": "base"}, "base"),), {"page": 1})])
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
    adapter.pages = [StreamPage((RecordChange("person:1", {"name": "base"}, "base", "local"),), {"page": 3})]
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
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="requires keeping"):
        SyncDiscrepancy.objects.resolve(discrepancy)


def test_page_reloads_conflicts_recorded_during_conditional_write(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(
        stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    link = RecordLink.objects.observe(stream, "one")
    RecordLink.objects.promote(link, source_payload={}, source_hash="base", mapped_payload={}, local_hash="base")
    SyncStream.objects.advance(stream, {}, exhausted=True)

    class RacingAdapter(MemoryAdapter):
        def write_back(
            self, link: Any, projection: Any, *, expected_version: str, using: str | None = None
        ) -> WriteBackResult:
            SyncDiscrepancy.objects.record(
                stream, link=link, kind=DiscrepancyKind.CONFLICT, code="concurrent_conflict", using=using
            )
            return super().write_back(link, projection, expected_version=expected_version, using=using)

    adapter = RacingAdapter(pages=[StreamPage((RecordChange("one", {}, "base", "local"),), {})])

    result = advance_stream(stream, adapter)

    assert result.count == 0
    assert len(adapter.written) == 1
    assert RecordRevision.objects.count() == 1
    conflict = SyncDiscrepancy.objects.get(pk=result.discrepancy_ids[0])
    assert conflict.code == "concurrent_conflict"
    assert conflict.status == DiscrepancyStatus.OPEN
    link.refresh_from_db()
    assert (link.local_base_hash, link.remote_base_hash) == ("base", "base")


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
        def apply_record(self, stream: Any, record: Any, *, using: str | None = None) -> ApplyResult:
            assert connections[using].in_atomic_block
            assert record is message
            assert message.sent_at is not None
            row, _ = AppliedRecord.objects.db_manager(using).update_or_create(
                key=message.external_id, defaults={"payload": {"sent_at": message.sent_at.isoformat()}}
            )
            return ApplyResult(target=row)

    stream = SyncStream.objects.current(stream_bridge, "messages", "INBOX")
    adapter = MessageAdapter(pages=[StreamPage((message,), {"uid": 1})])
    assert advance_stream(stream, adapter).count == 1
    stream.refresh_from_db()
    assert stream.cursor == {"uid": 1}
    assert AppliedRecord.objects.get().payload == {"sent_at": "2026-09-24T12:00:00+02:00"}
    assert not RecordLink.objects.exists()
    assert not RecordRevision.objects.exists()


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
    SyncStream.objects.current(stream_bridge, "records")
    manager_class = type(SyncStream.objects)
    lock_current = manager_class.lock_current

    def try_bridge_lock() -> None:
        close_old_connections()
        try:
            with (
                system_context(reason="test competing bridge cutover"),
                pytest.raises(DatabaseError) as denied,
                transaction.atomic(using="default"),
            ):
                Channel.objects.using("default").select_for_update(nowait=True).get(pk=stream_bridge.pk)
            assert denied.value.__cause__.sqlstate == "55P03"
        finally:
            connections.close_all()

    def verify_bridge_locked(manager: Any, stream: Any, *, using: str | None = None) -> Any:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(try_bridge_lock).result(timeout=10)
        return lock_current(manager, stream, using=using)

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
    stream = SyncStream.objects.current(stream_bridge, "events")
    with pytest.raises(AdapterContractError, match="plain finite JSON"):
        if boundary == "definition":

            class InvalidDefinitionAdapter(MemoryAdapter):
                def streams(
                    self, *, deadline: float | None = None, using: str | None = None
                ) -> Iterable[StreamDefinition]:
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
        def streams(self, *, deadline: float | None = None, using: str | None = None) -> Iterable[StreamDefinition]:
            return tuple(StreamDefinition("messages", partition) for partition in ("inbox", "sent"))

        def extract(
            self, stream: Any, page_bound: int, *, deadline: float | None = None, using: str | None = None
        ) -> StreamPage:
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


def test_due_semantic_rescan_never_overwrites_concurrent_local_changes(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    adapter = ReadKeysAdapter(pages=[StreamPage((RecordChange("one", {"name": "base"}, "base"),), {})])
    advance_stream(stream, adapter)
    link = RecordLink.objects.get(stream=stream)
    SyncDiscrepancy.objects.record(stream, link=link, kind=DiscrepancyKind.SEMANTIC, code="retry")
    adapter.remote = {"one": RecordChange("one", {"name": "remote"}, "remote", "local")}
    adapter.applied.clear()
    begin_stream_cycle(stream, adapter)
    assert not adapter.applied
    assert AppliedRecord.objects.get(key="one").payload == {"name": "base"}
    assert SyncDiscrepancy.objects.unresolved().filter(kind=DiscrepancyKind.CONFLICT).exists()


@pytest.mark.parametrize("keep", ["remote", "local"])
def test_failed_conflict_reread_keeps_quarantine(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch, keep: str
) -> None:
    stream = SyncStream.objects.current(
        stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    SyncStream.objects.advance(stream, {}, exhausted=True)
    link = RecordLink.objects.observe(stream, "one")
    discrepancy = SyncDiscrepancy.objects.record(stream, link=link, kind=DiscrepancyKind.CONFLICT, code="both_changed")

    class UnavailableAdapter(ReadKeysAdapter):
        def read_keys(self, stream: Any, keys: Sequence[str], *, using: str | None = None) -> Iterable[RecordChange]:
            raise OperationalError("remote offline")

    adapter = UnavailableAdapter()
    monkeypatch.setattr(Channel, "backend", property(lambda self: adapter))
    with pytest.raises(OperationalError, match="remote offline"):
        SyncDiscrepancy.objects.resolve_conflict(discrepancy, keep=keep)
    link.refresh_from_db()
    assert SyncDiscrepancy.objects.unresolved().filter(link=link).count() == 1
    assert link.status == LinkStatus.DISCREPANT
    assert adapter.closed == 1


@pytest.mark.parametrize("failure", ["missing_candidate", "new_remote_version", "baseline_required"])
def test_conflict_choice_requires_a_completed_apply_or_conditional_write(
    stream_bridge: Channel, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from django.core.exceptions import ValidationError

    from angee.integrate.streams import RemoteRejected

    stream = SyncStream.objects.current(
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
        local_hash="base",
        remote_version="v1",
    )
    discrepancy = SyncDiscrepancy.objects.record(
        stream,
        link=link,
        kind=DiscrepancyKind.CONFLICT,
        code="both_changed",
        source_hash="base",
    )
    SyncStream.objects.advance(stream, {}, exhausted=True)

    class RejectedAdapter(ReadKeysAdapter):
        def write_back(
            self, link: Any, projection: Any, *, expected_version: str, using: str | None = None
        ) -> WriteBackResult:
            assert expected_version == "v2"
            raise RemoteRejected(details={"status": 412})

    adapter = RejectedAdapter(remote={"one": RecordChange("one", {"name": "remote"}, "remote", "local", "v2")})
    monkeypatch.setattr(Channel, "backend", property(lambda self: adapter))
    keep = "local"
    if failure == "new_remote_version":
        adapter.candidates = (LocalChange("one", {"name": "local"}, "local"),)
    elif failure == "baseline_required":
        keep = "remote"
        old = RecordLink.objects.observe(stream, "expired")
        RecordLink.objects.tombstone(old)
        RecordLink.objects.filter(pk=old.pk).update(tombstoned_at=timezone.now() - timedelta(days=2))
        SyncStream.objects.filter(pk=stream.pk).update(last_advanced_at=timezone.now() - timedelta(days=2))
    with pytest.raises(ValidationError):
        SyncDiscrepancy.objects.resolve_conflict(discrepancy, keep=keep)
    assert SyncDiscrepancy.objects.unresolved().filter(link=link).count() == 1
    if failure == "new_remote_version":
        assert SyncDiscrepancy.objects.unresolved().get(link=link).source_hash == "remote"
    assert adapter.closed == 1


def test_stale_cursor_reset_cannot_overwrite_successor_progress(stream_bridge: Channel) -> None:
    retired = SyncStream.objects.current(stream_bridge, "events", cursor={"old": 1})
    first = reset_stream(retired, cursor={"new": 1}).stream
    SyncStream.objects.advance(first, {"new": 2})
    late = reset_stream(retired, cursor={"stale": 9}).stream
    late.refresh_from_db()
    assert late.pk == first.pk
    assert late.cursor == {"new": 2}
    assert SyncStream.objects.count() == 2


def test_reconcile_pulse_rejects_a_checkpoint_changed_during_transport(stream_bridge: Channel) -> None:
    stream = SyncStream.objects.current(
        stream_bridge, "contacts", kind=StreamKind.RECORD_REPLICA, reconcile_interval=timedelta(0)
    )

    class RacingAdapter(ReadKeysAdapter):
        def read_keys(self, stream: Any, keys: Sequence[str], *, using: str | None = None) -> Iterable[RecordChange]:
            SyncStream.objects.db_manager(using).filter(pk=stream.pk).update(reconcile_state={"newer": True})
            return super().read_keys(stream, keys, using=using)

    adapter = RacingAdapter(inventory=("one",), remote={"one": RecordChange("one", {}, "source")})
    with pytest.raises(RuntimeError, match="state changed"):
        reconcile_stream(stream, adapter)
    stream.refresh_from_db()
    assert stream.reconcile_state == {"newer": True}
    assert not RecordLink.objects.filter(stream=stream).exists()
    assert not AppliedRecord.objects.exists()
