"""PostgreSQL: composes racing on one claim, one blank handle, or the same handles in other orders or spellings."""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from email.utils import parseaddr
from typing import Any

import pytest
from django.db import connection, transaction
from django.test import override_settings
from rebac import actor_context, system_context, to_subject_ref

from angee.messaging.testing.models import Channel, Handle, Message, Recipient, Thread
from angee.parties.managers import HandleManager
from angee.provenance.context import changeset_model
from tests.conftest import make_integration
from tests.lock_waits import submit, wait_for_lock
from tests.messaging_campaign import make_user

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]

TRANSPORT = {"transport": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
"""Delivery configured: the test mailer is the transport mailer messaging sends through."""


def test_a_thread_is_opened_outside_any_transaction() -> None:
    """``open_thread`` takes its own transaction for the identity lock it reads under."""

    channel: Any = make_integration("compose-open", model=Channel, backend_class="email")
    assert not connection.in_atomic_block
    with system_context(reason="tests.messaging.open_thread"):
        opened = Thread.objects.open_thread(
            platform="email", channel=channel, message_external_id="open@example.test", subject="Open",
            group_by_subject=False,
        )
    assert (opened.external_id, opened.display_title) == ("msg:open@example.test", "Open")


@override_settings(MAILERS=TRANSPORT)
def test_a_second_keyed_compose_waits_on_the_first_claim_and_replays_its_message() -> None:
    """The contender has no earlier lock to wait on: it parks on the claim and then returns the first's message."""

    channel: Any = make_integration(
        "compose-race", model=Channel, backend_class="email", config={"from_email": "Desk <desk@example.test>"},
    )
    owner = channel.owner

    def compose() -> Any:
        return Message.objects.compose(
            channel=channel, to=["reader@example.test"], subject="Once", text="Once.", actor=owner,
            idempotency_key="k",
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            first = compose()
            contender, pid = submit(pool, compose)
            wait_for_lock(pid, contender)
        second = contender.result(timeout=10)
    assert first.pk == second.pk
    assert Message._base_manager.filter(direction="outbound").count() == 1
    claims = changeset_model()._base_manager.filter(scope=f"messaging.compose:{to_subject_ref(owner)}", key="k")
    assert claims.count() == 1


@override_settings(MAILERS=TRANSPORT)
def test_a_typed_name_waits_on_a_fill_of_the_blank_handle_and_keeps_what_it_wrote() -> None:
    """A name a person types only fills a blank handle, even when another transaction fills it first."""

    channel: Any = make_integration(
        "compose-fill", model=Channel, backend_class="email", config={"from_email": "Desk <desk@example.test>"},
    )
    with system_context(reason="tests.messaging.compose.fill_race"):
        blank = Handle.objects.upsert(platform="email", value="contact@example.test")
    assert blank.display_name == ""

    def compose() -> Any:
        return Message.objects.compose(
            channel=channel, to=["Typed Name <contact@example.test>"], text="Hello.", actor=channel.owner,
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            with system_context(reason="tests.messaging.compose.fill_race.sync"):
                Handle.objects.upsert(platform="email", value="contact@example.test", display_name="Directory Name")
            contender, pid = submit(pool, compose)
            wait_for_lock(pid, contender)
        contender.result(timeout=10)
    assert Handle._base_manager.get(pk=blank.pk).display_name == "Directory Name"


def _compose_both(monkeypatch: pytest.MonkeyPatch, channel: Any, one: list[str], two: list[str]) -> list[Any]:
    """Run two composes at once, each paused after its first recipient's handle until the other has one.

    Taken in different orders, the two then wait on each other's handle and PostgreSQL aborts one of them.
    """

    upsert = HandleManager.upsert
    named = {Handle.spelling_key_of("email", parseaddr(entry)[1]) for entry in (*one, *two)}
    first: dict[int, threading.Event] = {}
    arrived = (threading.Event(), threading.Event())

    def pausing(self: Any, **fields: Any) -> Any:
        handle = upsert(self, **fields)
        recipient = Handle.spelling_key_of("email", fields["value"]) in named
        mine = first.pop(threading.get_ident(), None) if recipient else None
        if mine is not None:
            mine.set()
            next(event for event in arrived if event is not mine).wait(timeout=3)
        return handle

    monkeypatch.setattr(HandleManager, "upsert", pausing)

    def compose(event: threading.Event, to: list[str]) -> Callable[[], Any]:
        def run() -> Any:
            first[threading.get_ident()] = event
            # Its own text: a body two composes share is one content-addressed row, which the second would wait on.
            return Message.objects.compose(channel=channel, to=to, text=f"To {to}.", actor=channel.owner)
        return run

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [submit(pool, compose(event, to))[0] for event, to in zip(arrived, (one, two), strict=True)]
        return [future.result(timeout=30) for future in futures]


def _recipient_keys(messages: list[Any]) -> list[list[str]]:
    """Return each message's recipients' spelling keys in the order its compose named them."""

    rows = Recipient._base_manager.order_by("pk")
    return [list(rows.filter(message=message).values_list("handle__spelling_key", flat=True)) for message in messages]


@override_settings(MAILERS=TRANSPORT)
def test_two_composes_naming_the_same_addresses_in_opposite_orders_take_their_handles_in_one_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Taken in the order each compose names them, the two handles deadlock.

    The order is the handles' spelling key, so it holds between two composes; it cannot order a
    compose against another writer that takes the same handles.
    """

    channel: Any = make_integration(
        "compose-order", model=Channel, backend_class="email", config={"from_email": "Desk <desk@example.test>"},
    )
    Message.objects.compose(channel=channel, to=["warm@example.test"], text="Warm.", actor=channel.owner)
    messages = _compose_both(
        monkeypatch, channel, ["Zed <z@example.test>", "Ann <y@example.test>"],
        ["Ann <y@example.test>", "Zed <z@example.test>"],
    )
    # The recipient rows keep the order each compose named them in.
    assert _recipient_keys(messages) == [["z@example.test", "y@example.test"], ["y@example.test", "z@example.test"]]
    assert Handle._base_manager.filter(value__in=("y@example.test", "z@example.test")).count() == 2


@override_settings(MAILERS=TRANSPORT)
def test_two_composes_naming_one_address_in_two_letter_cases_take_their_handles_in_one_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As typed, ``Zed@`` sorts before ``alice@`` and ``zed@`` after it; their spelling key sorts both after it."""

    channel: Any = make_integration(
        "compose-spellings", model=Channel, backend_class="email", config={"from_email": "Desk <desk@example.test>"},
    )
    Message.objects.compose(channel=channel, to=["warm@example.test"], text="Warm.", actor=channel.owner)
    messages = _compose_both(
        monkeypatch, channel, ["Zed@example.test", "alice@example.test"], ["zed@example.test", "alice@example.test"],
    )
    assert _recipient_keys(messages) == [["zed@example.test", "alice@example.test"]] * 2
    assert Handle._base_manager.filter(spelling_key__in=("alice@example.test", "zed@example.test")).count() == 2


@override_settings(MAILERS=TRANSPORT, DEFAULT_FROM_EMAIL="Angee <noreply@example.test>")
def test_two_system_mails_naming_the_same_addresses_in_opposite_orders_take_their_handles_in_one_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """System work lands its handles through the same ordered writer, under the non-user attribution."""

    from tests.test_messaging_system_mail import _install

    system = _install()
    Message.objects.compose_as_system(reason="tests.system_mail.warm", to=["warm@example.test"], text="Warm.")
    upsert = HandleManager.upsert
    first: dict[int, threading.Event] = {}
    arrived = {"a": threading.Event(), "b": threading.Event()}

    def pausing(self: Any, **fields: Any) -> Any:
        handle = upsert(self, **fields)
        mine = first.pop(threading.get_ident(), None)
        if mine is not None:
            mine.set()
            other = next(event for event in arrived.values() if event is not mine)
            other.wait(timeout=3)
        return handle

    monkeypatch.setattr(HandleManager, "upsert", pausing)

    cause = make_user("system-race-cause")

    def send(name: str, to: list[str]) -> Any:
        def run() -> Any:
            first[threading.get_ident()] = arrived[name]
            with actor_context(cause):
                return Message.objects.compose_as_system(reason="tests.system_mail.race", to=to, text=f"From {name}.")
        return run

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, _pid = submit(pool, send("a", ["Zed <z@example.test>", "Ann <y@example.test>"]))
        two, _pid = submit(pool, send("b", ["Ann <y@example.test>", "Zed <z@example.test>"]))
        messages = [one.result(timeout=30), two.result(timeout=30)]
    assert {message.channel_id for message in messages} == {system.pk}
    assert [
        list(Recipient._base_manager.filter(message=message).order_by("pk").values_list("handle__value", flat=True))
        for message in messages
    ] == [["z@example.test", "y@example.test"], ["y@example.test", "z@example.test"]]
    # The person who caused the mail becomes no handle's creator.
    handles = Handle._base_manager.filter(value__in=("y@example.test", "z@example.test"))
    assert list(handles.values_list("created_by_id", flat=True)) == [None, None]
