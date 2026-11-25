"""One owner writes an outgoing message, and the recipient model keeps its own thread."""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart, ParsedRecipient
from angee.messaging.testing.models import Channel, Handle, Message, Recipient, Thread
from tests.conftest import make_integration


def _assert_recipients_on_their_messages_threads() -> None:
    rows = Recipient._base_manager.values_list("thread_id", "message__thread_id")
    assert rows.exists()
    assert all(thread_id == message_thread_id for thread_id, message_thread_id in rows)


def test_a_recipient_row_is_inserted_on_its_messages_thread(db: None) -> None:
    """The model sets the row's thread from its message, refuses another, and loads no message it was given."""

    del db
    loaded = f'"{Message._meta.db_table}"."thread_id"'
    with system_context(reason="tests.messaging.recipient.thread"):
        thread, other = Thread._base_manager.create(), Thread._base_manager.create()
        message = Message._base_manager.create(thread=thread)
        handle = Handle._base_manager.create(platform="email", value="row@example.test")
        elsewhere = Handle._base_manager.create(platform="email", value="elsewhere@example.test")
        with CaptureQueriesContext(connection) as given:
            row = Recipient._base_manager.create(message=message, handle=handle, role="to")
        with CaptureQueriesContext(connection) as by_id:
            Recipient._base_manager.create(message_id=message.pk, handle=handle, role="cc")
        assert row.thread_id == thread.pk
        # REBAC probes the message's key for its relation either way; only a bare id loads the row.
        assert [query for query in given if loaded in query["sql"]] == []
        assert len([query for query in by_id if loaded in query["sql"]]) == 1
        same = Recipient._base_manager.create(message=message, thread=thread, handle=handle, role="bcc")
        assert same.thread_id == thread.pk
        with pytest.raises(ValueError, match="message's thread"):
            Recipient._base_manager.create(message=message, thread=other, handle=elsewhere, role="to")
    assert Recipient._base_manager.filter(message=message).count() == 3


def test_ingest_writes_each_recipient_on_its_messages_thread(db: None) -> None:
    """The ingest writer passes its message; every row it writes is on that message's thread."""

    del db
    channel: Any = make_integration("recipient-ingest", model=Channel, backend_class="manual")
    with system_context(reason="tests.messaging.recipient.ingest"):
        Message.objects.ingest(
            [
                ParsedMessage(
                    external_id=f"<ingest-{index}@example.test>",
                    platform="email",
                    subject="Shared subject",
                    sender=ParsedHandle(platform="email", value="from@example.test"),
                    recipients=(
                        ParsedRecipient(handle=ParsedHandle(platform="email", value="to@example.test")),
                        ParsedRecipient(handle=ParsedHandle(platform="email", value="to@example.test")),
                        ParsedRecipient(handle=ParsedHandle(platform="email", value="cc@example.test"), role="cc"),
                    ),
                    body=ParsedPart(text=f"Body {index}"),
                )
                for index in range(2)
            ],
            channel=channel,
        )
    assert Recipient._base_manager.count() == 4
    _assert_recipients_on_their_messages_threads()
