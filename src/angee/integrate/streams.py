"""Bounded synchronization: transport outside transactions, page and cursor inside.

Adapters own protocol parsing and domain projections. Due failures are
re-read by identity when supported; there is no durable work queue.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import copy
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from itertools import islice
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from deepdiff import DeepDiff
from django.core.exceptions import ValidationError
from django.db import DataError, close_old_connections, connection, connections, transaction
from django.db.models import Q
from django.utils import timezone
from pydantic import ConfigDict, JsonValue, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from rebac import system_context

from angee.base.serialization import canonical_json, canonical_json_sha256
from angee.integrate.errors import IntegrationError, safe_integration_failure
from angee.integrate.impl import AdapterContractError, BridgeImpl
from angee.integrate.states import (
    UNSET,
    ConflictKeep,
    LinkStatus,
    StreamDefinition,
    StreamDirection,
    StreamKind,
    StreamPhase,
    StreamState,
    SyncFailureKind,
)
from angee.integrate.sync import bridge_progress_context, current_bridge_progress
from angee.jobs.timeouts import task_time_budget
from angee.provenance.context import changeset, current_changeset

logger = logging.getLogger(__name__)

_CURSOR: TypeAdapter[dict[str, Any]] = TypeAdapter(
    dict[str, JsonValue], config=ConfigDict(strict=True, allow_inf_nan=False)
)


def _validated_cursor(value: Any) -> dict[str, Any]:
    """Reject malformed adapter cursor evidence at the driver contract boundary."""
    try:
        return _CURSOR.validate_python(value)
    except PydanticValidationError as exc:
        raise AdapterContractError("Stream cursors must contain plain finite JSON objects.") from exc


def _public_partition(partition: str) -> str:
    """Render URL partition identity without credentials or query parameters."""

    parts = urlsplit(partition)
    return parts.path if parts.scheme and parts.netloc else partition


@dataclass(frozen=True, slots=True)
class PartitionFailure:
    """The failed partition and operation, independent of shared progress updates."""

    definition: StreamDefinition
    stage: str
    error: Exception

    @property
    def details(self) -> dict[str, str]:
        """Return operator-safe identity and cause; URLs expose only their path."""

        return {
            "stream": self.definition.key,
            "partition": _public_partition(self.definition.partition),
            "stage": self.stage,
            "message": safe_integration_failure(self.error).message,
        }

    def log(self, bridge: Any) -> None:
        """Log the original traceback once, using non-secret bridge and partition identity."""

        details = self.details
        logger.error(
            "Bridge %s:%s stream %s partition %s failed during %s",
            bridge._meta.label_lower,
            bridge.pk,
            details["stream"],
            details["partition"],
            self.stage,
            exc_info=self.error,
        )


class BridgeSyncError(IntegrationError):
    """Bounded, deterministic partition refusals retaining their original causes."""

    def __init__(self, failures: Sequence[PartitionFailure]) -> None:
        self.failures = tuple(sorted(failures, key=lambda item: (item.definition.key, item.definition.partition)))
        messages = []
        for failure in self.failures:
            details = failure.details
            identity = details["stream"]
            if details["partition"]:
                identity += f" ({details['partition']})"
            messages.append(f"{identity}: {details['message']}")
        message = "; ".join(messages)
        transient = bool(self.failures) and all(
            isinstance(failure.error, IntegrationError) and failure.error.transient for failure in self.failures
        )
        hints = [
            failure.error.retry_after
            for failure in self.failures
            if isinstance(failure.error, IntegrationError) and failure.error.retry_after is not None
        ]
        super().__init__(
            message if len(message) <= 4096 else message[:4095] + "…",
            transient=transient,
            retry_after=max(hints) if hints else None,
        )


@dataclass(frozen=True, slots=True)
class StreamPage:
    """An extracted page and the plain JSON cursor covering all its records."""

    records: Sequence[Any]
    cursor: dict[str, Any]
    exhausted: bool = True
    resync_required: bool = False
    cursor_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RecordChange:
    """Remote observation and the adapter's current local mapped projection.

    Hashes describe snapshots on their respective sides, never timestamps. A
    missing local target has projection=None. Remote deletion uses tombstone=True
    and source_hash="".
    """

    external_key: str
    source_payload: Any
    source_hash: str
    remote_version: str = ""
    projection: Any = None
    target: Any = None
    mapping_version: int = 1
    dependency_digest: str = ""
    tombstone: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    mapped_payload: Any = UNSET
    """The remote projection in the same domain shape as the local projection."""
    failure: SemanticError | None = None
    """An adapter's refusal when the source cannot produce a mapped projection."""


