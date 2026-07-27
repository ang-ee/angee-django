"""Tests for the shared Meta Download Your Information dialect."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from angee.messaging.backup_ingest import ContentKeyCounter
from angee.messaging_integrate_meta.export import (
    MetaExport,
    decode_message,
    fix_encoding,
    load_json,
    message_content_projection,
    timestamp_milliseconds,
    timestamp_seconds,
)


def test_fix_encoding_and_json_load_normalize_nested_mojibake(tmp_path: Path) -> None:
    """Meta's Latin-1 codepoints are reversed recursively with safe fallback."""

    path = tmp_path / "data.json"
    path.write_text(
        json.dumps({"name": "RenÃ©e", "nested": ["ð\x9f\x98\x80", "already ✓"]}),
        encoding="utf-8",
    )

    assert load_json(path) == {"name": "Renée", "nested": ["😀", "already ✓"]}
    assert fix_encoding("already ✓") == "already ✓"


def test_multi_root_chunk_merge_timestamps_and_missing_media_marker(tmp_path: Path) -> None:
    """Overlapping roots merge numeric chunks and preserve unresolved media visibly."""

    named = tmp_path / "facebook-alex"
    bare = tmp_path
    activity = "your_facebook_activity"
    thread_parts = ("messages", "inbox", "thread_one")
    named_thread = named / activity
    bare_thread = bare / activity
    for base in (named_thread, bare_thread):
        (base.joinpath(*thread_parts)).mkdir(parents=True, exist_ok=True)
    (named / "media").mkdir(parents=True)
    (named / "media" / "photo.jpg").write_bytes(b"photo")

    _write_chunk(
        bare_thread.joinpath(*thread_parts, "message_2.json"),
        timestamp_ms=2000,
        content="later",
        media=[{"uri": "missing/photo.jpg"}],
    )
    _write_chunk(
        named_thread.joinpath(*thread_parts, "message_1.json"),
        timestamp_ms=1000,
        content="earlier",
        media=[{"uri": "media/photo.jpg"}],
    )
    # Exact overlap must not double-import the chunk.
    _write_chunk(
        bare_thread.joinpath(*thread_parts, "message_1.json"),
        timestamp_ms=1000,
        content="earlier",
        media=[{"uri": "media/photo.jpg"}],
    )

    export = MetaExport(
        tmp_path,
        activity_directory=activity,
        root_prefix="facebook-",
    )
    records = list(
        export.message_records(
            folders=("inbox",),
            counter=ContentKeyCounter(),
        )
    )
    parsed = [
        decode_message(
            record,
            platform="facebook",
            own_name="Alex",
        )
        for record in records
    ]

    assert export.roots == (tmp_path, named)
    assert [record.sent_at for record in records] == [
        datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        datetime(1970, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
    ]
    assert parsed[0].body is not None
    assert any(child.content == b"photo" for child in parsed[0].body.children)
    assert parsed[1].body is not None
    assert any(
        child.text == "[media unavailable: photo.jpg]"
        for child in parsed[1].body.children
    )
    assert parsed[0].thread is not None
    assert parsed[0].thread.metadata["folder"] == "inbox"


def test_derived_ids_survive_reaction_accrual_and_envelope_churn() -> None:
    """Engagement and cosmetic export-revision keys never shift a record's id.

    A newer re-export in which someone reacted to an old message (or Meta added
    an envelope key) must reproduce the same external id, or the row duplicates
    on the next import.
    """

    original = {
        "sender_name": "Bob",
        "timestamp_ms": 1000,
        "content": "hello",
        "photos": [{"uri": "your_facebook_activity/messages/inbox/chat/photos/123.jpg"}],
    }
    re_exported = {
        **original,
        "reactions": [{"reaction": "❤", "actor": "Alex"}],
        "is_geoblocked_for_viewer": False,
        # A folder move rewrites the media uri prefix; the basename is stable.
        "photos": [{"uri": "your_facebook_activity/messages/archived_threads/chat/photos/123.jpg"}],
    }
    assert message_content_projection(original) == message_content_projection(re_exported)

    first = ContentKeyCounter().key(
        thread_key="chat", timestamp_ms=1000, sender_key="Bob",
        content=message_content_projection(original),
    )
    second = ContentKeyCounter().key(
        thread_key="chat", timestamp_ms=1000, sender_key="Bob",
        content=message_content_projection(re_exported),
    )
    assert first == second


def test_thread_identity_survives_folder_moves(tmp_path: Path) -> None:
    """An accepted message request keeps its thread and message identities.

    The containing folder is an export-run fact: the same conversation moves
    from ``message_requests`` to ``inbox`` between exports, and both exports
    must produce identical thread keys and external ids. Within one export the
    two folder copies merge into one thread that records both folders.
    """

    def build(root: Path, folder: str) -> Path:
        thread_dir = root / "your_facebook_activity" / "messages" / folder / "chat_abc"
        thread_dir.mkdir(parents=True)
        _write_chunk(
            thread_dir / "message_1.json", timestamp_ms=1000, content="hi", media=[]
        )
        return root

    first_export = MetaExport(
        build(tmp_path / "before", "message_requests"),
        activity_directory="your_facebook_activity",
        root_prefix="facebook-",
    )
    second_export = MetaExport(
        build(tmp_path / "after", "inbox"),
        activity_directory="your_facebook_activity",
        root_prefix="facebook-",
    )
    folders = ("inbox", "message_requests")
    first = list(first_export.message_records(folders=folders, counter=ContentKeyCounter()))
    second = list(second_export.message_records(folders=folders, counter=ContentKeyCounter()))

    assert [record.external_id for record in first] == [record.external_id for record in second]
    assert first[0].thread_key == second[0].thread_key == "chat_abc"
    assert first[0].thread_metadata["folder"] == "message_requests"
    assert second[0].thread_metadata["folder"] == "inbox"


def test_generation_overlap_collapses_to_one_copy(tmp_path: Path) -> None:
    """Two merged export generations land each shared message once.

    Chunk digests differ (the newer generation appended one message), so the
    per-key collapse must keep the richer chunk's copies instead of minting an
    ``occurrence`` id per generation.
    """

    older = tmp_path / "facebook-old"
    newer = tmp_path / "facebook-new"
    row = {"sender_name": "Bob", "timestamp_ms": 1000, "content": "hello"}
    extra = {"sender_name": "Bob", "timestamp_ms": 2000, "content": "again"}
    for root, messages in ((older, [row]), (newer, [row, extra])):
        thread_dir = root / "your_facebook_activity" / "messages" / "inbox" / "chat_abc"
        thread_dir.mkdir(parents=True)
        (thread_dir / "message_1.json").write_text(
            json.dumps({"title": "A chat", "participants": [], "messages": messages}),
            encoding="utf-8",
        )

    export = MetaExport(
        tmp_path, activity_directory="your_facebook_activity", root_prefix="facebook-"
    )
    records = list(export.message_records(folders=("inbox",), counter=ContentKeyCounter()))

    assert [str(record.data.get("content")) for record in records] == ["hello", "again"]
    assert all(record.external_id.endswith(":0") for record in records)


def test_meta_timestamp_conventions_are_aware_utc() -> None:
    """Messages use milliseconds while every other section uses seconds."""

    expected = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert timestamp_milliseconds(int(expected.timestamp() * 1000)) == expected
    assert timestamp_seconds(int(expected.timestamp())) == expected


def _write_chunk(
    path: Path,
    *,
    timestamp_ms: int,
    content: str,
    media: list[dict[str, str]],
) -> None:
    """Write one minimal Meta message chunk."""

    path.write_text(
        json.dumps(
            {
                "title": "A chat",
                "participants": [{"name": "Alex"}, {"name": "Bob"}],
                "messages": [
                    {
                        "sender_name": "Bob",
                        "timestamp_ms": timestamp_ms,
                        "content": content,
                        "photos": media,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
