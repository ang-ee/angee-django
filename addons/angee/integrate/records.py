"""Stream epochs, replica identities, immutable revisions and quarantine.

Source declarations are bound in ``models`` for composer discovery. Adapters
retain remote vocabulary and domain ingest policy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connections, models, transaction
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone
from rebac import system_context

from angee.base.db import get_write_alias, refresh_deferred, related_on
from angee.base.fields import StateField
from angee.base.mixins import AppendOnlyQuerySet, AuditMixin, SqidMixin
from angee.base.models import AngeeManager, AngeeModel, AngeeQuerySet


class StreamKind(models.TextChoices):
    """Whether a stream carries append-only events or mutable replicas."""

    EVENT_FEED = "event_feed", "Event feed"
    RECORD_REPLICA = "record_replica", "Record replica"


class StreamDirection(models.TextChoices):
    """The sides a stream may write."""

    PULL = "pull", "Pull"
    PUSH = "push", "Push"
    BIDIRECTIONAL = "bidirectional", "Bidirectional"


class StreamPhase(models.TextChoices):
    """A new epoch verifies a baseline before accepting deltas."""

    BASELINE = "baseline", "Baseline"
    DELTA = "delta", "Delta"


class LinkStatus(models.TextChoices):
    """Observed identity and reconciliation state."""

    CURRENT = "current", "Current"
    OBSERVED = "observed", "Observed"
    UNAVAILABLE = "unavailable", "Unavailable"
    DISCREPANT = "discrepant", "Discrepant"
    WITHDRAWN = "withdrawn", "Withdrawn"
    TOMBSTONE = "tombstone", "Tombstone"


class DiscrepancyKind(models.TextChoices):
    """Recoverable record failures, independent of transport failures."""

    SEMANTIC = "semantic", "Semantic"
    CONFLICT = "conflict", "Conflict"
    MISSING_DEPENDENCY = "missing_dependency", "Missing dependency"
    REMOTE_REJECTED = "remote_rejected", "Remote rejected"


class DiscrepancyStatus(models.TextChoices):
    """Quarantine remains open until a successful rescan resolves it."""

    OPEN = "open", "Open"
    RETRY = "retry", "Retry"
    RESOLVED = "resolved", "Resolved"


class SyncStreamManager(AngeeManager):
    """Serialize stream discovery and epoch changes on the integration row."""

    def lock_current(self, stream: Any, *, using: str | None = None) -> Any:
        """Fence one page against epoch changes inside the caller's transaction.

        The stream row lock is shared with epoch retirement. A stale extracted
        page must fail before any domain records or cursor state are applied.
        """

        using = get_write_alias(self.model, using=using, bound=self, instance=stream)
        if not connections[using].in_atomic_block:
            raise RuntimeError("Locking a stream requires the page transaction.")
        with system_context(reason="integrate.stream.lock_current"):
            manager = self.db_manager(using)
            locked = manager.filter(pk=stream.pk).lock_if_supported().get()
            if manager.filter(
                bridge_ct_id=locked.bridge_ct_id,
                bridge_id=locked.bridge_id,
                key=locked.key,
                partition=locked.partition,
                generation__gt=locked.generation,
            ).exists():
                raise RuntimeError("The extracted page belongs to a retired stream generation.")
            return locked

    def current_for_bridge(self, bridge: models.Model, key: str, *, using: str | None = None) -> Any:
        """Return one current epoch per partition for the bridge's named stream."""

        using = get_write_alias(self.model, using=using, bound=self, instance=bridge)
        bridge_ct = ContentType.objects.db_manager(using).get_for_model(bridge, for_concrete_model=False)
        rows = (
            self.db_manager(using)
            .sudo(reason="integrate.stream.current_for_bridge")
            .filter(
                bridge_ct=bridge_ct,
                bridge_id=bridge.pk,
                key=key,
            )
        )
        latest = rows.filter(partition=OuterRef("partition")).order_by("-generation").values("generation")[:1]
        return rows.filter(generation=Subquery(latest)).order_by("partition")

    def current(
        self,
        bridge: models.Model,
        key: str,
        partition: str = "",
        *,
        kind: str = StreamKind.EVENT_FEED,
        direction: str = StreamDirection.PULL,
        cursor: Any = None,
        reconcile_interval: timedelta | None = None,
        absence_threshold: int = 2,
        tombstone_retention: timedelta | None = None,
        using: str | None = None,
    ) -> Any:
        """Return the latest epoch, creating its baseline once per partition.

        Cursor and policy arguments seed only the first epoch; subsequent calls
        preserve persisted progress and policy. Kind and direction cannot change
        for an existing identity. Change persisted policy explicitly on the row.
        """

        using = get_write_alias(self.model, using=using, bound=self, instance=bridge)
        bridge._state.db = using
        if absence_threshold < 1:
            raise ValidationError("A stream absence threshold must be positive.")
        integration = apps.get_model("integrate", "Integration")
        if bridge._meta.proxy or integration not in bridge._meta.get_parent_list():
            raise ValidationError("A stream bridge must be a concrete Integration child.")
        with system_context(reason="integrate.stream.current"), transaction.atomic(using=using):
            integration.objects.db_manager(using).filter(pk=bridge.pk).lock_if_supported().get()
            bridge_ct = ContentType.objects.db_manager(using).get_for_model(bridge, for_concrete_model=False)
            identity = {"bridge_ct": bridge_ct, "bridge_id": bridge.pk, "key": key, "partition": partition}
            manager = self.db_manager(using)
            stream = manager.filter(**identity).order_by("-generation").first()
            if stream is None:
                return manager.create(
                    **identity,
                    kind=kind,
                    direction=direction,
                    cursor={} if cursor is None else cursor,
                    reconcile_interval=reconcile_interval,
                    absence_threshold=absence_threshold,
                    tombstone_retention=tombstone_retention,
                )
            if (stream.kind, stream.direction) != (kind, direction):
                raise ValidationError("A stream's kind and direction cannot change between declarations.")
            return stream

    def bump_generation(self, stream: Any, *, using: str | None = None) -> Any:
        """Create one new baseline while retaining identities and history.

        Concurrent requests against a retired epoch return its successor. Links
        follow the current epoch without changing their verification marker.
        """

        using = get_write_alias(self.model, using=using, bound=self, instance=stream)
        with system_context(reason="integrate.stream.bump_generation"), transaction.atomic(using=using):
            refresh_deferred(stream, using=using)
            integration = apps.get_model("integrate", "Integration")
            integration.objects.db_manager(using).filter(pk=stream.bridge_id).lock_if_supported().get()
            manager = self.db_manager(using)
            latest = (
                manager.filter(
                    bridge_ct_id=stream.bridge_ct_id,
                    bridge_id=stream.bridge_id,
                    key=stream.key,
                    partition=stream.partition,
                )
                .order_by("-generation")
                .lock_if_supported()
                .first()
            )
            if latest.pk != stream.pk:
                return latest
            successor = manager.create(
                bridge_ct_id=latest.bridge_ct_id,
                bridge_id=latest.bridge_id,
                key=latest.key,
                partition=latest.partition,
                kind=latest.kind,
                direction=latest.direction,
                generation=latest.generation + 1,
                reconcile_interval=latest.reconcile_interval,
                absence_threshold=latest.absence_threshold,
                tombstone_retention=latest.tombstone_retention,
            )
            for model_name in ("RecordLink", "SyncDiscrepancy"):
                apps.get_model("integrate", model_name).objects.db_manager(using).filter(stream=latest).update(
                    stream=successor,
                )
            return successor

    def advance(
        self,
        stream: Any,
        cursor: Any,
        *,
        exhausted: bool = False,
        cursor_expires_at: datetime | None = None,
        using: str | None = None,
    ) -> Any:
        """Persist an opaque page cursor inside the caller's apply transaction."""

        using = get_write_alias(self.model, using=using, bound=self, instance=stream)
        with system_context(reason="integrate.stream.advance"), transaction.atomic(using=using):
            locked = self.lock_current(stream, using=using)
            locked.cursor = cursor
            locked.cursor_expires_at = cursor_expires_at
            locked.last_advanced_at = timezone.now()
            if exhausted:
                locked.phase = StreamPhase.DELTA
            fields = ["cursor", "cursor_expires_at", "last_advanced_at", "phase"]
            locked.save(using=using, update_fields=[*fields, "updated_at"])
            for field in fields:
                setattr(stream, field, getattr(locked, field))
            return stream