@dataclass(frozen=True, slots=True)
class LocalChange:
    """A local projection candidate; conditional write-back fences remote edits."""

    external_key: str
    projection: Any
    target: Any = None
    mapping_version: int = 1


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Applied evidence returned to the sole primary promoter: the driver.

    Adapters return the evidence actually applied, including mapping/dependency
    facts learned during apply. They must not promote the primary link themselves.
    Omitted target preserves its binding; explicit None withdraws it.
    """

    target: Any = UNSET
    mapped_payload: Any = UNSET
    count: int = 1
    dependency_digest: str = ""
    mapping_version: int = 1

    @property
    def bound_target(self) -> Any:
        """Return the applied binding, treating omission as no returned target."""
        return None if self.target is UNSET else self.target


@dataclass(frozen=True, slots=True)
class WriteBackResult:
    """Authoritative remote version/hash returned by a conditional remote write."""

    remote_version: str
    source_hash: str
    source_payload: Any = None
    tombstone: bool = False


class CursorInvalid(Exception):
    """The remote epoch expired; cursor optionally carries a safe baseline seed."""

    def __init__(self, *, cursor: dict[str, Any] | None = None) -> None:
        super().__init__("The remote cursor requires a new baseline.")
        self.cursor = cursor or {}


class SemanticError(Exception):
    """An adapter-owned, JSON-safe record refusal that does not abort its page."""

    def __init__(
        self, code: str, *, details: dict[str, Any] | None = None, kind: SyncFailureKind = SyncFailureKind.SEMANTIC
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
            kind=SyncFailureKind.CONFLICT if conflict else SyncFailureKind.REMOTE_REJECTED,
        )


class ChangeKind(StrEnum):
    """Three-way decision against the last synchronized bases."""

    UNCHANGED = "unchanged"
    APPLY = "apply"
    WRITE_BACK = "write_back"
    CONFLICT = "conflict"


def classify_change(
    *,
    base: Any,
    local: Any,
    remote: Any,
    tombstone: bool = False,
    mapping_changed: bool = False,
) -> ChangeKind:
    """Compare nested mapped facts; concurrent divergent edits require a choice."""
    base, local, remote = (json.loads(canonical_json(value)) for value in (base, local, remote))
    local_changed = bool(DeepDiff(base, local))
    remote_changed = bool(DeepDiff(base, remote)) or mapping_changed
    if local_changed and (remote_changed or tombstone):
        if not tombstone and not mapping_changed and not DeepDiff(local, remote):
            return ChangeKind.APPLY
        return ChangeKind.CONFLICT
    if remote_changed:
        return ChangeKind.APPLY
    if local_changed:
        return ChangeKind.WRITE_BACK
    return ChangeKind.UNCHANGED


def _check_write_precondition(link: Any, expected: dict[str, Any]) -> None:
    """Fence database reflection beside the remote conditional-write fence."""
    if not link.sync_precondition_matches(expected):
        raise RuntimeError("Record bases changed during write-back; reconcile before retrying.")


@dataclass(frozen=True, slots=True)
class PageResult:
    """Bounded result; the returned stream owns the durable continuation."""

    stream: Any
    count: int = 0
    exhausted: bool = False
    failure_ids: tuple[Any, ...] = ()
    reset: bool = False
    progress: str = ""

    def check_continuation(self, *, previous: str | None = None, resets: int = 0) -> tuple[str, int]:
        """Reject stalled pages and repeated baseline resets in one partition drain.

        The digest includes replica hashes and generation as well as the cursor:
        a changed observation can make progress without moving its cursor.
        Callers retain only the returned digest and reset count between pages.
        """
        resets += int(self.reset)
        if not self.reset and not self.exhausted and self.progress == previous:
            raise AdapterContractError("The stream repeated a page without advancing its cursor.")
        if resets > 1:
            raise RuntimeError("The remote rejected a fresh baseline cursor.")
        return self.progress, resets


def _resolve_applied(stream: Any, record: Any) -> None:
    # Event feeds retain stream-level refusals; only their domain owner knows
    # event identity. A healthy event cannot resolve another event's failure.
    if stream.kind != StreamKind.RECORD_REPLICA:
        return
    link = stream.links.model.objects.get(stream=stream, external_key=record.external_key)
    for failure in link.failures(open_only=True):
        if failure.kind != SyncFailureKind.CONFLICT:
            failure.resolve()


def _quarantine(stream: Any, record: Any, error: SemanticError, *, link: Any = None, record_key: str = "") -> Any:
    """Quarantine one record; an event adapter's record key keeps distinct events apart."""

    replica = stream.kind == StreamKind.RECORD_REPLICA and isinstance(record, RecordChange)
    details = dict(error.details)
    if replica:
        details["external_key"] = record.external_key
    elif record_key:
        details["external_key"] = record_key
    return (link if link is not None else stream).record_failure(
        kind=error.kind,
        code=error.code,
        source_hash=(
            record.source_hash if replica else canonical_json_sha256(["record_key", record_key]) if record_key else ""
        ),
        mapping_version=record.mapping_version if replica else 1,
        details=details,
        **({"outbox": isinstance(error, RemoteRejected)} if link is not None else {}),
    )


def reset_stream(stream: Any, *, cursor: dict[str, Any]) -> PageResult:
    """Retire an epoch and seed its replacement through the stream manager."""
    cursor = _validated_cursor(cursor)
    manager = type(stream).objects
    stream = manager.bump_generation(stream, cursor=cursor)
    return PageResult(stream, reset=True)


def _baseline_adoption(stream: Any) -> bool:
    """Two-way replicas adopt unbased identities until this generation completes its baseline."""
    return (
        stream.kind == StreamKind.RECORD_REPLICA
        and stream.direction == StreamDirection.BIDIRECTIONAL
        and not stream.has_completed_baseline()
    )


def _decision(record: RecordChange, *, base: Any, adopting: bool) -> ChangeKind:
    if adopting and base is None and record.target is not None and not record.tombstone:
        return ChangeKind.APPLY
    if record.mapped_payload is UNSET and not record.tombstone:
        raise AdapterContractError("Replica observations must include their remote mapped_payload.")
    mapping_changed = base is None or (
        base.mapping_version != record.mapping_version or base.dependency_digest != record.dependency_digest
    )
    remote = None if record.tombstone else record.mapped_payload
    # The transport response is authoritative echo evidence even when mapping
    # normalizes the value the remote stored. It never substitutes for a base.
    if (
        base is not None
        and base.origin == "local"
        and base.source_hash == record.source_hash
        and base.remote_version == record.remote_version
    ):
        remote = base.mapped_payload
    elif base is not None and record.tombstone and base.tombstoned_at is not None:
        remote = base.mapped_payload
    return classify_change(
        base=base.mapped_payload if base is not None else None,
        local=record.projection,
        remote=remote,
        tombstone=record.tombstone,
        mapping_changed=mapping_changed,
    )


