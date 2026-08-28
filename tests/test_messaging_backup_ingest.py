"""Tests for shared backup-ingest identity rules."""

from __future__ import annotations

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
