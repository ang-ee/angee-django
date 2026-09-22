"""Bounded synchronization: transport outside transactions, page and cursor inside.

Adapters own protocol parsing and domain projections. Due discrepancies are
re-read by identity when supported; there is no durable work queue.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from time import monotonic
from typing import Any, Protocol

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections, transaction
from django.utils import timezone
from rebac import system_context

from angee.base.db import get_write_alias
from angee.base.serialization import canonical_json_sha256, json_safe
from angee.integrate.records import DiscrepancyKind, DiscrepancyStatus, LinkStatus, StreamDirection, StreamKind
from angee.integrate.sync import bridge_progress_context, current_bridge_progress
from angee.jobs.autoconfig import SETTINGS as JOB_SETTINGS


@dataclass(frozen=True, slots=True)
class StreamDefinition:
    """One independently ordered partition; cursor seeds only its first epoch."""

    key: str
    partition: str = ""
    kind: str = StreamKind.EVENT_FEED
    direction: str = StreamDirection.PULL
    cursor: dict[str, Any] = field(default_factory=dict)
    reconcile_interval: timedelta | None = None
    absence_threshold: int = 2
    tombstone_retention: timedelta | None = None


@dataclass(frozen=True, slots=True)
class StreamPage:
    """An extracted page and the opaque position covering all its records."""

    records: Sequence[Any]
    cursor: dict[str, Any]
    exhausted: bool = True
    resync_required: bool = False
    cursor_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RecordChange:
    """Remote observation and the adapter's current local mapped projection.

    Hashes describe snapshots on their respective sides, never timestamps. A
    missing local target has local_hash="". The adapter reloads any target on the
    operation alias. Remote deletion uses tombstone=True and source_hash="".
    """

    external_key: str
    source_payload: Any
    source_hash: str
    local_hash: str = ""
    remote_version: str = ""
    projection: Any = None
    target: Any = None
    mapping_version: int = 1
    dependency_digest: str = ""
    tombstone: bool = False


@dataclass(frozen=True, slots=True)
class LocalChange:
    """A local projection candidate; conditional write-back fences remote edits."""

    external_key: str
    projection: Any
    local_hash: str
    target: Any = None
    mapping_version: int = 1


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """A domain ingest outcome; replica applies return the resulting local hash."""

    external_key: str = ""
    target: Any = None
    local_hash: str = ""
    mapped_payload: Any = None
    count: int = 1


@dataclass(frozen=True, slots=True)
class WriteBackResult:
    """Authoritative remote version/hash returned by a conditional remote write."""

    remote_version: str
    source_hash: str
    source_payload: Any = None


class CursorInvalid(Exception):
    """The remote epoch expired; cursor optionally carries a safe baseline seed."""

    def __init__(self, *, cursor: dict[str, Any] | None = None) -> None:
        super().__init__("The remote cursor requires a new baseline.")
        self.cursor = cursor or {}


class SemanticError(Exception):
    """An adapter-owned, JSON-safe record refusal that does not abort its page."""

    def __init__(
        self, code: str, *, details: dict[str, Any] | None = None, kind: str = DiscrepancyKind.SEMANTIC
    ) -> None:
        super().__init__(code)
        self.code, self.details, self.kind = code, details or {}, kind


class RemoteRejected(SemanticError):
    """A conditional-write conflict, or another operator-safe remote refusal."""

    def __init__(
        self, code: str = "remote_version_changed", *, details: dict[str, Any] | None = None, conflict: bool = True
    ) -> None:
        super().__init__(
            code,
            details=details,
            kind=DiscrepancyKind.CONFLICT if conflict else DiscrepancyKind.REMOTE_REJECTED,
        )


class StreamAdapter(Protocol):
    """Backend seam composed by Bridge.sync and bounded workflow stages.

    apply receives a singleton page inside a savepoint; yield one ApplyResult.
    Infrastructure failures propagate. Raise SemanticError (or ValidationError)
    only for record-local refusals. Apply performs database work only; fetch media
    in extract. Push adapters yield LocalChange and return WriteBackResult facts.
    """

    sync_parallelism: int | None
    sync_deadline: float | None

    def streams(self, *, using: str | None = None) -> Iterable[StreamDefinition]: ...
    def extract(self, stream: Any, page_bound: int, *, using: str | None = None) -> StreamPage: ...
    def read_keys(self, stream: Any, keys: Sequence[str], *, using: str | None = None) -> Iterable[RecordChange]:
        """Optional: read exactly these keys once, returning tombstones for missing keys.

        Like extract, perform transport and project local state outside a
        transaction. Adapters may omit this operation; rescan then requests a
        baseline and records that fallback on the due discrepancies.
        """

        ...

    def apply(self, stream: Any, page: StreamPage, *, using: str | None = None) -> Iterable[ApplyResult]: ...
    def finish_page(
        self, stream: Any, page: StreamPage, outcomes: Sequence[ApplyResult], *, using: str | None = None
    ) -> None: ...
    def enumerate_keys(self, stream: Any, *, using: str | None = None) -> Iterable[str]: ...
    def local_changes(self, stream: Any, *, using: str | None = None) -> Iterable[LocalChange]: ...
    def write_back(
        self, link: Any, projection: Any, *, expected_version: str, using: str | None = None
    ) -> WriteBackResult: ...
    def close(self) -> None: ...


class ChangeKind(StrEnum):
    """Three-way decision against the last synchronized bases."""

    UNCHANGED = "unchanged"
    APPLY = "apply"
    WRITE_BACK = "write_back"
    CONFLICT = "conflict"


def classify_change(
    *,
    remote_hash: str,
    local_hash: str,
    remote_base_hash: str,
    local_base_hash: str,
    tombstone: bool = False,
) -> ChangeKind:
    """Compare both sides independently, retaining delete-versus-edit conflicts."""

    remote_changed = remote_hash != remote_base_hash
    local_changed = local_hash != local_base_hash
    if local_changed and (remote_changed or tombstone):
        return ChangeKind.CONFLICT
    if remote_changed:
        return ChangeKind.APPLY
    if local_changed:
        return ChangeKind.WRITE_BACK
    return ChangeKind.UNCHANGED


@dataclass(frozen=True, slots=True)
class PageResult:
    """Bounded result; the returned stream owns the durable continuation."""

    stream: Any
    count: int = 0
    exhausted: bool = False
    discrepancy_ids: tuple[Any, ...] = ()
    reset: bool = False
    progress: str = ""


def _manager(name: str, *, using: str) -> Any:
    return apps.get_model("integrate", name).objects.db_manager(using)


def _source_hash(record: Any) -> str:
    if isinstance(record, RecordChange):
        return record.source_hash
    return canonical_json_sha256(json_safe(asdict(record) if is_dataclass(record) else record))


def _unresolved(stream: Any, record: Any, *, using: str) -> Any:
    return _manager("SyncDiscrepancy", using=using).filter(
        stream=stream,
        source_hash=_source_hash(record),
        mapping_version=record.mapping_version if isinstance(record, RecordChange) else 1,
        status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY),
    )


def _resolve_applied(stream: Any, record: Any, *, using: str) -> None:
    manager = _manager("SyncDiscrepancy", using=using)
    rows = (
        manager.filter(
            stream=stream,
            link__external_key=record.external_key,
            status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY),
        )
        if isinstance(record, RecordChange)
        else _unresolved(stream, record, using=using)
    )
    for discrepancy in rows.exclude(kind=DiscrepancyKind.CONFLICT):
        manager.resolve(discrepancy, using=using)


def _has_conflict(link: Any, *, using: str) -> bool:
    return (
        _manager("SyncDiscrepancy", using=using)
        .filter(
            link=link,
            kind=DiscrepancyKind.CONFLICT,
            status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY),
        )
        .exists()
    )


def _retry_at(stream: Any, attempts: int) -> datetime:
    cap = timedelta(days=1)
    if stream.reconcile_interval and stream.reconcile_interval > timedelta(0):
        cap = min(cap, stream.reconcile_interval)
    return timezone.now() + min(timedelta(minutes=2 ** min(max(0, attempts - 1), 11)), cap)


def _quarantine(stream: Any, record: Any, error: SemanticError, *, link: Any = None, using: str) -> Any:
    replica = isinstance(record, RecordChange)
    details = dict(error.details)
    if replica:
        details["external_key"] = record.external_key
    manager = _manager("SyncDiscrepancy", using=using)
    with transaction.atomic(using=using):
        row = manager.record(
            stream,
            link=link,
            kind=error.kind,
            code=error.code,
            source_hash=_source_hash(record),
            mapping_version=record.mapping_version if replica else 1,
            details=details,
            using=using,
        )
        if row.kind != DiscrepancyKind.CONFLICT:
            row.retry_at = _retry_at(stream, row.attempts)
            row.save(using=using, update_fields=["retry_at", "updated_at"])
            if link is not None:
                # Older failed source versions must not bypass this identity's
                # backoff when its newly read payload also fails semantically.
                manager.filter(
                    link=link, status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY)
                ).exclude(kind=DiscrepancyKind.CONFLICT).update(retry_at=row.retry_at)
        return row


def _reset(stream: Any, *, cursor: dict[str, Any], using: str) -> PageResult:
    manager = _manager("SyncStream", using=using)
    with transaction.atomic(using=using):
        stream = manager.bump_generation(stream, using=using)
        if cursor:
            manager.advance(stream, cursor, using=using)
    return PageResult(stream, reset=True)


def _decision(link: Any, record: RecordChange) -> ChangeKind:
    return classify_change(
        remote_hash=record.source_hash,
        local_hash=record.local_hash,
        remote_base_hash=link.remote_base_hash,
        local_base_hash=link.local_base_hash,
        tombstone=record.tombstone,
    )


def _promote(link: Any, record: RecordChange, outcome: ApplyResult, *, origin: str, using: str) -> None:
    manager = _manager("RecordLink", using=using)
    manager.promote(
        link,
        source_payload=record.source_payload,
        source_hash=record.source_hash,
        mapped_payload=outcome.mapped_payload,
        local_hash=outcome.local_hash,
        mapping_version=record.mapping_version,
        dependency_digest=record.dependency_digest,
        target=outcome.target,
        remote_version=record.remote_version,
        origin=origin,
        using=using,
    )
    if record.tombstone:
        manager.tombstone(link, using=using)


def _reflect_write(link: Any, record: RecordChange, result: WriteBackResult, *, using: str) -> None:
    _manager("RecordLink", using=using).promote(
        link,
        source_payload=result.source_payload,
        source_hash=result.source_hash,
        mapped_payload=record.projection,
        local_hash=record.local_hash,
        target=record.target,
        remote_version=result.remote_version,
        mapping_version=record.mapping_version,
        origin="local",
        using=using,
    )


def advance_stream(
    stream: Any, adapter: StreamAdapter, *, page_bound: int = 100, using: str | None = None
) -> PageResult:
    """Extract and commit one page; compose from a STANDARD workflow stage.

    Pass the returned stream to the next invocation: expired cursors create a
    BASELINE row. Caller owns adapter.close(). Never call inside an enclosing
    transaction. Stream state is fenced after extraction.
    """

    using = get_write_alias(type(stream), using=using, instance=stream)
    if connections[using].in_atomic_block:
        raise RuntimeError("Stream extraction must run outside a database transaction.")
    if page_bound < 1:
        raise ValueError("page_bound must be positive.")
    with system_context(reason="integrate.stream.advance"):
        stream.refresh_from_db(using=using)
        if stream.resync_required or (stream.cursor_expires_at and stream.cursor_expires_at <= timezone.now()):
            return _reset(stream, cursor={}, using=using)
        original_cursor = deepcopy(stream.cursor)
        try:
            page = (
                StreamPage((), original_cursor)
                if stream.direction == StreamDirection.PUSH
                else adapter.extract(stream, page_bound, using=using)
            )
        except CursorInvalid as error:
            return _reset(stream, cursor=error.cursor, using=using)
        if page.resync_required:
            return _reset(stream, cursor={}, using=using)
        return _apply_page(stream, adapter, page, original_cursor=original_cursor, using=using)


def _apply_page(
    stream: Any,
    adapter: StreamAdapter,
    page: StreamPage,
    *,
    original_cursor: dict[str, Any],
    advance_cursor: bool = True,
    using: str,
) -> PageResult:
    """Apply page or identity observations through the same fenced transaction."""

    # Conditional remote writes precede the transaction. Compare link bases
    # again before reflecting the response; a later local edit remains dirty.
    written: dict[str, tuple[Any, WriteBackResult | SemanticError]] = {}
    if stream.kind == StreamKind.RECORD_REPLICA and stream.direction != StreamDirection.PULL:
        for record in page.records:
            if not isinstance(record, RecordChange):
                raise TypeError("Replica pages must contain RecordChange values.")
            link = _manager("RecordLink", using=using).filter(stream=stream, external_key=record.external_key).first()
            if (
                link is not None
                and not _has_conflict(link, using=using)
                and _decision(link, record) == ChangeKind.WRITE_BACK
            ):
                bases = (link.remote_base_hash, link.local_base_hash, link.remote_version)
                try:
                    result = adapter.write_back(
                        link, record.projection, expected_version=link.remote_version, using=using
                    )
                except RemoteRejected as error:
                    result = error
                written[record.external_key] = (bases, result)
    count, discrepancies = 0, []
    applied: list[ApplyResult] = []
    with transaction.atomic(using=using):
        locked = _manager("SyncStream", using=using).lock_current(stream, using=using)
        if locked.cursor != original_cursor or locked.resync_required:
            raise RuntimeError("Stream state changed during extraction; retry the page.")
        for record in page.records:
            link = None
            try:
                with transaction.atomic(using=using):
                    if locked.kind == StreamKind.RECORD_REPLICA:
                        if not isinstance(record, RecordChange):
                            raise TypeError("Replica pages must contain RecordChange values.")
                        link = _manager("RecordLink", using=using).observe(locked, record.external_key, using=using)
                        if _has_conflict(link, using=using):
                            discrepancies.extend(
                                _manager("SyncDiscrepancy", using=using)
                                .filter(
                                    link=link,
                                    kind=DiscrepancyKind.CONFLICT,
                                    status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY),
                                )
                                .values_list("pk", flat=True)
                            )
                            continue
                        decision = _decision(link, record)
                        if decision == ChangeKind.CONFLICT:
                            raise SemanticError("both_changed", kind=DiscrepancyKind.CONFLICT)
                        if decision == ChangeKind.UNCHANGED:
                            _promote(
                                link,
                                record,
                                ApplyResult(
                                    target=record.target,
                                    local_hash=record.local_hash,
                                    mapped_payload=record.projection,
                                    count=0,
                                ),
                                origin=link.origin or "remote",
                                using=using,
                            )
                            _resolve_applied(locked, record, using=using)
                            continue
                        if decision == ChangeKind.WRITE_BACK:
                            if locked.direction == StreamDirection.PULL:
                                raise SemanticError("local_change_on_pull", kind=DiscrepancyKind.CONFLICT)
                            bases, result = written.get(record.external_key, (None, None))
                            if bases != (link.remote_base_hash, link.local_base_hash, link.remote_version):
                                raise RuntimeError("Record bases changed during extraction; retry the page.")
                            if isinstance(result, SemanticError):
                                raise result
                            if result is None:
                                raise RuntimeError("A conditional write result is missing.")
                            _reflect_write(link, record, result, using=using)
                            _resolve_applied(locked, record, using=using)
                            count += 1
                            continue
                    outcomes = tuple(adapter.apply(locked, replace(page, records=(record,)), using=using))
                    if len(outcomes) != 1:
                        raise RuntimeError("apply must return one outcome for its single record.")
                    if link is not None:
                        _promote(link, record, outcomes[0], origin="remote", using=using)
                    applied.extend(outcomes)
                    _resolve_applied(locked, record, using=using)
                    count += outcomes[0].count
            except (SemanticError, ValidationError) as error:
                # The record savepoint rolled back, including a new identity.
                if isinstance(record, RecordChange):
                    link = _manager("RecordLink", using=using).observe(locked, record.external_key, using=using)
                refusal = error if isinstance(error, SemanticError) else SemanticError("invalid_record")
                discrepancies.append(_quarantine(locked, record, refusal, link=link, using=using).pk)
        adapter.finish_page(locked, page, applied, using=using)
        if advance_cursor:
            _manager("SyncStream", using=using).advance(
                locked,
                page.cursor,
                exhausted=page.exhausted,
                cursor_expires_at=page.cursor_expires_at,
                using=using,
            )
    progress = canonical_json_sha256(
        [locked.generation, page.cursor, [_source_hash(record) for record in page.records]]
    )
    return PageResult(locked, count, page.exhausted, tuple(discrepancies), progress=progress)


def push_stream(stream: Any, adapter: StreamAdapter, *, using: str | None = None) -> PageResult:
    """Compare local candidates to their bases and conditionally write changed rows."""

    using = get_write_alias(type(stream), using=using, instance=stream)
    if connections[using].in_atomic_block:
        raise RuntimeError("Remote writes must run outside a database transaction.")
    if stream.kind != StreamKind.RECORD_REPLICA or stream.direction == StreamDirection.PULL:
        return PageResult(stream, exhausted=True)
    count, discrepancies = 0, []
    with system_context(reason="integrate.stream.push"):
        for candidate in adapter.local_changes(stream, using=using):
            if adapter.sync_deadline is not None and monotonic() >= adapter.sync_deadline:
                return PageResult(stream, count, discrepancy_ids=tuple(discrepancies))
            links = _manager("RecordLink", using=using)
            link = links.filter(stream=stream, external_key=candidate.external_key).first()
            if link is None:
                link = links.observe(stream, candidate.external_key, using=using)
            if _has_conflict(link, using=using) or candidate.local_hash == link.local_base_hash:
                continue
            record = RecordChange(
                candidate.external_key,
                None,
                link.remote_base_hash,
                candidate.local_hash,
                link.remote_version,
                candidate.projection,
                candidate.target,
                candidate.mapping_version,
            )
            bases = (link.remote_base_hash, link.local_base_hash, link.remote_version)
            try:
                result = adapter.write_back(
                    link, candidate.projection, expected_version=link.remote_version, using=using
                )
            except RemoteRejected as error:
                discrepancies.append(_quarantine(stream, record, error, link=link, using=using).pk)
                continue
            with transaction.atomic(using=using):
                _manager("SyncStream", using=using).lock_current(stream, using=using)
                locked = links.lock_if_supported().get(pk=link.pk)
                if (locked.remote_base_hash, locked.local_base_hash, locked.remote_version) != bases:
                    raise RuntimeError("Record bases changed during write-back; reconcile before retrying.")
                _reflect_write(locked, record, result, using=using)
            count += 1
    return PageResult(stream, count, True, tuple(discrepancies))


def reconcile_stream(stream: Any, adapter: StreamAdapter, *, using: str | None = None) -> int:
    """Run a due inventory sweep; absence is evidence, never immediate deletion."""

    using = get_write_alias(type(stream), using=using, instance=stream)
    if connections[using].in_atomic_block:
        raise RuntimeError("Remote enumeration must run outside a database transaction.")
    now = timezone.now()
    if stream.kind != StreamKind.RECORD_REPLICA or stream.reconcile_interval is None:
        return 0
    if stream.last_reconciled_at and stream.last_reconciled_at + stream.reconcile_interval > now:
        return 0
    keys = frozenset(adapter.enumerate_keys(stream, using=using))
    with system_context(reason="integrate.stream.reconcile"), transaction.atomic(using=using):
        locked = _manager("SyncStream", using=using).lock_current(stream, using=using)
        if locked.last_reconciled_at and locked.last_reconciled_at + locked.reconcile_interval > now:
            return 0
        links = _manager("RecordLink", using=using)
        absent = tuple(
            links.filter(stream=locked).exclude(external_key__in=keys).values_list("external_key", flat=True)
        )
        changed = links.mark_absent(locked, absent, using=using)
        for key in sorted(keys):
            # Inventory verifies identities; unseen payloads do not create links.
            if links.filter(stream=locked, external_key=key).exists():
                links.observe(locked, key, using=using)
        locked.last_reconciled_at = now
        locked.save(using=using, update_fields=["last_reconciled_at", "updated_at"])
    return changed


def begin_stream_cycle(stream: Any, adapter: StreamAdapter | None = None, *, using: str | None = None) -> Any:
    """Re-read due replica identities, or request a logged baseline fallback.

    Call once per cycle before advance_stream, outside any transaction. Optional
    read_keys returns RecordChange observations without moving the stream cursor.
    Conflicts require explicit resolution. Event feeds have no replica rescan.
    """

    using = get_write_alias(type(stream), using=using, instance=stream)
    if connections[using].in_atomic_block:
        raise RuntimeError("Stream rescan must run outside a database transaction.")
    with system_context(reason="integrate.stream.cycle"):
        manager = _manager("SyncStream", using=using)
        discrepancies = _manager("SyncDiscrepancy", using=using)
        read_keys = getattr(adapter, "read_keys", None)
        with transaction.atomic(using=using):
            stream = manager.lock_current(stream, using=using)
            if stream.kind == StreamKind.EVENT_FEED:
                return stream
            conflicts = discrepancies.filter(
                stream=stream,
                kind=DiscrepancyKind.CONFLICT,
                status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY),
            ).values_list("link_id", flat=True)
            blocked = set(conflicts)
            due = [
                row
                for row in discrepancies.rescan(stream, using=using)
                if row.kind != DiscrepancyKind.CONFLICT and row.link_id is not None and row.link_id not in blocked
            ]
            retention = stream.tombstone_retention
            retry = False
            if retention and (
                stream.last_advanced_at is None or stream.last_advanced_at + retention < timezone.now()
            ):
                retry = (
                    _manager("RecordLink", using=using)
                    .filter(
                        stream=stream,
                        status=LinkStatus.TOMBSTONE,
                        tombstoned_at__lt=timezone.now() - retention,
                    )
                    .exists()
                )
            if due and read_keys is None:
                retry = True
                for row in due:
                    row.details = {**row.details, "rescan": "baseline", "reason": "read_keys_unavailable"}
                    row.retry_at = _retry_at(stream, row.attempts)
                    row.save(using=using, update_fields=["details", "retry_at", "updated_at"])
            if retry:
                manager.filter(pk=stream.pk).update(resync_required=True)
                stream.resync_required = True
            if stream.resync_required or not due:
                return stream
            keys = tuple(
                _manager("RecordLink", using=using)
                .filter(stream=stream, pk__in=[row.link_id for row in due])
                .order_by("external_key")
                .values_list("external_key", flat=True)
            )
            original_cursor = deepcopy(stream.cursor)
        records = tuple(read_keys(stream, keys, using=using))
        if (
            any(not isinstance(record, RecordChange) for record in records)
            or len(records) != len(keys)
            or {record.external_key for record in records} != set(keys)
        ):
            raise ValueError("read_keys must return each requested key exactly once, including remote tombstones.")
        return _apply_page(
            stream,
            adapter,
            StreamPage(records, original_cursor, exhausted=False),
            original_cursor=original_cursor,
            advance_cursor=False,
            using=using,
        ).stream


def open_stream(bridge: Any, definition: StreamDefinition, *, using: str | None = None) -> Any:
    """Resolve a backend declaration to its current durable stream generation."""

    using = get_write_alias(type(bridge), using=using, instance=bridge)
    return _manager("SyncStream", using=using).current(
        bridge,
        definition.key,
        definition.partition,
        kind=definition.kind,
        direction=definition.direction,
        cursor=definition.cursor,
        reconcile_interval=definition.reconcile_interval,
        absence_threshold=definition.absence_threshold,
        tombstone_retention=definition.tombstone_retention,
        using=using,
    )


def _report(bridge: Any, message: str, **details: Any) -> None:
    reporter = current_bridge_progress()
    if reporter is not None:
        current = dict(bridge.sync_progress.get("details") or {})
        reporter.report(str(bridge.SyncStage.SYNCING), message=message, details={**current, **details})


def _drain(bridge: Any, adapter: StreamAdapter, definition: StreamDefinition, deadline: float, *, using: str) -> int:
    adapter.sync_deadline = deadline
    stream = begin_stream_cycle(open_stream(bridge, definition, using=using), adapter, using=using)
    landed, resets = 0, 0
    page_bound = max(1, int(bridge.config.get("sync_page_bound", 100)))
    exhausted = False
    previous = None
    while monotonic() < deadline:
        result = advance_stream(stream, adapter, page_bound=page_bound, using=using)
        stream, exhausted = result.stream, result.exhausted
        landed += result.count
        resets += int(result.reset)
        if not result.reset and not exhausted and result.progress == previous:
            raise RuntimeError("The stream repeated a page without advancing its cursor.")
        previous = result.progress
        if resets > 1:
            raise RuntimeError("The remote rejected a fresh baseline cursor.")
        _report(
            bridge,
            "Applied stream page",
            backend=type(adapter).__name__,
            stream=definition.key,
            partition=definition.partition,
            landed=landed,
            generation=stream.generation,
        )
        if exhausted:
            if monotonic() < deadline:
                landed += push_stream(stream, adapter, using=using).count
                reconcile_stream(stream, adapter, using=using)
            break
    if not exhausted:
        _report(
            bridge,
            "Sync time budget reached; resuming next run",
            stream=definition.key,
            partition=definition.partition,
            landed=landed,
            budget_exhausted=True,
        )
    return landed


def _drain_partition(bridge: Any, definition: StreamDefinition, deadline: float, *, using: str) -> int:
    close_old_connections()
    try:
        with system_context(reason="integrate.stream.partition"):
            row = type(bridge)._base_manager.db_manager(using).get(pk=bridge.pk)
            adapter = row.backend
            try:
                with bridge_progress_context(row, using=using):
                    return _drain(row, adapter, definition, deadline, using=using)
            finally:
                adapter.close()
    finally:
        connections.close_all()


def sync_bridge(bridge: Any, *, using: str | None = None) -> int:
    """Drive declared partitions within the existing bridge task's time budget."""

    using = get_write_alias(type(bridge), using=using, instance=bridge)
    bridge._state.db = using
    config = bridge.config
    soft_limit = float(getattr(settings, "CELERY_TASK_SOFT_TIME_LIMIT", JOB_SETTINGS["CELERY_TASK_SOFT_TIME_LIMIT"]))
    deadline = monotonic() + max(0.0, float(config.get("sync_time_budget", max(60.0, soft_limit - 60.0))))
    adapter = bridge.backend
    adapter.sync_deadline = deadline
    try:
        with system_context(reason="integrate.bridge.streams"):
            definitions = tuple(sorted(adapter.streams(using=using), key=lambda item: (item.key, item.partition)))
            identities = [(item.key, item.partition) for item in definitions]
            if len(identities) != len(set(identities)):
                raise ValueError("A backend declared the same stream partition twice.")
            parallelism = max(1, int(config.get("sync_parallelism", 4)))
            if adapter.sync_parallelism is not None:
                parallelism = min(parallelism, adapter.sync_parallelism)
            if connections[using].vendor != "postgresql":
                parallelism = 1
            if parallelism <= 1 or len(definitions) <= 1:
                return sum(_drain(bridge, adapter, definition, deadline, using=using) for definition in definitions)
            failures, landed = [], 0
            with ThreadPoolExecutor(
                max_workers=min(parallelism, len(definitions)), thread_name_prefix="bridge-sync"
            ) as pool:
                futures = {
                    pool.submit(
                        copy_context().run, _drain_partition, bridge, definition, deadline, using=using
                    ): definition
                    for definition in definitions
                }
                for future in as_completed(futures):
                    try:
                        landed += future.result()
                    except Exception as error:  # noqa: BLE001 -- healthy partitions have already committed.
                        failures.append((futures[future], error))
            if failures:
                raise RuntimeError("Bridge sync failed for one or more partitions.") from failures[0][1]
            return landed
    finally:
        adapter.close()
