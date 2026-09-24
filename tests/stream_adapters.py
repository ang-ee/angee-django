"""In-memory stream rows for transport-only adapter tests; DB tests use the driver."""

from collections import deque
from types import SimpleNamespace
from typing import Any


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
                for spec in self.backend.streams(deadline=deadline, using="default")
            }
            for stream in self.rows.values():
                if not stream.cursor and self.backend.bridge.cursor:
                    stream.cursor = self.backend.seed_cursor(stream, self.backend.bridge.cursor) or {}
            self.pending = deque(self.rows.values())
        while self.pending:
            stream = self.pending[0]
            page = self.backend.extract(stream, 200, deadline=deadline, using="default")
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
