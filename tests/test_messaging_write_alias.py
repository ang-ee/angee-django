"""D31 messaging writes retain their database through post-admission work."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.db import router, transaction
from rebac import system_context

from angee.graphql.publishing import mute_changes
from angee.messaging import delivery
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart
from angee.messaging.email import AnymailEmailChannelBackend
from tests.messaging_models import Fragment, Message, Part, Thread, ThreadFollower, ThreadNotification
from tests.test_messaging import MessageEdge, MessageStar
from tests.test_messaging import channel as channel
from tests.test_messaging import messaging_tables as messaging_tables
from tests.test_messaging_graphql import messaging_schema
from tests.test_transitions import TransitionRouter


@pytest.fixture
def messaging_alias(
    messaging_tables: None, database_alias: Callable[[str], AbstractContextManager[str]]
) -> Iterator[str]:
    """Expose the messaging schema through the shared connection factory."""

    with database_alias("messaging_writer") as alias:
        yield alias


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("prefetched", [False, True])
def test_email_render_binds_uncached_sender_on_explicit_alias(
    channel: Any, messaging_alias: str, monkeypatch: pytest.MonkeyPatch, prefetched: bool
) -> None:
    """Direct rendering binds the sender before the legacy rendering hook reads it."""

    with system_context(reason="messaging email routing setup"), mute_changes():
        landed = Message.objects.ingest(
            [
                ParsedMessage(
                    external_id="routed-email",
                    platform="email",
                    sender=ParsedHandle(platform="email", value="sender@example.com"),
                    body=ParsedPart(type="text/plain", text="Body"),
                )
            ],
            channel=channel,
        )[0]
        queryset = Message._base_manager.all()
        if prefetched:
            queryset = queryset.prefetch_related("parts__fragment")
        message = queryset.get(pk=landed.pk)
        if prefetched:
            for part in message.parts.all():
                if part.fragment_id is not None:
                    part.fragment.text = "Stale cache from another alias"
    routing = TransitionRouter("incorrect_writer")
    with monkeypatch.context() as patch, system_context(reason="messaging email routing"):
        patch.setattr(router, "routers", [routing])
        email = AnymailEmailChannelBackend(channel).email_message(message, using=messaging_alias)
        assert email.from_email == "sender@example.com"
        assert email.body == "Body"
        assert message.sender._state.db == messaging_alias
    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("selection", ["instance", "manager", "explicit"])
def test_post_edit_receipt_and_star_share_the_selected_alias(
    messaging_tables: None, messaging_alias: str, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    """Body fragments, fanout, receipts, stars and recount stay on the writer."""

    del messaging_tables
    with system_context(reason="messaging routing setup"), mute_changes():
        user = get_user_model().objects.create_user(username=f"routing-{selection}")
        recipient = get_user_model().objects.create_user(username=f"recipient-{selection}")
        thread = Thread.objects.db_manager(messaging_alias).create(platform="other")
        ThreadFollower.objects.db_manager(messaging_alias).create(thread_id=thread.pk, user_id=user.pk)
    manager = Message.objects
    kwargs: dict[str, Any] = {}
    if selection != "instance":
        thread._state.db = "incorrect_instance"
        manager = manager.db_manager(messaging_alias if selection == "manager" else "incorrect_manager")
    if selection == "explicit":
        kwargs["using"] = messaging_alias
    routing = TransitionRouter("incorrect_writer")
    with monkeypatch.context() as patch, system_context(reason="messaging routing exercise"), mute_changes():
        patch.setattr(router, "routers", [routing])
        posted = manager.post_to_thread(
            thread, body="Original", owner_id=user.pk, recipient_user_ids=(recipient.pk,), **kwargs
        )
        edited = Message.objects.update_content(posted, body="Edited", owner_id=user.pk)
        assert edited.status == Message.MessageStatus.EDITED
        assert len(edited.edit_history) == 1
        assert (
            Part.objects.using(messaging_alias).select_related("fragment").get(message_id=posted.pk).fragment.text
            == "Edited"
        )
        assert ThreadNotification.objects.using(messaging_alias).get(message_id=posted.pk).user_id == recipient.pk
        assert ThreadFollower.objects.using(messaging_alias).get(thread_id=thread.pk).last_read_message_id == posted.pk
        assert MessageStar.objects.set_starred(edited, user=user, starred=True)
        assert MessageStar.objects.set_starred(edited, user=user, starred=False) is False
        thread._state.db = messaging_alias
        Message.objects.unlink_from_thread(edited, thread=thread)
        assert not Thread.objects.using(messaging_alias).filter(pk=thread.pk).exists()
    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
def test_ingest_preserves_alias_through_resolve_replay_rebuild_and_quote_edges(
    channel: Any, messaging_alias: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise first insert, replay, content edit, recursive parts and quote graph."""

    routing = TransitionRouter("incorrect_writer")
    first = ParsedMessage(
        external_id="routed-first",
        platform="email",
        subject="Routed",
        sender=None,
        body=ParsedPart(type="text/plain", text="Shared body"),
    )
    second = replace(first, external_id="routed-second")
    with monkeypatch.context() as patch, system_context(reason="messaging ingest routing"), mute_changes():
        patch.setattr(router, "routers", [routing])
        landed = Message.objects.db_manager(messaging_alias).ingest([first, second], channel=channel, historical=True)
        replay = Message.objects.db_manager(messaging_alias).ingest([first], channel=channel, historical=True)
        assert replay[0].pk == landed[0].pk
        edited = replace(
            first,
            body=ParsedPart(type="multipart/mixed", children=(ParsedPart(type="text/plain", text="Edited body"),)),
        )
        Message.objects.db_manager(messaging_alias).ingest([edited], channel=channel, historical=True)
        row = Message.objects.using(messaging_alias).get(pk=landed[0].pk)
        assert len(row.edit_history) == 1
        assert Part.objects.using(messaging_alias).filter(message_id=row.pk).count() == 3
        assert MessageEdge.objects.using(messaging_alias).count() == 1
        assert Fragment.objects.using(messaging_alias).filter(text="Edited body").exists()
    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
