"""Channels models for Messaging; public imports remain on the package."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, router, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from django_compose.models import Composable
from rebac import (
    system_context,
)

from angee.base import locks as model_locks
from angee.base.fields.implementation import ImplClassField
from angee.integrate.impl import LiveBridgeImpl
from angee.integrate.live import PairingProjection, armed_material_key, skipped_password_marker
from angee.integrate.models import Bridge, IntegrationCreateMode, IntegrationLifecycle, IntegrationRuntimeStatus
from angee.messaging.backends import ChannelBackend, PairingActionError
from angee.messaging.managers import (
    ChannelManager,
)
from angee.messaging.webforms import WebformChanged, WebformSpec, WebformUnavailable, default_webform_schema
from angee.provenance.statements import (
    SourceKind,
)


class Channel(Bridge, inherits="integrate.Integration"):
    """A connected message transport for inbound and outbound email or social data.

    An ``integrate.Integration`` child (credential / owner / status from the
    connection substrate) and a ``Bridge`` (the scheduler + ``syncIntegration`` drive
    it through ``run_sync``). ``backend_class`` selects the protocol, contributed by
    the ``messaging_integrate_*`` addons (``imap``, the chat bridges), and ``config``
    carries source settings. ``sync()`` fetches + parses, then maps each message onto
    the messaging managers; outbound tasks resolve the same backend and call its
    ``deliver`` hook. Content sources may extend Channel while retaining their
    own backend and overlay.
    """

    rebac_grantable = {"reader": "write", "replier": "write"}
    integration_create_mode: ClassVar[IntegrationCreateMode | None] = IntegrationCreateMode.CONNECT
    live_impl_field: ClassVar[str | None] = "backend_class"

    backend_class = ImplClassField(ChannelBackend,
        default="manual",
        create_only=True,
    )
    """Registry key for the channel backend bound to this channel."""

    objects = ChannelManager()

    class Meta:
        """Django model options for the channel child model."""

        abstract = True
        rebac_resource_type = "messaging/channel"

    @property
    def backend(self) -> ChannelBackend:
        """Return this channel's selected backend, bound to this row."""

        backend_class = self.get_backend_class_impl()
        return backend_class(self)

    @property
    def source_kind(self) -> SourceKind | None:
        """The selected protocol declares whether this channel asserts statements, and as what."""

        return self.get_backend_class_impl().source_kind

    def can_reconcile_delivery(self) -> bool:
        """Read the concrete capability's backend declaration without opening it."""

        capability = self.concrete_capability()
        if capability._meta.concrete_model is not self._meta.concrete_model:
            return bool(capability.can_reconcile_delivery())
        return self.get_backend_class_impl().reconciles_delivery()

    def delivery_visibility_delay(self) -> timedelta:
        """Read the selected transport's propagation margin without opening it."""

        capability = self.concrete_capability()
        if capability._meta.concrete_model is not self._meta.concrete_model:
            return cast(timedelta, capability.delivery_visibility_delay())
        return self.get_backend_class_impl().delivery_visibility_delay

    def validate_webform_answers(self, answers: dict[str, Any]) -> None:
        """Validate domain meaning after the public form's scalar validation.

        Same-row Channel donors may override this hook and call ``super()`` to
        cooperate with other contributors. Raise ``ValidationError`` with answer
        field names as keys so the public form can return structured errors.
        """

        del answers

    def test_connection(self) -> str:
        """Exercise the selected backend's connection (the Integration test contract)."""

        return self.backend.test_connection()

    def purge_blockers(self) -> list[models.Model]:
        """Let model extensions contribute rows protecting this channel from purge.

        The purge owner counts its large ingested subtree separately; extensions
        return only retaining rows, whose names the preview scopes to its viewer.
        Native FK protection remains the authoritative delete check.
        """
        blockers = []
        for label in ("Thread", "Message"):
            pairs = apps.get_model("messaging", label).same_as.model
            blockers.extend(pairs._base_manager.filter(
                models.Q(left__channel_id=self.pk) | models.Q(right__channel_id=self.pk),
            ).order_by("pk"))
        return blockers

    def pairing(self) -> PairingProjection:
        """Return one live channel's vendor-neutral pairing projection."""

        return self.require_live_backend().pairing()


    def resume_pairing(self) -> None:
        """Start a live channel, clearing its runtime error but retaining identity.

        A stale pairing report (a dead QR, a rejected account) is dropped only when
        no session provably runs: a running session owns the report it is showing,
        and a process-local lock backend cannot see a worker's session at all.
        """

        impl = self.require_live_backend()
        with system_context(reason="messaging.resume_channel_pairing"):
            self.refresh_from_db(from_queryset=type(self)._base_manager.select_related("credential"))
            if model_locks.task_locks_are_cross_process() and not self.is_syncing:
                self.update_live_state(identity_key=impl.state_identity_key, drop_pairing_report=True)
            self.set_lifecycle(IntegrationLifecycle.CONNECTED)
            self.report_status(IntegrationRuntimeStatus.OK)
            self.start_live()


    @sensitive_variables("password", "material")
    def submit_pairing_password(self, password: str) -> None:
        """Store one transient secret under the armed material key and signal readiness."""

        self.require_live_backend()
        if not password:
            raise PairingActionError("A channel password is required.")
        with system_context(reason="messaging.submit_channel_password"):
            self.refresh_from_db(from_queryset=type(self)._base_manager.select_related("credential"))
            material_key = armed_material_key(self.subscription_state)
            if not material_key:
                raise PairingActionError("This channel is not awaiting a password.")
            credential = self.credential
            if credential is None:
                raise PairingActionError("This channel has no credential for password input.")
            credential.update_material(**{material_key: password})
            # See ``LiveSession._mark_awaiting_password`` for the awaiting tri-state.
            self.merge_subscription_state(awaiting="")


    def skip_pairing_password(self) -> None:
        """Skip one armed optional secret round and signal that choice to the session."""

        impl = self.require_live_backend()
        with system_context(reason="messaging.skip_channel_password"):
            self.refresh_from_db(from_queryset=type(self)._base_manager.select_related("credential"))
            material_key = armed_material_key(self.subscription_state)
            if not material_key:
                raise PairingActionError("This channel is not awaiting a password.")
            if not impl.pairing().can_skip:
                raise PairingActionError("This channel password cannot be skipped.")
            self.merge_subscription_state(awaiting=skipped_password_marker(material_key))


    def disconnect_pairing(self) -> None:
        """Stop a live channel and release its account while retaining pairing material."""

        impl = self.require_live_backend()
        with system_context(reason="messaging.disconnect_channel"):
            self.stop_live()
            impl.mark_disconnected(clear_identity=False)


    def outbound_sender(self) -> tuple[str, str]:
        """Return this connected channel's backend-owned default email sender."""
        if self.lifecycle != self.Lifecycle.CONNECTED:
            raise ValidationError("Select a connected email channel.")
        return self.backend.outbound_sender()

    def require_live_backend(self) -> LiveBridgeImpl:
        """Return the selected live backend or reject a poll-only channel."""
        impl = self.live_impl
        if not isinstance(impl, LiveBridgeImpl):
            raise PairingActionError("This action requires a live channel.")
        return impl

    def start_live(self) -> None:
        """Mark this channel live-desired, then dispatch the backend's live ingest.

        The Bridge live contract for channels: the base persists the desired
        state (so a reconciler can restart a dropped session), the selected
        backend owns the vendor dispatch. A poll-only backend's no-op hook makes
        this safely idempotent on any channel. The desire is merged under a row
        lock so a concurrent live session writing its own pairing keys cannot
        clobber it.
        """

        self.merge_subscription_state(desired=self.LiveState.LIVE)
        self.backend.start_live()

    def stop_live(self) -> None:
        """Mark this channel stop-desired, then dispatch the backend's live stop.

        A running live session notices the persisted desire on its next wake and
        exits cooperatively; the backend hook exists for vendors that also need
        an active teardown call. The desire is merged under a row lock so the
        running session cannot clobber it with a stale write.
        """

        self.merge_subscription_state(desired=self.LiveState.STOPPED)
        self.backend.stop_live()

    def _next_sync_at(self, *, now: Any) -> Any:
        """A live-desired channel stays out of the poll loop — push ingest owns it.

        ``record_sync``/``record_sync_error`` recompute the next poll through
        this hook, so a live channel's manual sync or live-session error never
        re-arms polling; a stopped or never-live channel keeps the interval.
        """

        if self.subscription_state.get("desired") == self.LiveState.LIVE:
            return None
        return super()._next_sync_at(now=now)