def _promote(link: Any, record: RecordChange, outcome: ApplyResult, *, origin: str) -> None:
    manager = type(link).objects
    manager.promote(
        link,
        source_payload=record.source_payload,
        source_hash=record.source_hash,
        mapped_payload=outcome.mapped_payload,
        mapping_version=outcome.mapping_version,
        dependency_digest=outcome.dependency_digest,
        target=outcome.target,
        remote_version=record.remote_version,
        origin=origin,
        tombstone=record.tombstone,
    )


def _reflect_write(link: Any, record: RecordChange, result: WriteBackResult) -> None:
    if not isinstance(result, WriteBackResult):
        raise AdapterContractError("write_back must return WriteBackResult.")
    type(link).objects.promote(
        link,
        source_payload=result.source_payload,
        source_hash=result.source_hash,
        mapped_payload=record.projection,
        target=record.target,
        remote_version=result.remote_version,
        mapping_version=record.mapping_version,
        dependency_digest=record.dependency_digest,
        origin="local",
        tombstone=result.tombstone,
    )


def _extract_page(adapter: BridgeImpl, stream: Any, page_bound: int, *, deadline: float | None) -> StreamPage:
    """Validate extracted page evidence before either advancement or reconciliation."""
    page = adapter.extract(stream, page_bound, deadline=deadline)
    if not isinstance(page, StreamPage):
        raise AdapterContractError("extract must return StreamPage.")
    if not isinstance(page.records, Sequence):
        raise AdapterContractError("StreamPage.records must be a sequence.")
    if len(page.records) > page_bound:
        raise AdapterContractError("extract exceeded page_bound.")
    return page


def advance_stream(
    stream: Any, adapter: BridgeImpl, *, page_bound: int = 100, deadline: float | None = None
) -> PageResult:
    """Extract and commit one page; compose from an IO workflow step.

    Pass the returned stream to the next invocation: expired cursors create a
    BASELINE row. Caller owns adapter.close(). Never call inside an enclosing
    transaction. Stream state is fenced after extraction.
    """

    if connection.in_atomic_block:
        raise RuntimeError("Stream extraction must run outside a database transaction.")
    if page_bound < 1:
        raise ValueError("page_bound must be positive.")
    with system_context(reason="integrate.stream.advance"):
        stream.refresh_from_db()
        if stream.resync_required:
            return reset_stream(stream, cursor={})
        if stream.cursor_expires_at and stream.cursor_expires_at <= timezone.now():
            return reset_stream(stream, cursor={})
        if not stream.accepts_pull():
            return PageResult(stream, exhausted=True)
        original_cursor = _validated_cursor(stream.cursor)
        try:
            page = (
                StreamPage((), original_cursor)
                if stream.direction == StreamDirection.PUSH
                else _extract_page(adapter, stream, page_bound, deadline=deadline)
            )
        except CursorInvalid as error:
            return reset_stream(stream, cursor=error.cursor)
        if page.resync_required:
            return reset_stream(stream, cursor={})
        return _apply_page(stream, adapter, page, original_cursor=original_cursor)


