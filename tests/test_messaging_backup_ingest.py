"""Tests for shared backup-ingest identity and batching rules."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from angee.messaging import backup_ingest
from angee.messaging.backends import ParsedMessage, ParsedPart
from angee.messaging.backup_ingest import ContentKeyCounter


def test_content_key_counter_distinguishes_content_and_true_duplicates() -> None:
    """Same-ms distinct content differs; exact repeats receive stable occurrences."""

    def ids() -> list[str]:
        counter = ContentKeyCounter()
        return [
            counter.key(
                thread_key="thread",
                timestamp_ms=42,
                sender_key="sender",
                content=content,
            )
            for content in (
                {"text": "one"},
                {"text": "two"},
                {"text": "same"},
                {"text": "same"},
                {"text": "same"},
            )
        ]

    first = ids()
    second = ids()

    assert first[0] != first[1]
    assert [value.rsplit(":", 1)[1] for value in first[2:]] == ["0", "1", "2"]
    assert second == first


@pytest.mark.parametrize("dry_run", [False, True])
def test_batch_ingest_bounds_parsed_media_and_preserves_write_policy(
    monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    """Neutral body bytes bound batches even when source records have no media DTO."""

    calls: list[dict[str, Any]] = []
    aliases: list[str] = []
    progress: list[int] = []

    class Manager:
        def db_manager(self, using: str) -> Manager:
            aliases.append(using)
            return self

        def ingest(self, messages: list[ParsedMessage], **kwargs: Any) -> None:
            calls.append({"messages": list(messages), **kwargs})

    channel = SimpleNamespace(_state=SimpleNamespace(adding=False, db="import-db"))
    monkeypatch.setattr(
        backup_ingest, "apps", SimpleNamespace(get_model=lambda *_args: SimpleNamespace(objects=Manager()))
    )
    monkeypatch.setattr(backup_ingest, "system_context", lambda **_kwargs: nullcontext())

    def parse(identifier: int) -> ParsedMessage:
        return ParsedMessage(
            external_id=str(identifier),
            platform="test",
            body=ParsedPart(
                type="multipart/mixed",
                children=(ParsedPart(text="hello"), ParsedPart(content=b"1234")),
            ),
        )

    total = backup_ingest.batch_ingest(
        channel,
        iter((1, 2, 3)),
        parse,
        reason="test backup ingest",
        max_batch_bytes=8,
        dry_run=dry_run,
        on_batch=progress.append,
    )

    assert total == 3
    assert progress == [2, 3]
    if dry_run:
        assert calls == []
        assert aliases == []
    else:
        assert aliases == ["import-db", "import-db"]
        assert [[message.external_id for message in call["messages"]] for call in calls] == [["1", "2"], ["3"]]
        assert all(call["channel"] is channel and call["historical"] and not call["quote_edges"] for call in calls)
