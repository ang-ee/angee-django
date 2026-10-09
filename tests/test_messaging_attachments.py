"""Attachment selection composes the inbound Part tree without consumer policy."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db.models import Prefetch
from rebac import actor_context, system_context

from angee.messaging.backends import ParsedPart
from angee.messaging.testing.models import Message, Part
from tests.test_messaging import _ingest, _parsed, _storage_drive
from tests.test_messaging import channel as channel


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("prefetched", [False, True])
def test_attached_files_select_one_forward_level_and_count_each_skipped_part(
    channel: Any, tmp_path: Path, prefetched: bool,
) -> None:
    """Ancestors veto attachments; inline text containers do not veto their files."""

    def attached(name: str, **fields: Any) -> ParsedPart:
        return ParsedPart(
            name=name, content=name.encode(), **{"type": "text/plain", "disposition": "attachment", **fields},
        )

    body = ParsedPart(type="multipart/mixed", children=(
        ParsedPart(text="Body only"),
        attached("direct.txt"),
        attached("direct.txt"),
        attached("image.png", type="image/png"),
        attached("opaque.bin", type="application/octet-stream"),
        attached("inline.txt", disposition="inline"),
        # A forwarded bill: a mailer presents the document inline, with its filename and no Content-ID.
        attached("inline-bill.pdf", type="application/pdf", disposition="inline"),
        attached("inline-bill.xml", type="application/xml", disposition="inline"),
        # Decoration stays out: a referenced image, and an inline image with no filename.
        attached("logo.png", type="image/png", disposition="inline", cid="logo@example.com"),
        ParsedPart(type="image/png", disposition="inline", content=b"png"),
        attached("cid.txt", cid="image@example.com"),
        ParsedPart(cid="parent@example.com", children=(attached("cid-child.txt"),)),
        attached("inline-parent.txt", disposition="inline", children=(attached("inline-child.txt"),)),
        ParsedPart(role="signature", children=(attached("signature-child.txt"),)),
        attached("signature.txt", role="signature", disposition="inline"),
        attached("signature.asc", type=" Application/PGP-Signature ; name=signature.asc"),
        attached("signature.p7s", type="application/pkcs7-signature"),
        ParsedPart(type="application/x-pkcs7-signature", children=(attached("signed-child.txt"),)),
        attached("delivery.txt", type="message/delivery-status", disposition="inline"),
        attached("disposition.txt", type="message/disposition-notification"),
        ParsedPart(type="multipart/report", children=(attached("report-child.txt"),)),
        attached("forward.eml", type="message/rfc822", children=(
            ParsedPart(type="multipart/mixed", children=(
                attached("forwarded.txt"),
                attached("nested.eml", type=" MESSAGE/RFC822 ; name=nested.eml", children=(
                    attached("too-deep.txt"),
                )),
            )),
        )),
        ParsedPart(type="text/plain", disposition="attachment", name="empty.txt", content=b""),
    ))
    with system_context(reason="test messaging attachment storage"):
        _storage_drive(tmp_path, owner=channel.owner)
    assert _ingest([replace(_parsed("attachment-selection"), body=body)], channel=channel) == 1
    with system_context(reason="test messaging attachment read"):
        message = Message._base_manager.get(external_id="attachment-selection")
        if prefetched:
            message = Message._base_manager.prefetch_related(Prefetch(
                "parts", queryset=Part.objects.select_related("fragment", "file", "file__mime_type"),
            )).get(pk=message.pk)

    scope = message.parts.with_actor(channel.owner)
    selected = list(scope.attached_files().select_related("file"))
    skipped = scope.attached_file_skip_counts()
    assert Counter(part.name for part in selected) == Counter({
        "direct.txt": 2, "image.png": 1, "opaque.bin": 1, "forwarded.txt": 1,
        "inline-bill.pdf": 1, "inline-bill.xml": 1,
    })
    direct = [part for part in selected if part.name == "direct.txt"]
    assert direct[0].file_id == direct[1].file_id
    assert all(part.file.size_bytes > 0 for part in selected)
    assert skipped == {
        "signature": 5, "delivery_report": 3, "inline": 7, "forward_depth": 1,
        "forwarded_message": 2, "empty_file": 1,
    }
    assert len(selected) + sum(skipped.values()) == scope.attachments().count()
    assert scope.filter(name="direct.txt").attached_files().count() == 2
    assert scope.attached_files().filter(name="direct.txt").count() == 2
    assert scope.filter(name="signature.txt").attached_file_skip_counts() == {"signature": 1}
    assert scope.none().attached_file_skip_counts() == {}

    with actor_context(channel.owner):
        assert set(message.parts.attached_files().values_list("pk", flat=True)) == {part.pk for part in selected}


@pytest.mark.django_db(transaction=True)
def test_attached_files_preserve_actor_scope_even_with_prefetched_parts(channel: Any, tmp_path: Path) -> None:
    """An elevated relation cache cannot bypass the queryset's pinned actor."""

    with system_context(reason="test messaging attachment admission setup"):
        _storage_drive(tmp_path, owner=channel.owner)
        outsider = get_user_model().objects.create_user(username="attachment-outsider")
    parsed = replace(_parsed("attachment-admission"), body=ParsedPart(
        type="text/plain", disposition="attachment", name="note.txt", content=b"Attached note",
    ))
    assert _ingest([parsed], channel=channel) == 1
    with system_context(reason="test messaging attachment admission prefetch"):
        message = Message._base_manager.prefetch_related(Prefetch(
            "parts", queryset=Part.objects.select_related("fragment", "file", "file__mime_type"),
        )).get(external_id="attachment-admission")
    with actor_context(channel.owner):
        scope = message.parts.with_actor(outsider)
        assert list(scope.attached_files()) == []
        assert scope.attached_file_skip_counts() == {}
    with actor_context(outsider):
        scope = message.parts.with_actor(channel.owner)
        assert scope.attached_files().count() == 1
        assert scope.attached_file_skip_counts() == {}


