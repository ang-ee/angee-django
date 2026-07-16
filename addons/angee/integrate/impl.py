"""Integration implementation descriptors.

An ``Integration`` row stores the registry key for integration-level behaviour.
Concrete addons contribute subclasses through ``ANGEE_INTEGRATION_IMPLS``; persisted
domain state belongs on real child models, not on descriptor-owned companion rows.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils.module_loading import import_string
from rebac import system_context

from angee.base.impl import ImplBase
from angee.integrate.connect import enabled_oauth_client_from_hint
from angee.integrate.constants import RUN_SESSION_TASK, SESSION_START_EXPIRES
from angee.integrate.live import PairingProjection, PairingState, SessionLoggedOut
from angee.tasks.enqueue import enqueue_task
from angee.tasks.locks import LockKey, task_lock


class IntegrationImpl(ImplBase):
    """Base descriptor for one row-selected integration implementation."""

    category = "none"
    label = "Integration"
    icon = ""
    oauth_client: ClassVar[str] = ""

    def __init__(self, integration: Any) -> None:
        """Bind this implementation to its owning integration row."""

        self.integration = integration

    def connect_oauth_client(self, owner_label: str) -> Any:
        """Return the enabled OAuth client this integration connects through.

        Falls back to the bound integration's vendor slug when the implementation
        declares no ``oauth_client`` hint; the vendor slug also feeds the
        ``{vendor}`` template.
        """

        vendor_slug = str(getattr(getattr(self.integration, "vendor", None), "slug", "") or "")
        hint = str(self.oauth_client or "")
        return enabled_oauth_client_from_hint(
            hint or vendor_slug,
            owner_label=owner_label,
            reason="integrate.graphql.connect_integration.oauth_client",
            vendor_slug=vendor_slug,
        )


class NullIntegrationImpl(IntegrationImpl):
    """Neutral implementation for a row that has chosen none."""

    key = "none"
    label = "None"


class BridgeImpl(IntegrationImpl):
    """Base descriptor for an inbound bridge — it pulls/subscribes to external data.

    Bridges run on a schedule (``run_due_bridges`` over ``Bridge.next_sync_at``) and
    keep their sync state on a concrete ``Bridge`` child model.
    """

    category = "bridge"
    label = "Bridge"
    icon = "plug"

    @property
    def bridge(self) -> Any:
        """Return the concrete bridge child this implementation is bound to."""

        return self.integration


class LiveBridgeImpl(BridgeImpl):
    """Base descriptor for a bridge backed by a long-lived live session task."""

    session_queue: ClassVar[str] = ""
    session_class: ClassVar[type[Any] | str | None] = None
    state_identity_key: ClassVar[str] = "own_id"

    @property
    def CLAIMING_LIFECYCLES(self) -> tuple[str, ...]:
        """Return lifecycles that retain a durable live-account claim."""

        lifecycle = type(self.bridge).Lifecycle
        return (str(lifecycle.CONNECTED), str(lifecycle.PAUSED))

    def session_class_resolved(self) -> type[Any]:
        """Return the worker-only live session class for this backend."""

        if self.session_class is None:
            raise NotImplementedError(f"{type(self).__name__} must define session_class.")
        if isinstance(self.session_class, str):
            resolved = import_string(self.session_class)
        else:
            resolved = self.session_class
        if not isinstance(resolved, type):
            raise TypeError(f"{type(self).__name__}.session_class must resolve to a class.")
        return resolved

    def start_live(self) -> None:
        """Dispatch this bridge's live session to its dedicated queue.

        Safe to repeat: the session task's non-blocking advisory-lock acquire
        makes a duplicate start exit immediately, and ``expires`` keeps an
        undelivered start from outliving the next reconciler tick.
        """

        if not self.session_queue:
            raise ImproperlyConfigured(
                f"{type(self).__name__} must define session_queue; a long-lived session on the "
                "shared prefork queue is killed by the global time limit and starves the pool."
            )
        enqueue_task(
            RUN_SESSION_TASK,
            kwargs={"model_label": self.bridge._meta.label_lower, "pk": self.bridge.pk},
            queue=self.session_queue,
            expires=SESSION_START_EXPIRES,
        )

    def account_lock_key(self, external_id: str) -> LockKey:
        """Return the cross-worker ownership key for one normalized account id."""

        normalized = self.normalize_account_id(external_id)
        if not normalized:
            raise ValueError("A live bridge account id is required.")
        return LockKey(f"{self.key}-account", (normalized,))

    @contextmanager
    def account_lock(self, external_id: str) -> Iterator[bool]:
        """Try to hold the account-scoped ownership lock."""

        with task_lock(self.account_lock_key(external_id)) as acquired:
            yield acquired

    def claim_account(self, external_id: str) -> bool:
        """Record ``external_id`` as this bridge's durable account identity.

        Returns whether the claim landed: ``False`` means another bridge already
        holds the account (:attr:`CLAIMING_LIFECYCLES`) and this one must not
        ingest it. The claim records identity and nothing else; it never moves
        lifecycle, because connection intent is the operator's to declare.

        One-owner-per-account is serialized by the account-scoped advisory lock
        the live session holds around this call, not by the database:
        ``lock_if_supported()`` locks the *claiming* row, so two bridges claiming
        one account lock two different rows and never serialize against each
        other. The row below is the durable record of the claim, not its
        enforcement point - there is no database constraint behind it, and on
        the process-local lock floor two workers can both pass the ``SELECT``.
        """

        normalized = self.normalize_account_id(external_id)
        if not normalized:
            raise ValueError("A live bridge account id is required.")
        model = type(self.bridge)
        with system_context(reason="integrate.live.account.claim"), transaction.atomic():
            row = (
                model.objects.sudo(reason="integrate.live.account.claim.row").lock_if_supported().get(pk=self.bridge.pk)
            )
            owners = self._account_owners(
                model.objects.sudo(reason="integrate.live.account.claim.owner"),
                normalized,
            )
            if owners.exists():
                return False
            state = dict(row.subscription_state)
            state[self.state_identity_key] = normalized
            row.subscription_state = state
            row.save(update_fields=["subscription_state", "updated_at"])
        self.bridge.refresh_from_db()
        return True

    def mark_disconnected(self, *, clear_identity: bool) -> None:
        """Record the operator's disconnect: lifecycle released, identity optional.

        The operator declares the lifecycle, so this write moves it through the
        Integration's own idempotent ``set_lifecycle``. ``clear_identity`` drops
        the claimed account and pairing report when the operator chose a wipe.
        """

        model = type(self.bridge)
        with system_context(reason="integrate.live.account.disconnect"), transaction.atomic():
            row = (
                model.objects.sudo(reason="integrate.live.account.disconnect.row")
                .lock_if_supported()
                .get(pk=self.bridge.pk)
            )
            row.set_lifecycle(type(row).Lifecycle.DISCONNECTED)
            if clear_identity:
                self._write_state(row, drop_identity=True, drop_pairing_report=True)
        self.bridge.refresh_from_db()

    def release_account(self, *, desired: Any) -> None:
        """Record a void claim: drop account identity and live desire, never lifecycle.

        The worker's release. A runtime handshake that proved this row's account
        claim void drops that claim, but the operator declared lifecycle and a
        handshake outcome does not get to revoke it. ``desired`` is the stop
        signal the live task and reconciler both read.
        """

        model = type(self.bridge)
        with system_context(reason="integrate.live.account.release"), transaction.atomic():
            row = (
                model.objects.sudo(reason="integrate.live.account.release.row")
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
        """Apply one row's state/progress edits, saving only changed fields."""

        fields: list[str] = []
        state = dict(row.subscription_state)
        if drop_identity:
            state.pop(self.state_identity_key, None)
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

    def _account_owners(self, manager: Any, external_id: str) -> Any:
        """Return other bridges holding a durable claim on ``external_id``."""

        return (
            manager.filter(
                backend_class=self.key,
                **{f"subscription_state__{self.state_identity_key}": external_id},
                lifecycle__in=self.CLAIMING_LIFECYCLES,
            )
            .exclude(pk=self.bridge.pk)
            .order_by("pk")
        )

    def pairing(self) -> PairingProjection:
        """Project durable identity plus the latest transient pairing report."""

        report = self._pairing_report()
        reported = PairingState.from_report(report.get("state"))
        raw_identity = self.bridge.subscription_state.get(self.state_identity_key) or report.get("own_id") or ""
        own_id = self.normalize_account_id(str(raw_identity))
        state = self._pairing_state(reported=reported, identity=own_id)
        duplicate = self._duplicate_owner(own_id) if state is PairingState.DUPLICATE_ACCOUNT else None
        return PairingProjection(
            state=state,
            qr=str(report.get("qr") or "") if state is PairingState.AWAITING_SCAN else "",
            own_id=own_id,
            account_label=self.account_label(own_id) if own_id else "",
            duplicate_channel_id="" if duplicate is None else str(duplicate.sqid),
            duplicate_channel_name="" if duplicate is None else str(duplicate.display_name),
        )

    def _pairing_report(self) -> Mapping[str, Any]:
        """Return the live session's last pairing report off ``sync_progress``."""

        progress = self.bridge.sync_progress
        details = progress.get("details") if isinstance(progress, Mapping) else None
        pairing = details.get("pairing") if isinstance(details, Mapping) else None
        return pairing if isinstance(pairing, Mapping) else {}

    def _pairing_state(self, *, reported: PairingState | None, identity: str) -> PairingState:
        """Resolve rendered state from lifecycle, durable identity, then report."""

        lifecycle = type(self.bridge).Lifecycle.from_value(self.bridge.lifecycle)
        if lifecycle is type(self.bridge).Lifecycle.PAUSED:
            return PairingState.PAUSED
        if lifecycle is type(self.bridge).Lifecycle.DISCONNECTED:
            return PairingState.STOPPED
        if reported is PairingState.LOGGED_OUT or reported is PairingState.DUPLICATE_ACCOUNT:
            return reported
        if identity:
            return PairingState.PAIRED
        if self.bridge.subscription_state.get("desired") != self.bridge.LiveState.LIVE:
            return PairingState.STOPPED
        if reported is PairingState.AWAITING_SCAN:
            return PairingState.AWAITING_SCAN
        return PairingState.STARTING

    def _duplicate_owner(self, external_id: str) -> Any | None:
        """Return the bridge that already owns ``external_id``, in caller scope."""

        if not external_id:
            return None
        return self._account_owners(type(self.bridge).objects, external_id).first()

    def normalize_account_id(self, raw: str) -> str:
        """Return the durable account id stored on ``subscription_state``."""

        return str(raw or "").strip()

    def account_label(self, own_id: str) -> str:
        """Return a human label for ``own_id``."""

        return own_id

    def pairing_report_identity(self, own_id: str) -> dict[str, str]:
        """Return identity fields added to a transient pairing report."""

        return {"own_id": own_id, "account_label": self.account_label(own_id)}

    def duplicate_account_error(self) -> Exception:
        """Return the runtime error recorded for a duplicate account rejection."""

        return RuntimeError("Another bridge already owns this account.")

    def logged_out_error(self) -> SessionLoggedOut:
        """Return the runtime error raised when the linked account removes this session."""

        return SessionLoggedOut("The linked account removed this session.")