def _apply_page(
    stream: Any,
    adapter: BridgeImpl,
    page: StreamPage,
    *,
    original_cursor: dict[str, Any],
    advance_cursor: bool = True,
    reconcile_state: dict[str, Any] | None = None,
    force_apply: frozenset[str] = frozenset(),
    reapply: Mapping[str, Mapping[str, Any]] | None = None,
) -> PageResult:
    """Apply page or identity observations through the same fenced transaction.

    ``reapply`` maps each due identity a rescan selected to the link state it was
    selected at. An identity whose link moved since (another attempt healed it, a
    newer page applied it) is skipped: its read is stale, as a page whose cursor
    moved during extraction is.
    """

    reapply = reapply or {}

    page = replace(page, cursor=_validated_cursor(page.cursor))
    adopting = _baseline_adoption(stream)
    links = stream.links.model.objects
    evidence_rows = None
    if stream.kind == StreamKind.RECORD_REPLICA:
        if any(not isinstance(record, RecordChange) for record in page.records):
            raise AdapterContractError("Replica pages must contain RecordChange values.")
        evidence_rows = links.filter(
            stream=stream, external_key__in=[record.external_key for record in page.records]
        ).with_sync_evidence()
    # Conditional remote writes precede the transaction. Compare link bases
    # again before reflecting the response; a later local edit remains dirty.
    written: dict[str, tuple[dict[str, Any], WriteBackResult | SemanticError]] = {}
    held: set[str] = set()
    if evidence_rows is not None and stream.writes_back:
        evidence = {link.external_key: link for link in evidence_rows}
        held = {key for key, link in evidence.items() if link.write_holds}
        for record in page.records:
            link = evidence.get(record.external_key)
            if (
                link is not None
                and record.external_key not in force_apply
                and not link.write_holds
                and not link.pinned
                and record.failure is None
                and _decision(
                    record,
                    base=link.applied_base,
                    adopting=adopting,
                )
                == ChangeKind.WRITE_BACK
            ):
                bases = link.sync_precondition
                result: WriteBackResult | SemanticError
                try:
                    result = adapter.write_back(link, record.projection, expected_version=link.remote_version)
                except RemoteRejected as error:
                    result = error
                written[record.external_key] = (bases, result)
    count = 0
    failures: list[str] = []
    applied: list[ApplyResult] = []
    with changeset(fresh=True, activity="pull", origin=stream), transaction.atomic():
        current_changeset(record=True)
        locked = type(stream).objects.lock_current(stream, fenced_to=stream)
        adapter.prepare_page(locked, page)
        evidence = (
            {}
            if evidence_rows is None
            else {link.external_key: link for link in evidence_rows.order_by("pk").lock_if_supported()}
        )
        seen: set[str] = set()
        for record in page.records:
            link = None
            revision = None
            try:
                with transaction.atomic():
                    if evidence_rows is not None:
                        # Repeated identities must see earlier writes and refusals in this page.
                        observed = (
                            evidence_rows.filter(external_key=record.external_key).first()
                            if record.external_key in seen
                            else evidence.get(record.external_key)
                        )
                        seen.add(record.external_key)
                        selected = reapply.get(record.external_key)
                        current = evidence.get(record.external_key)
                        if (
                            selected is not None
                            and current is not None
                            and record.external_key not in force_apply
                            and not current.sync_precondition_matches(selected)
                        ):
                            continue
                        link = links.observe(
                            locked,
                            record.external_key,
                            source_payload=record.source_payload,
                            source_hash=record.source_hash,
                            remote_version=record.remote_version,
                            metadata=record.metadata or None,
                        )
                        if record.failure is not None:
                            raise record.failure
                        conflicts = [
                            row.pk
                            for row in (observed.write_holds if observed is not None else ())
                            if row.kind == SyncFailureKind.CONFLICT
                        ]
                        if link.pinned and record.external_key not in force_apply:
                            continue
                        if conflicts:
                            failures.extend(conflicts)
                            continue
                        revision = observed.applied_base if observed is not None else None
                        decision = (
                            ChangeKind.APPLY
                            if record.external_key in force_apply
                            else _decision(record, base=revision, adopting=adopting)
                        )
                        if decision == ChangeKind.UNCHANGED and record.external_key in reapply:
                            decision = ChangeKind.APPLY
                        if decision == ChangeKind.CONFLICT:
                            raise SemanticError("both_changed", kind=SyncFailureKind.CONFLICT)
                        if decision == ChangeKind.UNCHANGED:
                            assert revision is not None
                            if record.tombstone:
                                links.tombstone(link)
                            else:
                                links.revalidate(link, target=record.target if record.target is not None else UNSET)
                                adapter.on_revalidated(locked, (link,))
                            _resolve_applied(locked, record)
                            continue
                        if decision == ChangeKind.WRITE_BACK:
                            if not locked.writes_back:
                                raise SemanticError("local_change_on_pull", kind=SyncFailureKind.CONFLICT)
                            write = written.get(record.external_key)
                            if write is None and record.external_key in held:
                                # A refused write waits out its retry time; the local change
                                # stays. The first pass's hold decides, so a refusal resolved
                                # since then waits for the next page rather than failing this one.
                                failures.extend(
                                    row.pk for row in (observed.write_holds if observed is not None else ())
                                )
                                continue
                            if write is None:
                                raise RuntimeError("A conditional write result is missing.")
                            bases, result = write
                            _check_write_precondition(observed, bases)
                            if isinstance(result, SemanticError):
                                raise result
                            _reflect_write(link, record, result)
                            _resolve_applied(locked, record)
                            count += 1
                            continue
                    application_base = link.applied_base_id if link is not None else None
                    outcome = adapter.apply_record(locked, record)
                    if not isinstance(outcome, ApplyResult):
                        raise AdapterContractError("apply_record must return ApplyResult.")
                    if link is not None:
                        if links.get(pk=link.pk).applied_base_id != application_base:
                            raise AdapterContractError(
                                "Adapters return ApplyResult; only the driver promotes the primary link."
                            )
                        if outcome.mapped_payload is UNSET:
                            raise AdapterContractError("Replica ApplyResult must include its applied mapped_payload.")
                        _promote(link, record, outcome, origin="remote")
                    _resolve_applied(locked, record)
                applied.append(outcome)
                count += outcome.count
            except (SemanticError, ValidationError, DataError) as error:
                # The record savepoint rolled back, including a new identity.
                if locked.kind == StreamKind.RECORD_REPLICA and isinstance(record, RecordChange):
                    link = links.observe(
                        locked,
                        record.external_key,
                        source_payload=record.source_payload,
                        source_hash=record.source_hash,
                        remote_version=record.remote_version,
                        metadata=record.metadata or None,
                    )
                if isinstance(error, SemanticError):
                    refusal = error
                else:
                    fields = (
                        sorted(error.error_dict)
                        if isinstance(error, ValidationError) and hasattr(error, "error_dict")
                        else ["unknown"]
                    )
                    refusal = SemanticError(
                        "invalid_record" if isinstance(error, ValidationError) else "invalid_record_data",
                        details={
                            "stream": locked.key,
                            "partition": _public_partition(locked.partition),
                            "external_key": (
                                record.external_key if isinstance(record, RecordChange) else adapter.record_key(record)
                            ),
                            "fields": fields,
                            "message": (
                                f"Record validation failed for fields: {', '.join(fields)}."
                                if isinstance(error, ValidationError)
                                else safe_integration_failure(error).message
                            ),
                        },
                    )
                record_key = "" if isinstance(record, RecordChange) else adapter.record_key(record)
                failures.append(_quarantine(locked, record, refusal, link=link, record_key=record_key).pk)
        adapter.finish_page(locked, page, applied)
        if reconcile_state is not None:
            locked.reconcile_state = reconcile_state
            locked.save(update_fields=["reconcile_state", "updated_at"])
        if advance_cursor:
            type(locked).objects.advance(
                locked,
                page.cursor,
                exhausted=page.exhausted,
                cursor_expires_at=page.cursor_expires_at,
            )
    hashes = [record.source_hash for record in page.records] if locked.kind == StreamKind.RECORD_REPLICA else []
    progress = canonical_json_sha256([locked.generation, page.cursor, hashes])
    return PageResult(locked, count, page.exhausted, tuple(failures), progress=progress)


