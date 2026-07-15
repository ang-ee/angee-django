"""WhatsApp channel backend: the live-session and account-identity seam.

The session itself — socket, QR pairing, ingest — runs in a long-lived task on
the dedicated ``whatsapp`` Celery queue (:mod:`.tasks` / :mod:`.session`); this
backend is the Channel-facing seam. It dispatches that session and owns every
WhatsApp-specific read of, and write to, its own channel row: the account claim,
the release of that claim, the operator's disconnect, and the pairing projection
the console renders. ``backend_class`` selects it per row, so a foreign channel
resolves a different backend and never reaches this code; the backend boundary
is re-asserted once at the addon's action entry points, where an operator-named
record arrives untyped (``connect._require_whatsapp``).

Two releases, deliberately distinct. :meth:`WhatsAppChannelBackend.mark_disconnected`
is the *operator's*: it moves the lifecycle, because the lifecycle is declared
intent and the operator is who declares it. :meth:`WhatsAppChannelBackend.release_account`
is the *worker's*: it drops a claim a runtime handshake proved void and never
touches the lifecycle, because how far a handshake got belongs on
``runtime_status``/``sync_progress`` (``docs/backend/guidelines.md``).

``fetch_messages`` is empty because the bridge is push-mode: the session ingests
out of band and ``next_sync_at`` is never populated, so the poll scheduler
ignores the channel (a manual ``syncIntegration`` is a cheap no-op).
``start_live`` dispatches the session task; stopping is cooperative through the
base's persisted desired-state, so ``stop_live`` needs no vendor action beyond
the inherited no-op.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from django.db import transaction
from rebac import system_context

from angee.integrate.models import IntegrationLifecycle
from angee.messaging.backends import ChannelBackend, ParsedMessage
from angee.messaging_integrate_whatsapp.client import PairingState, WhatsappPairingType
from angee.messaging_integrate_whatsapp.constants import (
    RECONCILER_INTERVAL,
    RUN_SESSION_TASK,
    SESSION_QUEUE,
)
from angee.messaging_integrate_whatsapp.parser import bare_jid, phone_for_jid
from angee.tasks.enqueue import enqueue_task

SESSION_START_EXPIRES = RECONCILER_INTERVAL
"""Discard an unconsumed session start after one reconciler tick — the beat
re-enqueues while the channel stays live-desired, so a saturated or absent
worker never accumulates a stale backlog."""


class WhatsAppChannelBackend(ChannelBackend):
    """Channel backend for a linked WhatsApp account — one live session per channel."""

    key = "whatsapp"
    label = "WhatsApp"
    icon = "message-circle"

    CLAIMING_LIFECYCLES: ClassVar[tuple[str, ...]] = (
        str(IntegrationLifecycle.CONNECTED),
        str(IntegrationLifecycle.PAUSED),
    )
    """The lifecycles that hold a durable claim on a WhatsApp account: a connected
    channel is using it and a paused one retains it, while a disconnected channel
    has released it and its stale ``own_jid`` claims nothing."""

    def fetch_messages(self) -> list[ParsedMessage]:
        """Return nothing — a push bridge ingests from its live session, never a poll."""

        return []

    def start_live(self) -> None:
        """Dispatch this channel's live session to the dedicated queue.

        Safe to repeat: the session task's non-blocking advisory-lock acquire
        makes a duplicate start exit immediately, and ``expires`` keeps an
        undelivered start from outliving the next reconciler tick.
        """

        enqueue_task(
            RUN_SESSION_TASK,
            kwargs={"channel_id": self.bridge.pk},
            queue=SESSION_QUEUE,
            expires=SESSION_START_EXPIRES,
        )

    # -- account identity --------------------------------------------------

    def claim_account(self, jid: str) -> bool:
        """Record ``jid`` as this channel's durable account identity.

        Returns whether the claim landed: ``False`` means another channel already
        holds the account (:attr:`CLAIMING_LIFECYCLES`) and this one must not
        ingest it.

        The claim records identity and nothing else. It never moves the
        lifecycle, because connection intent is the operator's to declare
        (``connect.resume_whatsapp_pairing``) — a claim that connected a row
        would manufacture an intent nobody expressed, and would revert an
        operator's disconnect within one reconciler tick.

        One-owner-per-account is serialized by the account-scoped advisory
        :func:`~.client.whatsapp_account_lock_key` the live session holds around
        this call, not by the database: ``lock_if_supported()`` locks the
        *claiming* row, so two channels claiming one JID lock two different rows
        and never serialize against each other. The row below is the durable
        record of the claim, not its enforcement point — there is no DB
        constraint behind it, and on the process-local lock floor
        (``angee.tasks.locks.LocalLockBackend``, ``cross_process = False``) two
        workers can both pass the SELECT.
        """

        normalized = bare_jid(jid)
        if not normalized:
            raise ValueError("A WhatsApp account JID is required.")
        model = type(self.bridge)
        with system_context(reason="messaging_integrate_whatsapp.account.claim"), transaction.atomic():
            row = (
                model.objects.sudo(reason="messaging_integrate_whatsapp.account.claim.row")
                .lock_if_supported()
                .get(pk=self.bridge.pk)
            )
            owners = self._account_owners(
                model.objects.sudo(reason="messaging_integrate_whatsapp.account.claim.owner"),
                normalized,
            )
            if owners.exists():
                return False
            state = dict(row.subscription_state)
            state["own_jid"] = normalized
            row.subscription_state = state
            row.save(update_fields=["subscription_state", "updated_at"])
        self.bridge.refresh_from_db()
        return True

    def mark_disconnected(self, *, clear_identity: bool) -> None:
        """Record the operator's disconnect: lifecycle released, identity optional.

        The operator declares the lifecycle, so this write moves it, through the
        Integration's own idempotent ``set_lifecycle`` — an already-disconnected
        row is left alone instead of hitting the guarded transition.

        ``clear_identity`` drops the claimed ``own_jid`` (a wiped pairing owns
        nothing) *and* the session's last pairing report, which described that
        identity and is stale the moment it is dropped. A retained identity keeps
        its report; a disconnected row renders ``STOPPED`` from the lifecycle
        either way (:meth:`_pairing_state`), so no stale report is read back.
        """

        model = type(self.bridge)
        with system_context(reason="messaging_integrate_whatsapp.account.disconnect"), transaction.atomic():
            row = (
                model.objects.sudo(reason="messaging_integrate_whatsapp.account.disconnect.row")
                .lock_if_supported()
                .get(pk=self.bridge.pk)
            )
            row.set_lifecycle(IntegrationLifecycle.DISCONNECTED)
            if clear_identity:
                self._write_state(row, drop_identity=True, drop_pairing_report=True)
        self.bridge.refresh_from_db()

    def release_account(self, *, desired: Any) -> None:
        """Record a void claim: drop ``own_jid`` and the live desire, never the lifecycle.

        The worker's release. A runtime handshake that ended in a phone-side
        logout or a rejected duplicate proves this row's ``own_jid`` claims
        nothing, so the claim goes — but the operator declared the lifecycle and
        a handshake outcome does not get to revoke it. ``desired`` is the stop
        signal (``run_session`` and ``ensure_sessions`` both gate on it); the
        caller records the outcome itself on ``runtime_status``/``sync_progress``,
        which is where "how far did this get" lives.
        """

        model = type(self.bridge)
        with system_context(reason="messaging_integrate_whatsapp.account.release"), transaction.atomic():
            row = (
                model.objects.sudo(reason="messaging_integrate_whatsapp.account.release.row")
                .lock_if_supported()
                .get(pk=self.bridge.pk)
            )
            self._write_state(row, drop_identity=True, desired=desired)
        self.bridge.refresh_from_db()

    def _write_state(
        self,
        row: Any,
        *,
        drop_identity: bool = False,
        drop_pairing_report: bool = False,
        desired: Any | None = None,
    ) -> None:
        """Apply one row's state/progress edits, saving only the fields that changed."""

        fields: list[str] = []
        state = dict(row.subscription_state)
        if drop_identity:
            state.pop("own_jid", None)
        if desired is not None:
            state["desired"] = str(getattr(desired, "value", desired))
        if state != row.subscription_state:
            row.subscription_state = state
            fields.append("subscription_state")
        if drop_pairing_report:
            progress = dict(row.sync_progress) if isinstance(row.sync_progress, Mapping) else {}
            details = dict(progress.get("details") or {}) if isinstance(progress.get("details"), Mapping) else {}
            if details.pop("pairing", None) is not None:
                progress["details"] = details
                row.sync_progress = progress
                fields.append("sync_progress")
        if fields:
            row.save(update_fields=[*fields, "updated_at"])

    def _account_owners(self, manager: Any, jid: str) -> Any:
        """Return the *other* channels holding a durable claim on ``jid``, oldest first.

        The one place this addon spells "who owns this account"; the caller picks
        the scope — elevated for a system claim, the actor's own for a console
        projection.
        """

        return (
            manager.filter(
                backend_class=self.key,
                subscription_state__own_jid=jid,
                lifecycle__in=self.CLAIMING_LIFECYCLES,
            )
            .exclude(pk=self.bridge.pk)
            .order_by("pk")
        )

    # -- console projection ------------------------------------------------

    def pairing(self) -> WhatsappPairingType:
        """Project durable identity plus the latest transient report for the dialog.

        The row answers everything settled — ``PAUSED``/``STOPPED`` from the
        lifecycle, ``PAIRED`` from the claimed account identity — and the
        session's report only fills in what is genuinely in flight: the QR, a
        logout, a rejected duplicate.
        """

        report = self._pairing_report()
        reported = PairingState.from_report(report.get("state"))
        jid = str(self.bridge.subscription_state.get("own_jid") or report.get("jid") or "")
        state = self._pairing_state(reported=reported, jid=jid)
        duplicate = self._duplicate_owner(jid) if state is PairingState.DUPLICATE_ACCOUNT else None
        return WhatsappPairingType(
            state=state,
            qr=str(report.get("qr") or "") if state is PairingState.AWAITING_SCAN else "",
            jid=jid,
            phone=phone_for_jid(jid),
            duplicate_channel_id="" if duplicate is None else str(duplicate.sqid),
            duplicate_channel_name="" if duplicate is None else str(duplicate.display_name),
        )

    def _pairing_report(self) -> Mapping[str, Any]:
        """Return the live session's last pairing report off ``sync_progress``."""

        progress = self.bridge.sync_progress
        details = progress.get("details") if isinstance(progress, Mapping) else None
        pairing = details.get("pairing") if isinstance(details, Mapping) else None
        return pairing if isinstance(pairing, Mapping) else {}

    def _pairing_state(self, *, reported: PairingState | None, jid: str) -> PairingState:
        """Resolve the rendered state from the declared lifecycle, then the runtime report.

        The lifecycle answers first and alone, because it is what the operator
        declared: released is ``STOPPED``, paused is ``PAUSED``, and neither
        needs a second opinion from a report. Only a row the operator still wants
        connected asks the session how far it actually got — a logout or a
        rejected duplicate is a runtime outcome, and the report is where the
        worker records it.
        """

        lifecycle = IntegrationLifecycle.from_value(self.bridge.lifecycle)
        if lifecycle is IntegrationLifecycle.PAUSED:
            return PairingState.PAUSED
        if lifecycle is IntegrationLifecycle.DISCONNECTED:
            return PairingState.STOPPED
        if reported is PairingState.LOGGED_OUT or reported is PairingState.DUPLICATE_ACCOUNT:
            return reported
        if jid:
            return PairingState.PAIRED
        if self.bridge.subscription_state.get("desired") != self.bridge.LiveState.LIVE:
            return PairingState.STOPPED
        if reported is PairingState.AWAITING_SCAN:
            return PairingState.AWAITING_SCAN
        return PairingState.STARTING

    def _duplicate_owner(self, jid: str) -> Any | None:
        """Return the channel that already owns ``jid``, read in the caller's scope.

        A rejected channel's report deliberately carries no foreign row
        attributes — its owner must not learn another operator's channel name or
        sqid from their own row's ``sync_progress``, which ``channelChanged``
        broadcasts. The conflicting channel is named here instead, off the
        caller's own read rather than off the broadcast payload.
        """

        if not jid:
            return None
        return self._account_owners(type(self.bridge).objects, jid).first()