class SyncStream(SqidMixin, AuditMixin, AngeeModel):
    """One opaque cursor for a bridge stream partition in a particular epoch."""

    runtime = True
    sqid_prefix = "sst_"
    bridge_ct = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    bridge_id = models.PositiveBigIntegerField()
    bridge = GenericForeignKey("bridge_ct", "bridge_id", for_concrete_model=False)
    # Every concrete Bridge is an Integration MTI child. Its GFK id already
    # identifies that permission owner; this ORM relation adds no second column.
    integration = models.ForeignObject(
        "integrate.Integration",
        on_delete=models.PROTECT,
        from_fields=("bridge_id",),
        to_fields=("id",),
        related_name="sync_streams",
    )
    key = models.CharField(max_length=160)
    partition = models.CharField(max_length=255, blank=True)
    kind = StateField(choices_enum=StreamKind)
    direction = StateField(choices_enum=StreamDirection)
    generation = models.PositiveIntegerField(default=1)
    phase = StateField(choices_enum=StreamPhase, default=StreamPhase.BASELINE)
    cursor = models.JSONField(default=dict, blank=True)
    cursor_expires_at = models.DateTimeField(null=True, blank=True)
    resync_required = models.BooleanField(default=False)
    last_advanced_at = models.DateTimeField(null=True, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    reconcile_interval = models.DurationField(null=True, blank=True)
    absence_threshold = models.PositiveIntegerField(default=2)
    tombstone_retention = models.DurationField(null=True, blank=True)
    objects = SyncStreamManager()

    class Meta:
        abstract = True
        rebac_resource_type = "integrate/sync_stream"
        rebac_id_attr = "pk"
        constraints = (
            models.UniqueConstraint(
                fields=("bridge_ct", "bridge_id", "key", "partition", "generation"), name="uniq_sync_stream_generation"
            ),
            models.CheckConstraint(condition=Q(absence_threshold__gte=1), name="sync_stream_absence_positive"),
        )


class RecordLinkManager(AngeeManager):
    """Own replica identity, applied bases and count-based absence policy."""

    def observe(
        self,
        stream: Any,
        external_key: str,
        *,
        remote_version: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        using: str | None = None,
    ) -> Any:
        """Retain a remote identity without advancing either applied base."""

        using = get_write_alias(self.model, using=using, bound=self, instance=stream)
        with system_context(reason="integrate.record.observe"), transaction.atomic(using=using):
            stream = type(stream).objects.db_manager(using).lock_current(stream, using=using)
            if stream.kind != StreamKind.RECORD_REPLICA:
                raise ValidationError("Event feeds do not create record links.")
            link, _ = self.db_manager(using).lock_if_supported().get_or_create(stream=stream, external_key=external_key)
            if (
                remote_version is not None
                and remote_version != link.remote_version
                and link.status != LinkStatus.DISCREPANT
            ):
                link.status = LinkStatus.OBSERVED
            # The stored version belongs to the last applied change. Observation
            # cannot turn a failed write-back into applied evidence.
            link.last_seen_at = timezone.now()
            link.last_verified_generation = stream.generation
            link.absence_count = 0
            if metadata is not None:
                link.metadata = dict(metadata)
            link.save(
                using=using,
                update_fields=[
                    "status",
                    "last_seen_at",
                    "last_verified_generation",
                    "absence_count",
                    "metadata",
                    "updated_at",
                ],
            )
            return link

    def promote(
        self,
        link: Any,
        *,
        source_payload: Any,
        source_hash: str,
        mapped_payload: Any,
        local_hash: str,
        mapping_version: int = 1,
        dependency_digest: str = "",
        target: models.Model | None = None,
        remote_version: str = "",
        origin: str = "remote",
        using: str | None = None,
    ) -> Any:
        """Append exact applied evidence and advance both comparison bases."""

        using = get_write_alias(self.model, using=using, bound=self, instance=link)
        with system_context(reason="integrate.record.promote"), transaction.atomic(using=using):
            locked = self.db_manager(using).filter(pk=link.pk).lock_if_supported().get()
            revisions = apps.get_model("integrate", "RecordRevision").objects.db_manager(using)
            previous = revisions.filter(link=locked).order_by("-number").first()
            if mapped_payload is None:
                mapped_payload = previous.mapped_payload if previous is not None else {}
            facts = dict(
                source_payload={} if source_payload is None else source_payload,
                source_hash=source_hash,
                mapping_version=mapping_version,
                mapped_payload=mapped_payload,
                dependency_digest=dependency_digest,
            )
            if (
                previous is not None
                and previous.applied_at is not None
                and all(getattr(previous, field) == value for field, value in facts.items())
            ):
                revision = previous
            else:
                revision = revisions.append(locked, **facts, applied_at=timezone.now(), using=using)
            if target is not None:
                target._state.db = using
                locked.target_ct = ContentType.objects.db_manager(using).get_for_model(target)
                locked.target_id = str(target.pk)
            locked.remote_base_hash, locked.local_base_hash = source_hash, local_hash
            locked.remote_version, locked.origin, locked.status = remote_version, origin, LinkStatus.CURRENT
            locked.tombstoned_at = None
            fields = [
                "target_ct_id",
                "target_id",
                "remote_base_hash",
                "local_base_hash",
                "remote_version",
                "origin",
                "status",
                "tombstoned_at",
            ]
            locked.save(using=using, update_fields=[*fields, "updated_at"])
            for field in fields:
                setattr(link, field, getattr(locked, field))
            return revision

    def mark_absent(self, stream: Any, keys: Iterable[str], *, using: str | None = None) -> int:
        """Count a completed sweep's missing keys, retaining tombstones."""

        using = get_write_alias(self.model, using=using, bound=self, instance=stream)
        with system_context(reason="integrate.record.mark_absent"), transaction.atomic(using=using):
            stream = type(stream).objects.db_manager(using).lock_current(stream, using=using)
            rows = (
                self.db_manager(using)
                .filter(stream=stream, external_key__in=tuple(keys))
                .exclude(
                    status=LinkStatus.TOMBSTONE,
                )
                .order_by("pk")
                .lock_if_supported()
            )
            count = 0
            for link in rows:
                link.absence_count += 1
                if link.status != LinkStatus.DISCREPANT:
                    link.status = LinkStatus.UNAVAILABLE
                    if link.absence_count >= stream.absence_threshold:
                        link.status, link.tombstoned_at = LinkStatus.TOMBSTONE, timezone.now()
                link.save(using=using, update_fields=["absence_count", "status", "tombstoned_at", "updated_at"])
                count += 1
            return count

    def tombstone(self, link: Any, *, using: str | None = None) -> Any:
        """Retain a confirmed remote deletion; never delete the identity."""

        using = get_write_alias(self.model, using=using, bound=self, instance=link)
        with system_context(reason="integrate.record.tombstone"), transaction.atomic(using=using):
            locked = self.db_manager(using).filter(pk=link.pk).lock_if_supported().get()
            if locked.status != LinkStatus.TOMBSTONE:
                locked.status, locked.tombstoned_at = LinkStatus.TOMBSTONE, timezone.now()
                locked.save(using=using, update_fields=["status", "tombstoned_at", "updated_at"])
            link.status, link.tombstoned_at = locked.status, locked.tombstoned_at
            return link


class RecordLink(SqidMixin, AuditMixin, AngeeModel):
    """A stable remote identity with the two last-applied comparison bases."""

    runtime = True
    sqid_prefix = "rlk_"
    stream = models.ForeignKey("integrate.SyncStream", on_delete=models.PROTECT, related_name="links")
    external_key = models.CharField(max_length=512)
    target_ct = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    target_id = models.CharField(max_length=255, null=True, blank=True)
    target = GenericForeignKey("target_ct", "target_id")
    status = StateField(choices_enum=LinkStatus, default=LinkStatus.OBSERVED)
    remote_version = models.CharField(max_length=512, blank=True)
    remote_base_hash = models.CharField(max_length=64, blank=True)
    local_base_hash = models.CharField(max_length=64, blank=True)
    origin = models.CharField(max_length=16, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_verified_generation = models.PositiveIntegerField(default=0)
    absence_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    tombstoned_at = models.DateTimeField(null=True, blank=True)
    objects = RecordLinkManager()

    class Meta:
        abstract = True
        rebac_resource_type = "integrate/record_link"
        rebac_id_attr = "pk"
        constraints = (models.UniqueConstraint(fields=("stream", "external_key"), name="uniq_stream_record_key"),)


class RecordRevisionQuerySet(AppendOnlyQuerySet, AngeeQuerySet[Any]):
    """Reject every bulk mutation of retained applied evidence."""

    def immutable_error(self, operation: str) -> Exception:
        """Report one invariant for instance and collection mutation paths."""

        return ValidationError("Record revisions are immutable.")


class RecordRevisionManager(AngeeManager.from_queryset(RecordRevisionQuerySet)):  # type: ignore[misc]
    """Allocate revision numbers while holding the stable identity lock."""

    def append(
        self,
        link: Any,
        *,
        source_payload: Any,
        source_hash: str,
        mapping_version: int,
        mapped_payload: Any = None,
        dependency_digest: str = "",
        applied_at: datetime | None = None,
        using: str | None = None,
    ) -> Any:
        """Append one revision; numbering and prior come from retained rows."""

        using = get_write_alias(self.model, using=using, bound=self, instance=link)
        with system_context(reason="integrate.revision.append"), transaction.atomic(using=using):
            locked = type(link).objects.db_manager(using).filter(pk=link.pk).lock_if_supported().get()
            manager = self.db_manager(using)
            prior = manager.filter(link=locked).order_by("-number").first()
            return manager.create(
                link=locked,
                number=1 if prior is None else prior.number + 1,
                prior=prior,
                source_payload={} if source_payload is None else source_payload,
                source_hash=source_hash,
                mapping_version=mapping_version,
                mapped_payload={} if mapped_payload is None else mapped_payload,
                dependency_digest=dependency_digest,
                applied_at=applied_at,
            )


class RecordRevision(SqidMixin, AuditMixin, AngeeModel):
    """Immutable observed and mapped payload history for one replica identity."""

    runtime = True
    sqid_prefix = "rrv_"
    link = models.ForeignKey("integrate.RecordLink", on_delete=models.PROTECT, related_name="revisions")
    number = models.PositiveIntegerField()
    source_payload = models.JSONField()
    source_hash = models.CharField(max_length=64)
    mapping_version = models.PositiveIntegerField()
    mapped_payload = models.JSONField(default=dict, blank=True)
    dependency_digest = models.CharField(max_length=64, blank=True)
    prior = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="successors")
    applied_at = models.DateTimeField(null=True, blank=True)
    objects = RecordRevisionManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        rebac_resource_type = "integrate/record_revision"
        rebac_id_attr = "pk"
        constraints = (models.UniqueConstraint(fields=("link", "number"), name="uniq_record_revision_number"),)

    def save(self, *args: Any, using: str | None = None, **kwargs: Any) -> None:
        """Permit insertion only; applied evidence never changes in place."""

        using = get_write_alias(type(self), using=using, instance=self)
        self._state.db = using
        if self.pk and type(self)._base_manager.using(using).filter(pk=self.pk).exists():
            raise ValidationError("Record revisions are immutable.")
        super().save(*args, using=using, **kwargs)

    def delete(self, *args: Any, using: str | None = None, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Refuse deletion even when no successor references the revision."""

        raise ValidationError("Record revisions are immutable.")


class SyncDiscrepancyManager(AngeeManager):
    """Coalesce unresolved record failures and expose due rescan candidates."""

    def record(
        self,
        stream: Any,
        *,
        kind: str,
        code: str,
        source_hash: str = "",
        mapping_version: int = 1,
        details: Mapping[str, Any] | None = None,
        link: Any = None,
        retry_at: datetime | None = None,
        using: str | None = None,
    ) -> Any:
        """Refresh an open source-version failure, retaining resolved history."""

        using = get_write_alias(self.model, using=using, bound=self, instance=stream)
        with system_context(reason="integrate.discrepancy.record"), transaction.atomic(using=using):
            stream = type(stream).objects.db_manager(using).lock_current(stream, using=using)
            if link is not None:
                link._state.db = using
                locked_link = type(link).objects.db_manager(using).filter(pk=link.pk).lock_if_supported().get()
                if locked_link.stream_id != stream.pk:
                    raise ValidationError("A discrepancy link must belong to its stream.")
            row, _ = self.db_manager(using).get_or_create(
                stream=stream,
                kind=kind,
                code=code,
                source_hash=source_hash,
                mapping_version=mapping_version,
                status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY),
                defaults={"link": link, "status": DiscrepancyStatus.OPEN},
            )
            row.link = link
            row.details = {**row.details, **dict(details or {})}
            row.status, row.resolved_at = DiscrepancyStatus.OPEN, None
            row.retry_at = None if kind == DiscrepancyKind.CONFLICT else retry_at
            row.attempts += 1
            row.save(
                using=using,
                update_fields=["link", "details", "status", "retry_at", "resolved_at", "attempts", "updated_at"],
            )
            if link is not None:
                type(link).objects.db_manager(using).filter(pk=link.pk).update(status=LinkStatus.DISCREPANT)
                link.status = LinkStatus.DISCREPANT
            return row

    def resolve(self, discrepancy: Any, *, using: str | None = None) -> Any:
        """Resolve retained quarantine after its record has been re-applied."""

        using = get_write_alias(self.model, using=using, bound=self, instance=discrepancy)
        with system_context(reason="integrate.discrepancy.resolve"), transaction.atomic(using=using):
            link = related_on(discrepancy, "link", required=False, using=using)
            if link is not None:
                link = type(link).objects.db_manager(using).filter(pk=link.pk).lock_if_supported().get()
            row = self.db_manager(using).filter(pk=discrepancy.pk).lock_if_supported().get()
            if row.status != DiscrepancyStatus.RESOLVED:
                row.status, row.resolved_at, row.retry_at = DiscrepancyStatus.RESOLVED, timezone.now(), None
                row.save(using=using, update_fields=["status", "resolved_at", "retry_at", "updated_at"])
            if (
                link is not None
                and link.status == LinkStatus.DISCREPANT
                and not self.db_manager(using)
                .filter(
                    link=link,
                    status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY),
                )
                .exists()
            ):
                link.status = (
                    LinkStatus.CURRENT if link.remote_base_hash and link.local_base_hash else LinkStatus.OBSERVED
                )
                link.save(using=using, update_fields=["status", "updated_at"])
            return row

    def rescan(self, stream: Any, *, using: str | None = None) -> tuple[Any, ...]:
        """Return due unresolved rows; a rescan is not a durable work queue."""

        using = get_write_alias(self.model, using=using, bound=self, instance=stream)
        with system_context(reason="integrate.discrepancy.rescan"):
            return tuple(
                self.db_manager(using)
                .filter(
                    stream=stream,
                    status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY),
                )
                .filter(Q(retry_at__isnull=True) | Q(retry_at__lte=timezone.now()))
                .order_by("pk")
            )


class SyncDiscrepancy(SqidMixin, AuditMixin, AngeeModel):
    """A per-source-version failure to revisit through the adapter's rescan."""

    runtime = True
    sqid_prefix = "sdc_"
    stream = models.ForeignKey("integrate.SyncStream", on_delete=models.PROTECT, related_name="discrepancies")
    link = models.ForeignKey(
        "integrate.RecordLink", null=True, blank=True, on_delete=models.PROTECT, related_name="discrepancies"
    )
    kind = StateField(choices_enum=DiscrepancyKind)
    code = models.CharField(max_length=160)
    source_hash = models.CharField(max_length=64, blank=True)
    mapping_version = models.PositiveIntegerField(default=1)
    details = models.JSONField(default=dict, blank=True)
    status = StateField(choices_enum=DiscrepancyStatus, default=DiscrepancyStatus.OPEN)
    attempts = models.PositiveIntegerField(default=0)
    retry_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    objects = SyncDiscrepancyManager()

    class Meta:
        abstract = True
        rebac_resource_type = "integrate/sync_discrepancy"
        rebac_id_attr = "pk"
        constraints = (
            models.UniqueConstraint(
                fields=("stream", "kind", "code", "source_hash", "mapping_version"),
                condition=Q(status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY)),
                name="uniq_open_sync_discrepancy",
            ),
        )
