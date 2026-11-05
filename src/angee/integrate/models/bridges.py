"""Native bridges owners for integration contracts."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, cast

from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.utils import timezone
from rebac import (
    database_context,
    system_context,
)
from rebac.database import write_alias

from angee.base.fields.implementation import ImplClassField
from angee.base.fields.state import StateField
from angee.base.fields.text import DiagnosticTextField
from angee.base.locks import LockKey, LockSnapshot, record_lock_key, task_lock, task_locks_are_cross_process
from angee.base.models.persistence import require_composed_type, require_projection
from angee.iam.models.base import ProtectedModel
from angee.integrate.backends.base import BridgeImpl, ConnectionDiscovery, IntegrationImpl, LiveBridgeImpl
from angee.integrate.choices import (
    IntegrationLifecycle,
    IntegrationRuntimeStatus,
)
from angee.integrate.errors import (
    IntegrationError,
    safe_integration_failure,
)
from angee.integrate.live import PairingProjection, PairingState, armed_material_key
from angee.integrate.locks import bridge_is_locked, bridge_lock_key, bridge_session_is_hosted
from angee.integrate.querysets import IntegrationQuerySet
from angee.integrate.states import (
    StreamState,
)
from angee.integrate.streams import sync_bridge
from angee.integrate.sync import SyncDispatch, bridge_progress_context, bridge_sync_context
from angee.jobs.enqueue import enqueue_task

if TYPE_CHECKING:
    from angee.integrate.models.connections import Credential
    from angee.integrate.models.integrations import Integration


logger = logging.getLogger(__name__)


def merge_json_state(
    instance: Integration,
    field_name: str,
    values: Mapping[str, Any],
    *,
    path: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Merge keys at a JSON path under the row lock, preserving sibling owners.

    Each supplied value replaces its key; omitted keys survive. This is shared by
    subscription state and cursor slices. Full opaque cursor replacement belongs
    to SyncStreamManager.advance. The instance receives the committed document.
    """

    if not isinstance(instance._meta.get_field(field_name), models.JSONField):
        raise TypeError("JSON state merge requires a JSONField.")
    using = write_alias(type(instance), using=instance._state.db, instance=instance)
    with database_context(using), system_context(reason="integrate.json_state.merge"), transaction.atomic(using=using):
        row = instance.lock_capability()
        value = getattr(row, field_name)
        document = deepcopy(value) if isinstance(value, dict) else {}
        node = document
        for key in path:
            if not isinstance(node.get(key), dict):
                node[key] = {}
            node = node[key]
        node.update(deepcopy(values))
        setattr(row, field_name, document)
        row.save(using=row._state.db, update_fields=[field_name, "updated_at"])
    setattr(instance, field_name, document)
    return document


