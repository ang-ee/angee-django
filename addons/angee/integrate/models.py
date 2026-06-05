"""Source models for Angee's integration runtime primitives.

Migration note: account-scoped webhook subscriptions now cascade with their
account; a human must run ``uv run examples/notes-angee/manage.py makemigrations integrate``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django_sqids import SqidsField
from rebac import system_context
from rebac.managers import RebacManager

from angee.base.fields import EncryptedField, StateField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.base.relations import grant_owner
from angee.integrate.events import EventKind
from angee.integrate.net import validate_public_url
from angee.integrate.webhooks import PinnedWebhookClient, WebhookDeliveryError

logger = logging.getLogger(__name__)


class CapabilityStatus(models.TextChoices):
    """Lifecycle state for a concrete integration capability."""

    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    ERROR = "error", "Error"
    DISABLED = "disabled", "Disabled"


class Capability(SqidMixin, AuditMixin, AngeeModel):
    """Abstract base for domain-owned capabilities.

    The concrete domain subclass owns its ``rebac_resource_type``. A pure base:
    ``_composer_emits = False`` keeps it out of runtime emission (the domain
    subclass is what the composer emits).
    """

    _composer_emits = False

    account = models.ForeignKey("iam.ExternalAccount", on_delete=models.PROTECT, related_name="+")
    config = models.JSONField(default=dict)
    status = StateField(choices_enum=CapabilityStatus, default=CapabilityStatus.ACTIVE)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_status = models.CharField(max_length=64, blank=True)
    use_count_24h = models.PositiveIntegerField(default=0)
    error_count_24h = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Django model options for abstract capability inheritance."""

        abstract = True

    def report_status(self, *, status: CapabilityStatus | str, error: str = "") -> None:
        """Record local status telemetry and push this capability's account contribution.

        The caller owns persistence for this capability row. ``ExternalAccount``
        owns its rollup write and tracks each capability by the reporting
        capability primary key.
        """

        reported_at = timezone.now()
        self.status = status  # type: ignore[assignment]  # StateField descriptor unmodeled by django-stubs
        self.last_used_at = reported_at
        self.last_used_status = str(status)
        self.last_error = error
        self.last_error_at = reported_at if error else None

        self.account.note_capability_status(capability_key=str(self.pk), status=status, error=error)


class Bridge(Capability):
    """Abstract base for capabilities that synchronize or subscribe to vendor data.

    Another pure base — ``_composer_emits`` is non-inherited, so this re-declares
    the opt-out (``Capability``'s does not carry down).
    """

    _composer_emits = False

    cursor = models.JSONField(default=dict)
    poll_interval = models.PositiveIntegerField(default=300)
    subscription_state = models.JSONField(default=dict)
    next_subscription_refresh_at = models.DateTimeField(null=True, blank=True)
    last_sync_started_at = models.DateTimeField(null=True, blank=True)
    last_sync_completed_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=64, blank=True)
    last_sync_items = models.PositiveIntegerField(default=0)
    next_sync_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        """Django model options for abstract bridge inheritance."""

        abstract = True

    def mark_sync_started(self, *, now: datetime) -> None:
        """Persist the start timestamp for one scheduler sync attempt."""

        self.last_sync_started_at = now
        with transaction.atomic():
            self.save(update_fields=["last_sync_started_at", "updated_at"])

    def record_sync(self, result: int, *, now: datetime) -> None:
        """Persist one successful scheduler sync result and healthy status report."""

        self.last_sync_completed_at = now
        self.last_sync_status = "ok"
        self.last_sync_items = result
        self.next_sync_at = self._next_sync_at(now=now)
        with transaction.atomic():
            self.report_status(status="active")
            self.save(
                update_fields=[
                    "cursor",
                    "last_error",
                    "last_error_at",
                    "last_sync_completed_at",
                    "last_sync_items",
                    "last_sync_status",
                    "last_used_at",
                    "last_used_status",
                    "next_sync_at",
                    "status",
                    "updated_at",
                ]
            )

    def record_sync_error(self, error: Exception, *, now: datetime) -> None:
        """Persist one failed scheduler sync result and error status report."""

        error_message = f"{type(error).__name__}: {error}"[:500]
        self.last_sync_status = "error"
        self.next_sync_at = self._next_sync_at(now=now)
        with transaction.atomic():
            self.report_status(status="error", error=error_message)
            self.save(
                update_fields=[
                    "last_error",
                    "last_error_at",
                    "last_sync_status",
                    "last_used_at",
                    "last_used_status",
                    "next_sync_at",
                    "status",
                    "updated_at",
                ]
            )

    def sync(self) -> int:
        """Synchronize this bridge with its external system."""

        raise NotImplementedError("Bridge subclasses must implement sync().")

    def handle_webhook(self, payload: Any) -> None:
        """Apply one verified inbound webhook payload to this bridge."""

        raise NotImplementedError("Bridge subclasses must implement handle_webhook().")

    def verify_webhook(self, request: Any) -> bool:
        """Return whether an inbound webhook request is authentic for this bridge."""

        raise NotImplementedError("Bridge subclasses must implement verify_webhook().")

    def dispatch_inbound(self, request_or_payload: Any) -> bool:
        """Verify one inbound webhook and apply it to this bridge when authentic."""

        if not self.verify_webhook(request_or_payload):
            return False
        self.handle_webhook(request_or_payload)
        return True

    def start_live(self) -> None:
        """Start or renew this bridge's live vendor subscription."""

        raise NotImplementedError("Bridge subclasses must implement start_live().")

    def stop_live(self) -> None:
        """Stop this bridge's live vendor subscription."""

        raise NotImplementedError("Bridge subclasses must implement stop_live().")

    def _next_sync_at(self, *, now: datetime) -> datetime:
        """Return the next polling timestamp from this bridge's interval."""

        return now + timedelta(seconds=int(self.poll_interval))