class ChannelWebform(Composable, extends="messaging.Channel"):
    """Same-row public-form configuration and message mapping for ``messaging.Channel``."""


    hasura_readable_fields: ClassVar[tuple[str, ...]] = (
        "slug",
        "is_published",
        "form_schema_version",
        "form_schema",
        "max_body_bytes",
        "max_field_bytes",
    )
    hasura_filterable_fields: ClassVar[tuple[str, ...]] = ("slug", "is_published", "form_schema_version")
    hasura_sortable_fields: ClassVar[tuple[str, ...]] = hasura_filterable_fields
    hasura_aggregatable_fields: ClassVar[tuple[str, ...]] = ()
    hasura_groupable_fields: ClassVar[tuple[str, ...]] = ("is_published", "form_schema_version")
    hasura_updatable_fields: ClassVar[tuple[str, ...]] = hasura_readable_fields

    slug = models.SlugField(max_length=80, blank=True, default="")
    is_published = models.BooleanField(default=False)
    form_schema_version = models.PositiveSmallIntegerField(
        default=1,
        validators=(MinValueValidator(1),),
    )
    form_schema = models.JSONField(default=default_webform_schema, blank=True)
    max_body_bytes = models.PositiveIntegerField(
        default=65_536,
        validators=(MinValueValidator(1),),
    )
    max_field_bytes = models.PositiveIntegerField(
        default=4_096,
        validators=(MinValueValidator(1),),
    )

    class Meta:
        """Abstract webform columns and the public-slug identity constraint."""

        abstract = True
        constraints = (
            models.UniqueConstraint(
                fields=("slug",),
                condition=~models.Q(slug=""),
                name="uq_messaging_channel_webform_slug",
            ),
        )

    def clean(self) -> None:
        """Keep published/form-specific facts attached only to webform channels."""

        super().clean()
        is_webform = str(self.backend_class) == "webform"
        errors: dict[str, str] = {}
        if (self.slug or self.is_published) and not is_webform:
            errors["backend_class"] = "A public form requires the webform channel backend."
        if self.is_published and not self.slug:
            errors["slug"] = "A published public form requires a slug."
        if errors:
            raise ValidationError(errors)
        if is_webform:
            self.webform_spec()

    def submit_webform(self, parsed: Any) -> None:
        """Compare the observed published configuration and ingest atomically."""
        using = self._state.db or router.db_for_write(type(self), instance=self)
        with system_context(reason=f"public form {self.slug}"), transaction.atomic(using=using):
            locked = type(self).objects.using(using).published_webforms(self.slug).select_for_update().filter(
                pk=self.pk,
            ).first()
            if locked is None:
                raise WebformUnavailable
            if any(getattr(locked, name) != getattr(self, name) for name in (
                "form_schema_version", "form_schema", "max_body_bytes", "max_field_bytes",
            )):
                raise WebformChanged
            apps.get_model("messaging", "Message").objects.db_manager(using).ingest(
                [parsed], channel=locked, quote_edges=False,
            )

    def webform_spec(self) -> WebformSpec:
        """Return this row's server-validated versioned form spec."""

        return WebformSpec.deserialize(
            self.form_schema,
            version=int(self.form_schema_version),
        )

    def webform_message(
        self,
        *,
        submission_id: str,
        answers: dict[str, Any],
    ) -> Any:
        """Map one validated answer envelope to messaging's neutral ingest DTO."""

        from angee.messaging.backends import (
            ParsedHandle,
            ParsedMessage,
            ParsedPart,
            ParsedThread,
        )

        spec = self.webform_spec()
        email_field = spec.email_field
        email = str(answers.get(email_field.name) or "").strip() if email_field else ""
        content_answers = (
            {name: value for name, value in answers.items() if name != email_field.name} if email_field else answers
        )
        stable_id = f"webform:{self.slug}:{submission_id}"
        sender = ParsedHandle(
            platform="other",
            value=stable_id,
            external_id=stable_id,
            display_name="Anonymous",
        )
        title = str(self.display_name or f"Public form {self.slug}")
        now = timezone.now()
        return ParsedMessage(
            external_id=stable_id,
            platform="other",
            sender=sender,
            sent_at=now,
            received_at=now,
            thread=ParsedThread(
                external_id=f"webform:{self.slug}",
                title=title,
                metadata={"webform_slug": self.slug},
            ),
            body=ParsedPart(
                type="text/plain",
                role="body",
                text=spec.render_markdown(content_answers),
            ),
            metadata={
                "webform": {
                    "slug": self.slug,
                    "schema_version": int(self.form_schema_version),
                    "submission_id": submission_id,
                    "answers": content_answers,
                    "unverified_submitter_email": email or None,
                }
            },
        )