class Client(IntegrationImpl):
    """Base descriptor for an outbound client — it calls out to an external service.

    The counterpart of :class:`BridgeImpl` (which pulls data in): a client sends
    requests to a remote API. The call itself lives on the concrete subclass; this
    base only carries the ``client`` category.
    """

    category = "client"
    label = "Client"
    icon = "send"


class QueuedClient(Client):
    """Base for a client whose work is meant to run asynchronously, with retries.

    The vocabulary for calls too slow or failure-prone to run inline — outbound
    sends, or long-running remote jobs like training / video inference. A concrete
    subclass implements :meth:`run`; ``max_retries``/``retry_backoff_base_seconds``
    declare its retry policy.

    NOTE: no async dispatcher is wired yet. The stack earmarks Celery for queues and
    retries (``docs/stack.md``) but it is not locked, so this base only fixes the
    contract a future Celery (or due-time scanner) layer will drive — it must not be
    relied on for dispatch until that lands. A provider that submits a remote job and
    polls would persist the remote handle on its owning child model and reschedule
    until done.
    """

    max_retries: int = 5
    retry_backoff_base_seconds: int = 10

    def run(self, payload: dict[str, Any]) -> Any:
        """Perform one unit of queued work; implemented by the concrete client."""

        raise NotImplementedError
