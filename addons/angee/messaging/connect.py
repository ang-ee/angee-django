"""Live-channel pairing services shared by messaging backend addons."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.views.decorators.debug import sensitive_variables
from rebac import system_context

from angee.base.db import get_write_alias
from angee.integrate.impl import LiveBridgeImpl
from angee.integrate.live import (
    PairingProjection,
    armed_material_key,
    await_session_exit,
    reset_session_store,
    skipped_password_marker,
)
from angee.integrate.models import IntegrationLifecycle, IntegrationRuntimeStatus


class PairingActionError(ValueError):
    """A safe, actionable pairing precondition failure."""


def channel_pairing(channel: Any) -> PairingProjection:
    """Return one live channel's vendor-neutral pairing projection."""

    return _live_impl(channel).pairing()


def resume_channel_pairing(channel: Any, *, using: str | None = None) -> None:
    """Declare a live channel connected, clear its runtime error, and start it."""

    using = get_write_alias(type(channel), using=using, instance=channel)
    channel._state.db = using
    _live_impl(channel)
    with system_context(reason="messaging.resume_channel_pairing"):
        channel.refresh_from_db(
            using=using, from_queryset=type(channel)._base_manager.db_manager(using).select_related("credential")
        )
        channel.set_lifecycle(IntegrationLifecycle.CONNECTED, using=using)
        channel.report_status(IntegrationRuntimeStatus.OK)
        channel.start_live()


@sensitive_variables("password", "material")
def submit_channel_password(channel: Any, password: str, *, using: str | None = None) -> None:
    """Store one transient secret under the armed material key and signal readiness."""

    using = get_write_alias(type(channel), using=using, instance=channel)
    channel._state.db = using
    _live_impl(channel)
    if not password:
        raise PairingActionError("A channel password is required.")
    with system_context(reason="messaging.submit_channel_password"):
        channel.refresh_from_db(
            using=using, from_queryset=type(channel)._base_manager.db_manager(using).select_related("credential")
        )
        material_key = armed_material_key(channel.subscription_state)
        if not material_key:
            raise PairingActionError("This channel is not awaiting a password.")
        credential = channel.credential
        if credential is None:
            raise PairingActionError("This channel has no credential for password input.")
        credential.update_material(using=using, **{material_key: password})
        # See ``LiveSession._mark_awaiting_password`` for the awaiting tri-state.
        channel.merge_subscription_state(awaiting="", using=using)


def skip_channel_password(channel: Any, *, using: str | None = None) -> None:
    """Skip one armed optional secret round and signal that choice to the session."""

    using = get_write_alias(type(channel), using=using, instance=channel)
    channel._state.db = using
    impl = _live_impl(channel)
    with system_context(reason="messaging.skip_channel_password"):
        channel.refresh_from_db(
            using=using, from_queryset=type(channel)._base_manager.db_manager(using).select_related("credential")
        )
        material_key = armed_material_key(channel.subscription_state)
        if not material_key:
            raise PairingActionError("This channel is not awaiting a password.")
        if not impl.pairing().can_skip:
            raise PairingActionError("This channel password cannot be skipped.")
        channel.merge_subscription_state(awaiting=skipped_password_marker(material_key), using=using)


def reset_channel_pairing(channel: Any, *, using: str | None = None) -> None:
    """Stop a live channel, wipe its released session store, and restart pairing."""

    using = get_write_alias(type(channel), using=using, instance=channel)
    channel._state.db = using
    impl = _live_impl(channel)
    with system_context(reason="messaging.reset_channel_pairing"):
        channel.stop_live()
        try:
            await_session_exit(channel)
        except TimeoutError as error:
            raise PairingActionError("The channel is still shutting down; try again shortly.") from error
        except ImproperlyConfigured as error:
            raise PairingActionError("Resetting this channel requires a cross-process task lock backend.") from error
        channel.refresh_from_db(
            using=using, from_queryset=type(channel)._base_manager.db_manager(using).select_related("credential")
        )
        if channel.credential is not None:
            channel.credential.update_material(
                using=using,
                **dict.fromkeys(impl.transient_material_keys),
            )
        reset_session_store(channel)
        impl.mark_disconnected(clear_identity=True)
    resume_channel_pairing(channel)


def disconnect_channel(channel: Any, *, using: str | None = None) -> None:
    """Stop a live channel and release its account while retaining pairing material."""

    using = get_write_alias(type(channel), using=using, instance=channel)
    channel._state.db = using
    impl = _live_impl(channel)
    with system_context(reason="messaging.disconnect_channel"):
        channel.stop_live()
        impl.mark_disconnected(clear_identity=False)


def _live_impl(channel: Any) -> LiveBridgeImpl:
    """Return the selected live implementation or reject a poll-only channel."""

    impl = channel.live_impl
    if not isinstance(impl, LiveBridgeImpl):
        raise PairingActionError("This action requires a live channel.")
    return impl