@pytest.mark.django_db(transaction=True)
def test_attached_files_body_only_message_has_no_skips(channel: Any) -> None:
    """Only stored-file parts contribute to selection or skip counts."""

    assert _ingest([_parsed("attachment-body-only")], channel=channel) == 1
    with system_context(reason="test messaging body-only attachment read"):
        message = Message._base_manager.get(external_id="attachment-body-only")
    scope = message.parts.with_actor(channel.owner)
    assert list(scope.attached_files()) == []
    assert scope.attached_file_skip_counts() == {}


@pytest.mark.django_db(transaction=True)
def test_attached_files_remain_message_scoped_and_use_stored_mime_facts(channel: Any, tmp_path: Path) -> None:
    """Names do not veto ordinary files, and a report veto stays in its message."""

    with system_context(reason="test messaging attachment scopes"):
        _storage_drive(tmp_path, owner=channel.owner)
    report = ParsedPart(type="multipart/report", children=(ParsedPart(
        name="report.txt", type="text/plain", disposition="attachment", content=b"Report",
    ),))
    ordinary = ParsedPart(type="multipart/mixed", children=(
        ParsedPart(name="signature.asc", type="text/plain", disposition="attachment", content=b"Ordinary file"),
        ParsedPart(name="partial.bin", type="message/partial", disposition="attachment", content=b"Partial file"),
        ParsedPart(type="message/rfc822", disposition="attachment", name="unparsed.eml", content=b"Raw forward"),
    ))
    assert _ingest([
        replace(_parsed("attachment-report"), body=report),
        replace(_parsed("attachment-ordinary"), body=ordinary),
    ], channel=channel) == 2
    with system_context(reason="test messaging scoped attachment read"):
        messages = {row.external_id: row for row in Message._base_manager.all()}
    scope = messages["attachment-ordinary"].parts.with_actor(channel.owner)
    assert set(scope.attached_files().values_list("name", flat=True)) == {"signature.asc", "partial.bin"}
    assert scope.attached_file_skip_counts() == {"forwarded_message": 1}
    assert messages["attachment-report"].parts.with_actor(channel.owner).attached_file_skip_counts() == {
        "delivery_report": 1,
    }
    all_parts = Part.objects.with_actor(channel.owner)
    assert all_parts.attached_files().count() == 2
    assert all_parts.attached_file_skip_counts() == {"delivery_report": 1, "forwarded_message": 1}


@pytest.mark.django_db(transaction=True)
def test_attached_files_never_follow_foreign_parents_or_repeat_cycles(channel: Any, tmp_path: Path) -> None:
    """The shared ancestor owner keeps malformed edges finite and message-local."""

    with system_context(reason="test messaging malformed attachment storage"):
        _storage_drive(tmp_path, owner=channel.owner)
    attached = ParsedPart(type="text/plain", disposition="attachment", name="note.txt", content=b"Note")
    assert _ingest([
        replace(_parsed("attachment-local"), body=ParsedPart(type="multipart/mixed", children=(attached, attached))),
        replace(_parsed("attachment-foreign"), body=ParsedPart(type="multipart/report", children=(attached,))),
    ], channel=channel) == 2
    with system_context(reason="test messaging malformed attachment edges"):
        message = Message._base_manager.get(external_id="attachment-local")
        files = list(Part._base_manager.filter(message=message, file__isnull=False))
        foreign = Part._base_manager.get(message__external_id="attachment-foreign", type="multipart/report")
        Part.objects.filter(pk=files[0].pk).update(parent_id=foreign.pk)
        Part.objects.filter(pk=files[1].pk).update(parent_id=files[1].pk)
    scope = message.parts.with_actor(channel.owner)
    assert set(scope.attached_files().values_list("pk", flat=True)) == {part.pk for part in files}
    assert scope.attached_file_skip_counts() == {}


@pytest.mark.django_db(transaction=True)
def test_attached_files_queryset_is_limited_to_classified_ids(channel: Any, tmp_path: Path) -> None:
    """New file uses wait for classification instead of entering a saved selector."""

    with system_context(reason="test messaging attachment snapshot storage"):
        _storage_drive(tmp_path, owner=channel.owner)
    parsed = replace(_parsed("attachment-snapshot"), body=ParsedPart(
        type="text/plain", disposition="attachment", name="note.txt", content=b"Note",
    ))
    assert _ingest([parsed], channel=channel) == 1
    with system_context(reason="test messaging attachment snapshot message"):
        message = Message._base_manager.get(external_id="attachment-snapshot")
        original = Part._base_manager.get(message=message, file__isnull=False)
    scope = message.parts.with_actor(channel.owner)
    selected = scope.attached_files()
    with system_context(reason="test messaging attachment snapshot new uses"):
        added = Part.objects.create(message=message, file_id=original.file_id, disposition="attachment")
        Part.objects.create(message=message, file_id=original.file_id, disposition="attachment", role="signature")
    assert list(selected.values_list("pk", flat=True)) == [original.pk]
    assert set(scope.attached_files().values_list("pk", flat=True)) == {original.pk, added.pk}
    assert scope.attached_file_skip_counts() == {"signature": 1}
