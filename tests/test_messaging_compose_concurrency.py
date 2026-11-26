"""PostgreSQL: two composes of one keyed message land one message, and a typed name never overwrites a filled one."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.db import connection, transaction
from django.test import override_settings
from rebac import system_context, to_subject_ref

from angee.messaging.testing.models import Channel, Handle, Message, Thread
from angee.provenance.context import changeset_model
from tests.conftest import make_integration
from tests.lock_waits import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


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


@override_settings(MAILERS={"transport": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}})
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


@override_settings(MAILERS={"transport": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}})
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
