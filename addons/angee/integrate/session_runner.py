"""Transport-independent live session execution and terminal outcome handling."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, NoReturn

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from rebac import system_context

from angee.integrate.impl import LiveBridgeImpl
from angee.integrate.live import PairingState, SessionLoggedOut
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.models import Bridge, IntegrationRuntimeStatus
from angee.integrate.session_process import BridgeSessionProcess, SessionProcessError
from angee.integrate.sync import bridge_progress_context
from angee.jobs.locks import task_lock, task_locks_are_cross_process

logger = logging.getLogger(__name__)


def run_bridge_session_job(
    model_label: str,
    pk: Any,
    *,
    stop_event: threading.Event,
    in_child: bool = False,
    on_shutdown: Callable[[], None] | None = None,
    on_stalled_shutdown: Callable[[], NoReturn] | None = None,
) -> dict[str, Any]:
    """Run one bridge's live session for the life of its vendor connection."""

    with system_context(reason="integrate.run_bridge_session"):
        bridge = _bridge(model_label, pk)
        if bridge is None:
            return {"ok": True, "skipped": True, "reason": "not-a-bridge"}
        if type(bridge).live_implementation_field() is None:
            return {"ok": True, "skipped": True, "reason": "not-live-capable"}
        impl = bridge.live_impl
        if not isinstance(impl, LiveBridgeImpl):
            return {"ok": True, "skipped": True, "reason": "not-live-capable"}
        if bridge.subscription_state.get("desired") != bridge.LiveState.LIVE:
            return {"ok": True, "skipped": True, "reason": "not-live-desired"}
        if type(bridge).Lifecycle.from_value(bridge.lifecycle) is not type(bridge).Lifecycle.CONNECTED:
            return {"ok": True, "skipped": True, "reason": "not-connected"}
        if impl.session_isolation not in ("thread", "process"):
            raise ImproperlyConfigured(f"Unknown live session isolation: {impl.session_isolation!r}")
        if impl.session_isolation == "process" and not in_child:
            if not task_locks_are_cross_process():
                raise ImproperlyConfigured("Process-isolated live sessions require cross-process task locks.")
            with task_lock(bridge.live_session_host_lock_key()) as acquired:
                if not acquired:
                    return {"ok": True, "skipped": True, "reason": "session-already-hosted"}
                try:
                    return BridgeSessionProcess(model_label, pk, stop_event=stop_event).run()
                except SessionProcessError as error:
                    logger.error("Live session process for %s %s exited with code %s.", model_label, pk, error.exitcode)
                    with bridge_advisory_lock(bridge) as session_acquired:
                        if session_acquired:
                            bridge.report_live_session_interrupted(exitcode=error.exitcode)
                    return {"ok": False, "process_error": True, "exitcode": error.exitcode}
        with bridge_advisory_lock(bridge) as acquired:
            if not acquired:
                return {"ok": True, "skipped": True, "reason": "session-already-running"}
            # A queued start may outlive an operator stop or a terminal outcome.
            # Revalidate under the same lock that guards opening the session store.
            bridge.refresh_from_db()
            if stop_event.is_set():
                return {"ok": True, "skipped": True, "reason": "host-stopping"}
            if bridge.subscription_state.get("desired") != bridge.LiveState.LIVE:
                return {"ok": True, "skipped": True, "reason": "not-live-desired"}
            if bridge.lifecycle != bridge.Lifecycle.CONNECTED:
                return {"ok": True, "skipped": True, "reason": "not-connected"}
            if bridge.runtime_status != IntegrationRuntimeStatus.OK:
                return {"ok": True, "skipped": True, "reason": "runtime-error"}
            impl = _require_live_impl(bridge)
            if impl.session_isolation == "process" and not in_child:
                # The implementation changed while the task acquired its lock.
                # Let reconciliation route the next start through the host.
                return {"ok": True, "skipped": True, "reason": "implementation-changed"}
            with bridge_progress_context(bridge) as reporter:
                session = impl.session_class_resolved()(
                    bridge,
                    reporter=reporter,
                    stop_event=stop_event,
                    on_shutdown=on_shutdown,
                    on_stalled_shutdown=on_stalled_shutdown,
                )
                try:
                    state = session.run()
                except SessionLoggedOut as error:
                    _record_logged_out(bridge, error, session=session)
                    return {"ok": False, "logged_out": True}
                if state == PairingState.DUPLICATE_ACCOUNT:
                    _record_duplicate_account(bridge, session=session)
                    return {"ok": False, "duplicate_account": True}
                if session.outcome_error is not None:
                    bridge.record_sync_error(session.outcome_error, now=timezone.now())
                    return {"ok": False, "session_error": True, "state": state}
                reporter.report(bridge.SyncStage.IDLE, details={"pairing": {"state": state}})
        return {"ok": True, "state": state, "items": session.landed}


def _record_logged_out(bridge: Any, error: Exception, *, session: Any) -> None:
    """Record a logout as runtime failure, then release desire/account and store.

    The lifecycle is untouched: the operator declared this bridge connected and
    a handshake outcome does not revoke that. ``record_sync_error`` puts the
    outcome on runtime health and the failed sync stage, which also keeps the
    reconciler from redispatching a session that can only fail again.

    Error bookkeeping must run first: while desire still reads live, the
    bridge's next-sync policy keeps it out of the poll loop, so the failed stage
    is not masked by a later no-op poll. Only then is desire cleared under its
    row-locked merge.
    """

    impl = _require_live_impl(bridge)
    bridge.record_sync_error(error, now=timezone.now())
    impl.release_account(desired=bridge.LiveState.STOPPED)
    session.discard_store()


def _record_duplicate_account(bridge: Any, *, session: Any) -> None:
    """Record a rejected duplicate as runtime failure and release the void claim.

    Being told another bridge owns this account is a handshake outcome, not the
    operator changing intent, so runtime health records it and lifecycle stands.
    The store is discarded only if this session created the pairing material it
    would delete.
    """

    impl = _require_live_impl(bridge)
    error = session.duplicate_error or impl.duplicate_account_error()
    bridge.record_sync_error(error, now=timezone.now())
    impl.release_account(desired=bridge.LiveState.STOPPED)
    session.discard_new_store()


def _bridge(model_label: str, pk: Any) -> Any | None:
    """Return one bridge row for a live session task, or ``None``."""

    try:
        app_label, model_name = str(model_label).split(".", 1)
    except ValueError:
        return None
    model = apps.get_model(app_label, model_name)
    if not issubclass(model, Bridge):
        return None
    return model._default_manager.filter(pk=pk).first()


def _require_live_impl(bridge: Any) -> LiveBridgeImpl:
    """Return the bridge's live implementation after an earlier live gate."""

    impl = bridge.live_impl
    if not isinstance(impl, LiveBridgeImpl):
        raise TypeError("Bridge no longer resolves to a live implementation.")
    return impl
