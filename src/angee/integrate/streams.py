"""Bounded synchronization: transport outside transactions, page and cursor inside.

Adapters own protocol parsing and domain projections. Due failures are
re-read by identity when supported; there is no durable work queue.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import nullcontext
from contextvars import copy_context
from copy import copy
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from itertools import islice
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import DataError, close_old_connections, connection, connections, transaction
from django.utils import timezone
from pydantic import ConfigDict, JsonValue, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from rebac import system_context

from angee.base.serialization import canonical_json_sha256, json_equal
from angee.integrate.errors import IntegrationError, safe_integration_failure
from angee.integrate.impl import AdapterContractError, BridgeImpl
from angee.integrate.states import (
    EXTERNAL_KEY_LENGTH,
    LOCAL_VERSION_LENGTH,
    UNSET,
    LinkStatus,
    StreamDefinition,
    StreamDirection,
    StreamKind,
    StreamPhase,
    StreamState,
    SyncFailureKind,
    exempt_from_discovery,
)
from angee.integrate.sync import (
    current_progress_bridge,
    progress_bridge_context,
    sync_deadline,
    sync_deadline_context,
)
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
    absent: Sequence[str] = ()
    """Replica keys a complete listing on this page did not include.

    The driver counts each once against the stream's absence threshold
    (``RecordLinkManager.mark_absent``), so one incomplete listing never
    tombstones a record. An adapter reports them on one page per listing, and
    none when the listing came back empty while records exist.
    """


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
    dependency_digest: str = ""
    tombstone: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    mapped_payload: Any = UNSET
    """The remote projection in the same domain shape as the local projection."""
    failure: SemanticError | None = None
    """An adapter's refusal when the source cannot produce a mapped projection."""
    local_version: str | None = ""
    """The local version ``projection`` was read at (:attr:`LocalChange.local_version`); a push records it."""