def push_stream(
    stream: Any,
    adapter: BridgeImpl,
    *,
    external_keys: frozenset[str] | None = None,
    deadline: float | None = None,
) -> PageResult:
    """Conditionally write local candidates after a two-way replica's first baseline."""

    if connection.in_atomic_block:
        raise RuntimeError("Remote writes must run outside a database transaction.")
    count = 0
    failures: list[str] = []
    with changeset(fresh=True, activity="push", origin=stream), system_context(reason="integrate.stream.push"):
        stream.refresh_from_db()
        if not stream.writes_back or _baseline_adoption(stream):
            return PageResult(stream, exhausted=True)
        links = stream.links.model.objects
        candidates: set[str] = set()
        for candidate, link in _push_candidates(stream, adapter, external_keys):
            if deadline is not None and monotonic() >= deadline:
                return PageResult(stream, count, failure_ids=tuple(failures))
            candidates.add(candidate.external_key)
            if link is None:
                link, revision = links.observe(stream, candidate.external_key), None
            else:
                # The batch loaded its links before the writes ahead of this one; a
                # concurrent push or conflict resolution may have moved this link since.
                link = links.filter(pk=link.pk).with_sync_evidence().get()
                if link.pinned or link.write_holds or not link.local_changed(candidate.projection):
                    continue
                revision = link.applied_base
            record = RecordChange(
                candidate.external_key,
                None,
                link.source_hash,
                remote_version=link.remote_version,
                projection=candidate.projection,
                target=candidate.target,
                mapping_version=candidate.mapping_version,
                dependency_digest=revision.dependency_digest if revision is not None else "",
            )
            bases = link.sync_precondition
            try:
                result = adapter.write_back(link, candidate.projection, expected_version=link.remote_version)
            except RemoteRejected as error:
                failures.append(_quarantine(stream, record, error, link=link).pk)
                continue
            with changeset(fresh=True, activity="push", origin=stream), transaction.atomic():
                current_changeset(record=True)
                locked_stream = type(stream).objects.lock_current(stream, fenced_to=stream)
                locked = links.lock_if_supported().get(pk=link.pk)
                _check_write_precondition(locked, bases)
                _reflect_write(locked, record, result)
                # The write the remote once refused has now landed.
                _resolve_applied(locked_stream, record)
            count += 1
        if external_keys is None:
            # A due refusal whose record has nothing left to write (deleted or
            # reverted locally) has nothing to retry, and no rescan reads it.
            for link in links.filter(stream=stream).with_open_failures():
                if (
                    link.external_key not in candidates
                    and link.outbox_last_error
                    and (link.outbox_retry_at is None or link.outbox_retry_at <= timezone.now())
                ):
                    for refusal in link.failures(open_only=True):
                        if refusal.slot is None and refusal.kind != SyncFailureKind.CONFLICT:
                            refusal.resolve()

    return PageResult(stream, count, True, tuple(failures))


@dataclass(frozen=True, slots=True)
class PushPreview:
    """What one push of a stream would write, read without writing anything.

    ``writes`` are the external keys the push would send; ``held`` are those an
    open conflict, or a refused write still in its backoff, keeps back. A stream
    that does not write back, or still adopts its first baseline, previews empty.
    """

    writes: tuple[str, ...] = ()
    held: tuple[str, ...] = ()


def preview_push(stream: Any, adapter: BridgeImpl) -> PushPreview:
    """Return the keys :func:`push_stream` would write now, without writing anything."""

    with system_context(reason="integrate.stream.preview_push"):
        if not stream.writes_back or _baseline_adoption(stream):
            return PushPreview()
        writes: list[str] = []
        held: list[str] = []
        for candidate, link in _push_candidates(stream, adapter, None):
            (held if link is not None and (link.pinned or link.write_holds) else writes).append(candidate.external_key)
        return PushPreview(tuple(writes), tuple(held))


_CANDIDATE_BATCH = 500
"""Local changes whose links one query loads, with their sync evidence."""


def _push_candidates(
    stream: Any, adapter: BridgeImpl, external_keys: frozenset[str] | None
) -> Iterator[tuple[LocalChange, Any]]:
    """Yield each local change that differs from its link's base, with that link or ``None`` when new.

    A link carries its sync evidence (:meth:`RecordLinkQuerySet.with_sync_evidence`),
    so the preview reads its write holds without a query each. The push re-reads a
    link just before writing it, since the batch predates the writes ahead of it.
    """

    candidates = iter(adapter.local_changes(stream, keys=external_keys))
    links = stream.links.model.objects
    while batch := list(islice(candidates, _CANDIDATE_BATCH)):
        known = {
            link.external_key: link
            for link in links.filter(
                stream=stream, external_key__in=[candidate.external_key for candidate in batch]
            ).with_sync_evidence()
        }
        for candidate in batch:
            link = known.get(candidate.external_key)
            if link is None or link.local_changed(candidate.projection):
                yield candidate, link


def read_stream_keys(adapter: BridgeImpl, stream: Any, keys: Sequence[str]) -> tuple[RecordChange, ...]:
    """Read a bounded identity set and enforce the declared adapter contract."""
    records = tuple(islice(adapter.read_keys(stream, keys), len(keys) + 1))
    if (
        any(not isinstance(record, RecordChange) for record in records)
        or len(records) != len(keys)
        or {record.external_key for record in records} != set(keys)
    ):
        raise AdapterContractError(
            "read_keys must return each requested key exactly once, including remote tombstones."
        )
    return records


