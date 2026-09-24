"""Message-scoped Part navigation shares MIME reading order and joined reads."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.db import connection
from django.db.models import Prefetch
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from angee.graphql.publishing import mute_changes
from tests.conftest import File
from tests.messaging_models import Fragment, Message, Part
from tests.test_messaging import _storage_drive
from tests.test_messaging import messaging_tables as messaging_tables


@pytest.fixture
def part_tree(messaging_tables: None) -> tuple[Message, dict[str, Part]]:
    """Persist mixed, alternative, related and forwarded MIME branches."""

    del messaging_tables
    with system_context(reason="messaging part tree setup"), mute_changes():
        message = Message.objects.create(platform="email")
        parts: dict[str, Part] = {}
        for name, parent, position, media, text in (
            ("mixed", None, 0, "multipart/mixed", None),
            ("alternative", "mixed", 0, "multipart/alternative", None),
            ("plain", "alternative", 0, "text/plain", "Plain body"),
            ("related", "alternative", 1, "multipart/related", None),
            ("html", "related", 0, "text/html", "<p>HTML body</p>"),
            ("image", "related", 1, "image/png", None),
            ("forwarded", "alternative", 2, "message/rfc822", None),
            ("forwarded_plain", "forwarded", 0, "text/plain", "Forwarded body"),
            ("empty_plain", "mixed", 1, "text/plain", "  "),
            ("attachment", "mixed", 2, "application/pdf", None),
            ("delivery", None, 1, "message/delivery-status", None),
            ("header", "delivery", 0, "text/plain", "Delivery detail"),
        ):
            parts[name] = Part.objects.create(
                message=message,
                parent=parts[parent] if parent is not None else None,
                position=position,
                type=media,
                role="header" if name == "header" else "body",
                disposition="attachment" if name == "attachment" else "inline",
                fragment=Fragment.objects.upsert(text=text) if text is not None else None,
            )
    return message, parts


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("prefetched", [False, True])
def test_part_navigation_and_mime_predicates(part_tree: tuple[Message, dict[str, Part]], prefetched: bool) -> None:
    """Navigation returns rows; MIME and text selection remain caller predicates."""

    message, parts = part_tree
    if prefetched:
        with system_context(reason="messaging part tree prefetch"):
            message = Message._base_manager.prefetch_related(
                Prefetch("parts", queryset=Part._base_manager.select_related("fragment").order_by("-pk"))
            ).get(pk=message.pk)
    expected = [parts[name] for name in parts]
    with CaptureQueriesContext(connection) as queries:
        assert Part.objects.reading_order_for_message(message) == expected
        assert Part.objects.children_for_message(message, None) == [parts["mixed"], parts["delivery"]]
        assert Part.objects.children_for_message(message, parts["alternative"].pk) == [
            parts["plain"],
            parts["related"],
            parts["forwarded"],
        ]
        ancestors = Part.objects.ancestors_for_message(message, parts["html"].pk)
        assert ancestors == [parts["related"], parts["alternative"], parts["mixed"]]
        assert ancestors[0].pk == parts["html"].parent_id
        assert any(part.type == "multipart/alternative" for part in ancestors)
        assert Part.objects.ancestors_for_message(message, parts["mixed"].pk) == []
        descendants = Part.objects.descendants_for_message(message, parts["alternative"].pk)
        assert descendants == [
            parts["plain"],
            parts["related"],
            parts["html"],
            parts["image"],
            parts["forwarded"],
            parts["forwarded_plain"],
        ]
        assert any(part.type == "text/plain" and part.fragment.text.strip() for part in descendants)
        assert not any(
            part.type == "text/plain" and part.fragment.text.strip()
            for part in Part.objects.descendants_for_message(message, parts["related"].pk)
        )
        assert Part.objects.children_for_message(message, parts["plain"].pk) == []
        assert Part.objects.descendants_for_message(message, parts["plain"].pk) == []
    assert len(queries) == (0 if prefetched else 9)


@pytest.mark.django_db(transaction=True)
def test_part_boundaries_are_included_and_prune_only_their_direction(
    part_tree: tuple[Message, dict[str, Part]],
) -> None:
    """A caller can isolate forwarded content without encoding MIME policy in Part."""

    message, parts = part_tree

    def boundary(part: Part) -> bool:
        return part.type == "message/rfc822"

    assert Part.objects.ancestors_for_message(message, parts["forwarded_plain"].pk, stop_at=boundary) == [
        parts["forwarded"]
    ]
    assert Part.objects.ancestors_for_message(message, parts["forwarded_plain"].pk) == [
        parts["forwarded"],
        parts["alternative"],
        parts["mixed"],
    ]
    assert Part.objects.descendants_for_message(message, parts["alternative"].pk, stop_at=boundary) == [
        parts["plain"],
        parts["related"],
        parts["html"],
        parts["image"],
        parts["forwarded"],
    ]
    assert Part.objects.descendants_for_message(message, parts["forwarded"].pk, stop_at=boundary) == [
        parts["forwarded_plain"]
    ]
    assert Part.objects.ancestors_for_message(message, parts["html"].pk, stop_at=lambda part: True) == [
        parts["related"]
    ]
    assert Part.objects.descendants_for_message(message, parts["alternative"].pk, stop_at=lambda part: True) == [
        parts["plain"],
        parts["related"],
        parts["forwarded"],
    ]


@pytest.mark.django_db(transaction=True)
def test_part_queries_never_follow_cross_message_parent_edges(
    part_tree: tuple[Message, dict[str, Part]],
) -> None:
    """Malformed foreign parents cannot admit rows from another message."""

    message, parts = part_tree
    with system_context(reason="messaging cross-message part setup"), mute_changes():
        other = Message.objects.create(platform="email")
        foreign = Part.objects.create(message=other, parent=parts["plain"], type="text/plain")
        Part.objects.filter(pk=parts["mixed"].pk).update(parent_id=foreign.pk)
    assert Part.objects.ancestors_for_message(message, parts["plain"].pk) == [parts["alternative"], parts["mixed"]]
    assert Part.objects.children_for_message(message, parts["plain"].pk) == []
    assert Part.objects.descendants_for_message(message, parts["plain"].pk) == []
    for part_id in (foreign.pk, max(foreign.pk, *(part.pk for part in parts.values())) + 1):
        assert Part.objects.children_for_message(message, part_id) == []
        assert Part.objects.ancestors_for_message(message, part_id) == []
        assert Part.objects.descendants_for_message(message, part_id) == []
    ordered = Part.objects.reading_order_for_message(message)
    assert ordered[:2] == [parts["delivery"], parts["header"]]
    assert {part.pk for part in ordered} == {part.pk for part in parts.values()}
    assert len(ordered) == len(parts)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("prefetched", [False, True])
def test_part_ties_cycles_and_orphan_components_have_deterministic_order(
    messaging_tables: None, prefetched: bool
) -> None:
    """Sibling sqids break ties, and rootless cycles remain finite and visible."""

    del messaging_tables
    with system_context(reason="messaging cyclic part setup"), mute_changes():
        message = Message.objects.create(platform="email")
        root = Part.objects.create(message=message, position=9)
        left = Part.objects.create(message=message, parent=root, position=0)
        right = Part.objects.create(message=message, parent=root, position=0)
        first = Part.objects.create(message=message, position=0)
        second = Part.objects.create(message=message, parent=first, position=0)
        Part.objects.filter(pk=first.pk).update(parent_id=second.pk)
        first.parent_id = second.pk
        alone = Part.objects.create(message=message, position=0)
        Part.objects.filter(pk=alone.pk).update(parent_id=alone.pk)
        alone.parent_id = alone.pk
        if prefetched:
            message = Message._base_manager.prefetch_related(
                Prefetch("parts", queryset=Part._base_manager.order_by("-pk"))
            ).get(pk=message.pk)
    assert Part.objects.reading_order_for_message(message) == [
        root,
        *sorted((left, right), key=lambda part: str(part.sqid)),
        second,
        first,
        alone,
    ]
    assert Part.objects.children_for_message(message, root.pk) == sorted((left, right), key=lambda part: str(part.sqid))
    assert Part.objects.ancestors_for_message(message, first.pk) == [second]
    assert Part.objects.descendants_for_message(message, first.pk) == [second]
    assert Part.objects.ancestors_for_message(message, alone.pk) == []
    assert Part.objects.descendants_for_message(message, alone.pk) == []


@pytest.mark.django_db(transaction=True)
def test_empty_message_and_missing_parent_in_native_prefetch(messaging_tables: None) -> None:
    """Empty forests and incomplete native prefetches do not invent ancestors."""

    del messaging_tables
    with system_context(reason="messaging incomplete part setup"), mute_changes():
        message = Message.objects.create(platform="email")
        assert Part.objects.reading_order_for_message(message) == []
        assert Part.objects.children_for_message(message, None) == []
        assert Part.objects.ancestors_for_message(message, 1) == []
        assert Part.objects.descendants_for_message(message, 1) == []
        parent = Part.objects.create(message=message)
        orphan = Part.objects.create(message=message, parent=parent)
        leaf = Part.objects.create(message=message, parent=orphan)
        message = Message._base_manager.prefetch_related(
            Prefetch("parts", queryset=Part._base_manager.exclude(pk=parent.pk))
        ).get(pk=message.pk)
    with CaptureQueriesContext(connection) as queries:
        assert Part.objects.reading_order_for_message(message) == [orphan, leaf]
        assert Part.objects.ancestors_for_message(message, leaf.pk) == [orphan]
        assert Part.objects.descendants_for_message(message, orphan.pk) == [leaf]
        assert Part.objects.children_for_message(message, parent.pk) == []
    assert len(queries) == 0


@pytest.mark.django_db(transaction=True)
def test_part_database_load_eagerly_fetches_content_without_creating_a_cache(
    part_tree: tuple[Message, dict[str, Part]], tmp_path: Path
) -> None:
    """Fragments and file MIME rows use the same one query as the tree load."""

    message, parts = part_tree
    with system_context(reason="messaging part file setup"), mute_changes():
        _storage_drive(tmp_path, owner=None)
        file = File.objects.ingest_bytes(b"Attached text", filename="note.txt")
        Part.objects.filter(pk=parts["attachment"].pk).update(file_id=file.pk)
    with CaptureQueriesContext(connection) as queries:
        ordered = Part.objects.reading_order_for_message(message)
        for part in ordered:
            if part.fragment_id is not None:
                assert isinstance(part.fragment.text, str)
            if part.file_id is not None:
                assert part.file.filename == "note.txt"
                assert part.file.mime_type.mime_type == "text/plain"
    assert len(queries) == 1
    assert "parts" not in getattr(message, "_prefetched_objects_cache", {})


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "cache_state",
    ["complete", "deferred_parent", "uncached_fragment"],
)
def test_prefetched_tree_reloads_incomplete_fields_and_content(
    part_tree: tuple[Message, dict[str, Part]],
    tmp_path: Path,
    cache_state: str,
) -> None:
    """Only complete rows and content caches can bypass the joined read."""

    message, parts = part_tree
    with system_context(reason="messaging cached content setup"), mute_changes():
        _storage_drive(tmp_path, owner=None)
        file = File.objects.ingest_bytes(b"Attached text", filename="note.txt")
        Part.objects.filter(pk=parts["attachment"].pk).update(file_id=file.pk)
        queryset = Part._base_manager.select_related("fragment", "file", "file__mime_type")
        if cache_state == "deferred_parent":
            queryset = queryset.defer("parent_id")
        message = Message._base_manager.prefetch_related(Prefetch("parts", queryset=queryset)).get(pk=message.pk)
    cached = {part.pk: part for part in message.parts.all()}
    plain = cached[parts["plain"].pk]
    attachment = cached[parts["attachment"].pk]
    if cache_state == "uncached_fragment":
        del plain._state.fields_cache["fragment"]
    with CaptureQueriesContext(connection) as queries:
        ordered = Part.objects.reading_order_for_message(message)
        assert ordered == list(parts.values())
        loaded = {part.pk: part for part in ordered}
        assert loaded[plain.pk].fragment.text == "Plain body"
        assert loaded[attachment.pk].file.filename == "note.txt"
        assert loaded[attachment.pk].file.mime_type.mime_type == "text/plain"
    assert len(queries) == (0 if cache_state == "complete" else 1)
