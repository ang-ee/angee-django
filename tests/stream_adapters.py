"""In-memory stream rows for transport-only adapter tests; DB tests use the driver."""

from collections import deque
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from angee.integrate.states import SyncFailure
from angee.integrate.testing.models import RecordLink


def applied_history() -> Any:
    """Read application snapshots pinned by current or historical link states."""

    return RecordLink.history.filter(
        history_id__in=RecordLink.history.order_by()
        .values("applied_base_id")
        .union(RecordLink.objects.order_by().values("applied_base_id"))
    )


def failure_state(failure: SyncFailure, **values: Any) -> SyncFailure:
    """Reload retained evidence, optionally seeding a test's scheduling facts."""

    owner = failure.link or failure.stream
    owner.refresh_from_db()
    if values:
        state = owner.outbox_last_error if failure.slot is None else owner.inbound[failure.slot]
        state.update(
            {key: value.isoformat() if isinstance(value, datetime) else value for key, value in values.items()}
        )
        fields = ["inbound"]
        if failure.slot is None:
            fields = ["outbox_last_error"]
            if "retry_at" in values:
                owner.outbox_retry_at = values["retry_at"]
                fields.append("outbox_retry_at")
        owner.save(update_fields=fields)
    return next(row for row in owner.failures() if row.slot == failure.slot)


class AdapterPages:
    """Consume the actual extract protocol while retaining committed test cursors."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.rows: dict[str, Any] = {}
        self.pending: deque[Any] | None = None

    def next_batch(self, *, deadline: float | None = None) -> list[Any]:
        """Return the next nonempty page, committing only successful extractions."""

        if self.pending is None:
            self.rows = {
                spec.partition: SimpleNamespace(
                    key=spec.key,
                    partition=spec.partition,
                    generation=1,
                    cursor=spec.cursor,
                    config=spec.config,
                )
                for spec in self.backend.streams(deadline=deadline)
            }
            for stream in self.rows.values():
                if not stream.cursor and self.backend.bridge.cursor:
                    stream.cursor = self.backend.seed_cursor(stream, self.backend.bridge.cursor) or {}
            self.pending = deque(self.rows.values())
        while self.pending:
            stream = self.pending[0]
            page = self.backend.extract(stream, 200, deadline=deadline)
            stream.cursor = page.cursor
            if page.exhausted:
                self.pending.popleft()
            if page.records:
                return list(page.records)
        self.backend.close()
        return []

    @property
    def cursors(self) -> dict[str, dict[str, Any]]:
        """Expose the independent retained cursors by partition."""

        return {key: stream.cursor for key, stream in self.rows.items()}
