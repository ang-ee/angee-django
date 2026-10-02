"""Live-channel pairing services shared by messaging backend addons."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.views.decorators.debug import sensitive_variables
from rebac import current_actor, system_context

from angee.integrate.impl import LiveBridgeImpl
from angee.integrate.live import (
    PairingProjection,
    armed_material_key,
    await_session_exit,
    reset_session_store,
    session_store_path,
    skipped_password_marker,
)
from angee.integrate.models import IntegrationLifecycle, IntegrationRuntimeStatus
from angee.jobs.locks import task_locks_are_cross_process


class PairingActionError(ValueError):
    """A safe, actionable pairing precondition failure."""


def channel_pairing(channel: Any) -> PairingProjection:
    """Return one live channel's vendor-neutral pairing projection."""

    return _live_impl(channel).pairing()


def resume_channel_pairing(channel: Any) -> None:
    """Start a live channel, clearing its runtime error but retaining identity.

    A stale pairing report (a dead QR, a rejected account) is dropped only when
    no session provably runs: a running session owns the report it is showing,
    and a process-local lock backend cannot see a worker's session at all.
    """

    impl = _live_impl(channel)
    with system_context(reason="messaging.resume_channel_pairing"):
        channel.refresh_from_db(from_queryset=type(channel)._base_manager.select_related("credential"))
        if task_locks_are_cross_process() and not channel.is_syncing:
            channel.update_live_state(identity_key=impl.state_identity_key, drop_pairing_report=True)
        channel.set_lifecycle(IntegrationLifecycle.CONNECTED)
        channel.report_status(IntegrationRuntimeStatus.OK)
        channel.start_live()


def resume_or_create_channel(user: Any, *, name: str, backend_class: str) -> Any:
    """Restart the user's newest unfinished pairing channel, or create one.

    A reused channel keeps its display name; ``name`` applies only to creation.
    Reuse resets an existing session store: a released claim can follow a
    duplicate-account rejection while the store still holds that linked
    account. Without a store there is nothing to wipe, so the channel simply
    resumes. A process-local lock backend cannot prove the reset safe, so there
    a channel with a store is left alone and a new one is created. Reset errors
    propagate without creating another channel, so an unproven shutdown leaves
    the store intact and the caller can retry shortly.
    """

    channel_model = apps.get_model("messaging", "Channel")
    impl_class = channel_model.resolve_impl_class(channel_model.live_impl_field, backend_class)
    if not issubclass(impl_class, LiveBridgeImpl):
        raise PairingActionError("This action requires a live channel.")
    actor = current_actor() or user
    with system_context(reason="messaging.resume_or_create_channel"), transaction.atomic():
        channel = channel_model.objects.unfinished_pairing(user, backend_class).first()
        if channel is not None and session_store_path(channel).exists() and not task_locks_are_cross_process():
            channel = None
        reused = channel is not None
        if channel is None:
            channel = channel_model.objects.create_disconnected(user, name=name, backend_class=backend_class)
    if reused and session_store_path(channel).exists():
        reset_channel_pairing(channel)
    else:
        resume_channel_pairing(channel)
    return channel.with_actor(actor)


@sensitive_variables("password", "material")
def submit_channel_password(channel: Any, password: str) -> None:
    """Store one transient secret under the armed material key and signal readiness."""

    _live_impl(channel)
    if not password:
        raise PairingActionError("A channel password is required.")
    with system_context(reason="messaging.submit_channel_password"):
        channel.refresh_from_db(from_queryset=type(channel)._base_manager.select_related("credential"))
        material_key = armed_material_key(channel.subscription_state)
        if not material_key:
            raise PairingActionError("This channel is not awaiting a password.")
        credential = channel.credential
        if credential is None:
            raise PairingActionError("This channel has no credential for password input.")
        credential.update_material(**{material_key: password})
        # See ``LiveSession._mark_awaiting_password`` for the awaiting tri-state.
        channel.merge_subscription_state(awaiting="")


def skip_channel_password(channel: Any) -> None:
    """Skip one armed optional secret round and signal that choice to the session."""

    impl = _live_impl(channel)
    with system_context(reason="messaging.skip_channel_password"):
        channel.refresh_from_db(from_queryset=type(channel)._base_manager.select_related("credential"))
        material_key = armed_material_key(channel.subscription_state)
        if not material_key:
            raise PairingActionError("This channel is not awaiting a password.")
        if not impl.pairing().can_skip:
            raise PairingActionError("This channel password cannot be skipped.")
        channel.merge_subscription_state(awaiting=skipped_password_marker(material_key))


def reset_channel_pairing(channel: Any) -> None:
    """Stop a live channel, wipe its released session store, and restart pairing."""

    impl = _live_impl(channel)
    with system_context(reason="messaging.reset_channel_pairing"):
        channel.stop_live()
        try:
            await_session_exit(channel)
        except TimeoutError as error:
            raise PairingActionError("The channel is still shutting down; try again shortly.") from error
        except ImproperlyConfigured as error:
            raise PairingActionError("Resetting this channel requires a cross-process task lock backend.") from error
        channel.refresh_from_db(from_queryset=type(channel)._base_manager.select_related("credential"))
        if channel.credential is not None:
            channel.credential.update_material(
                **dict.fromkeys(impl.transient_material_keys),
            )
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
        raise PairingActionError("This action requires a live channel.")
    return impl
