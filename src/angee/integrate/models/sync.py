"""Native sync owners for integration contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any, ClassVar, Self, cast

from deepdiff import DeepDiff
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connections, models, transaction
from django.db.models import Q
from django.utils import timezone
from rebac import (
    SubjectRef,
    database_context,
    system_context,
    to_subject_ref,
)
from rebac.database import write_alias

from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.base.models.base import record_display_label
from angee.base.models.merge_references import MergePolicy, references
from angee.base.models.persistence import require_composed_type, require_projection
from angee.base.serialization import canonical_json
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.refs import ProtectedRecordRefMixin
from angee.integrate.managers import (
    RecordLinkManager,
    SyncStreamManager,
)
from angee.integrate.mixins import SyncStateMixin
from angee.integrate.models.bridges import Bridge
from angee.integrate.querysets import RecordLinkQuerySet
from angee.integrate.states import (
    ConflictKeep,
    LinkStatus,
    OutboxState,
    StreamDirection,
    StreamKind,
    StreamPhase,
    StreamState,
    SyncFailure,
    SyncFailureKind,
    SyncFailureStatus,
)
from angee.integrate.streams import begin_stream_cycle, push_stream, read_stream_keys
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryUnscopedManager
from angee.provenance.statements import (
    Statement,
    StatementLinkMixin,
)


class SyncStream(SyncStateMixin, AuditMixin, ProtectedDataModel, concrete=True):
    """An epoch's opaque progress and adapter-owned policy for one partition."""

    record_display_paths: ClassVar[tuple[str, ...]] = ("key", "partition")

    sqid_prefix = "sst_"
    integration = models.ForeignKey("integrate.Integration", on_delete=models.PROTECT, related_name="sync_streams")
    key = models.CharField(max_length=160)
    partition = models.CharField(max_length=255, blank=True)
    kind = StateField(choices_enum=StreamKind)
    direction = StateField(choices_enum=StreamDirection)
    snapshot = models.BooleanField(default=False)
    authoritative = models.BooleanField(default=False)
    """Whether this stream's source is the system of record for the aspects it asserts.

    Set on a SYSTEM_OF_RECORD source, its statements outrank people's ordinary
    (normal-rank) edits in every aspect whose precedence lists that kind; unset,
    the stream is a system of reference below people. A person's confirm or
    dismiss stays on top either way. Flip through
    :meth:`SyncStreamManager.set_authoritative`: statements are retained, so the
    flip is a re-derivation, never a migration.
    """
    generation = models.PositiveIntegerField(default=1)
    phase = StateField(choices_enum=StreamPhase, default=StreamPhase.BASELINE)
    state = StateField(choices_enum=StreamState, default=StreamState.OPEN)
    cursor = models.JSONField(default=dict, blank=True)
    reconcile_state = models.JSONField(default=dict, blank=True)
    config = models.JSONField(default=dict, blank=True)
    cursor_expires_at = models.DateTimeField(null=True, blank=True)
    resync_required = models.BooleanField(default=False)
    last_advanced_at = models.DateTimeField(null=True, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    reconcile_interval = models.DurationField(null=True, blank=True)
    absence_threshold = models.PositiveIntegerField(default=2)
    tombstone_retention = models.DurationField(null=True, blank=True)
    objects = SyncStreamManager()
    unscoped_objects = AngeeHistoryUnscopedManager()

    def __str__(self) -> str:
        """Label this stream by its adapter key and optional partition."""

        return f"{self.key} ({self.partition})" if self.partition else self.key

    @property
    def operation_fence(self) -> tuple[Any, ...]:
        """Return all mutable stream facts that external work must fence."""

        return self.state, self.phase, self.cursor, self.reconcile_state, self.resync_required

    def accepts_pull(self) -> bool:
        """Whether this generation accepts another inbound page."""

        return (
            self.state == StreamState.OPEN
            and not self.resync_required
            and (self.cursor_expires_at is None or self.cursor_expires_at > timezone.now())
        )

    def accepts_rescan(self) -> bool:
        """Whether this generation accepts repair and reconciliation work."""

        return (
            self.state == StreamState.OPEN
            and self.direction != StreamDirection.PUSH
            and self.kind == StreamKind.RECORD_REPLICA
        )

    @property
    def writes_back(self) -> bool:
        """Whether this stream writes local changes to its remote: a replica that is not pull-only."""

        return (
            self.state == StreamState.OPEN
            and self.kind == StreamKind.RECORD_REPLICA
            and self.direction != StreamDirection.PULL
        )

    def adopts_baseline(self) -> bool:
        """Two-way replicas adopt unbased identities until this baseline completes."""

        return (
            self.kind == StreamKind.RECORD_REPLICA
            and self.direction == StreamDirection.BIDIRECTIONAL
            and not self.has_completed_baseline()
        )

    def save_reconcile(self, state: dict[str, Any] | None) -> None:
        """Persist a locked sweep checkpoint or its completion timestamp."""

        if state is None:
            self.last_reconciled_at = timezone.now()
        self.reconcile_state = state or {}
        self.save(using=self._state.db, update_fields=["reconcile_state", "last_reconciled_at", "updated_at"])

    def resolve_applied(self, record: Any) -> None:
        """Resolve nonconflict failures for an applied replica, never an event peer."""

        if self.kind == StreamKind.RECORD_REPLICA:
            self.links.get(external_key=record.external_key).resolve_applied()

    def has_completed_baseline(self) -> bool:
        """Read this generation's persisted completion; earlier baselines do not count."""

        return type(self).unscoped_objects.filter(pk=self.pk, phase=StreamPhase.DELTA).exists()

    @property
    def failure_stream(self) -> SyncStream:
        return self

    def sync_failures(
        self,
        *,
        open_only: bool = False,
        kind: SyncFailureKind | None = None,
    ) -> tuple[SyncFailure, ...]:
        """Read stream and linked failures through their owning row permissions."""
        actor, unscoped = self.effective_actor(strict=True)
        links = self.links.all()
        if unscoped:
            links = links.sudo(reason="integrate.stream.sync_failures")
        else:
            links = links.with_actor(actor)
        if open_only:
            prefetched = getattr(self, "open_failure_links", None)
            if prefetched is not None:
                links = prefetched
            else:
                links = require_composed_type(links, RecordLinkQuerySet).with_open_failures()
        else:
            links = links.only("stream_id", "inbound", "outbox_last_error").order_by("pk")
        rows = list(self.failures(open_only=open_only))
        for link in links:
            link.stream = self
            link.__dict__["_source_stream"] = self
            rows.extend(link.failures(open_only=open_only))
        return tuple(row for row in rows if kind is None or row.kind == kind)

    class Meta:
        abstract = True
        base_manager_name = "unscoped_objects"
        rebac_resource_type = "integrate/sync_stream"
        rebac_id_attr = "pk"
        constraints = (
            models.UniqueConstraint(
                fields=("integration", "key", "partition", "generation"), name="uniq_sync_stream_generation"
            ),
            models.CheckConstraint(condition=Q(absence_threshold__gte=1), name="sync_stream_absence_positive"),
        )


class RecordLink(
    ProtectedRecordRefMixin, SyncStateMixin, AuditMixin, HistoryMixin, StatementLinkMixin, ProtectedDataModel,
    concrete=True
):
    """A remote identity retaining observations, application facts and run state.

    ``applied_base`` pins this link's own historical application snapshot; an
    observation never advances it. Its protected FK keeps the active comparison
    base out of native history pruning. ``pinned`` withholds automatic updates.

    ``inbound`` retains a list of failures, including closed evidence. Each entry
    carries kind, code, source_hash, mapping_version, details, status, attempts,
    retry_at, resolved_at, created_at and updated_at. Times are ISO 8601 strings
    or null. Several open failure identities can coexist; resolution updates an
    entry instead of removing it. History captures each change to the list.
    ``outbox_last_error`` retains the same evidence shape for the latest refused
    or failed write; a null outbox state means no write is pending.
    """

    record_display_paths: ClassVar[tuple[str, ...]] = ("external_key",)

    # A link's history is sync evidence read through its target: no timeline of its own.
    timeline_subject = False

    merge_policy = MergePolicy.MOVE
    accepts_any_record_target = True
    sqid_prefix = "rlk_"
    authority_related = ("stream",)
    stream = models.ForeignKey("integrate.SyncStream", on_delete=models.PROTECT, related_name="links")

    presentation_related: ClassVar[tuple[str, ...]] = ("stream__integration",)

    @property
    def aggregate_root(self) -> RecordLink:
        """The applied aggregate root whose compound children share its evidence."""
        parent = self.parent
        return parent if parent is not None else self

    @property
    def applied_snapshot(self) -> Any | None:
        """The pinned application snapshot for this identity's aggregate."""
        return self.aggregate_root.applied_base

    def application_count(self) -> int:
        """Count distinct retained applications of this identity's aggregate root."""
        root = self.aggregate_root
        return root.history.using(root._state.db).exclude(applied_at=None).values("applied_at").distinct().count()

    def resolve_target(self) -> models.Model | None:
        """Resolve fresh internal evidence through Django's native base-manager pointer.

        Unlike an ordinary cached relation read, source reconciliation must see
        current target facts. Missing model classes and missing rows retain the
        same empty evidence meaning. This does not confer actor-facing admission.
        """
        if not self.record_ref.model_label:
            return None
        reference = self.record_ref_field()
        if reference.is_cached(self):
            reference.delete_cached_value(self)
        return self.target

    @property
    def statement_source_label(self) -> str:
        """Name the connection and stream retaining this remote statement evidence."""

        stream = self.stream
        stream_name = f"{stream.key} ({stream.partition})" if stream.partition else stream.key
        return f"{record_display_label(stream.integration)} · {stream_name} · {self.external_key}"

    @property
    def authoritative(self) -> bool:
        """The stream decides authority for every statement this link backs."""

        return bool(self.stream.authoritative)

    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    external_key = models.CharField(max_length=512)
    asserted_by = models.TextField(blank=True, editable=False)
    target_content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    target_object_id = models.CharField(max_length=255, null=True, blank=True)
    target = GenericForeignKey("target_content_type", "target_object_id")
    status = StateField(choices_enum=LinkStatus, default=LinkStatus.OBSERVED)
    source_payload = models.JSONField(default=dict, blank=True)
    source_hash = models.CharField(max_length=64, blank=True)
    mapped_payload = models.JSONField(default=dict, blank=True, null=True)
    mapping_version = models.PositiveIntegerField(default=1)
    dependency_digest = models.CharField(max_length=64, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_base = models.ForeignKey(
        "integrate.HistoricalRecordLink", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    pinned = models.BooleanField(default=False)
    outbox_state = StateField(choices_enum=OutboxState, null=True, blank=True)
    outbox_retry_at = models.DateTimeField(null=True, blank=True)
    outbox_last_error = models.JSONField(default=dict, blank=True)
    remote_version = models.CharField(max_length=512, blank=True)
    origin = models.CharField(max_length=16, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_verified_generation = models.PositiveIntegerField(default=0)
    absence_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    tombstoned_at = models.DateTimeField(null=True, blank=True)
    objects = RecordLinkManager()
    unscoped_objects = AngeeHistoryUnscopedManager()

    def __str__(self) -> str:
        """Label this remote identity by its external key."""

        return self.external_key

    @property
    def failure_stream(self) -> SyncStream:
        return self.stream

    @property
    def source_url(self) -> str | None:
        """Provider URL projected through the connection's implementation."""

        if "_source_stream" not in self.__dict__:
            require_projection(self, "_source_stream")
            return self.get_source_url()
        return self._source_capability(self.__dict__["_source_stream"]).source_url(self)

    def get_source_url(self) -> str | None:
        """Resolve one link's provider URL explicitly outside a prepared list."""

        return self._source_capability(self.stream).source_url(self)

    @property
    def source_label(self) -> str:
        """Provider-owned human label for this retained identity."""

        if "_source_stream" not in self.__dict__:
            require_projection(self, "_source_stream")
            return self.get_source_label()
        return self._source_capability(self.__dict__["_source_stream"]).source_label(self)

    def get_source_label(self) -> str:
        """Resolve one link's provider label explicitly outside a prepared list."""

        return self._source_capability(self.stream).source_label(self)

    @staticmethod
    def _source_capability(stream: SyncStream) -> Bridge:
        return cast(Bridge, stream.integration.concrete_capability())

    def _lock_sync_state(self) -> Self:
        """Follow a link's current epoch before fencing a retained failure mutation."""
        self.refresh_from_db(fields=("stream",))
        return super()._lock_sync_state()

    def failures(self, *, open_only: bool = False) -> tuple[SyncFailure, ...]:
        rows = list(super().failures(open_only=open_only))
        if self.outbox_last_error:
            row = SyncFailure.from_state(self, None, self.outbox_last_error)
            if not open_only or row.is_open:
                rows.append(row)
        return tuple(rows)

    def asserting_source(self) -> SubjectRef:
        """The principal this link's observations assert as: its retained stamp, else its integration."""

        return SubjectRef.parse(self.asserted_by) if self.asserted_by else to_subject_ref(self.stream.integration)

    def withdraw_statements(self) -> int:
        """Withdraw the source assertions no other link of the source still evidences.

        One source can observe a record through several links (an instance behind
        several company streams, a contact in two address books) while each of its
        assertions names one of them. While another of the source's links still
        observes the record, the assertions move to it through the shared reference
        move, which keeps them asserted; the last link's tombstone withdraws them.
        Two siblings tombstoned at once could otherwise move the assertions onto each
        other: this relies on the bridge advisory lock, held by every sync job, webhook
        landing and live session of the source's integration, to serialize them.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        source = self.asserting_source()
        statements = [
            (reference, statement_model)
            for reference in references(type(self))
            if issubclass(statement_model := reference.model, Statement) and reference.columns == ("link",)
        ]
        observing = (
            type(self)
            ._base_manager.using(using)
            .filter(
                target_content_type_id=self.target_content_type_id,
                target_object_id=self.target_object_id,
                tombstoned_at__isnull=True,
            )
            .exclude(pk=self.pk)
            .exclude(status__in=(LinkStatus.TOMBSTONE, LinkStatus.WITHDRAWN))
            .select_related("stream__integration")
            .order_by("pk")
        )
        targeted = self.target_content_type_id is not None and bool(self.target_object_id)
        sibling = next((link for link in observing if link.asserting_source() == source), None) if targeted else None
        if sibling is None:
            return sum(
                statement_model.objects.db_manager(using).withdraw(source, link=self)
                for _reference, statement_model in statements
            )
        with system_context(reason="integrate.link_evidence_move"):
            for reference, _statement_model in statements:
                rows = reference.rows(self).using(using).filter(asserted_by=str(source))
                if rows.exists():
                    reference.move(rows, survivor=sibling)
        return 0

    def update_sync_status(self) -> None:
        """Open failures dominate observation/application status until resolved."""
        if self.failures(open_only=True):
            self.status = LinkStatus.FAILED
        elif self.status == LinkStatus.FAILED:
            if self.tombstoned_at is not None:
                self.status = LinkStatus.TOMBSTONE
            else:
                self.status = LinkStatus.CURRENT if self.applied_base_id else LinkStatus.OBSERVED

    def on_sync_state_saved(self, locked: Self) -> None:
        locked.update_sync_status()
        fields = ["inbound", "outbox_state", "outbox_retry_at", "outbox_last_error", "status"]
        locked.save(using=locked._state.db, update_fields=[*fields, "updated_at"])
        for name in fields:
            setattr(self, name, getattr(locked, name))

    def record_failure(
        self,
        *,
        kind: SyncFailureKind,
        code: str,
        source_hash: str = "",
        mapping_version: int = 1,
        details: Mapping[str, Any] | None = None,
        outbox: bool = False,
    ) -> SyncFailure:
        """Retain current failure evidence; older versions of the same problem close.

        A link owns one remote identity, so a newer source or mapping supersedes
        the same inbound problem. Conflicts still require an explicit decision;
        stream-level failures may describe distinct events and keep their slots.
        """
        using = write_alias(type(self), using=self._state.db, instance=self)

        if not outbox:
            with database_context(using), transaction.atomic(using=using):
                locked = self._lock_sync_state()
                for prior in locked.failures(open_only=True):
                    if (
                        prior.slot is not None
                        and kind != SyncFailureKind.CONFLICT
                        and (prior.kind, prior.code) == (kind, code)
                        and (prior.source_hash, prior.mapping_version) != (source_hash, mapping_version)
                    ):
                        locked.resolve_failure(prior.slot)
                return super().record_failure(
                    kind=kind, code=code, source_hash=source_hash, mapping_version=mapping_version, details=details
                )
        with database_context(using), transaction.atomic(using=using):
            locked = self._lock_sync_state()
            data = deepcopy(locked.outbox_last_error)
            identity = dict(kind=kind, code=code, source_hash=source_hash, mapping_version=mapping_version)
            if data.get("status") == SyncFailureStatus.RESOLVED or any(
                data.get(name) != value for name, value in identity.items()
            ):
                if data and data["status"] != SyncFailureStatus.RESOLVED:
                    locked.resolve_failure(None, conflict=True)
                data = {}
            data = locked._failure_data(
                data, kind=kind, code=code, source_hash=source_hash, mapping_version=mapping_version, details=details
            )
            locked.outbox_last_error, locked.outbox_state = data, OutboxState.REFUSED
            locked.outbox_retry_at = datetime.fromisoformat(data["retry_at"]) if data["retry_at"] else None
            self.on_sync_state_saved(locked)
            return SyncFailure.from_state(self, None, data)

    def _transition_failure(self, slot: int | None, *, retry: bool, conflict: bool = False) -> SyncFailure:
        if slot is not None:
            return super()._transition_failure(slot, retry=retry, conflict=conflict)
        using = write_alias(type(self), using=self._state.db, instance=self)

        with database_context(using), transaction.atomic(using=using):
            locked = self._lock_sync_state()
            data = locked.outbox_last_error
            locked._transition_state(data, retry=retry, conflict=conflict)
            locked.outbox_state = cast(Any, OutboxState.PENDING if retry else None)
            locked.outbox_retry_at = datetime.fromisoformat(data["retry_at"]) if data["retry_at"] else None
            self.on_sync_state_saved(locked)
            return SyncFailure.from_state(self, None, data)

    def resolve_applied(self) -> None:
        """Successful application resolves refusals while retaining explicit conflicts."""

        for failure in self.failures(open_only=True):
            if failure.kind != SyncFailureKind.CONFLICT:
                failure.resolve()

    def resolve_conflict(self, slot: int | None, keep: ConflictKeep) -> SyncFailure:
        """Fresh-read and retain the chosen side of one linked conflict."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        link = self
        if keep not in ConflictKeep.values:
            raise ValidationError("Choose remote or local changes.")
        if connections[using].in_atomic_block:
            raise RuntimeError("Conflict resolution must run outside a database transaction.")
        with system_context(reason="integrate.record.resolve_conflict"):
            link = type(link).objects.db_manager(using).with_sync_evidence().get(pk=link.pk)
            row = link.failure_at(slot)
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
                with database_context(using), transaction.atomic(using=using):
                    type(stream).objects.db_manager(using).lock_current(stream, fenced_to=stream)
                    locked = type(owner).objects.db_manager(using).lock_if_supported().get(pk=owner.pk)
                    current_link = type(link).objects.db_manager(using).lock_if_supported().get(pk=link.pk)
                    current = current_link.failure_at(slot)
                    if not current.is_open:
                        return current
                    if not locked.sync_precondition_matches(precondition):
                        raise RuntimeError("Record bases changed during conflict resolution; retry the choice.")
                    if remote is not None:
                        type(locked).objects.db_manager(using).observe(
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
                if type(link).objects.db_manager(using).filter(pk__in=(link.pk, owner.pk)).open_failures():
                    raise ValidationError("The record could not be synchronized; its failure remains open.")
                fresh = type(link).objects.db_manager(using).get(pk=link.pk)
                return fresh.failure_at(slot)
            except Exception:
                if resolved:
                    fresh = type(link).objects.db_manager(using).get(pk=link.pk)
                    if not type(link).objects.db_manager(using).filter(pk__in=(link.pk, owner.pk)).open_failures():
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

    def local_changed(self, projection: Any) -> bool:
        """Compare a JSON-native projection with this link's protected applied base."""
        if self.applied_base is None:
            return projection is not None
        return bool(
            DeepDiff(
                json.loads(canonical_json(self.applied_base.mapped_payload)),
                json.loads(canonical_json(projection)),
            )
        )

    def pin(self) -> None:
        """Withhold automatic inbound application and write-back."""
        self._set_pinned(True)

    def unpin(self) -> None:
        """Resume automatic synchronization on the next cycle."""
        self._set_pinned(False)

    def _set_pinned(self, pinned: bool) -> None:
        with transaction.atomic():
            locked = self._lock_sync_state()
            locked.pinned = pinned
            locked.save(using=locked._state.db, update_fields=["pinned", "updated_at"])
            self.pinned = pinned

    @property
    def write_holds(self) -> tuple[SyncFailure, ...]:
        """Conflicts hold indefinitely; an outbox refusal holds until due."""
        now = timezone.now()
        return tuple(
            row
            for row in self.failures(open_only=True)
            if row.kind == SyncFailureKind.CONFLICT
            or (row.slot is None and row.retry_at is not None and row.retry_at > now)
        )

    @property
    def sync_precondition(self) -> dict[str, Any]:
        """The retained state fenced across an external conditional write."""
        return {
            "applied_base": self.applied_base_id,
            "source_hash": self.source_hash,
            "remote_version": self.remote_version,
        }

    def sync_precondition_matches(self, expected: Mapping[str, Any]) -> bool:
        """Compare the complete owner-defined conditional-write fence."""

        return self.sync_precondition == expected

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Retain the integration's native SubjectRef when the identity is saved."""

        if not self.asserted_by:
            self.asserted_by = str(self.asserting_source())
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "asserted_by"}
        super().save(*args, **kwargs)

    class Meta:
        abstract = True
        base_manager_name = "unscoped_objects"
        rebac_resource_type = "integrate/record_link"
        rebac_id_attr = "pk"
        constraints = (models.UniqueConstraint(fields=("stream", "external_key"), name="uniq_stream_record_key"),)
        indexes = (models.Index(fields=("target_content_type", "target_object_id")),)
