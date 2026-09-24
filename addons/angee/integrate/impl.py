"""Implementation descriptors owned by concrete integration capabilities."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from angee.base.db import get_write_alias, related_on
from angee.base.impl import ImplBase
from angee.integrate.connect import enabled_oauth_client_from_hint
from angee.integrate.constants import RUN_SESSION_TASK, SESSION_START_EXPIRES
from angee.integrate.live import PairingProjection, SessionLoggedOut
from angee.jobs.enqueue import enqueue_task
from angee.jobs.locks import LockKey

if TYPE_CHECKING:
    from angee.integrate.streams import (
        ApplyResult,
        LocalChange,
        RecordChange,
        StreamDefinition,
        StreamPage,
        WriteBackResult,
    )


class AdapterContractError(TypeError):
    """A bridge implementation violated the record-sync contract."""


class IntegrationImpl(ImplBase):
    """Base descriptor for one row-selected integration implementation."""

    category = "none"
    label = "Integration"
    icon = ""
    oauth_client: ClassVar[str] = ""

    def __init__(self, integration: Any) -> None:
        """Bind this implementation to its owning integration row."""

        self.integration = integration

    def connect_oauth_client(self, owner_label: str, *, using: str | None = None) -> Any:
        """Return the enabled OAuth client this integration connects through.

        Falls back to the bound integration's vendor slug when the implementation
        declares no ``oauth_client`` hint; the vendor slug also feeds the
        ``{vendor}`` template.
        """

        using = get_write_alias(type(self.integration), using=using, instance=self.integration)
        self.integration._state.db = using
        vendor = related_on(self.integration, "vendor", using=using, required=False)
        vendor_slug = str(getattr(vendor, "slug", "") or "")
        hint = str(self.oauth_client or "")
        return enabled_oauth_client_from_hint(
            hint or vendor_slug,
            owner_label=owner_label,
            reason="integrate.graphql.connect_integration.oauth_client",
            vendor_slug=vendor_slug,
            using=using,
        )


class BridgeImpl(IntegrationImpl):
    """Base descriptor for a bridge that exchanges data with an external system.

    Bridges run through the queued due scheduler over ``Bridge.next_sync_at`` and
    keep their sync state on a concrete ``Bridge`` child model.
    """

    category = "bridge"
    label = "Bridge"
    icon = "plug"
    sync_parallelism: ClassVar[int | None] = None
    """Optional protocol cap; the bridge config and database may lower it."""
    supports_identity_reads: ClassVar[bool] = False
    """Declare identity reads; otherwise retries request a fresh baseline."""

    def streams(self, *, deadline: float | None = None, using: str | None = None) -> Iterable[StreamDefinition]:
        """Declare each independently ordered partition exactly once."""
        raise AdapterContractError("Stream adapters must declare their partitions.")

    def seed_config(self, legacy_cursor: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return config defaults and the remaining legacy cursor without mutating it.

        Remove migrated policy from the returned cursor so later partitions cannot
        restore it after an operator changes config. Retain their progress for
        ``seed_cursor``. The driver persists both values under the bridge lock.
        """
        return {}, legacy_cursor

    def seed_cursor(self, stream: Any, legacy_cursor: dict[str, Any]) -> dict[str, Any] | None:
        """Translate a legacy bridge position once when opening its first empty epoch."""
        return None

    def extract(
        self, stream: Any, page_bound: int, *, deadline: float | None = None, using: str | None = None
    ) -> StreamPage:
        """Fetch at most page_bound records outside transactions within the deadline."""
        raise AdapterContractError("Stream adapters must extract bounded pages.")

    def read_keys(self, stream: Any, keys: Sequence[str], *, using: str | None = None) -> Iterable[RecordChange]:
        """Read every requested identity exactly once, including missing-key tombstones."""
        raise AdapterContractError("This adapter does not support identity reads.")

    def apply_record(self, stream: Any, record: Any, *, using: str | None = None) -> ApplyResult:
        """Apply one record using database work only; return its applied evidence.

        The driver owns primary link promotion. Record-local refusals raise
        SemanticError or ValidationError; infrastructure failures propagate.
        """
        raise AdapterContractError("Stream adapters must apply individual records.")

    def close(self) -> None:
        """Release any transport resources after the caller finishes the cycle."""

    @property
    def bridge(self) -> Any:
        """Return the concrete bridge child this implementation is bound to."""

        return self.integration

    def enumerate_keys(self, stream: Any, *, after: str | None = None, using: str | None = None) -> Iterable[str]:
        """Yield unique remote identities in stable order, resuming after a key.

        Seek directly past ``after`` instead of materializing the inventory or
        replaying its prefix. The adapter owns its identity ordering.
        """

        raise AdapterContractError("Replica adapters must enumerate remote keys.")

    def prepare_page(self, stream: Any, page: StreamPage, *, using: str | None = None) -> None:
        """Lock the page's complete identity/target set before record savepoints.

        This optional hook runs inside the page transaction: database work only,
        in a canonical order. Fetch all remote facts during extraction.
        """

    def on_revalidated(self, stream: Any, links: Sequence[Any], *, using: str | None = None) -> None:
        """Restore native projection visibility after unchanged-row revalidation."""

    def on_absent(self, stream: Any, links: Sequence[Any], *, using: str | None = None) -> None:
        """Withdraw native projection visibility when absence changes link status."""

    def finish_page(
        self, stream: Any, page: StreamPage, outcomes: Sequence[ApplyResult], *, using: str | None = None
    ) -> None:
        """Finish domain batch relationships before the page cursor commits."""

    def local_changes(
        self, stream: Any, *, keys: frozenset[str] | None = None, using: str | None = None
    ) -> Iterable[LocalChange]:
        """Project only selected local identities, or all candidates when keys is None."""

        raise AdapterContractError("Push adapters must project local changes.")

    def write_back(
        self, link: Any, projection: Any, *, expected_version: str, using: str | None = None
    ) -> WriteBackResult:
        """Conditionally write a remote record or raise RemoteRejected."""

        raise AdapterContractError("Push adapters must implement conditional write-back.")