def test_queued_delivery_retains_alias_in_commit_payload_and_status_write(
    channel: Any, messaging_alias: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The callback transports using and the worker claim/finalization consume it."""

    with system_context(reason="messaging delivery routing setup"), mute_changes():
        message = Message.objects.db_manager(messaging_alias).create(
            channel_id=channel.pk,
            platform="email",
            direction="outbound",
            status="failed",
            external_id="routed-delivery",
        )
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(delivery, "enqueue_task", lambda _name, *, kwargs: payloads.append(kwargs))
    routing = TransitionRouter("incorrect_writer")
    channels = Mock()
    channels.db_manager.return_value = channels
    channels.sudo.return_value = channels
    channels.get.return_value = SimpleNamespace(backend=SimpleNamespace(deliver=lambda message: True))
    get_model = delivery.apps.get_model

    def delivery_model(app_label: str, model_name: str | None = None) -> Any:
        if (app_label, model_name) == ("messaging", "Channel"):
            return SimpleNamespace(objects=channels)
        return get_model(app_label, model_name)

    monkeypatch.setattr(delivery.apps, "get_model", delivery_model)
    monkeypatch.setattr(delivery, "task_lock", lambda _key: nullcontext(True))
    with monkeypatch.context() as patch, system_context(reason="messaging delivery routing"), mute_changes():
        patch.setattr(router, "routers", [routing])
        with transaction.atomic(using=messaging_alias):
            assert delivery.queue_message_delivery(message)
            assert payloads == []
        assert payloads == [
            {
                "model_label": message._meta.label_lower,
                "pk": message.pk,
                "external_id": message.external_id,
                "using": messaging_alias,
            }
        ]
        outcome = delivery.run_message_delivery(**payloads[0])
        assert outcome["delivered"] is True
        channels.db_manager.assert_called_once_with(messaging_alias)
        message.refresh_from_db(using=messaging_alias)
        assert message.status == Message.MessageStatus.SENT
        assert message.sent_at is not None
    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
def test_schema_star_selects_writer_before_loading_message(
    messaging_alias: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation admission, star insertion and removal share the selected writer."""

    with system_context(reason="messaging schema routing setup"), mute_changes():
        user = get_user_model().objects.create_user(username="schema-routing")
        message = Message.objects.db_manager(messaging_alias).create(
            platform="email", direction="internal", status="sent", preview="Star through the schema"
        )
    routing = TransitionRouter(messaging_alias)
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))
    with monkeypatch.context() as patch, system_context(reason="messaging schema routing"), mute_changes():
        patch.setattr(router, "routers", [routing])
        mutation = messaging_schema.MessagingMutation()
        result = mutation.set_inbox_message_starred(info, str(message.sqid), starred=True)
        assert result.pk == message.pk
        assert result._state.db == messaging_alias
        assert MessageStar.objects.using(messaging_alias).filter(message_id=message.pk, user_id=user.pk).exists()
        mutation.set_inbox_message_starred(info, str(message.sqid), starred=False)
        assert not MessageStar.objects.using(messaging_alias).filter(message_id=message.pk, user_id=user.pk).exists()
    assert routing.writes == [None, None]