@dataclass(frozen=True, slots=True)
class LocalChange:
    """A local projection candidate; conditional write-back fences remote edits."""

    external_key: str
    projection: Any
    target: Any = None
    local_version: str | None = ""
    """The version of the local state ``projection`` was read from, in the same read; ``""`` when unversioned.

    Exact for what the projection reads, and compared only for equality: a push
    records it with the base it writes (``RecordLink.local_version``), so a row
    whose version is unchanged is unchanged since that base, and an adapter may
    leave it out of a full read (``RecordLinkManager.changed_since_base``). At
    most ``LOCAL_VERSION_LENGTH`` long. ``None`` is a versioned row without a
    version yet (``RecordLinkManager.with_local_versions``): the push baselines
    it (``RecordLinkManager.settle``).
    """


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Applied evidence returned to the sole primary promoter: the driver.

    Adapters return the evidence actually applied, including dependency facts
    learned during apply. The driver stamps the stream's declared mapping
    version; adapters must not promote the primary link themselves.
    Omitted target preserves its binding; explicit None withdraws it.
    """

    target: Any = UNSET
    mapped_payload: Any = UNSET
    count: int = 1
    dependency_digest: str = ""

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
    """Compare nested mapped facts as JSON values (``json_equal``); concurrent divergent edits require a choice."""
    local_changed = not json_equal(base, local)
    remote_changed = not json_equal(base, remote) or mapping_changed
    if local_changed and (remote_changed or tombstone):
        if not tombstone and not mapping_changed and json_equal(local, remote):
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
    appeared: int = 0
    """Links of a container-holding stream that became live containers on this page: new, revived or bound."""

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
        mapping_version=stream.mapping_version,
        details=details,
        **({"outbox": isinstance(error, RemoteRejected)} if link is not None else {}),
    )


def _require_discovery_ready(stream: Any) -> None:
    """Refuse a remote write or a reset while the stream's integration is not discovery-ready.

    The driver asks before each one, outside the page transaction, on the
    integration as it is stored now (``Integration.require_stored_discovery_ready``);
    every commit resting on remote facts is also refused under the stream lock
    (``SyncStreamManager.lock_current``).
    """

    stream._meta.get_field("integration").related_model.require_stored_discovery_ready(stream.integration_id)


def reset_stream(stream: Any, *, cursor: dict[str, Any]) -> PageResult:
    """Supersede an epoch and seed its replacement through the stream manager.

    A retired generation whose container is gone is not superseded
    (``bump_generation`` returns it), so its page reports exhausted, not reset.
    No stream resets while its integration awaits discovery.
    """
    cursor = _validated_cursor(cursor)
    _require_discovery_ready(stream)
    manager = type(stream).objects
    successor = manager.bump_generation(stream, cursor=cursor)
    if successor.pk == stream.pk:
        return PageResult(successor, exhausted=True)
    return PageResult(successor, reset=True)


def _decision(record: RecordChange, *, base: Any, adopting: bool, mapping_version: int) -> ChangeKind:
    if adopting and base is None and record.target is not None and not record.tombstone:
        return ChangeKind.APPLY
    if record.mapped_payload is UNSET and not record.tombstone:
        raise AdapterContractError("Replica observations must include their remote mapped_payload.")
    mapping_changed = base is None or (
        base.mapping_version != mapping_version or base.dependency_digest != record.dependency_digest
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


def _promote(stream: Any, link: Any, record: RecordChange, outcome: ApplyResult, *, origin: str) -> None:
    manager = type(link).objects
    manager.promote(
        link,
        source_payload=record.source_payload,
        source_hash=record.source_hash,
        mapped_payload=outcome.mapped_payload,
        mapping_version=stream.mapping_version,
        dependency_digest=outcome.dependency_digest,
        target=outcome.target,
        remote_version=record.remote_version,
        origin=origin,
        tombstone=record.tombstone,
    )


def _withdraw_absent(stream: Any, adapter: BridgeImpl, links: Sequence[Any]) -> None:
    """Apply the adapter's absence verdicts, then record each withdrawal it reports as its link's applied base.

    A tombstone found by absence is recorded as a page tombstone is (:func:`_promote`
    with ``tombstone=True``): the withdrawn local projection becomes the base, so a
    record that returns compares as a remote change and applies.
    """

    withdrawn = adapter.on_absent(stream, links) or {}
    for link in links:
        outcome = withdrawn.get(link.pk)
        if outcome is None or link.status != LinkStatus.TOMBSTONE:
            continue
        type(link).objects.promote(
            link,
            source_payload={},
            source_hash="",
            mapped_payload=outcome.mapped_payload,
            mapping_version=stream.mapping_version,
            dependency_digest=outcome.dependency_digest,
            target=outcome.target,
            origin="remote",
            tombstone=True,
        )


def _lock_written(
    stream: Any, adapter: BridgeImpl, links: Any, link: Any, record: RecordChange
) -> tuple[Any, RecordChange]:
    """Lock a written candidate's row, then its link; return the link, and ``record`` binding no row its key lost.

    A push reads its candidates before it writes them. Meanwhile the row may be
    deleted, or, on a push stream, leave the key it was written under (a moved
    override), whether or not its link was bound yet. The row is locked first
    (``RecordLinkManager.lock_target``, the order a deletion and
    ``RecordLinkManager.release_pushes`` take), so a deletion or move still running
    finishes first. Then a row that is gone, or that the adapter's keyed projection
    (``local_changes(stream, keys={key})``) no longer yields under this key, binds
    nothing: binding it would bind a deleted row, or a moved row's old key, which
    no release names again. The link stays released, and the next push writes its
    removal over this write's version. A link on any other stream holds its row
    (``RecordLink.live_reference_filter``), so its row cannot leave its key.
    """

    target = record.target
    named = (
        target is not None
        and links.lock_target(target)
        and (
            stream.direction != StreamDirection.PUSH
            or any(
                change.target == target
                for change in adapter.local_changes(stream, keys=frozenset({record.external_key}))
            )
        )
    )
    locked = links.lock_if_supported().get(pk=link.pk)
    if target is not None and not named:
        record = replace(record, target=None, local_version="")
    return locked, record


def _reflect_write(stream: Any, link: Any, record: RecordChange, result: WriteBackResult) -> None:
    if not isinstance(result, WriteBackResult):
        raise AdapterContractError("write_back must return WriteBackResult.")
    type(link).objects.promote(
        link,
        source_payload=result.source_payload,
        source_hash=result.source_hash,
        mapped_payload=record.projection,
        target=record.target,
        remote_version=result.remote_version,
        mapping_version=stream.mapping_version,
        dependency_digest=record.dependency_digest,
        origin="local",
        tombstone=result.tombstone,
        local_version=record.local_version,
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
    with system_context(reason="integrate.stream.advance"), sync_deadline_context(deadline):
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
    adopting = stream.adopts_baseline()
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
    # Discovery's refusal of a write after others were sent: those are reflected, the rest wait.
    unsent: IntegrationError | None = None
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
                    mapping_version=stream.mapping_version,
                )
                == ChangeKind.WRITE_BACK
            ):
                try:
                    _require_discovery_ready(stream)
                except IntegrationError as stopped:
                    if not written:
                        raise
                    unsent = stopped
                    break
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
    # A container that stops observing (tombstoned or withdrawn) retires the streams it holds.
    ended = any(record.tombstone for record in page.records if isinstance(record, RecordChange)) or any(
        isinstance(result, WriteBackResult) and result.tombstone for _bases, result in written.values()
    )
    # A page that sent a write commits through the exemption: the write landed, and the check
    # before the first one, after the extraction, confirmed what the page read.
    with (
        changeset(fresh=True, activity="pull", origin=stream),
        exempt_from_discovery() if written else nullcontext(),
        transaction.atomic(),
    ):
        current_changeset(record=True)
        locked = type(stream).objects.lock_current(stream, fenced_to=stream)
        adapter.prepare_page(locked, page)
        evidence = (
            {}
            if evidence_rows is None
            else {link.external_key: link for link in evidence_rows.order_by("pk").lock_if_supported()}
        )
        holding = evidence_rows is not None and locked.holds_containers
        live_before = (
            set(evidence_rows.live_containers().values_list("pk", flat=True))
            if evidence_rows is not None and holding
            else set()
        )
        seen: set[str] = set()
        for record in page.records:
            link = None
            observed = None
            revision = None
            if evidence_rows is not None and len(record.external_key) > EXTERNAL_KEY_LENGTH:
                # No link can hold this identity: the stream keeps one refusal and the page goes on.
                message = f"The remote identity is longer than {EXTERNAL_KEY_LENGTH} characters."
                refusal = SemanticError("identity_too_long", details={"message": message})
                failures.append(_quarantine(locked, record, refusal).pk)
                continue
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
                            else _decision(
                                record, base=revision, adopting=adopting, mapping_version=locked.mapping_version,
                            )
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
                            locked.resolve_applied(record)
                            continue
                        if decision == ChangeKind.WRITE_BACK:
                            if not locked.writes_back:
                                raise SemanticError("local_change_on_pull", kind=SyncFailureKind.CONFLICT)
                            write = written.get(record.external_key)
                            if write is None and (record.external_key in held or unsent is not None):
                                # A refused write waits out its retry time, and one discovery
                                # stopped waits for it; the local change stays. The first pass's
                                # hold decides, so a refusal resolved since then waits for the
                                # next page rather than failing this one.
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
                            _reflect_write(locked, link, record, result)
                            locked.resolve_applied(record)
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
                        _promote(locked, link, record, outcome, origin="remote")
                        ended = ended or outcome.target is None
                    locked.resolve_applied(record)
                applied.append(outcome)
                count += outcome.count
            except (SemanticError, ValidationError, DataError) as error:
                # The record savepoint rolled back, including a new identity. Observe it again
                # only when observing succeeded: an identity that failed to observe stays out.
                if link is not None and locked.kind == StreamKind.RECORD_REPLICA and isinstance(record, RecordChange):
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
                        # Django ValidationError has error_dict only for field-keyed errors.
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
        if page.absent:
            if evidence_rows is None:
                raise AdapterContractError("Only replica pages report absent keys.")
            absent = links.mark_absent(locked, page.absent)
            if absent:
                _withdraw_absent(locked, adapter, absent)
            ended = ended or any(link.status == LinkStatus.TOMBSTONE for link in absent)
        if ended:
            type(locked).objects.retire_children(locked)
        appeared = (
            links.filter(stream=locked, external_key__in=[record.external_key for record in page.records])
            .live_containers()
            .exclude(pk__in=live_before)
            .count()
            if holding
            else 0
        )
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
    if unsent is not None:
        raise unsent
    hashes = [record.source_hash for record in page.records] if locked.kind == StreamKind.RECORD_REPLICA else []
    progress = canonical_json_sha256([locked.generation, page.cursor, hashes])
    return PageResult(locked, count, page.exhausted, tuple(failures), progress=progress, appeared=appeared)


def push_stream(
    stream: Any,
    adapter: BridgeImpl,
    *,
    external_keys: frozenset[str] | None = None,
    deadline: float | None = None,
) -> PageResult:
    """Conditionally write local candidates after a two-way replica's first baseline.

    The cycle's deadline (``sync_deadline()``: ``deadline``, else the enclosing
    sync's) is checked between candidates; a push stream's released links are
    candidates too, their removal (:func:`push_candidates`). While the stream's
    sweep is due (``SyncStream.sweep_due``) the push compares every local row; a
    push stream's push that ran through every candidate completes that sweep, which
    no absence pass does for it (``SyncStream.save_reconcile``). No candidate is
    written while the stream's integration awaits discovery; each asks before it
    is sent, and a write already sent is reflected even when discovery turned
    pending since.
    """

    if connection.in_atomic_block:
        raise RuntimeError("Remote writes must run outside a database transaction.")
    count = 0
    failures: list[str] = []
    with (
        changeset(fresh=True, activity="push", origin=stream),
        system_context(reason="integrate.stream.push"),
        sync_deadline_context(deadline),
    ):
        stream.refresh_from_db()
        if not stream.writes_back or stream.adopts_baseline():
            return PageResult(stream, exhausted=True)
        links = stream.links.model.objects
        candidates: set[str] = set()
        sweep = external_keys is None and stream.direction == StreamDirection.PUSH and stream.sweep_due()
        for candidate, link in push_candidates(stream, adapter, external_keys, settle=True):
            if (limit := sync_deadline()) is not None and monotonic() >= limit:
                return PageResult(stream, count, failure_ids=tuple(failures))
            candidates.add(candidate.external_key)
            if link is None:
                link, revision = links.observe(stream, candidate.external_key), None
            else:
                # The batch loaded its links before the writes ahead of this one; a
                # concurrent push, conflict resolution or release may have moved this link since.
                current = links.filter(pk=link.pk).with_sync_evidence().get()
                if (
                    current.pinned
                    or current.write_holds
                    or not current.local_changed(candidate.projection)
                    # Released since the batch read it (its row deleted, or moved off this
                    # key): the projection read is no longer the key's, and its removal is the next push's.
                    or (link.target_object_id is not None and current.target_object_id is None)
                ):
                    continue
                link, revision = current, current.applied_base
            record = RecordChange(
                candidate.external_key,
                None,
                link.source_hash,
                remote_version=link.remote_version,
                projection=candidate.projection,
                target=candidate.target,
                dependency_digest=revision.dependency_digest if revision is not None else "",
                local_version=candidate.local_version,
            )
            # Asked once per write, after the skips: a skipped candidate sends nothing.
            _require_discovery_ready(stream)
            bases = link.sync_precondition
            try:
                result = adapter.write_back(link, candidate.projection, expected_version=link.remote_version)
            except RemoteRejected as error:
                # The remote answered the write it was sent: its refusal is recorded whatever discovery says.
                with exempt_from_discovery():
                    failures.append(_quarantine(stream, record, error, link=link).pk)
                continue
            # The write landed: it is reflected whatever discovery says, so the next push neither repeats nor
            # conflicts with it. The next candidate asks again before it is sent.
            with (
                changeset(fresh=True, activity="push", origin=stream),
                exempt_from_discovery(),
                transaction.atomic(),
            ):
                current_changeset(record=True)
                locked_stream = type(stream).objects.lock_current(stream, fenced_to=stream)
                locked, record = _lock_written(locked_stream, adapter, links, link, record)
                _check_write_precondition(locked, bases)
                _reflect_write(locked_stream, locked, record, result)
                # The write the remote once refused has now landed.
                locked_stream.resolve_applied(record)
                if result.tombstone:
                    type(locked_stream).objects.retire_children(locked_stream)
            count += 1
        if sweep:
            with transaction.atomic():
                swept = type(stream).objects.lock_current(stream, fenced_to=stream)
                swept.save_reconcile(None)
                stream.last_reconciled_at = swept.last_reconciled_at
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
        if not stream.writes_back or stream.adopts_baseline():
            return PushPreview()
        writes: list[str] = []
        held: list[str] = []
        for candidate, link in push_candidates(stream, adapter, None):
            (held if link is not None and (link.pinned or link.write_holds) else writes).append(candidate.external_key)
        return PushPreview(tuple(writes), tuple(held))


_CANDIDATE_BATCH = 500
"""Local changes whose links one query loads, with their sync evidence."""


def push_candidates(
    stream: Any, adapter: BridgeImpl, external_keys: frozenset[str] | None, *, settle: bool = False
) -> Iterator[tuple[LocalChange, Any]]:
    """Yield each local change that differs from its link's base, with that link or ``None`` when new.

    A link carries its sync evidence (:meth:`RecordLinkQuerySet.with_sync_evidence`),
    so the preview reads its write holds without a query each. The push re-reads a
    link just before writing it, since the batch predates the writes ahead of it.

    ``settle`` (the push's; a preview writes nothing) records the newer local
    version of each candidate whose projection still equals its base, and
    baselines one read without a version (:meth:`RecordLinkManager.settle`), so an
    adapter that reads only rows changed since their base
    (:meth:`RecordLinkManager.changed_since_base`) stops reading a row touched
    without a change to its projection. A local version longer than
    ``LOCAL_VERSION_LENGTH`` is the adapter's contract error, refused before
    anything of its batch is written.

    On a push stream, a written link whose row went (released by its deletion,
    ``RecordLinkManager.release_pushes``, or still naming a row a delete that locked
    nothing removed: ``RecordLinkQuerySet.released``) is a removal,
    ``LocalChange(key, None)``, unless the adapter named its key again: the adapter
    writes it as ``write_back(link, None)`` and its link is tombstoned.
    """

    candidates = iter(adapter.local_changes(stream, keys=external_keys))
    links = stream.links.model.objects
    named: set[str] = set()
    while batch := list(islice(candidates, _CANDIDATE_BATCH)):
        if any(len(candidate.local_version or "") > LOCAL_VERSION_LENGTH for candidate in batch):
            raise AdapterContractError(f"A local version is at most {LOCAL_VERSION_LENGTH} characters long.")
        named.update(candidate.external_key for candidate in batch)
        known = {
            link.external_key: link
            for link in links.filter(
                stream=stream, external_key__in=[candidate.external_key for candidate in batch]
            ).with_sync_evidence()
        }
        unchanged = []
        for candidate in batch:
            link = known.get(candidate.external_key)
            if link is None or link.local_changed(candidate.projection):
                yield candidate, link
            elif settle:
                unchanged.append((link, candidate))
        if unchanged:
            links.settle(stream, unchanged)
    if stream.direction != StreamDirection.PUSH:
        return
    written = links.filter(stream=stream).exclude(applied_base=None).exclude(status=LinkStatus.TOMBSTONE)
    if external_keys is not None:
        written = written.filter(external_key__in=external_keys)
    released = written.released().with_sync_evidence().order_by("external_key")
    for link in released.iterator(chunk_size=_CANDIDATE_BATCH):
        if link.external_key not in named and link.local_changed(None):
            yield LocalChange(link.external_key, None), link


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


def reconcile_stream(stream: Any, adapter: BridgeImpl, *, page_bound: int = 100, deadline: float | None = None) -> int:
    """Commit one bounded sweep pulse, returning the number of absence observations.

    Continue while stream.reconcile_state is nonempty. Enumeration seeks after
    its last committed key; each key page is read and applied before bounded
    root/child absence passes. Without identity reads, a separate extraction
    baseline precedes enumeration. The adapter cursor remains opaque. A retired
    stream runs only the retirement pass its retirement started: the same
    bounded root/child absence passes, tombstoning each link at once. The pass
    pauses, touching nothing, while its container observes again; reopening the
    stream (SyncStreamManager.bump_generation) drops it.
    """

    if connection.in_atomic_block:
        raise RuntimeError("Remote enumeration must run outside a database transaction.")
    if page_bound < 1:
        raise ValueError("page_bound must be positive.")
    with (
        changeset(fresh=True, activity="pull", origin=stream),
        system_context(reason="integrate.stream.reconcile"),
        sync_deadline_context(deadline),
    ):
        manager = type(stream).objects
        identity_reads = adapter.supports_identity_reads
        stream.refresh_from_db()

        def idle(row: Any) -> bool:
            if row.state == StreamState.RETIRED:
                return not row.reconcile_state
            return not row.accepts_rescan() or row.reconcile_interval is None

        if idle(stream):
            return 0
        with transaction.atomic():
            locked = manager.lock_current(stream)
            if idle(locked):
                return 0
            if (
                locked.state == StreamState.RETIRED
                and locked.links.model.objects.filter(pk=locked.container_id).observing().exists()
            ):
                # The container came back: the pass pauses, touching nothing; reopening drops it.
                return 0
            state = _validated_cursor(locked.reconcile_state) or None
            if state is None:
                if not locked.sweep_due():
                    stream.reconcile_state = locked.reconcile_state
                    return 0
                state = {
                    "started_at": timezone.now().isoformat(),
                    "phase": "enumerate" if identity_reads else "extract",
                    "after": None,
                }
                locked.save_reconcile(state)
            original_cursor = _validated_cursor(locked.cursor)
        if state["phase"] == "extract":
            baseline = copy(locked)
            baseline.cursor = state.get("cursor", {})
            baseline.phase = StreamPhase.BASELINE
            page = _extract_page(adapter, baseline, page_bound, deadline=deadline)
            if page.resync_required:
                raise CursorInvalid()
            if not page.exhausted and json_equal(page.cursor, baseline.cursor):
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
                    locked.save_reconcile(next_state)
        else:
            with transaction.atomic():
                locked = manager.lock_current(locked, fenced_to=locked)
                links = locked.links.model.objects
                started = datetime.fromisoformat(state["started_at"])
                rows = links.filter(stream=locked).absence_candidates(
                    started=started,
                    children=state["phase"] != "roots",
                )
                if state["after"] is not None:
                    rows = rows.filter(pk__gt=state["after"])
                batch = tuple(rows.order_by("pk").only("pk", "external_key").lock_if_supported()[:page_bound])
                transitions = links.mark_absent(
                    locked, [link.external_key for link in batch], confirmed=locked.state == StreamState.RETIRED
                )
                if transitions:
                    _withdraw_absent(locked, adapter, transitions)
                if any(link.status == LinkStatus.TOMBSTONE for link in transitions):
                    manager.retire_children(locked)
                if batch:
                    locked.save_reconcile({**state, "after": batch[-1].pk})
                elif state["phase"] == "roots":
                    locked.save_reconcile({**state, "phase": "children", "after": None})
                else:
                    locked.save_reconcile(None)
                stream.reconcile_state, stream.last_reconciled_at = locked.reconcile_state, locked.last_reconciled_at
                return len(batch)
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
            stream = reset_stream(stream, cursor={}).stream
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
    snapshot, authority, mapping version) through ``SyncStream.objects.current``, so a changed
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
    progress = current_progress_bridge()
    if progress is not None:
        progress.report_progress(str(bridge.SyncStage.SYNCING), message=message, details=details)


def _drain(bridge: Any, adapter: BridgeImpl, definition: StreamDefinition, deadline: float) -> tuple[int, int]:
    """Drain one partition; return the records it landed and the container links that appeared.

    A child whose container stopped being live since the declaration pauses. Past
    the deadline nothing is opened or read: the partition reports its budget.
    """

    container = definition.container
    if container is not None and not type(container).objects.filter(pk=container.pk).live_containers().exists():
        return 0, 0
    if monotonic() >= deadline:
        _report_budget(bridge, definition, landed=0)
        return 0, 0
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
        landed, appeared, resets = 0, 0, 0
        page_bound = max(1, int(bridge.config.get("sync_page_bound", 100)))
        exhausted = False
        previous = None
        while monotonic() < deadline:
            stage = "pull"
            result = advance_stream(stream, adapter, page_bound=page_bound, deadline=deadline)
            stream, exhausted = result.stream, result.exhausted
            landed += result.count
            appeared += result.appeared
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
            _report_budget(bridge, definition, landed=landed)
        return landed, appeared
    except Exception as error:  # noqa: BLE001 -- retain the operation that actually failed.
        raise BridgeSyncError((PartitionFailure(definition, stage, error),)) from error


def _report_budget(bridge: Any, definition: StreamDefinition, *, landed: int) -> None:
    _report(
        bridge,
        "Sync time budget reached; resuming next run",
        stream=definition.key,
        partition=definition.partition,
        landed=landed,
        budget_exhausted=True,
    )


def _retire(bridge: Any, adapter: BridgeImpl, definition: StreamDefinition, deadline: float) -> None:
    """Run the pending retirement passes of the streams a container-holding partition retired, within the deadline.

    A pass pauses (makes no progress) while its container observes again; reopening
    is the child's next declaration.
    """

    try:
        manager = bridge.sync_streams.model.objects
        parent = manager.current_for_bridge(bridge, definition.key).filter(partition=definition.partition).first()
        if parent is None:
            return
        page_bound = max(1, int(bridge.config.get("sync_page_bound", 100)))
        for child in manager.retiring(parent):
            while monotonic() < deadline:
                pending = child.reconcile_state
                reconcile_stream(child, adapter, page_bound=page_bound, deadline=deadline)
                if not child.reconcile_state or json_equal(child.reconcile_state, pending):
                    break
    except Exception as error:  # noqa: BLE001 -- retain the operation that actually failed.
        raise BridgeSyncError((PartitionFailure(definition, "retire", error),)) from error


def _drain_partition(bridge: Any, definition: StreamDefinition, deadline: float) -> tuple[int, int]:
    close_old_connections()
    try:
        with system_context(reason="integrate.stream.partition"):
            row = type(bridge)._base_manager.get(pk=bridge.pk)
            adapter = row.capability_impl
            try:
                with progress_bridge_context(row):
                    return _drain(row, adapter, definition, deadline)
            finally:
                adapter.close()
    finally:
        connections.close_all()


def _declare(adapter: BridgeImpl, deadline: float) -> tuple[StreamDefinition, ...]:
    """Read the adapter's declared partitions in their stable order, each once."""

    definitions = tuple(sorted(adapter.streams(deadline=deadline), key=lambda item: (item.key, item.partition)))
    identities = [(item.key, item.partition) for item in definitions]
    if len(identities) != len(set(identities)):
        raise AdapterContractError("A backend declared the same stream partition twice.")
    return definitions


def _holders(wave: Sequence[StreamDefinition]) -> dict[tuple[str, str], tuple[str, str] | None]:
    """Map each partition of the wave to the partition holding its container, when that one is in the wave too."""

    identities = {(item.key, item.partition) for item in wave}
    holders: dict[tuple[str, str], tuple[str, str] | None] = {}
    for item in wave:
        holder = item.container.stream if item.container is not None else None
        identity = (holder.key, holder.partition) if holder is not None else None
        holders[item.key, item.partition] = identity if identity in identities else None
    return holders


def _holder_depths(holders: Mapping[tuple[str, str], tuple[str, str] | None]) -> dict[tuple[str, str], int]:
    """Count each partition's holders within the wave; a cyclic or self-held declaration fails fast."""

    depths: dict[tuple[str, str], int] = {}
    for identity in holders:
        chain: list[tuple[str, str]] = []
        current: tuple[str, str] | None = identity
        while current is not None and current not in depths:
            if current in chain:
                raise AdapterContractError(f"Stream containers form a cycle: {chain[chain.index(current):]}.")
            chain.append(current)
            current = holders[current]
        depth = -1 if current is None else depths[current]
        for item in reversed(chain):
            depth += 1
            depths[item] = depth
    return depths


def _drain_wave(
    bridge: Any, adapter: BridgeImpl, wave: Sequence[StreamDefinition], deadline: float, parallelism: int
) -> tuple[int, int, list[PartitionFailure]]:
    """Drain independent partitions in order, or concurrently; total what landed and what appeared.

    A partition held by a container starts only after the stream holding that
    container finished its pages and sweep in this wave: sequentially by holder
    depth, concurrently once the holder's worker completes. The holders'
    retirement passes run last, as they change no live container.
    """

    landed = appeared = 0
    failures: list[PartitionFailure] = []
    holders = _holders(wave)
    depths = _holder_depths(holders)
    if parallelism <= 1 or len(wave) <= 1:
        for definition in sorted(wave, key=lambda item: (depths[item.key, item.partition], item.key, item.partition)):
            try:
                counts = _drain(bridge, adapter, definition, deadline)
            except BridgeSyncError as error:
                failures.extend(error.failures)
                continue
            landed, appeared = landed + counts[0], appeared + counts[1]
    else:
        held: dict[tuple[str, str], list[StreamDefinition]] = {}
        for definition in wave:
            holder = holders[definition.key, definition.partition]
            if holder is not None:
                held.setdefault(holder, []).append(definition)
        with ThreadPoolExecutor(max_workers=min(parallelism, len(wave)), thread_name_prefix="bridge-sync") as pool:
            # Each worker records its own changeset row under the cycle's boundary
            # and actor (angee.provenance.context), so the copied context is safe to share.
            futures: dict[Future[tuple[int, int]], StreamDefinition] = {}

            def submit(items: Sequence[StreamDefinition]) -> None:
                for item in items:
                    futures[pool.submit(copy_context().run, _drain_partition, bridge, item, deadline)] = item

            submit([item for item in wave if holders[item.key, item.partition] is None])
            while futures:
                done, _pending = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    definition = futures.pop(future)
                    submit(held.pop((definition.key, definition.partition), ()))
                    try:
                        counts = future.result()
                    except BridgeSyncError as error:
                        failures.extend(error.failures)
                        continue
                    except Exception as error:  # noqa: BLE001 -- partition setup/cleanup can also fail.
                        failures.append(PartitionFailure(definition, "partition", error))
                        continue
                    landed, appeared = landed + counts[0], appeared + counts[1]
        if held:
            raise AdapterContractError(f"Held stream partitions were never released: {sorted(held)}.")
    for definition in wave:
        if definition.holds_containers and monotonic() < deadline:
            try:
                _retire(bridge, adapter, definition, deadline)
            except BridgeSyncError as error:
                failures.extend(error.failures)
    return landed, appeared, failures


def sync_bridge(bridge: Any) -> int:
    """Drive declared partitions within the existing bridge task's time budget.

    Every declared partition drains in the first wave, in its order and
    parallelism, children of containers applied before this cycle included; a
    child starts after the stream holding its container. A wave in which a
    container-holding stream (``StreamDefinition.holds_containers``) gained a
    live container (created, revived or newly bound) declares again, so its
    children drain in a further wave of this cycle; no wave starts after the
    deadline.
    """

    config = bridge.config
    worker_budget = task_time_budget().total_seconds()
    deadline = monotonic() + max(0.0, min(worker_budget, float(config.get("sync_time_budget", worker_budget))))
    adapter = bridge.capability_impl
    try:
        # The cycle's deadline is set once: _drain's identity reads and sweep read it, and each
        # pool worker inherits it through its copied context.
        with system_context(reason="integrate.bridge.streams"), sync_deadline_context(deadline):
            parallelism = max(1, int(config.get("sync_parallelism", 4)))
            if adapter.sync_parallelism is not None:
                parallelism = min(parallelism, adapter.sync_parallelism)
            if connection.vendor != "postgresql":
                parallelism = 1
            failures: list[PartitionFailure] = []
            landed = 0
            drained: set[tuple[str, str]] = set()
            wave = _declare(adapter, deadline)
            while wave:
                wave_landed, appeared, wave_failures = _drain_wave(bridge, adapter, wave, deadline, parallelism)
                landed += wave_landed
                failures.extend(wave_failures)
                drained.update((item.key, item.partition) for item in wave)
                if not appeared or monotonic() >= deadline:
                    break
                try:
                    declared = _declare(adapter, deadline)
                except Exception:
                    # The declaration's own failure must not hide partitions that already failed.
                    for failure in failures:
                        failure.log(bridge)
                    raise
                wave = tuple(item for item in declared if (item.key, item.partition) not in drained)
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
