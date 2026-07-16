"""Live-channel pairing services shared by messaging backend addons."""

from __future__ import annotations

from typing import Any

from django.views.decorators.debug import sensitive_variables
from rebac import system_context

from angee.integrate.impl import LiveBridgeImpl
from angee.integrate.live import PairingProjection, await_session_exit, reset_session_store
from angee.integrate.models import IntegrationLifecycle, IntegrationRuntimeStatus


def channel_pairing(channel: Any) -> PairingProjection:
    """Return one live channel's vendor-neutral pairing projection."""

    return _live_impl(channel).pairing()


def resume_channel_pairing(channel: Any) -> None:
    """Declare a live channel connected, clear its runtime error, and start it."""

    _live_impl(channel)
    with system_context(reason="messaging.resume_channel_pairing"):
        channel.refresh_from_db()
        channel.set_lifecycle(IntegrationLifecycle.CONNECTED)
        channel.report_status(IntegrationRuntimeStatus.OK)
        channel.start_live()


@sensitive_variables("password", "material")
def submit_channel_password(channel: Any, password: str) -> None:
    """Store one transient account password and signal that it is ready."""

    _live_impl(channel)
    if not password:
        raise ValueError("A channel password is required.")
    with system_context(reason="messaging.submit_channel_password"):
        channel.refresh_from_db()
        if channel.subscription_state.get("awaiting") != "password":
            raise ValueError("This channel is not awaiting a password.")
        credential = channel.credential
        if credential is None:
            raise ValueError("This channel has no credential for password input.")
        credential.update_material(password=password)
        # See ``LiveSession._mark_awaiting_password`` for the awaiting tri-state.
        channel.merge_subscription_state(awaiting="")


def reset_channel_pairing(channel: Any) -> None:
    """Stop a live channel, wipe its released session store, and restart pairing."""

    impl = _live_impl(channel)
    with system_context(reason="messaging.reset_channel_pairing"):
        channel.stop_live()
        await_session_exit(channel)
        channel.refresh_from_db(fields=["credential"])
        if channel.credential is not None:
            channel.credential.update_material(password=None)
        reset_session_store(channel)
        impl.mark_disconnected(clear_identity=True)
    resume_channel_pairing(channel)


def disconnect_channel(channel: Any) -> None:
    """Stop a live channel and release its account while retaining pairing material."""

    impl = _live_impl(channel)
    with system_context(reason="messaging.disconnect_channel"):
        channel.stop_live()
        impl.mark_disconnected(clear_identity=False)


def _live_impl(channel: Any) -> LiveBridgeImpl:
    """Return the selected live implementation or reject a poll-only channel."""

    impl = channel.live_impl
    if not isinstance(impl, LiveBridgeImpl):
        raise ValueError("This action requires a live channel.")
    return impl