def resolve_conflict(link: Any, slot: int | None, keep: ConflictKeep) -> Any:
    """Fresh-read and retain the chosen side of one linked conflict."""

    if keep not in ConflictKeep.values:
        raise ValidationError("Choose remote or local changes.")
    if connection.in_atomic_block:
        raise RuntimeError("Conflict resolution must run outside a database transaction.")
    with system_context(reason="integrate.record.resolve_conflict"):
        link = type(link).objects.with_sync_evidence().get(pk=link.pk)
        row = next(row for row in link.failures() if row.slot == slot)
        if row.kind != SyncFailureKind.CONFLICT:
            raise ValidationError("Conflict resolution requires a linked conflict.")
        if not row.is_open:
            return row
        stream = link.stream
        if stream.state == StreamState.CLOSED:
            raise ValidationError("Resync the closed stream before resolving its conflict.")
        owner = link.parent if link.parent is not None else link
        adapter = stream.integration.concrete_capability().capability_impl
        keys = frozenset((owner.external_key,))
        resolved = False
        try:
            if not adapter.supports_identity_reads:
                raise ValidationError("Conflict resolution requires an adapter with identity reads.")
            if stream.resync_required:
                raise ValidationError("Complete the requested stream baseline before resolving its conflict.")
            if keep == ConflictKeep.LOCAL and (not stream.writes_back or not stream.has_completed_baseline()):
                raise ValidationError("Keeping local changes requires a completed writable baseline.")
            precondition = owner.sync_precondition
            remote = read_stream_keys(adapter, stream, tuple(keys))[0] if keep == ConflictKeep.LOCAL else None
            with transaction.atomic():
                type(stream).objects.lock_current(stream, fenced_to=stream)
                locked = type(owner).objects.lock_if_supported().get(pk=owner.pk)
                current_link = type(link).objects.lock_if_supported().get(pk=link.pk)
                current = next(row for row in current_link.failures() if row.slot == slot)
                if not current.is_open:
                    return current
                if not locked.sync_precondition_matches(precondition):
                    raise RuntimeError("Record bases changed during conflict resolution; retry the choice.")
                if remote is not None:
                    type(locked).objects.observe(
                        stream,
                        locked.external_key,
                        source_payload=remote.source_payload,
                        source_hash=remote.source_hash,
                        remote_version=remote.remote_version,
                    )
                current_link.resolve_failure(slot, conflict=True)
            resolved = True
            if keep == ConflictKeep.REMOTE:
                prepared = begin_stream_cycle(stream, adapter, force_apply=keys)
                if prepared.resync_required:
                    raise ValidationError("Complete the requested stream baseline before resolving its conflict.")
            elif not push_stream(stream, adapter, external_keys=keys).count:
                raise ValidationError("The selected local record could not be written; its failure remains open.")
            if type(link).objects.filter(pk__in=(link.pk, owner.pk)).open_failures():
                raise ValidationError("The record could not be synchronized; its failure remains open.")
            fresh = type(link).objects.get(pk=link.pk)
            return next(row for row in fresh.failures() if row.slot == slot)
        except Exception:
            if resolved:
                fresh = type(link).objects.get(pk=link.pk)
                if not type(link).objects.filter(pk__in=(link.pk, owner.pk)).open_failures():
                    fresh.record_failure(
                        kind=row.kind,
                        code=row.code,
                        source_hash=row.source_hash,
                        mapping_version=row.mapping_version,
                        details=row.details,
                        outbox=slot is None,
                    )
            raise
        finally:
            adapter.close()


def _save_reconcile(stream: Any, state: dict[str, Any] | None) -> None:
    if state is None:
        stream.last_reconciled_at = timezone.now()
    stream.reconcile_state = state or {}
    stream.save(update_fields=["reconcile_state", "last_reconciled_at", "updated_at"])


