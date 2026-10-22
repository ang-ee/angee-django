"""Retained inbound failure state shared by record links and stream epochs."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Self

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from angee.integrate.states import SyncFailure, SyncFailureKind, SyncFailureStatus
from angee.models.query import AngeeQuerySet

if TYPE_CHECKING:
    from angee.integrate.models import SyncStream


class SyncStateMixin(models.Model):
    """Own inbound slots; concrete owners supply their stream and persistence hook."""

    inbound = models.JSONField(default=list, blank=True)

    class Meta:
        abstract = True

    @property
    def failure_stream(self) -> SyncStream:
        """Return the epoch whose lock and backoff govern this owner."""
        raise NotImplementedError

    def failures(self, *, open_only: bool = False) -> tuple[SyncFailure, ...]:
        rows = (SyncFailure.from_state(self, slot, data) for slot, data in enumerate(self.inbound))
        return tuple(row for row in rows if not open_only or row.is_open)

    def _lock_sync_state(self) -> Self:
        stream = self.failure_stream
        type(stream).objects.lock_current(stream)
        rows = type(self)._default_manager.get_queryset()
        assert isinstance(rows, AngeeQuerySet)
        return rows.lock_if_supported().get(pk=self.pk)

    def on_sync_state_saved(self, locked: Self) -> None:
        """Persist inbound state and refresh the caller; links extend the saved fields."""
        locked.save(update_fields=["inbound", "updated_at"])
        self.inbound = locked.inbound

    def _failure_data(
        self,
        data: dict[str, Any],
        *,
        kind: SyncFailureKind,
        code: str,
        source_hash: str,
        mapping_version: int,
        details: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        now = timezone.now()
        attempts = data.get("attempts", 0) + 1
        retry = None if kind == SyncFailureKind.CONFLICT else self.failure_retry_at(attempts)
        return {
            **data,
            "kind": kind,
            "code": code,
            "source_hash": source_hash,
            "mapping_version": mapping_version,
            "details": {**data.get("details", {}), **dict(details or {})},
            "status": SyncFailureStatus.OPEN,
            "attempts": attempts,
            "retry_at": retry.isoformat() if retry else None,
            "resolved_at": None,
            "created_at": data.get("created_at", now.isoformat()),
            "updated_at": now.isoformat(),
        }

    def record_failure(
        self,
        *,
        kind: SyncFailureKind,
        code: str,
        source_hash: str = "",
        mapping_version: int = 1,
        details: Mapping[str, Any] | None = None,
    ) -> SyncFailure:
        """Coalesce an open inbound identity; retain closed evidence and backoff."""
        with transaction.atomic():
            locked = self._lock_sync_state()
            identity = dict(kind=kind, code=code, source_hash=source_hash, mapping_version=mapping_version)
            slot = next(
                (
                    index
                    for index, entry in enumerate(locked.inbound)
                    if entry["status"] != SyncFailureStatus.RESOLVED
                    and all(entry[name] == value for name, value in identity.items())
                ),
                len(locked.inbound),
            )
            prior = deepcopy(locked.inbound[slot]) if slot < len(locked.inbound) else {}
            data = locked._failure_data(
                prior, kind=kind, code=code, source_hash=source_hash, mapping_version=mapping_version, details=details
            )
            if slot == len(locked.inbound):
                locked.inbound.append(data)
            else:
                locked.inbound[slot] = data
            if data["retry_at"] is not None:
                for entry in locked.inbound:
                    if entry["status"] != SyncFailureStatus.RESOLVED and entry["kind"] != SyncFailureKind.CONFLICT:
                        entry["retry_at"] = data["retry_at"]
            self.on_sync_state_saved(locked)
            return SyncFailure.from_state(self, slot, data)

    def failure_retry_at(self, attempts: int) -> datetime:
        """Semantic and fallback attempts share the stream's bounded backoff."""
        cap = timedelta(days=1)
        interval = self.failure_stream.reconcile_interval
        if interval and interval > timedelta(0):
            cap = min(cap, interval)
        return timezone.now() + min(timedelta(minutes=2 ** min(max(0, attempts - 1), 11)), cap)

    def _transition_state(self, data: dict[str, Any], *, retry: bool, conflict: bool) -> None:
        if not data:
            raise ValidationError("The retained failure no longer exists.")
        if retry and data["status"] == SyncFailureStatus.RESOLVED:
            raise ValidationError("A resolved failure cannot be retried.")
        if not retry and data["kind"] == SyncFailureKind.CONFLICT and not conflict:
            raise ValidationError("A conflict requires keeping remote or local changes.")
        now = timezone.now()
        data.update(
            status=SyncFailureStatus.RETRY if retry else SyncFailureStatus.RESOLVED,
            retry_at=now.isoformat() if retry and data["kind"] != SyncFailureKind.CONFLICT else None,
            resolved_at=None if retry else now.isoformat(),
            updated_at=now.isoformat(),
        )

    def _transition_failure(self, slot: int | None, *, retry: bool, conflict: bool = False) -> SyncFailure:
        with transaction.atomic():
            locked = self._lock_sync_state()
            if slot is None or slot < 0 or slot >= len(locked.inbound):
                raise ValidationError("The retained inbound failure no longer exists.")
            data = locked.inbound[slot]
            locked._transition_state(data, retry=retry, conflict=conflict)
            self.on_sync_state_saved(locked)
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
            self.on_sync_state_saved(locked)
