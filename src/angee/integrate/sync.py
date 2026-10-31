"""Bridge sync context and progress helpers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Any

from angee.graphql.publishing import publication_ingestion_context


class SyncDispatch(StrEnum):
    """A durable execution owner will report this sync's terminal result later."""

    DISPATCHED = "dispatched"


@contextmanager
def bridge_sync_context() -> Iterator[None]:
    """Mark a bridge sync and annotate the changes it publishes."""

    with publication_ingestion_context():
        yield


_current_bridge_progress: ContextVar[BridgeProgressReporter | None] = ContextVar(
    "angee_current_bridge_progress",
    default=None,
)


class BridgeProgressReporter:
    """Route execution-context progress to its owning bridge."""

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
        """Delegate telemetry to Bridge.report_progress; supplied details replace prior details.

        Dispatch identity belongs to Bridge.sync_run_id, independently of this
        payload. A progress report preserves a queued stage until work starts.
        """

        return self.bridge.report_progress(stage, message=message, details=details, **extra)


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
    "SyncDispatch",
    "bridge_progress_context",
    "bridge_sync_context",
    "current_bridge_progress",
]
