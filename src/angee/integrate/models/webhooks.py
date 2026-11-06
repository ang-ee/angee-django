"""Native webhooks owners for integration contracts."""

from __future__ import annotations

import logging
import secrets

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from angee.base.fields.encrypted import EncryptedField
from angee.base.fields.text import DiagnosticTextField
from angee.base.serialization import canonical_json
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.ownership import OwnerMixin
from angee.integrate.managers import (
    WebhookSubscriptionManager,
)
from angee.integrate.validators import validate_public_url
from angee.integrate.webhooks import PinnedWebhookClient, WebhookDeliveryError

logger = logging.getLogger(__name__)

_WEBHOOK_FAILURE_MESSAGE = "Webhook delivery failed."


class WebhookSubscription(OwnerMixin, ProtectedDataModel, concrete=True):
    """Outbound webhook endpoint owned by one user."""

    sqid_prefix = "whs_"
    target_url = models.URLField(max_length=2048, validators=(validate_public_url,))
    secret = EncryptedField()
    impl_app_filter = models.JSONField(default=list, blank=True)
    integration_filter = models.ForeignKey(
        "integrate.Integration",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    enabled = models.BooleanField(default=True, db_index=True)
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_delivery_status = models.CharField(max_length=64, blank=True, default="")
    last_error = DiagnosticTextField(blank=True, default="")
    consecutive_failures = models.PositiveIntegerField(default=0)

    objects = WebhookSubscriptionManager()

    class Meta:
        """Django model options for webhook subscriptions."""

        abstract = True
        rebac_resource_type = "integrate/webhook_subscription"

    _delivery_update_fields = (
        "consecutive_failures",
        "last_delivery_at",
        "last_delivery_status",
        "last_error",
        "updated_at",
    )

    def deliver(self, body: bytes) -> str:
        """POST one signed event body to this subscription's pinned target; raise on non-2xx."""

        return PinnedWebhookClient(str(self.target_url)).post(secret=str(self.secret), body=body)

    def deliver_recorded(self, body: bytes) -> tuple[bool, str]:
        """Deliver one body, persist telemetry, and return ``(ok, status_or_error)``."""

        try:
            status = self.deliver(body)
        except Exception as exc:
            logger.error(
                "Webhook delivery failed for subscription %s (%s).",
                self.public_id,
                type(exc).__name__,
            )
            message = self._delivery_error_message(exc)
            self.record_delivery_failure(status=self._delivery_failure_status(exc), error=message)
            return False, message
        self.record_delivery(status)
        return True, status

    def deliver_test(self) -> tuple[bool, str]:
        """Send a test event, persist telemetry, and return an action result tuple."""

        body = canonical_json({"type": "test", "subscription": self.public_id}).encode("utf-8")
        ok, result = self.deliver_recorded(body)
        return (True, f"Delivered (status {result}).") if ok else (False, f"Delivery failed: {result}")

    @staticmethod
    def _delivery_failure_status(exc: Exception) -> str:
        """Return an HTTP status string from a delivery exception when available."""

        if isinstance(exc, WebhookDeliveryError):
            return exc.status
        return ""

    @staticmethod
    def _delivery_error_message(exc: Exception) -> str:
        """Return a compact telemetry message for a delivery exception."""

        if isinstance(exc, WebhookDeliveryError) and exc.status:
            return f"Webhook returned HTTP {exc.status}."
        if isinstance(exc, ValidationError):
            return "Webhook target is invalid."
        return _WEBHOOK_FAILURE_MESSAGE

    def rotate_secret(self) -> str:
        """Generate a new signing secret, persist it, and return the plaintext once.

        The subscription owns its signing material: a console action calls this to
        roll the secret. The plaintext is returned only here (for one-time display);
        reads never expose it.
        """

        new_secret = secrets.token_urlsafe(32)
        self.secret = new_secret
        self.save(update_fields=["secret", "updated_at"])
        return new_secret

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
        self.refresh_from_db(fields=("consecutive_failures",))
