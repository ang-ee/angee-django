"""Cross-owner session reset and historical aliases to Channel policy owners."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from rebac import system_context

from angee.integrate.live import await_session_exit, reset_session_store
from angee.messaging.backends import PairingActionError as PairingActionError
from angee.messaging.models import Channel


def reset_channel_pairing(channel: Any) -> None:
    """Stop a live channel, wipe its released session store, and restart pairing."""

    impl = channel.require_live_backend()
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
    channel.resume_pairing()


channel_pairing = Channel.pairing
resume_channel_pairing = Channel.resume_pairing
submit_channel_password = Channel.submit_pairing_password
skip_channel_password = Channel.skip_pairing_password
disconnect_channel = Channel.disconnect_pairing


def resume_or_create_channel(user: Any, *, name: str, backend_class: str) -> Any:
    """Historical caller seam; the runtime Channel manager owns pairing reuse."""
    return apps.get_model("messaging", "Channel").objects.resume_or_create_pairing(
        user, name=name, backend_class=backend_class,
    )
