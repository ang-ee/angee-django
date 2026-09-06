"""Integration implementation descriptors.

An ``Integration`` row stores the registry key for integration-level behaviour.
Concrete addons contribute subclasses through ``ANGEE_INTEGRATION_IMPLS``; persisted
domain state belongs on real child models, not on descriptor-owned companion rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from angee.base.impl import ImplBase
from angee.integrate.connect import enabled_oauth_client_from_hint
from angee.integrate.constants import RUN_SESSION_TASK, SESSION_START_EXPIRES
from angee.integrate.live import PairingProjection, SessionLoggedOut
from angee.jobs.enqueue import enqueue_task
from angee.jobs.locks import LockKey


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

    Bridges run through the queued due scheduler over ``Bridge.next_sync_at`` and
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
    transient_material_keys: ClassVar[tuple[str, ...]] = ("password",)

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

        return self.bridge.live_account_lock_key(self.key, self.normalize_account_id(external_id))

    @contextmanager
    def account_lock(self, external_id: str) -> Iterator[bool]:
        """Try to hold the account-scoped ownership lock."""

        with self.bridge.live_account_lock(self.key, self.normalize_account_id(external_id)) as acquired:
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

        return self.bridge.claim_live_account(
            self.key,
            self.normalize_account_id(external_id),
            identity_key=self.state_identity_key,
        )

    def mark_disconnected(self, *, clear_identity: bool) -> None:
        """Record the operator's disconnect: lifecycle released, identity optional.

        The operator declares the lifecycle, so this write moves it through the
        Integration's own idempotent ``set_lifecycle``. ``clear_identity`` drops
        the claimed account and pairing report when the operator chose a wipe.
        """

        self.bridge.disconnect_live_account(identity_key=self.state_identity_key, clear_identity=clear_identity)

    def release_account(self, *, desired: Any) -> None:
        """Record a void claim: drop account identity and live desire, never lifecycle.

        The worker's release. A runtime handshake that proved this row's account
        claim void drops that claim, but the operator declared lifecycle and a
        handshake outcome does not get to revoke it. ``desired`` is the stop
        signal the live task and reconciler both read.
        """

        self.bridge.release_live_account(identity_key=self.state_identity_key, desired=desired)

    def pairing(self) -> PairingProjection:
        """Project durable identity plus the latest transient pairing report."""

        return self.bridge.live_pairing(self)

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