class Bridge(ProtectedModel, concrete=False, demands="integrate.Integration"):
    """Abstract base for child models that synchronize or subscribe to vendor data.

    Pure bridge state and behavior. A materialized bridge extends
    ``integrate.Integration`` so common identity, credential, status, and audit
    fields stay on the integration parent row while bridge-specific settings stay
    on the child.
    """

    if TYPE_CHECKING:

        @property
        def capability_impl(self) -> BridgeImpl:
            """The integration parent's read-only selected bridge implementation."""
            ...

    class SyncStage(models.TextChoices):
        """Generic lifecycle stage for bridge sync jobs."""

        IDLE = "idle", "Idle"
        QUEUED = "queued", "Queued"
        DISCOVERING = "discovering", "Discovering"
        SYNCING = "syncing", "Syncing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class LiveState(models.TextChoices):
        """Desired live-ingest state persisted in ``subscription_state["desired"]``.

        The base owns this vocabulary: bridge start/stop methods write it, and a
        live implementation's session loop and reconciler read it. Stopping is
        cooperative - a running session notices ``STOPPED`` and exits.
        """

        LIVE = "live", "Live"
        STOPPED = "stopped", "Stopped"

    live_impl_field: ClassVar[str | None] = None
    """Name the ImplClassField that may select a long-lived session implementation.

    Periodic-only bridges leave this unset. Their periodic sync remains in the due
    scheduler, but the live-session reconciler does not inspect or dispatch them.
    """

    config = models.JSONField(default=dict, blank=True)
    """Bridge-scoped settings interpreted by the selected backend."""
    poll_interval = models.PositiveIntegerField(default=300)
    subscription_state = models.JSONField(default=dict, blank=True)
    last_sync_started_at = models.DateTimeField(null=True, blank=True)
    last_sync_completed_at = models.DateTimeField(null=True, blank=True)
    last_sync_items = models.PositiveIntegerField(default=0)
    sync_stage = StateField(choices_enum=SyncStage, max_length=32, default=SyncStage.IDLE, db_index=True)
    sync_error = DiagnosticTextField(blank=True, default="")
    sync_run_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    """Opaque execution-owner run identity, retained after terminal settlement."""
    sync_progress = models.JSONField(default=dict, blank=True)
    last_sync_summary = models.JSONField(default=dict, blank=True)
    next_sync_at = models.DateTimeField(null=True, blank=True, db_index=True)
    """Next scheduler poll. NULL means unscheduled: a bridge enters the poll loop
    when its first connected sync records a result; paused one-shot syncs keep it
    NULL. The scheduler claims a connected due row by pushing this one interval
    out for the duration of the run."""

    class Meta:
        """Django model options for abstract bridge inheritance."""

        abstract = True

    def source_url(self, link: Any) -> str | None:
        """Delegate a retained remote identity URL to the selected connector."""

        return self.capability_impl.source_url(link)

    def source_label(self, link: Any) -> str:
        """Delegate the remote label to the selected connector."""

        return self.capability_impl.source_label(link)

    @property
    def open_failure_count(self) -> int:
        """Unresolved failures of current epochs, independent of the last run's completion."""

        if "open_sync_streams" not in self.__dict__:
            require_projection(self, "open_sync_streams")
            return self.get_open_failure_count()
        streams = cast(list[Any], self.__dict__["open_sync_streams"])
        return sum(len(stream.sync_failures(open_only=True)) for stream in streams)

    def get_open_failure_count(self) -> int:
        """Count one bridge's unresolved failures explicitly outside a projected list."""

        return sum(
            len(stream.sync_failures(open_only=True))
            for stream in self.sync_streams.filter(state=StreamState.OPEN)
        )

    @classmethod
    def live_implementation_field(cls) -> ImplClassField | None:
        """Return this bridge's explicitly declared live implementation field."""

        field_name = cls.live_impl_field
        if field_name is None:
            return None
        field = cls._meta.get_field(field_name)
        if not isinstance(field, ImplClassField):
            raise ImproperlyConfigured(
                f"{cls._meta.label}.{field_name} must be an ImplClassField to select live implementations."
            )
        return field

    @classmethod
    def live_implementation_keys(cls) -> tuple[str, ...]:
        """Resolve the registered implementations that support a live session."""

        field = cls.live_implementation_field()
        if field is None:
            return ()
        keys = []
        for key in field.registered_keys():
            try:
                impl = field.resolve_class(key)
            except AttributeError, ImportError, ImproperlyConfigured:
                logger.exception("Skipping unresolvable %s live implementation %r.", cls._meta.label_lower, key)
                continue
            if issubclass(impl, LiveBridgeImpl):
                keys.append(key)
        return tuple(keys)

    def reconcile_live_session(self, *, cross_process: bool) -> str | None:
        """Dispatch a missing healthy session; return its queue when apparently starved."""

        impl = self.live_impl
        if not isinstance(impl, LiveBridgeImpl):
            return None
        if cross_process and (bridge_is_locked(self) or bridge_session_is_hosted(self)):
            return None
        if self.subscription_state.get("desired") != self.LiveState.LIVE:
            self.start_live()
        else:
            impl.start_live()
        return impl.session_queue if cross_process and self.sync_stage in self.LIVE_SYNC_STAGES else ""

    @property
    def live_impl(self) -> IntegrationImpl:
        """Return this bridge's selected runtime implementation."""

        field = type(self).live_implementation_field()
        if field is None:
            raise TypeError(f"{self._meta.label} is a periodic-only bridge.")
        impl_class = cast(type[IntegrationImpl], field.resolve_for(self))
        return impl_class(self)

    @property
    def is_syncing(self) -> bool:
        """Return whether a worker currently holds this bridge's live sync lock."""

        if "_is_syncing" not in self.__dict__:
            require_projection(self, "_is_syncing")
            return self.get_is_syncing()
        return bool(self.__dict__["_is_syncing"])

    def prime_is_syncing(self, locks: LockSnapshot) -> bool:
        """Project this bridge's live lock from the page's one lock snapshot."""

        self.__dict__["_is_syncing"] = locks.is_held(bridge_lock_key(self))
        return self.is_syncing

    def get_is_syncing(self) -> bool:
        """Check one bridge's live lock explicitly for non-list operations."""

        return bridge_is_locked(self)

    def live_account_lock_key(self, impl_key: str, external_id: str) -> LockKey:
        """Return the cross-worker lock for one backend-normalized account id."""

        if not external_id:
            raise ValueError("A live bridge account id is required.")
        return LockKey(f"{impl_key}-account", (external_id,))

    def live_account_lock(self, impl_key: str, external_id: str) -> Any:
        """Try to hold the account-scoped ownership lock."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        return task_lock(self.live_account_lock_key(impl_key, external_id), using=using)

    def live_account_owners(
        self,
        impl_key: str,
        external_id: str,
        *,
        identity_key: str,
        queryset: Any | None = None,
    ) -> Any:
        """Return other rows that retain the same durable live-account claim."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        scope = require_composed_type(
            queryset if queryset is not None else type(self).objects.db_manager(using).all(),
            IntegrationQuerySet,
        )
        return scope.live_account_owners(
            self,
            impl_key,
            external_id,
            identity_key=identity_key,
        )

    def claim_live_account(self, impl_key: str, external_id: str, *, identity_key: str) -> bool:
        """Persist this row's account claim when no live sibling owns it."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        if not external_id:
            raise ValueError("A live bridge account id is required.")
        model = type(self)
        with (
            system_context(reason="integrate.live.account.claim"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            row = (
                model.objects.db_manager(using)
                .sudo(reason="integrate.live.account.claim.row")
                .lock_if_supported()
                .get(pk=self.pk)
            )
            owners = row.live_account_owners(
                impl_key,
                external_id,
                identity_key=identity_key,
                queryset=model.objects.db_manager(using).sudo(reason="integrate.live.account.claim.owner"),
            )
            if owners.exists():
                return False
            state = dict(row.subscription_state)
            state[identity_key] = external_id
            row.subscription_state = state
            row.save(using=row._state.db, update_fields=["subscription_state", "updated_at"])
        self.refresh_from_db(
            using=using,
        )
        return True

    def update_live_state(
        self,
        *,
        identity_key: str,
        drop_identity: bool = False,
        drop_pairing_report: bool = False,
        desired: Any | None = None,
    ) -> None:
        """Apply durable live-session state changes and save only changed fields."""

        fields: list[str] = []
        state = dict(self.subscription_state)
        if drop_identity:
            state.pop(identity_key, None)
        if desired is not None:
            state["desired"] = str(getattr(desired, "value", desired))
        if state != self.subscription_state:
            self.subscription_state = state
            fields.append("subscription_state")
        if drop_pairing_report:
            progress = dict(self.sync_progress) if isinstance(self.sync_progress, Mapping) else {}
            details = dict(progress.get("details") or {}) if isinstance(progress.get("details"), Mapping) else {}
            if details.pop("pairing", None) is not None:
                progress["details"] = details
                self.sync_progress = progress
                fields.append("sync_progress")
        if fields:
            self.save(using=self._state.db, update_fields=[*fields, "updated_at"])

    def disconnect_live_account(self, *, identity_key: str, clear_identity: bool) -> None:
        """Disconnect under a row lock and optionally clear claim and pairing state."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        model = type(self)
        with (
            system_context(reason="integrate.live.account.disconnect"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            row = (
                model.objects.db_manager(using)
                .sudo(reason="integrate.live.account.disconnect.row")
                .lock_if_supported()
                .get(pk=self.pk)
            )
            row.set_lifecycle(type(row).Lifecycle.DISCONNECTED)
            if clear_identity:
                row.update_live_state(identity_key=identity_key, drop_identity=True, drop_pairing_report=True)
        self.refresh_from_db(
            using=using,
        )

    def release_live_account(self, *, identity_key: str, desired: Any) -> None:
        """Clear a void account claim without changing operator-owned lifecycle."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        model = type(self)
        with (
            system_context(reason="integrate.live.account.release"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            row = (
                model.objects.db_manager(using)
                .sudo(reason="integrate.live.account.release.row")
                .lock_if_supported()
                .get(pk=self.pk)
            )
            row.update_live_state(identity_key=identity_key, drop_identity=True, desired=desired)
        self.refresh_from_db(
            using=using,
        )

    def live_pairing(self, impl: Any) -> Any:
        """Project pairing state from durable row facts and backend identity formatting."""

        progress: Mapping[str, Any] = self.sync_progress if isinstance(self.sync_progress, Mapping) else {}
        details_value = progress.get("details")
        details: Mapping[str, Any] = details_value if isinstance(details_value, Mapping) else {}
        report_value = details.get("pairing")
        report: Mapping[str, Any] = report_value if isinstance(report_value, Mapping) else {}
        reported = PairingState.from_report(report.get("state"))
        raw_identity = self.subscription_state.get(impl.state_identity_key) or report.get("own_id") or ""
        own_id = impl.normalize_account_id(str(raw_identity))
        lifecycle = type(self).Lifecycle.from_value(self.lifecycle)
        if lifecycle is type(self).Lifecycle.PAUSED:
            state = PairingState.PAUSED
        elif lifecycle is type(self).Lifecycle.DISCONNECTED:
            state = PairingState.STOPPED
        elif reported in (PairingState.AWAITING_PASSWORD, PairingState.LOGGED_OUT, PairingState.DUPLICATE_ACCOUNT):
            state = reported
        elif own_id:
            state = PairingState.PAIRED
        elif self.subscription_state.get("desired") != self.LiveState.LIVE:
            state = PairingState.STOPPED
        elif reported is PairingState.AWAITING_SCAN:
            state = PairingState.AWAITING_SCAN
        else:
            state = PairingState.STARTING
        duplicate = None
        if own_id and state is PairingState.DUPLICATE_ACCOUNT:
            duplicate = self.live_account_owners(
                impl.key,
                own_id,
                identity_key=impl.state_identity_key,
            ).first()
        return PairingProjection(
            state=state,
            qr=str(report.get("qr") or "") if state is PairingState.AWAITING_SCAN else "",
            message=str(report.get("message") or "") if state is PairingState.AWAITING_PASSWORD else "",
            can_skip=(
                bool(report.get("can_skip")) and bool(armed_material_key(self.subscription_state))
                if state is PairingState.AWAITING_PASSWORD
                else False
            ),
            own_id=own_id,
            account_label=impl.account_label(own_id) if own_id else "",
            duplicate_channel_id="" if duplicate is None else str(duplicate.sqid),
            duplicate_channel_name="" if duplicate is None else str(duplicate.display_name),
        )

    # Direct syncs hold their advisory lock; dispatched syncs retain a run pointer
    # until their execution owner durably reports the terminal outcome.
    LIVE_SYNC_STAGES: ClassVar[tuple[SyncStage, ...]] = (
        SyncStage.DISCOVERING,
        SyncStage.SYNCING,
    )

    @property
    def effective_sync_stage(self) -> SyncStage:
        """Reconcile direct workers against their lock; dispatched runs settle durably.

        Queued work has not acquired a lock yet. A retained dispatch is owned by
        its execution engine until terminal delivery. Process-local locks cannot
        prove another worker's liveness, so that backend trusts the stored stage.
        """

        return self._effective_sync_stage(lambda: self.is_syncing)

    def get_effective_sync_stage(self) -> SyncStage:
        """Reconcile one bridge outside a projected list."""

        return self._effective_sync_stage(self.get_is_syncing)

    def _effective_sync_stage(self, is_syncing: Callable[[], bool]) -> SyncStage:
        """Apply the one live-lock reconciliation rule to either read shape."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        stage = self.SyncStage(self.sync_stage)
        if (
            stage in self.LIVE_SYNC_STAGES
            and not self.sync_is_dispatched
            and task_locks_are_cross_process(using=using)
            and not is_syncing()
        ):
            return self.SyncStage.FAILED
        return stage

    @property
    def sync_is_dispatched(self) -> bool:
        """Whether a retained run owns terminal reporting for this bridge."""

        return self.sync_run_id is not None and self.sync_stage in (self.SyncStage.QUEUED, *self.LIVE_SYNC_STAGES)

    def claim_dispatch(self, run_id: int, *, now: datetime | None = None) -> bool:
        """Claim a run only while the caller's observed execution owner is current.

        Admission owns run eligibility. This compare-and-set may replace a
        terminal run awaiting delivery, but never a concurrently claimed run.
        Repeating a busy claim leaves its start time and telemetry intact. A
        settled run may reclaim the bridge when its execution owner retries it.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        if type(run_id) is not int or run_id <= 0:
            raise ValueError("A positive run identity is required.")
        expected_run_id = self.sync_run_id
        with (
            system_context(reason="integrate.bridge.claim_dispatch"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            row = self.lock_capability()
            if not row.discovery_ready:
                return False
            if row.sync_run_id != expected_run_id or (row.sync_run_id == run_id and row.sync_is_dispatched):
                return False
            row.sync_run_id = run_id
            row.next_sync_at = None
            row.mark_sync_started(now=now or timezone.now())
            row.save(using=row._state.db, update_fields=["sync_run_id", "next_sync_at", "updated_at"])
        self.refresh_from_db(
            using=using,
        )
        return True

    def settle_dispatch(
        self,
        run_id: int,
        *,
        result: int = 0,
        error: Exception | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Settle the expected busy dispatch once, retaining its inspection ID."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        with (
            system_context(reason="integrate.bridge.settle_dispatch"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            row = self.lock_capability()
            if row.sync_run_id != run_id or not row.sync_is_dispatched:
                return False
            at = now or timezone.now()
            if error is None:
                row.record_sync(result, now=at)
            else:
                row.record_sync_error(error, now=at)
        self.refresh_from_db(
            using=using,
        )
        return True

    def sync_lock_key(self) -> LockKey:
        """Return the advisory task lock key for this bridge sync."""

        return record_lock_key(self._meta.label_lower, self.pk, "sync")

    def live_session_host_lock_key(self) -> LockKey:
        """Deduplicate process hosts without borrowing the child's store lock."""

        return record_lock_key(self._meta.label_lower, self.pk, "live-host")

    def report_live_session_interrupted(self, *, exitcode: int | None) -> None:
        """Report a reaped child failure while keeping automatic recovery eligible.

        The caller holds the bridge lock after the child exits. Native crashes
        are host failures, not evidence that the account needs fresh credentials.
        Terminal session outcomes and a concurrent operator stop remain intact.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        with (
            system_context(reason="integrate.live.interrupted"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            row = self.lock_capability()
            if (
                row.lifecycle != row.Lifecycle.CONNECTED
                or row.runtime_status != IntegrationRuntimeStatus.OK
                or row.subscription_state.get("desired") != row.LiveState.LIVE
            ):
                return
            with bridge_progress_context(row) as reporter:
                reporter.report(
                    row.SyncStage.FAILED,
                    message="The live session process stopped unexpectedly; automatic recovery will retry.",
                    details={"session": {"state": "interrupted", "exitcode": exitcode}},
                )

    _SYNC_OUTCOME_KEYS = ("error", "items", "completed_at")
    """Marker keys that describe one run's outcome; a new marker starts without them."""

    def report_progress(
        self,
        stage: str,
        *,
        message: str = "",
        details: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Merge telemetry under a row lock, preserving a pending queue stage."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        with database_context(using), transaction.atomic(using=using):
            row = (
                type(self)
                .objects.db_manager(using)
                .sudo(reason="integrate.bridge.progress")
                .lock_if_supported()
                .get(pk=self.pk)
            )
            existing = row.sync_progress if isinstance(row.sync_progress, Mapping) else {}
            payload = dict(existing)
            queue_pending = str(row.sync_stage) == str(row.SyncStage.QUEUED)
            if not queue_pending:
                payload["stage"] = str(stage)
            if message:
                payload["message"] = message
            elif "message" in payload:
                payload.pop("message")
            if details is not None:
                payload["details"] = dict(details)
            payload.update(extra)

            row.sync_progress = payload
            update_fields = ["sync_progress", "updated_at"]
            if not queue_pending and str(stage) in getattr(row.SyncStage, "values", ()):
                row.sync_stage = row.SyncStage(stage)
                update_fields.append("sync_stage")
            row.save(using=row._state.db, update_fields=update_fields)
        self.sync_progress = payload
        self.sync_stage = row.sync_stage
        return payload

    def _sync_marker(self, **values: Any) -> dict[str, Any]:
        """Return ``sync_progress`` with this run's marker keys set, others kept.

        ``sync_progress`` has disjoint owners: the scheduler writes the lifecycle
        marker (``stage``/``queued_at``/``started_at`` and the run's outcome),
        while ``report_progress`` writes ``details`` — a live session's pairing
        report lives there, and it is the QR the operator is currently looking
        at. Replacing the whole dict drops it, so a marker merges the way
        :meth:`report_progress` already does and
        sets only the keys it owns. A previous run's outcome is the marker's own
        to drop: a failed run's ``error`` must not ride into the next run's
        ``completed`` marker.
        """

        existing = self.sync_progress if isinstance(self.sync_progress, Mapping) else {}
        kept = {key: value for key, value in existing.items() if key not in self._SYNC_OUTCOME_KEYS}
        return {**kept, **values}

    def mark_sync_started(self, *, now: datetime) -> bool:
        """Admit one attempt under the connection gate, preserving repeated starts."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        fields = ["last_sync_started_at", "sync_error", "sync_progress", "sync_stage"]
        with system_context(reason="integrate.bridge.start"), database_context(using), transaction.atomic(using=using):
            row = self.lock_capability()
            if not row.discovery_ready:
                return False
            if row.sync_stage == row.SyncStage.SYNCING and row.last_sync_started_at == now:
                return True
            row.last_sync_started_at = now
            row.sync_stage = row.SyncStage.SYNCING
            row.sync_error = ""
            row.sync_progress = row._sync_marker(stage=row.SyncStage.SYNCING, started_at=now.isoformat())
            row.save(using=row._state.db, update_fields=[*fields, "updated_at"])
        self.refresh_from_db(using=using, fields=fields)
        return True

    def claim_sync(self, *, now: datetime) -> bool:
        """Claim the next interval only after the connection is ready for work."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        with system_context(reason="integrate.bridge.claim"), database_context(using), transaction.atomic(using=using):
            row = self.lock_capability()
            if not row.discovery_ready:
                return False
            row.next_sync_at = row._next_sync_at(now=now)
            row.save(using=row._state.db, update_fields=["next_sync_at", "updated_at"])
        self.next_sync_at = row.next_sync_at
        return True

    def request_sync(self, *, now: datetime | None = None, persist: bool = True) -> int:
        """Admit one queued sync and dispatch its durable timestamp token."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        if self.pk is None:
            raise ValueError("Cannot queue an unsaved bridge.")
        timestamp = now or timezone.now()
        if persist:
            with (
                system_context(reason="integrate.bridge.request_sync"),
                database_context(using),
                transaction.atomic(using=using),
            ):
                self.mark_sync_queued(now=timestamp)
        enqueue_task(
            "integrate.sync_bridge_now",
            kwargs={
                "model_label": self._meta.label_lower,
                "pk": self.pk,
                "timestamp": timestamp.isoformat(),
                "using": using,
            },
            using=using,
        )
        return 1

    def mark_sync_queued(self, *, now: datetime) -> None:
        """Persist that a worker task has been queued for this bridge."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        with system_context(reason="integrate.bridge.queue"), database_context(using), transaction.atomic(using=using):
            row = self.lock_capability()
            if not row.discovery_ready:
                raise IntegrationError("Connection discovery must finish before syncing.")
            if not row.sync_is_dispatched:
                row.sync_stage = self.SyncStage.QUEUED
                row.sync_error = ""
                row.sync_run_id = None
                row.sync_progress = row._sync_marker(stage=self.SyncStage.QUEUED, queued_at=now.isoformat())
                row.save(
                    using=row._state.db,
                    update_fields=["sync_error", "sync_run_id", "sync_progress", "sync_stage", "updated_at"],
                )
        self.sync_stage, self.sync_error, self.sync_progress = row.sync_stage, row.sync_error, row.sync_progress
        self.sync_run_id = row.sync_run_id

    def reset_sync_queue(self, *, now: datetime) -> None:
        """Make a failed queue dispatch due again for the next scheduler pass."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        self.next_sync_at = now
        self.sync_stage = self.SyncStage.IDLE
        self.sync_progress = {}
        with database_context(using), transaction.atomic(using=using):
            self.save(using=self._state.db, update_fields=["next_sync_at", "sync_progress", "sync_stage", "updated_at"])

    @property
    def sync_queue_occurrence(self) -> str | None:
        """Return the declared queued timestamp token, including dispatched replay."""

        if not isinstance(self.sync_progress, Mapping):
            return None
        token = self.sync_progress.get("queued_at")
        return token if isinstance(token, str) and token else None

    def sync_queue_token_matches(self, timestamp: datetime) -> bool:
        """Return whether a queued task payload still matches this bridge row."""

        return self.sync_stage == self.SyncStage.QUEUED and self.sync_queue_occurrence == timestamp.isoformat()

    def release_sync_queue(self, *, now: datetime) -> bool:
        """Release a queue claim this run declined; return whether it was still ours.

        A declined run is terminal for its queue token — the work is not happening.
        Leaving the row ``QUEUED`` makes the stale-queue recovery in
        ``angee.integrate.scheduler`` re-queue it every ``_QUEUED_RECOVERY_SECONDS``
        forever, because that recovery exists to repair a *lost* enqueue and cannot
        tell one from a declined run. So the task that declines the work is the one
        that clears the claim.

        Guarded by the queue token and re-read under a row lock: whoever holds the
        sync lock may already have moved this row on (a concurrent
        :meth:`mark_sync_started`), and the decliner must never overwrite the
        holder's stage. Mirrors :meth:`merge_subscription_state`.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        with (
            system_context(reason="integrate.bridge.release_sync_queue"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            row = self.lock_capability()
            if not row.sync_queue_token_matches(now):
                return False
            row.sync_stage = row.SyncStage.IDLE
            row.save(using=row._state.db, update_fields=["sync_stage", "updated_at"])
        self.sync_stage = self.SyncStage.IDLE
        return True

    @property
    def requires_connection_discovery(self) -> bool:
        """Whether the selected implementation declares asynchronous discovery."""

        if not any(isinstance(field, ImplClassField) for field in self._meta.fields):
            return False
        return self.capability_impl_class.requires_connection_discovery

    def discover_connection(self, credential: Credential) -> ConnectionDiscovery:
        """Invoke the adapter's read-only remote hook outside a transaction."""

        adapter = self.capability_impl
        try:
            return adapter.discover_connection(credential)
        finally:
            adapter.close()

    def apply_discovery(self, discovery: ConnectionDiscovery) -> None:
        """Compose the domain's database apply hook under the integration-owned fence."""

        if not self.requires_connection_discovery:
            if discovery.data or discovery.credential is not None:
                raise IntegrationError("This bridge does not accept connection discovery results.")
            return
        adapter = self.capability_impl
        try:
            adapter.apply_discovery(discovery)
        finally:
            adapter.close()

    def prepare_connection_discovery(self) -> None:
        """Suspend polling and cooperatively stop a live session without changing sync ownership."""

        self.next_sync_at = None
        self.save(using=self._state.db, update_fields=["next_sync_at", "updated_at"])
        if not self.requires_connection_discovery:
            return
        impl = self.capability_impl_class
        if issubclass(impl, LiveBridgeImpl):
            self.update_live_state(identity_key=impl.state_identity_key, desired=self.LiveState.STOPPED)

    def connection_discovery_finished(self, *, succeeded: bool) -> None:
        """Arm the first eligible poll; retained workflow runs keep their scheduling claim."""

        now = timezone.now()
        self.next_sync_at = now if succeeded and not self.sync_is_dispatched and self._next_sync_at(now=now) else None
        self.save(using=self._state.db, update_fields=["next_sync_at", "updated_at"])

    def record_sync(self, result: int, *, now: datetime) -> None:
        """Persist one successful scheduler sync result and healthy status report."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        self.last_sync_completed_at = now
        self.last_sync_items = result
        self.sync_stage = self.SyncStage.COMPLETED
        self.sync_error = ""
        self.sync_progress = self._sync_marker(
            stage=self.SyncStage.COMPLETED,
            items=result,
            completed_at=now.isoformat(),
        )
        self.last_sync_summary = {
            "status": "ok",
            "items": result,
            "completed_at": now.isoformat(),
        }
        with database_context(using), transaction.atomic(using=using):
            # Credential attachment may have started discovery during extraction.
            self.lock_capability()
            self.refresh_from_db(
                using=using,
                fields=[
                    "discovery_retry_at",
                    "discovery_generation",
                    "discovery_completed_at",
                    "lifecycle",
                ],
            )
            self.next_sync_at = self._next_sync_at(now=now)
            if self.discovery_ready:
                self.report_status(status=IntegrationRuntimeStatus.OK)
            self.save(
                using=self._state.db,
                update_fields=[
                    "last_sync_summary",
                    "last_sync_completed_at",
                    "last_sync_items",
                    "next_sync_at",
                    "sync_error",
                    "sync_progress",
                    "sync_stage",
                    "updated_at",
                ],
            )

    def record_sync_error(self, error: Exception, *, now: datetime) -> None:
        """Persist one failed scheduler sync result and error status report."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        failure = safe_integration_failure(error)
        error_message = failure.message
        self.sync_stage = self.SyncStage.FAILED
        self.sync_error = error_message
        self.sync_progress = self._sync_marker(stage=self.SyncStage.FAILED, error=error_message)
        with database_context(using), transaction.atomic(using=using):
            self.lock_capability()
            self.refresh_from_db(
                using=using,
                fields=[
                    "discovery_retry_at",
                    "discovery_generation",
                    "discovery_completed_at",
                    "lifecycle",
                ],
            )
            self.next_sync_at = self._next_sync_at(now=now)
            if self.next_sync_at is not None and isinstance(error, IntegrationError) and error.retry_after is not None:
                self.next_sync_at = max(self.next_sync_at, max(now, timezone.now()) + error.retry_after)
            if self.discovery_ready:
                self.report_status(status=IntegrationRuntimeStatus.ERROR, error=failure)
            self.save(
                using=self._state.db,
                update_fields=[
                    "next_sync_at",
                    "sync_error",
                    "sync_progress",
                    "sync_stage",
                    "updated_at",
                ],
            )

    def clear_sync_error(self) -> None:
        """Clear a stale failure once a live session proves the account healthy.

        The mirror of :meth:`record_sync_error` — that method is the only writer
        of ``sync_error``, ``sync_stage == FAILED``, and the ``error``
        outcome key, and only the poll scheduler's start/finish markers drop them
        again. A live-desired bridge never enters the poll loop
        (:meth:`Channel._next_sync_at` returns ``None`` while ``desired`` is
        ``live``), so a months-old failure would otherwise outlive a successful
        reconnect forever. :meth:`LiveSession._mark_paired` calls this the moment
        it reports ``OK``: reaching ``PAIRED`` is the proof the failure is no
        longer true, and resume/start is not — a failure stays true until the
        session actually pairs.

        Re-reads under a row lock because the session's pairing report
        (:meth:`report_progress`) is the concurrent
        writer of the same ``sync_progress`` dict: the pairing ``details`` (the QR
        the operator is looking at) and the scheduler's ``stage``/``queued_at``
        markers must survive. Dropping the failure is exactly :meth:`_sync_marker`
        with no new values — it keeps everything but this run's outcome keys. A row
        with nothing to clear is left untouched so a healthy reconnect writes
        nothing to the change feed.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        with (
            system_context(reason="integrate.bridge.clear_sync_error"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            row = self.lock_capability()
            progress = row.sync_progress if isinstance(row.sync_progress, Mapping) else {}
            if not row.sync_error and row.sync_stage != row.SyncStage.FAILED and "error" not in progress:
                return
            row.sync_stage = row.SyncStage.COMPLETED if row.last_sync_completed_at else row.SyncStage.IDLE
            row.sync_error = ""
            row.sync_progress = row._sync_marker()
            row.save(
                using=row._state.db,
                update_fields=[
                    "sync_stage",
                    "sync_error",
                    "sync_progress",
                    "updated_at",
                ],
            )
        self.sync_stage = row.sync_stage
        self.sync_error = row.sync_error
        self.sync_progress = row.sync_progress

    def run_sync(self, *, now: datetime) -> int | SyncDispatch:
        """Run one sync attempt and persist its lifecycle telemetry."""

        if self.sync_is_dispatched:
            return SyncDispatch.DISPATCHED
        if not self.mark_sync_started(now=now):
            raise IntegrationError("Connection discovery must finish before syncing.")
        try:
            with bridge_sync_context(), bridge_progress_context(self):
                result = self.sync()
            if result is SyncDispatch.DISPATCHED:
                return result
            # Partitioned syncs report through thread-local Bridge instances.
            # Re-read their last merged payload before this parent writes the
            # terminal marker, or its stale in-memory value drops budget/cursor
            # detail. The scheduler timestamp identifies the attempt; completion
            # is when the external work actually finished.
            self.refresh_from_db(fields=["sync_progress"])
            self.record_sync(result, now=timezone.now())
        except Exception as error:
            self.refresh_from_db(fields=["sync_progress"])
            self.record_sync_error(error, now=timezone.now())
            raise
        return result

    def sync(self) -> int | SyncDispatch:
        """Drive backend streams; concrete bridges may override this sync seam."""

        return sync_bridge(self)

    def start_live(self) -> None:
        """Start or renew this bridge's live vendor subscription."""

        raise NotImplementedError("Bridge subclasses must implement start_live().")

    def stop_live(self) -> None:
        """Stop this bridge's live vendor subscription."""

        raise NotImplementedError("Bridge subclasses must implement stop_live().")

    def merge_subscription_state(self, **values: Any) -> dict[str, Any]:
        """Merge sub-keys into ``subscription_state`` under a row lock; return it.

        This method merges only — a key can never be removed; consumed markers
        settle to a sentinel value defined by their owning flow.
        ``subscription_state`` has disjoint owners — the operator writes
        ``desired`` (start/stop), a live session writes its own pairing facts —
        that may write concurrently from separately-loaded instances. A full
        read-modify-write from a stale instance would clobber the other owner's
        key, so this re-reads the row locked, merges only the given keys, and
        saves through the shared nested-JSON owner.
        """

        return merge_json_state(self, "subscription_state", values)

    def _next_sync_at(self, *, now: datetime) -> datetime | None:
        """Return the next polling timestamp from this bridge's interval.

        ``None`` keeps paused/disconnected bridges unscheduled after an explicit
        one-shot sync. A push-mode child (a live chat channel) may also override
        this to stay out of the poll loop while its live ingest owns delivery.
        """

        if (
            IntegrationLifecycle.from_value(self.lifecycle) is not IntegrationLifecycle.CONNECTED
            or not self.discovery_ready
        ):
            return None
        return now + timedelta(seconds=int(self.poll_interval))
