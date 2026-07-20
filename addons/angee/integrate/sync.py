"""Bridge sync context and progress helpers."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from django.db import transaction

from angee.base.sync import sync_ingestion_active, sync_ingestion_context

bridge_sync_context = sync_ingestion_context
bridge_sync_active = sync_ingestion_active

_current_bridge_progress: ContextVar[BridgeProgressReporter | None] = ContextVar(
    "angee_current_bridge_progress",
    default=None,
)


class BridgeProgressReporter:
    """Persist generic progress for the bridge currently being synchronized."""

    def __init__(self, bridge: Any) -> None:
        self.bridge = bridge

    def report(
        self,
        stage: str,
        *,
        message: str = "",
        details: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Merge progress under a row lock without clobbering a queued run."""

        with transaction.atomic():
            row = (
                type(self.bridge)
                .objects.sudo(reason="integrate.bridge.progress")
                .lock_if_supported()
                .get(pk=self.bridge.pk)
            )
            existing = row.sync_progress if isinstance(row.sync_progress, Mapping) else {}
            payload = dict(existing)
            queue_pending = str(row.sync_stage) == str(row.SyncStage.QUEUED)
            if not queue_pending:
                payload["stage"] = str(stage)
            if message:
                payload["message"] = message
            elif "message" in payload:
                payload.pop("message")
            if details is not None:
                payload["details"] = dict(details)
            payload.update(extra)

            row.sync_progress = payload
            update_fields = ["sync_progress", "updated_at"]
            if not queue_pending and str(stage) in getattr(row.SyncStage, "values", ()):
                row.sync_stage = str(stage)
                update_fields.append("sync_stage")
            row.save(update_fields=update_fields)
        self.bridge.sync_progress = payload
        self.bridge.sync_stage = row.sync_stage
        return payload


@contextmanager
def bridge_progress_context(bridge: Any) -> Any:
    """Make ``bridge`` progress reporting available to nested sync code."""

    reporter = BridgeProgressReporter(bridge)
    token = _current_bridge_progress.set(reporter)
    try:
        yield reporter
    finally:
        _current_bridge_progress.reset(token)


def current_bridge_progress() -> BridgeProgressReporter | None:
    """Return the current bridge progress reporter, if a bridge sync is active."""

    return _current_bridge_progress.get()


__all__ = [
    "BridgeProgressReporter",
    "bridge_progress_context",
    "bridge_sync_active",
    "bridge_sync_context",
    "current_bridge_progress",
]