class LiveBridgeImpl(BridgeImpl):
    """Base descriptor for a bridge backed by a long-lived live session task."""

    session_queue: ClassVar[str] = ""
    session_isolation: ClassVar[Literal["thread", "process"]] = "thread"
    """Use a fresh interpreter per session when the vendor can abort its host."""
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

    def start_live(self, *, using: str | None = None) -> None:
        """Dispatch this bridge's live session to its dedicated queue.

        Safe to repeat: the session task's non-blocking advisory-lock acquire
        makes a duplicate start exit immediately, and ``expires`` keeps an
        undelivered start from outliving the next reconciler tick.
        """

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        self.bridge._state.db = using
        if not self.session_queue:
            raise ImproperlyConfigured(
                f"{type(self).__name__} must define session_queue; a long-lived session on the "
                "shared prefork queue is killed by the global time limit and starves the pool."
            )
        enqueue_task(
            RUN_SESSION_TASK,
            kwargs={"model_label": self.bridge._meta.label_lower, "pk": self.bridge.pk, "using": using},
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

    def claim_account(self, external_id: str, *, using: str | None = None) -> bool:
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

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        self.bridge._state.db = using
        return self.bridge.claim_live_account(
            self.key,
            self.normalize_account_id(external_id),
            identity_key=self.state_identity_key,
        )

    def mark_disconnected(self, *, clear_identity: bool, using: str | None = None) -> None:
        """Record the operator's disconnect: lifecycle released, identity optional.

        The operator declares the lifecycle, so this write moves it through the
        Integration's own idempotent ``set_lifecycle``. ``clear_identity`` drops
        the claimed account and pairing report when the operator chose a wipe.
        """

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        self.bridge._state.db = using
        self.bridge.disconnect_live_account(identity_key=self.state_identity_key, clear_identity=clear_identity)

    def release_account(self, *, desired: Any, using: str | None = None) -> None:
        """Record a void claim: drop account identity and live desire, never lifecycle.

        The worker's release. A runtime handshake that proved this row's account
        claim void drops that claim, but the operator declared lifecycle and a
        handshake outcome does not get to revoke it. ``desired`` is the stop
        signal the live task and reconciler both read.
        """

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        self.bridge._state.db = using
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
