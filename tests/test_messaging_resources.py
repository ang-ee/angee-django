"""Resource rows write chatter through the threaded record's verbs, as their author."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rebac import system_context

from angee.messaging.testing.models import ActivityType, Message, ThreadActivity
from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import Resource
from tests.chatterdemo.models import ChatterDoc
from tests.conftest import make_addon
from tests.test_messaging import ThreadedTicket

User = get_user_model()
USERS = f"010_{User._meta.label_lower}.yaml"


def _addon(tmp_path: Path, files: dict[str, str]):
    """Write one install-tier addon whose files load in the given order."""

    for name, body in files.items():
        (tmp_path / name).write_text(body)
    return make_addon(
        name="tests.chatter_seed", label="chatter_seed", path=tmp_path, resources={"install": list(files)},
    )


def _load(addon):
    return Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])


CHATTER = {
    USERS: "- _xref: author\n  username: chatter-author\n  email: chatter-author@example.com\n  password: \"!\"\n",
    "020_messaging.threadedticket.yaml": "- _xref: ticket\n  title: Seeded ticket\n",
    "030_messaging.message.yaml": (
        "- _xref: question\n  record: chatter_seed.ticket\n  author: chatter_seed.author\n"
        "  body: Which day does it break?\n  client_creation_key: seed-question\n"
        "- _xref: reply\n  record: chatter_seed.ticket\n  author: chatter_seed.author\n"
        "  body: Mondays, after the export.\n  parent: chatter_seed.question\n  edited: true\n"
        "- _xref: note\n  record: chatter_seed.ticket\n  author: chatter_seed.author\n"
        "  body: Called the requester.\n  kind: note\n"
    ),
    "040_messaging.threadactivity.yaml": (
        "- _xref: call\n  record: chatter_seed.ticket\n  author: chatter_seed.author\n"
        "  activity_type: call\n  due_date: 2026-09-01\n  note: Walked through the export.\n"
    ),
}


@pytest.mark.django_db(transaction=True)
def test_rows_post_comments_replies_notes_and_activities_as_their_author(
    composed_tables: None, tmp_path: Path,
) -> None:
    del composed_tables
    with system_context(reason="test.chatter_resources.catalogue"):
        ActivityType.objects.create(key="call", name="Call")
    addon = _addon(tmp_path, CHATTER)

    result = _load(addon)

    assert result.created == 6
    with system_context(reason="test.chatter_resources.readback"):
        author = User.objects.get(username="chatter-author")
        ticket = ThreadedTicket.objects.get(title="Seeded ticket")
        thread = ticket.message_thread(create=False)
        question = Message.objects.get(client_creation_key="seed-question")
        reply = Message.objects.get(parent=question)
        note = Message.objects.get(thread=thread, subtype__key="note")
        activity = ThreadActivity.objects.get(thread=thread)
    assert {question.thread_id, reply.thread_id, note.thread_id} == {thread.pk}
    assert {question.created_by_id, reply.created_by_id, note.created_by_id} == {author.pk}
    assert question.preview == "Which day does it break?"
    assert len(reply.edit_history) == 1 and not question.edit_history
    assert (activity.due_date, activity.note) == (date(2026, 9, 1), "Walked through the export.")
    assert activity.status == "done"
    with system_context(reason="test.chatter_resources.ledger"):
        assert Resource.objects.get(xref="reply").target_instance().pk == reply.pk

    replay = _load(addon)

    assert (replay.created, replay.updated, replay.skipped) == (0, 0, 6)
    with system_context(reason="test.chatter_resources.replay"):
        assert Message.objects.filter(thread=thread, created_by=author).count() == 3
        assert ThreadActivity.objects.filter(thread=thread).count() == 1


@pytest.mark.django_db(transaction=True)
def test_a_changed_chatter_row_is_refused_rather_than_reposted(composed_tables: None, tmp_path: Path) -> None:
    del composed_tables
    with system_context(reason="test.chatter_resources.catalogue"):
        ActivityType.objects.create(key="call", name="Call")
    files = dict(CHATTER)
    addon = _addon(tmp_path, files)
    _load(addon)
    (tmp_path / "030_messaging.message.yaml").write_text(
        files["030_messaging.message.yaml"].replace("Which day does it break?", "Which hour does it break?")
    )

    with pytest.raises((ResourceLoadError, ValidationError), match="append-only"):
        _load(addon)

    with system_context(reason="test.chatter_resources.unchanged"):
        assert Message.objects.get(client_creation_key="seed-question").preview == "Which day does it break?"


@pytest.mark.django_db(transaction=True)
def test_the_record_owner_refuses_an_author_without_post_access(composed_tables: None, tmp_path: Path) -> None:
    del composed_tables
    addon = _addon(tmp_path, {
        USERS: (
            "- _xref: author\n  username: chatter-outsider\n  email: chatter-outsider@example.com\n"
            "  password: \"!\"\n"
        ),
        "020_chatterdemo.chatterdoc.yaml": "- _xref: doc\n  title: Owned document\n",
        "030_messaging.message.yaml": (
            "- _xref: comment\n  record: chatter_seed.doc\n  author: chatter_seed.author\n  body: Not mine to post.\n"
        ),
    })

    with pytest.raises(ResourceLoadError, match="requires 'post' access"):
        _load(addon)

    with system_context(reason="test.chatter_resources.refused"):
        doc = ChatterDoc.objects.filter(title="Owned document").first()
        assert not Message.objects.filter(created_by__username="chatter-outsider").exists()
        assert doc is None or not Message.objects.filter(
            thread__attachments__object_id=str(doc.pk), subtype__key="comment",
        ).exists()
