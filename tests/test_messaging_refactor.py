"""Native session cleanup survives a vendor's failed shutdown hook."""

import asyncio
from queue import Queue
from types import SimpleNamespace

import pytest
from django.test import override_settings
from rebac import system_context

from angee.messaging.session import AsyncioLiveSession
from angee.messaging.testing.models import Thread
from tests.conftest import make_integration
from tests.test_messaging import channel as channel


def test_asyncio_owner_drains_tasks_after_vendor_teardown_failure():
    events = []

    class Session(AsyncioLiveSession):
        async def _run_client(self):
            async def background():
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append("background-finished")

            asyncio.create_task(background())
            await asyncio.sleep(0)

        async def _download_coro(self, payload, fact):
            return None

        def _is_logged_out(self, error):
            return False

        def _teardown_loop(self, loop):
            events.append("vendor-close")
            raise RuntimeError("vendor close failed")

        def _stop_main(self, loop, *, deadline):
            pass

    session = object.__new__(Session)
    session.bridge = SimpleNamespace(sqid="session-test")
    session.live_impl = SimpleNamespace(label="Test")
    session.events = Queue()
    session._connect()
    assert events == ["vendor-close", "background-finished"]
    assert session._loop.is_closed()
    assert session.events.get_nowait() == ("disconnected", None)


@pytest.mark.django_db(transaction=True)
def test_thread_name_population_retains_selected_set(channel):
    """A queryset verb must not title rows excluded by the caller's selection."""
    with system_context(reason="test.messaging.selected_names"):
        first = Thread.objects.create(channel=channel, external_id="selected")
        second = Thread.objects.create(channel=channel, external_id="excluded")
        named = Thread.objects.filter(pk=first.pk).name_untitled({first.pk: "First", second.pk: "Second"})
        assert named == 1
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.title.text == "First"
        assert second.title_id is None


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
def test_email_selection_and_sender_stay_on_declared_connected_channel(channel):
    with system_context(reason="test.messaging.outbound_email"):
        channel = make_integration(
            "billing", model=type(channel), backend_class="email", lifecycle="connected",
            config={"from_email": "Billing <billing@example.test>"},
        )
        selected = type(channel).objects.filter(pk=channel.pk).outbound_email()
        assert list(selected) == [channel]
        assert channel.outbound_sender() == ("Billing", "billing@example.test")
        channel.pause()
        assert not selected.all().exists()
