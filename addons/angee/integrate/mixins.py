"""Retained inbound failure state shared by record links and stream epochs."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from angee.base.mixins import AuditMixin, HistoryMixin
from angee.base.models import AngeeDataModel
from angee.integrate.states import ConflictKeep, DiscrepancyKind, DiscrepancyStatus, LinkStatus, OutboxState


class SyncFailure(BaseModel):
    """A state projection whose reads and transitions inherit its row's policy.

    IDs address an append-only inbound slot or the current link outbox. Closed
    inbound slots stay; replaced outbox evidence remains in native link history.
    """

    model_config = ConfigDict(frozen=True)
    id: str
    stream_id: str
    link_id: str | None = None
    kind: DiscrepancyKind
    code: str
    source_hash: str = ""
    mapping_version: int = 1
    details: dict[str, Any] = Field(default_factory=dict)
    status: DiscrepancyStatus = DiscrepancyStatus.OPEN
    attempts: int = 0
    retry_at: datetime | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    _owner: Any = PrivateAttr()
    _slot: int | None = PrivateAttr()

    @classmethod
    def from_state(cls, owner: Any, slot: int | None, data: dict[str, Any]) -> SyncFailure:
        """Bind one typed view to the row that owns its state transitions."""
        stream, link = owner.failure_stream, owner.failure_link
        row = cls.model_validate(
            {
                **data,
                "id": f"{owner.public_id}:{'outbox@' + data['created_at'] if slot is None else slot}",
                "stream_id": stream.public_id,
                "link_id": link.public_id if link is not None else None,
            }
        )
        row._owner, row._slot = owner, slot
        return row

    @property
    def is_open(self) -> bool:
        return self.status != DiscrepancyStatus.RESOLVED

    @property
    def pk(self) -> str:
        """Use the same state address in page results and operator actions."""
        return self.id

    @property
    def slot(self) -> int | None:
        return self._slot

    @property
    def stream(self) -> Any:
        return self._owner.failure_stream

    @property
    def link(self) -> Any:
        return self._owner.failure_link

    @property
    def link_pk(self) -> Any:
        return self.link.pk if self.link is not None else None

    def resolve(self) -> SyncFailure:
        return self._owner.resolve_failure(self._slot)

    def retry(self) -> SyncFailure:
        return self._owner.retry_failure(self._slot)

    def resolve_conflict(self, *, keep: ConflictKeep) -> SyncFailure:
        if self.link is None:
            raise ValidationError("Conflict resolution requires a linked conflict.")
        return self.link.resolve_conflict(self._slot, keep=keep)


class SyncStateMixin(AuditMixin, HistoryMixin, AngeeDataModel):
    """One inbound failure identity and lifecycle for streams and links."""

    inbound = models.JSONField(default=list, blank=True)

    class Meta:
        abstract = True

    @property
    def failure_stream(self) -> Any:
        return self

    @property
    def failure_link(self) -> Any:
        return None

    def failures(self, *, open_only: bool = False) -> tuple[SyncFailure, ...]:
        rows = [SyncFailure.from_state(self, slot, data) for slot, data in enumerate(self.inbound)]
        link = self.failure_link
        if link is not None and link.outbox_last_error:
            rows.append(SyncFailure.from_state(self, None, link.outbox_last_error))
        return tuple(row for row in rows if not open_only or row.is_open)

    def _lock_sync_state(self) -> Any:
        stream = self.failure_stream
        locked = type(stream).objects.lock_current(stream)
        return locked if self.failure_link is None else type(self).objects.lock_if_supported().get(pk=self.pk)

    def _save_sync_state(self, locked: Any) -> None:
        fields = ["inbound"]
        if locked.failure_link is not None:
            fields += ["outbox_state", "outbox_retry_at", "outbox_last_error", "status"]
            if locked.failures(open_only=True):
                locked.status = LinkStatus.DISCREPANT
            elif locked.status == LinkStatus.DISCREPANT:
                locked.status = LinkStatus.CURRENT if locked.applied_base_id else LinkStatus.OBSERVED
        locked.save(update_fields=[*fields, "updated_at"])
        for name in fields:
            setattr(self, name, getattr(locked, name))

    def record_failure(
        self,
        *,
        kind: DiscrepancyKind,
        code: str,
        source_hash: str = "",
        mapping_version: int = 1,
        details: Mapping[str, Any] | None = None,
        outbox: bool = False,
    ) -> SyncFailure:
        """Coalesce an open identity; retain closed evidence and retry backoff."""
        with transaction.atomic():
            locked = self._lock_sync_state()
            identity = dict(kind=kind, code=code, source_hash=source_hash, mapping_version=mapping_version)
            slot = None
            if outbox:
                if locked.failure_link is None:
                    raise ValidationError("An outbox requires a record link.")
                data = deepcopy(locked.outbox_last_error)
                if data.get("status") == DiscrepancyStatus.RESOLVED or any(
                    data.get(name) != value for name, value in identity.items()
                ):
                    if data and data["status"] != DiscrepancyStatus.RESOLVED:
                        locked.resolve_failure(None)
                    data = {}
            else:
                slot = next(
                    (
                        index
                        for index, entry in enumerate(locked.inbound)
                        if entry["status"] != DiscrepancyStatus.RESOLVED
                        and all(entry[name] == value for name, value in identity.items())
                    ),
                    len(locked.inbound),
                )
                data = deepcopy(locked.inbound[slot]) if slot < len(locked.inbound) else {}
            now = timezone.now()
            attempts = data.get("attempts", 0) + 1
            retry = None if kind == DiscrepancyKind.CONFLICT else locked.failure_retry_at(attempts)
            data.update(
                {
                    **identity,
                    "details": {**data.get("details", {}), **dict(details or {})},
                    "status": DiscrepancyStatus.OPEN,
                    "attempts": attempts,
                    "retry_at": retry.isoformat() if retry else None,
                    "resolved_at": None,
                    "created_at": data.get("created_at", now.isoformat()),
                    "updated_at": now.isoformat(),
                }
            )
            if outbox:
                locked.outbox_last_error = data
                locked.outbox_state, locked.outbox_retry_at = OutboxState.REFUSED, retry
            else:
                if slot == len(locked.inbound):
                    locked.inbound.append(data)
                else:
                    locked.inbound[slot] = data
                if retry is not None:
                    for entry in locked.inbound:
                        if entry["status"] != DiscrepancyStatus.RESOLVED and entry["kind"] != DiscrepancyKind.CONFLICT:
                            entry["retry_at"] = retry.isoformat()
            self._save_sync_state(locked)
            return SyncFailure.from_state(self, slot, data)

    def failure_retry_at(self, attempts: int) -> datetime:
        """Semantic and fallback attempts share the stream's bounded backoff."""
        cap = timedelta(days=1)
        interval = self.failure_stream.reconcile_interval
        if interval and interval > timedelta(0):
            cap = min(cap, interval)
        return timezone.now() + min(timedelta(minutes=2 ** min(max(0, attempts - 1), 11)), cap)

    def _transition_failure(self, slot: int | None, *, retry: bool, conflict: bool = False) -> SyncFailure:
        with transaction.atomic():
            locked = self._lock_sync_state()
            data = locked.outbox_last_error if slot is None else locked.inbound[slot]
            if not data:
                raise ValidationError("The retained failure no longer exists.")
            if retry and data["status"] == DiscrepancyStatus.RESOLVED:
                raise ValidationError("A resolved discrepancy cannot be retried.")
            if not retry and data["kind"] == DiscrepancyKind.CONFLICT and not conflict:
                raise ValidationError("A conflict requires keeping remote or local changes.")
            now = timezone.now()
            data.update(
                status=DiscrepancyStatus.RETRY if retry else DiscrepancyStatus.RESOLVED,
                retry_at=now.isoformat() if retry and data["kind"] != DiscrepancyKind.CONFLICT else None,
                resolved_at=None if retry else now.isoformat(),
                updated_at=now.isoformat(),
            )
            if slot is None:
                locked.outbox_state = OutboxState.PENDING if retry else None
                locked.outbox_retry_at = now if retry and data["kind"] != DiscrepancyKind.CONFLICT else None
            self._save_sync_state(locked)
            return SyncFailure.from_state(self, slot, data)

    def resolve_failure(self, slot: int | None, *, conflict: bool = False) -> SyncFailure:
        return self._transition_failure(slot, retry=False, conflict=conflict)

    def retry_failure(self, slot: int | None) -> SyncFailure:
        return self._transition_failure(slot, retry=True)

    def defer_failure(self, row: SyncFailure, *, details: Mapping[str, Any]) -> None:
        """Retain the baseline fallback using the same retry delay as a refusal."""
        with transaction.atomic():
            locked = self._lock_sync_state()
            if row.slot is None:
                raise ValidationError("An outbox failure cannot request an inbound baseline.")
            data = locked.inbound[row.slot]
            data["details"].update(details)
            data["retry_at"] = locked.failure_retry_at(row.attempts).isoformat()
            self._save_sync_state(locked)