def reconcile_stream(stream: Any, adapter: BridgeImpl, *, page_bound: int = 100, deadline: float | None = None) -> int:
    """Commit one bounded sweep pulse, returning the number of absence observations.

    Continue while stream.reconcile_state is nonempty. Enumeration seeks after
    its last committed key; each key page is read and applied before bounded
    root/child absence passes. Without identity reads, a separate extraction
    baseline precedes enumeration. The adapter cursor remains opaque.
    """

    if connection.in_atomic_block:
        raise RuntimeError("Remote enumeration must run outside a database transaction.")
    if page_bound < 1:
        raise ValueError("page_bound must be positive.")
    with changeset(fresh=True, activity="pull", origin=stream), system_context(reason="integrate.stream.reconcile"):
        manager = type(stream).objects
        identity_reads = adapter.supports_identity_reads
        stream.refresh_from_db()
        if not stream.accepts_rescan() or stream.reconcile_interval is None:
            return 0
        with transaction.atomic():
            locked = manager.lock_current(stream)
            if not locked.accepts_rescan() or locked.reconcile_interval is None:
                return 0
            state = _validated_cursor(locked.reconcile_state) or None
            if state is None:
                now = timezone.now()
                if locked.last_reconciled_at and locked.last_reconciled_at + locked.reconcile_interval > now:
                    stream.reconcile_state = locked.reconcile_state
                    return 0
                state = {
                    "started_at": now.isoformat(),
                    "phase": "enumerate" if identity_reads else "extract",
                    "after": None,
                }
                _save_reconcile(locked, state)
            original_cursor = _validated_cursor(locked.cursor)
        if state["phase"] == "extract":
            baseline = copy(locked)
            baseline.cursor = state.get("cursor", {})
            baseline.phase = StreamPhase.BASELINE
            page = _extract_page(adapter, baseline, page_bound, deadline=deadline)
            if page.resync_required:
                raise CursorInvalid()
            if not page.exhausted and page.cursor == baseline.cursor:
                raise AdapterContractError("Reconciliation baseline did not advance its cursor.")
            state = {**state, "cursor": page.cursor, "phase": "enumerate" if page.exhausted else "extract"}
            locked = _apply_page(
                locked,
                adapter,
                page,
                original_cursor=original_cursor,
                advance_cursor=False,
                reconcile_state=state,
            ).stream
        elif state["phase"] == "enumerate":
            keys = tuple(islice(adapter.enumerate_keys(locked, after=state["after"]), page_bound))
            if any(not isinstance(key, str) or not key for key in keys) or len(keys) != len(set(keys)):
                raise AdapterContractError("enumerate_keys must yield unique nonempty external keys.")
            if state["after"] in keys:
                raise AdapterContractError("enumerate_keys must seek exclusively after its continuation key.")
            next_state = {**state, "after": keys[-1] if keys else None}
            if not keys:
                next_state["phase"] = "roots"
            if identity_reads and keys:
                page = StreamPage(read_stream_keys(adapter, locked, keys), original_cursor, exhausted=False)
                locked = _apply_page(
                    locked,
                    adapter,
                    page,
                    original_cursor=original_cursor,
                    advance_cursor=False,
                    reconcile_state=next_state,
                ).stream
            else:
                with transaction.atomic():
                    locked = manager.lock_current(locked, fenced_to=locked)
                    links = locked.links.model.objects
                    for key in keys:
                        if not links.filter(stream=locked, external_key=key).exists():
                            raise AdapterContractError("The extraction baseline omitted an enumerated identity.")
                        links.observe(locked, key)
                    _save_reconcile(locked, next_state)
        else:
            with transaction.atomic():
                locked = manager.lock_current(locked, fenced_to=locked)
                links = locked.links.model.objects
                started = datetime.fromisoformat(state["started_at"])
                rows = links.filter(stream=locked).exclude(status=LinkStatus.TOMBSTONE)
                if state["phase"] == "roots":
                    rows = rows.filter(parent__isnull=True).filter(
                        Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=started)
                    )
                else:
                    missing_parents = links.filter(stream=locked, parent__isnull=True, absence_count__gt=0).filter(
                        Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=started)
                    )
                    rows = rows.filter(parent_id__in=missing_parents.values("pk"))
                if state["after"] is not None:
                    rows = rows.filter(pk__gt=state["after"])
                batch = tuple(rows.order_by("pk").lock_if_supported()[:page_bound])
                changed = links.mark_absent(locked, [link.external_key for link in batch])
                previous = {link.pk: link.status for link in batch}
                transitions = tuple(
                    link
                    for link in links.filter(pk__in=previous).order_by("pk")
                    if link.status != previous[link.pk]
                    and link.status in (LinkStatus.UNAVAILABLE, LinkStatus.TOMBSTONE)
                )
                if transitions:
                    adapter.on_absent(locked, transitions)
                if batch:
                    _save_reconcile(locked, {**state, "after": batch[-1].pk})
                elif state["phase"] == "roots":
                    _save_reconcile(locked, {**state, "phase": "children", "after": None})
                else:
                    _save_reconcile(locked, None)
                stream.reconcile_state, stream.last_reconciled_at = locked.reconcile_state, locked.last_reconciled_at
                return changed
        stream.reconcile_state, stream.last_reconciled_at = locked.reconcile_state, locked.last_reconciled_at
        return 0


def begin_stream_cycle(
    stream: Any,
    adapter: BridgeImpl | None = None,
    *,
    page_bound: int = 100,
    force_apply: frozenset[str] = frozenset(),
) -> Any:
    """Re-read due replica identities, or request a logged baseline fallback.

    Call once per cycle before advance_stream, outside any transaction. Adapters
    declaring supports_identity_reads return RecordChange observations through
    read_keys without moving the stream cursor. The reads happen outside the
    stream lock, so a due identity is applied only while its link is still as it
    was selected; one that another attempt or page moved first keeps that result.
    Conflicts require explicit resolution. Event feeds have no replica rescan.
    """

    if connection.in_atomic_block:
        raise RuntimeError("Stream rescan must run outside a database transaction.")
    if page_bound < 1:
        raise ValueError("page_bound must be positive.")
    with changeset(fresh=True, activity="pull", origin=stream), system_context(reason="integrate.stream.cycle"):
        manager = type(stream).objects
        links_manager = stream.links.model.objects
        identity_reads = adapter is not None and adapter.supports_identity_reads
        stream.refresh_from_db()
        if stream.resync_required:
            stream = manager.bump_generation(stream)
        with transaction.atomic():
            stream = manager.lock_current(stream)
            if not stream.accepts_rescan():
                return stream
            due = links_manager.filter(stream=stream).rescan(limit=page_bound)
            retention = stream.tombstone_retention
            retry = False
            if retention and (stream.last_advanced_at is None or stream.last_advanced_at + retention < timezone.now()):
                retry = links_manager.filter(
                    stream=stream,
                    status=LinkStatus.TOMBSTONE,
                    tombstoned_at__lt=timezone.now() - retention,
                ).exists()
            if force_apply and not identity_reads:
                raise AdapterContractError("Explicit remote resolution requires identity reads.")
            if due and not identity_reads:
                retry = True
                for row in due:
                    row.link.defer_failure(row, details={"rescan": "baseline", "reason": "read_keys_unavailable"})
            if retry:
                manager.filter(pk=stream.pk).update(resync_required=True)
                stream.resync_required = True
            if stream.resync_required or (not due and not force_apply) or adapter is None:
                return stream
            links = (
                links_manager.filter(stream=stream, pk__in=[row.link_pk for row in due])
                .select_related("parent")
                .order_by("external_key")
            )
            keys, reapply = set(force_apply), {}
            for link in links:
                parent = link.parent
                owner = parent if parent is not None else link
                keys.add(owner.external_key)
                reapply[owner.external_key] = owner.sync_precondition
            if not keys:
                return stream
            original_cursor = _validated_cursor(stream.cursor)
        records = read_stream_keys(adapter, stream, tuple(sorted(keys)))
        return _apply_page(
            stream,
            adapter,
            StreamPage(records, original_cursor, exhausted=False),
            original_cursor=original_cursor,
            advance_cursor=False,
            force_apply=force_apply,
            reapply=reapply,
        ).stream