class WebhookSubscriptionManager(RebacManager):
    """Manager for webhook subscriptions and their owner relationship grants."""

    def create(self, **kwargs: Any) -> Any:
        """Create a subscription, then grant its owner under a narrow bypass.

        The create itself stays REBAC-authorized (the integrate/webhook_subscription
        ``create`` permission gates it); only the owner-relation write — which no
        actor holds permission for — runs under ``system_context``.
        """

        with transaction.atomic():
            instance = super().create(**kwargs)
            with system_context(reason="integrate.webhook_subscription.owner_grant"):
                grant_owner(instance, instance.owner)
        return instance

    def deliver_event(
        self,
        *,
        kind: EventKind,
        payload: Any,
        impl_app: str = "",
        account: Any | None = None,
    ) -> dict[str, int]:
        """Deliver one integration event to every matching enabled subscription.

        Actor-less framework fan-out: it reads subscriptions across all owners, so
        it runs under ``system_context``. Each subscription matches and delivers
        itself; this method only owns the row-set loop and the success/error tally.
        """

        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        delivered = 0
        errors = 0
        with system_context(reason="integrate.webhooks.deliver"):
            for subscription in self.filter(enabled=True).order_by("pk"):
                if not subscription.matches(kind=kind, impl_app=impl_app, account=account):
                    continue
                try:
                    status = subscription.deliver(body)
                except Exception as exc:
                    logger.exception("Webhook delivery failed for subscription %s.", subscription.public_id)
                    subscription.record_delivery_failure(
                        status=self._failure_status(exc),
                        error=self._error_message(exc),
                    )
                    errors += 1
                else:
                    subscription.record_delivery(status)
                    delivered += 1
        return {"delivered": delivered, "errors": errors}

    @staticmethod
    def _failure_status(exc: Exception) -> str:
        """Return an HTTP status string from a delivery exception when available."""

        if isinstance(exc, WebhookDeliveryError):
            return exc.status
        return ""

    @staticmethod
    def _error_message(exc: Exception) -> str:
        """Return a compact telemetry message for a delivery exception."""

        if isinstance(exc, ValidationError):
            return "; ".join(str(message) for message in exc.messages)
        return f"{type(exc).__name__}: {exc}"


class WebhookSubscription(SqidMixin, AuditMixin, AngeeModel):
    """Outbound webhook endpoint owned by one user."""

    sqid = SqidsField(real_field_name="id", prefix="whs", min_length=8)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="webhook_subscriptions",
    )
    target_url = models.URLField(max_length=2048, validators=(validate_public_url,))
    secret = EncryptedField()
    event_kinds = models.JSONField(default=list)
    impl_app_filter = models.JSONField(default=list)
    account_filter = models.ForeignKey(
        "iam.ExternalAccount",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    enabled = models.BooleanField(default=True, db_index=True)
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_delivery_status = models.CharField(max_length=64, blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    consecutive_failures = models.PositiveIntegerField(default=0)

    objects = WebhookSubscriptionManager()

    class Meta:
        """Django model options for webhook subscriptions."""

        abstract = True
        rebac_resource_type = "integrate/webhook_subscription"
        rebac_id_attr = "sqid"

    _delivery_update_fields = (
        "consecutive_failures",
        "last_delivery_at",
        "last_delivery_status",
        "last_error",
        "updated_at",
    )

    def matches(self, *, kind: str, impl_app: str, account: Any | None) -> bool:
        """Return whether this subscription should receive one event."""

        if kind not in {str(value) for value in self.event_kinds or ()}:
            return False
        impl_app_filter = tuple(str(value) for value in self.impl_app_filter or ())
        if impl_app_filter and impl_app not in impl_app_filter:
            return False
        if self.account_filter_id is None:
            return True
        return account is not None and self.account_filter_id == getattr(account, "pk", None)

    def deliver(self, body: bytes) -> str:
        """POST one signed event body to this subscription's pinned target; raise on non-2xx."""

        return PinnedWebhookClient(str(self.target_url)).post(secret=str(self.secret), body=body)

    def record_delivery(self, status: str) -> None:
        """Persist success telemetry for one delivery attempt (mirrors ``Bridge.record_sync``)."""

        self.last_delivery_at = timezone.now()
        self.last_delivery_status = status
        self.last_error = ""
        self.consecutive_failures = 0
        self.save(update_fields=self._delivery_update_fields)

    def record_delivery_failure(self, *, status: str, error: str) -> None:
        """Persist failure telemetry for one delivery attempt (mirrors ``Bridge.record_sync_error``).

        Takes the already-classified ``status``/``error``: the delivery layer
        owns turning a delivery exception into those strings.
        """

        self.last_delivery_at = timezone.now()
        self.last_delivery_status = status
        self.last_error = error
        # Atomic add so concurrent fan-outs to the same subscription don't lose an
        # increment — this counter is the thing failure policy gates on.
        self.consecutive_failures = models.F("consecutive_failures") + 1
        self.save(update_fields=self._delivery_update_fields)