def open_stream(
    bridge: Any,
    key: str,
    partition: str,
    adapter: BridgeImpl,
    *,
    definition: StreamDefinition | None = None,
    deadline: float | None = None,
) -> Any:
    """Open a partition against its current declaration and retained stream progress.

    Opening reconciles: the stream takes what its declaration says now (direction,
    snapshot, authority) through ``SyncStream.objects.current``, so a changed
    declaration reaches an existing stream on every path. A caller that already
    discovered declarations supplies its matching definition to avoid repeating
    transport discovery; without one the adapter declares again. A drain opens its
    partition once; a caller resuming a partition it opened resolves the current
    epoch through ``SyncStream.objects.current_for_bridge``.
    """

    manager = bridge.sync_streams.model.objects
    with system_context(reason="integrate.stream.open"):
        if definition is None:
            definitions = [
                item for item in adapter.streams(deadline=deadline) if (item.key, item.partition) == (key, partition)
            ]
            if len(definitions) != 1:
                raise AdapterContractError("A backend must declare the requested stream partition exactly once.")
            definition = definitions[0]
        elif (definition.key, definition.partition) != (key, partition):
            raise AdapterContractError("The supplied stream declaration must match the requested partition.")
        definition = replace(definition, cursor=_validated_cursor(definition.cursor))
        return manager.current(bridge, definition)


def _report(bridge: Any, message: str, **details: Any) -> None:
    reporter = current_bridge_progress()
    if reporter is not None:
        reporter.report(str(bridge.SyncStage.SYNCING), message=message, details=details)


def _drain(bridge: Any, adapter: BridgeImpl, definition: StreamDefinition, deadline: float) -> int:
    stage = "open"
    try:
        stream = open_stream(
            bridge,
            definition.key,
            definition.partition,
            adapter,
            definition=definition,
            deadline=deadline,
        )
        stage = "rescan"
        stream = begin_stream_cycle(stream, adapter)
        landed, resets = 0, 0
        page_bound = max(1, int(bridge.config.get("sync_page_bound", 100)))
        exhausted = False
        previous = None
        while monotonic() < deadline:
            stage = "pull"
            result = advance_stream(stream, adapter, page_bound=page_bound, deadline=deadline)
            stream, exhausted = result.stream, result.exhausted
            landed += result.count
            if monotonic() >= deadline:
                break
            stage = "continuation"
            previous, resets = result.check_continuation(previous=previous, resets=resets)
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
                    stage = "push"
                    landed += push_stream(stream, adapter, deadline=deadline).count
                    while monotonic() < deadline:
                        stage = "reconcile"
                        reconcile_stream(stream, adapter, page_bound=page_bound, deadline=deadline)
                        if not stream.reconcile_state:
                            break
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
    except Exception as error:  # noqa: BLE001 -- retain the operation that actually failed.
        raise BridgeSyncError((PartitionFailure(definition, stage, error),)) from error


def _drain_partition(bridge: Any, definition: StreamDefinition, deadline: float) -> int:
    close_old_connections()
    try:
        with system_context(reason="integrate.stream.partition"):
            row = type(bridge)._base_manager.get(pk=bridge.pk)
            adapter = row.capability_impl
            try:
                with bridge_progress_context(row):
                    return _drain(row, adapter, definition, deadline)
            finally:
                adapter.close()
    finally:
        connections.close_all()


def sync_bridge(bridge: Any) -> int:
    """Drive declared partitions within the existing bridge task's time budget."""

    config = bridge.config
    worker_budget = task_time_budget().total_seconds()
    deadline = monotonic() + max(0.0, min(worker_budget, float(config.get("sync_time_budget", worker_budget))))
    adapter = bridge.capability_impl
    try:
        with system_context(reason="integrate.bridge.streams"):
            definitions = tuple(sorted(adapter.streams(deadline=deadline), key=lambda item: (item.key, item.partition)))
            identities = [(item.key, item.partition) for item in definitions]
            if len(identities) != len(set(identities)):
                raise AdapterContractError("A backend declared the same stream partition twice.")
            parallelism = max(1, int(config.get("sync_parallelism", 4)))
            if adapter.sync_parallelism is not None:
                parallelism = min(parallelism, adapter.sync_parallelism)
            if connection.vendor != "postgresql":
                parallelism = 1
            failures: list[PartitionFailure] = []
            landed = 0
            if parallelism <= 1 or len(definitions) <= 1:
                for definition in definitions:
                    try:
                        landed += _drain(bridge, adapter, definition, deadline)
                    except BridgeSyncError as error:
                        failures.extend(error.failures)
            else:
                with ThreadPoolExecutor(
                    max_workers=min(parallelism, len(definitions)), thread_name_prefix="bridge-sync"
                ) as pool:
                    # Each worker records its own changeset row under the cycle's boundary
                    # and actor (angee.provenance.context), so the copied context is safe to share.
                    futures = {
                        pool.submit(copy_context().run, _drain_partition, bridge, definition, deadline): definition
                        for definition in definitions
                    }
                    for future in as_completed(futures):
                        try:
                            landed += future.result()
                        except BridgeSyncError as error:
                            failures.extend(error.failures)
                        except Exception as error:  # noqa: BLE001 -- partition setup/cleanup can also fail.
                            failures.append(PartitionFailure(futures[future], "partition", error))
            if failures:
                sync_error = BridgeSyncError(failures)
                for failure in sync_error.failures:
                    failure.log(bridge)
                details = [failure.details for failure in sync_error.failures]
                # All workers have stopped: a later healthy page cannot overwrite
                # the failed partition with its own progress before settlement.
                _report(
                    bridge,
                    sync_error.public_message,
                    stream=details[0]["stream"],
                    partition=details[0]["partition"],
                    stage=details[0]["stage"],
                    failures=details,
                )
                raise sync_error from sync_error.failures[0].error
            return landed
    finally:
        adapter.close()
