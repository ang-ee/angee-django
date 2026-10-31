"""Native integrations owners for integration contracts."""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from copy import copy
from datetime import datetime, timedelta
from typing import Any, ClassVar, Self, cast

import httpx2
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import OperationalError, connections, models, transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from rebac import (
    PermissionDenied,
    database_context,
    system_context,
)
from rebac.database import read_alias, write_alias

from angee.base.fields.implementation import ImplClassField
from angee.base.fields.state import StateField
from angee.base.fields.text import DiagnosticTextField
from angee.base.lineage import concrete_child, concrete_child_models
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.implementations import ImplDefaultsMixin
from angee.base.models.ownership import OwnerMixin
from angee.base.models.transitions import StateTransitions, save_state, transition
from angee.base.permissions import actor_user_id, instance_actor
from angee.graphql.capabilities import held_permissions
from angee.integrate.backends.base import ConnectionDiscovery
from angee.integrate.choices import (
    IntegrationCreateMode,
    IntegrationLifecycle,
    IntegrationRuntimeStatus,
)
from angee.integrate.constants import DISCOVERY_ATTEMPT_LIMIT, DISCOVERY_TASK, DISCOVERY_TASK_EXPIRES
from angee.integrate.errors import (
    INTEGRATION_FAILURE_MESSAGE,
    IntegrationError,
    IntegrationFailure,
    safe_integration_failure,
)
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.managers import (
    IntegrationManager,
    VendorManager,
)
from angee.integrate.models.bridges import Bridge
from angee.integrate.models.connections import Credential
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.registry import models_with
from angee.integrate.signals import discovery_finished
from angee.integrate.streams import preview_push
from angee.jobs.enqueue import enqueue_task
from angee.provenance.contrib.angee.managers import AngeeHistoryUnscopedManager
from angee.provenance.statements import (
    SourceKind,
    StatementSourceMixin,
)
from angee.resources.mixins import ResourceLoadMixin

logger = logging.getLogger(__name__)

_UNSET = object()
_INTEGRATION_FAILURE_MESSAGE = INTEGRATION_FAILURE_MESSAGE
_DISCOVERY_RETRY_EXHAUSTED_MESSAGE = "Connection discovery exhausted its retries; retry discovery to continue."


class Vendor(ResourceLoadMixin, AuditMixin, AngeeDataModel, concrete=True):
    """Admin-managed third-party catalogue (GitHub, Google, Slack, …).

    The single source of truth for "what is this third party" — branding and
    reference metadata only. New integration addons add their own row via an
    master-tier resource seed (``adopt: slug``). The connect-side ``OAuthClient``
    carries its own ``slug``; that is a deliberately independent namespace, not a
    foreign key into this catalogue.
    """

    catalogue = True
    catalogue_tier = "master"

    sqid_prefix = "vnd_"
    slug = models.SlugField(unique=True)
    display_name = models.CharField(max_length=128)
    website_url = models.URLField(blank=True)
    icon = models.CharField(max_length=128, blank=True)
    description = models.TextField(blank=True)

    objects = VendorManager()

    class Meta:
        """Django model options for integration vendors."""

        abstract = True
        ordering = ("slug",)
        rebac_resource_type = "integrate/vendor"

    def __str__(self) -> str:
        """Return the display label used by Django surfaces."""

        return self.display_name or self.slug


def _save_lifecycle(instance: Integration, source: Any, target: Any, **kwargs: Any) -> None:
    """Acquire the final capability before the transition primitive locks its caller."""

    instance.lock_capability()
    save_state(instance, source, target, **kwargs)


def _save_connected(instance: Integration, source: Any, target: Any, **kwargs: Any) -> None:
    """The connected transition owns discovery and the resulting first poll."""

    _save_lifecycle(instance, source, target, **kwargs)
    instance.await_discovery()
    instance.refresh_from_db(using=instance._state.db)


class Integration(StatementSourceMixin, ImplDefaultsMixin, OwnerMixin, AngeeDataModel, concrete=True):
    """A product/workspace integration to a vendor account.

    The first-class "what we're connected to and what runs over it": it draws a
    ``credential`` (and optionally an ``account``) from the connection substrate to
    authenticate, points at a catalogue ``vendor``, and stores the implementation
    capability. Domain-specific behavior, state, and config live on concrete child
    models. ``owner`` is the user owning a personal connection; a platform-managed
    install is inserted with ``save(ownerless=True)`` and keeps no owner.
    """

    rebac_grantable = {"reader": "write"}
    integration_create_mode: ClassVar[IntegrationCreateMode | None] = None

    Lifecycle = IntegrationLifecycle
    """Expose the lifecycle vocabulary off the row for callers that cannot import this module."""

    sqid_prefix = "int_"
    # Operator-given label (the connect flow sets it); blank falls back to the
    # vendor-derived :attr:`display_label`. The one human name for every child
    # (directory, channel, …), so it is not re-buried in each child's config.
    display_name = models.CharField(max_length=255, blank=True, default="")
    concrete_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, null=True, editable=False)
    """Concrete MTI model identity used for server-side grouping."""

    def declared_concrete_model(self) -> type[Integration] | None:
        """Resolve the saved final MTI kind through Django's ContentType cache."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        if self.concrete_type_id is None:
            return None
        model = ContentType.objects.db_manager(using).get_for_id(self.concrete_type_id).model_class()
        if model is not None:
            assert issubclass(model, Integration)
        return model

    @property
    def source_kind(self) -> SourceKind | None:
        """The capability child owns classification of this persisted principal."""

        model = self.declared_concrete_model()
        if model is None or model is type(self):
            return super().source_kind
        # The declared kind may sit several MTI levels down (an Odoo instance is a
        # Channel, a Channel an Integration): resolve it along the parent chain.
        child = self._concrete_child(model, actor=None, authorized=False)
        return child.source_kind if child is not None else None

    @classmethod
    def check(cls, **kwargs: Any) -> list[Any]:
        """Require one unambiguous primary-key path for each capability child."""

        errors = super().check(**kwargs)
        for child in (model for model in cls._meta.apps.get_models() if model is not cls and issubclass(model, cls)):
            if len(child._meta.parents) > 1:
                errors.append(
                    checks.Error(
                        f"{child._meta.label} has more than one Integration parent path.",
                        hint="Declare one primary-key MTI parent for a routed capability.",
                        obj=child,
                        id="integrate.E004",
                    )
                )
        return errors

    @classmethod
    def concrete_child_models(cls) -> tuple[type[Self], ...]:
        """Return installed direct children in stable model-label order."""

        return concrete_child_models(cls)

    @classmethod
    def concrete_child_cache_attr(cls, child_model: type[Integration], *, authorized: bool) -> str:
        """Return the private per-query cache name for one concrete child type."""

        scope = "authorized" if authorized else "integrity"
        return f"_angee_integration_child_{scope}_{child_model._meta.app_label}_{child_model._meta.model_name}"

    def _concrete_child[ChildT: Integration](
        self, model: type[ChildT], *, actor: Any, authorized: bool
    ) -> ChildT | None:
        """Return one child from this row's collection cache or scoped storage."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        queryset = (
            model.objects.db_manager(using).with_actor(actor)
            if authorized
            else model.objects.db_manager(using).sudo(reason="integrate.integration.child_integrity")
        )
        if type(self) not in model._meta.parents:
            parent_model = next(iter(model._meta.parents))
            assert issubclass(parent_model, Integration)
            parent = self._concrete_child(parent_model, actor=actor, authorized=False)
            if parent is None:
                return None
            return concrete_child(
                parent,
                model,
                queryset=queryset,
                cache_attr=type(self).concrete_child_cache_attr(model, authorized=authorized),
            )
        return concrete_child(
            self,
            model,
            queryset=queryset,
            cache_attr=type(self).concrete_child_cache_attr(model, authorized=authorized),
        )

    def concrete_children(
        self, *, actor: Any, exposed_model_labels: set[str]
    ) -> tuple[list[Integration], list[Integration]]:
        """Return installed and actor-readable child rows without leaking denied siblings."""

        integrity: list[Integration] = []
        authorized: list[Integration] = []
        declared = self.declared_concrete_model()
        for child_model in type(self).concrete_child_models():
            if declared is not None and declared is not child_model and issubclass(declared, child_model):
                child_model = cast(type[Integration], declared)
            child = self._concrete_child(child_model, actor=actor, authorized=False)
            if child is None:
                continue
            integrity.append(child)
            if child_model._meta.label in exposed_model_labels:
                visible = self._concrete_child(child_model, actor=actor, authorized=True)
                if visible is not None:
                    authorized.append(visible)
        return integrity, authorized

    @classmethod
    def capability_impl_field(cls) -> ImplClassField:
        """Use the nearest capability's selector, including MTI refinements."""

        for owner in (cls, *cls._meta.get_parent_list()):
            fields = [field for field in owner._meta.local_fields if isinstance(field, ImplClassField)]
            if len(fields) == 1:
                return fields[0]
            if fields:
                raise ImproperlyConfigured(
                    f"{owner._meta.label} must declare one capability implementation field; found {len(fields)}."
                )
        raise ImproperlyConfigured(f"{cls._meta.label} has no capability implementation field.")

    @property
    def capability_impl_class(self) -> type[Any]:
        """Resolve the selected implementation without opening its transport."""

        model = self.declared_concrete_model() or type(self)
        field = model.capability_impl_field()
        key = self.__dict__.get("_connection_impl_key")
        return field.resolve_class(key) if key is not None else field.resolve_for(self.concrete_capability())

    @property
    def capability_impl(self) -> Any:
        """Construct the runtime selected by this concrete capability."""

        return self.capability_impl_class(self)

    @property
    def oauth_client_hint(self) -> str:
        """Select connection intent without constructing the capability runtime."""

        return self.capability_impl_class.oauth_client

    def connection_oauth_slug(self, owner_label: str = "") -> str:
        """Expand this capability's declared registration using its vendor identity."""

        hint = str(self.oauth_client_hint or "").strip()
        if not hint:
            raise OAuthFlowError(
                "oauth_client_not_connectable",
                400,
                f"{owner_label or self._meta.label} has no OAuth client.",
            )
        return hint.format(vendor=self.connection_vendor_slug())

    def connect_oauth_client(self, owner_label: str) -> Any:
        """Resolve the ready registration for this capability's declared connection."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        model = apps.get_model("integrate", "OAuthClient")
        client = (
            model.objects.db_manager(using)
            .sudo(reason="integrate.integration.connect_oauth_client")
            .enabled_for_slug(
                self.connection_oauth_slug(owner_label),
            )
        )
        if client is None:
            raise OAuthFlowError("oauth_client_not_connectable", 400, f"{owner_label} has no enabled OAuth client.")
        return client

    @classmethod
    def credential_projection_expressions(cls) -> dict[str, Any]:
        """Public health scalars from a protected credential, without a joined object."""

        using = read_alias(cls)

        credential = (
            apps.get_model("integrate", "Credential")
            .objects.db_manager(using)
            .sudo(
                reason="integrate.connection.public_health",
            )
            .filter(pk=OuterRef("credential_id"))
        )
        return {
            f"_connection_{name}": Subquery(credential.values(name)[:1])
            for name in ("status", "expires_at", "last_refresh_status")
        }

    @classmethod
    def oauth_projection_expressions(cls) -> dict[str, Any]:
        """Read the vendor hint and nearest concrete selector as elevated scalars."""

        using = read_alias(cls)

        vendor = (
            apps.get_model("integrate", "Vendor")
            .objects.db_manager(using)
            .sudo(
                reason="integrate.connection.public_vendor",
            )
            .filter(pk=OuterRef("vendor_id"))
        )
        selectors = []
        base = apps.get_model("integrate", "Integration")
        for model in sorted(apps.get_models(), key=lambda model: model._meta.label_lower):
            if model is base or not issubclass(model, base):
                continue
            if not any(isinstance(field, ImplClassField) for field in model._meta.fields):
                continue
            field = model.capability_impl_field()
            rows = (
                model.objects.db_manager(using)
                .sudo(reason="integrate.connection.public_selector")
                .filter(pk=OuterRef("pk"))
            )
            selectors.append(
                models.When(
                    concrete_type_id=ContentType.objects.db_manager(using).get_for_model(model).pk,
                    then=Subquery(rows.values(field.name)[:1]),
                )
            )
        return {
            "_connection_vendor_slug": Subquery(vendor.values("slug")[:1]),
            "_connection_impl_key": models.Case(*selectors, default=models.Value(""), output_field=models.CharField()),
        }

    def connection_vendor_slug(self) -> str:
        """Read branding used by connection selection without requiring Vendor read."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        if "_connection_vendor_slug" in self.__dict__:
            return str(self.__dict__["_connection_vendor_slug"] or "")
        vendor = apps.get_model("integrate", "Vendor")
        return str(
            vendor.objects.db_manager(using)
            .sudo(reason="integrate.connection.vendor")
            .values_list(
                "slug",
                flat=True,
            )
            .get(pk=self.vendor_id)
        )

    def is_oauth_connectable(self, *, ready_slugs: Collection[str] | None = None) -> bool:
        """Use registration readiness without constructing a runtime or loading secrets."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        if (self.declared_concrete_model() or type(self)) is apps.get_model("integrate", "Integration"):
            return False
        try:
            slug = self.connection_oauth_slug()
        except OAuthFlowError:
            return False
        if ready_slugs is not None:
            return slug in ready_slugs
        return (
            apps.get_model("integrate", "OAuthClient")
            .objects.db_manager(using)
            .connectable()
            .filter(slug=slug)
            .exists()
        )

    def can_connect(
        self,
        actor: Any = None,
        *,
        write_access: bool | None = None,
        ready_slugs: Collection[str] | None = None,
    ) -> bool:
        """OAuth Connect requires ownership, write, registration and consent need.

        A healthy grant with failed discovery is repaired by Retry, so that row
        never offers both Retry and Connect.
        """

        row = copy(self).with_actor(actor) if actor is not None else self
        return (
            row.owner_id == actor_user_id(instance_actor(row))
            and (row.has_access("write") if write_access is None else write_access)
            and (row.credential_id is None or row.is_reconnect_required)
            and row.is_oauth_connectable(ready_slugs=ready_slugs)
        )

    def require_connect(self, actor: Any) -> None:
        """Enforce the same OAuth admission projected to the record client."""

        self.require_access("write", actor)
        if not self.can_connect(actor):
            raise PermissionDenied("Integration OAuth Connect is unavailable to the current user.")

    def can_pause(self) -> bool:
        """Whether the guarded Pause verb is eligible for this row."""

        return self.lifecycle_transitions.can_run(self, "pause")

    def can_disconnect(self) -> bool:
        """Whether the guarded Disconnect verb is eligible for this row."""

        return self.lifecycle_transitions.can_run(self, "disconnect")

    def can_resume(self) -> bool:
        """Operator Resume reuses an attached grant and never substitutes for consent."""

        return (
            "resume"
            in held_permissions(
                self,
                ("resume",),
                model=apps.get_model("integrate", "Integration"),
            )
            and not self.is_reconnect_required
            and (
                self.lifecycle == IntegrationLifecycle.PAUSED
                or (self.lifecycle == IntegrationLifecycle.DISCONNECTED and self.credential_id is not None)
            )
        )

    def can_retry_discovery(self) -> bool:
        """Retry only failed, settled discovery with an attached healthy grant."""

        return (
            "retry_discovery"
            in held_permissions(
                self,
                ("retry_discovery",),
                model=apps.get_model("integrate", "Integration"),
            )
            and self.lifecycle
            in (
                IntegrationLifecycle.CONNECTED,
                IntegrationLifecycle.PAUSED,
            )
            and self.credential_id is not None
            and not self.is_reconnect_required
            and not self.discovery_ready
            and not self.discovery_pending
        )

    def lock_capability(self) -> Self:
        """Lock the final MTI row and its ancestors in the same order as bridge work.

        Call inside an atomic block. Parent-addressed verbs resolve the child
        before acquiring any row lock, so they cannot invert the worker order.
        """

        # The physical lock target is an integrity fact, independent of whether
        # the caller can read the child. The locked queryset retains its scope.
        using = write_alias(type(self), using=self._state.db, instance=self)

        target = self
        if self.declared_concrete_model() is not type(self):
            target = copy(self).sudo(reason="integrate.connection.lock_target").concrete_capability()
        return type(target).objects.db_manager(using).lock_if_supported().get(pk=self.pk)

    def resume(self) -> None:
        """Recheck Resume admission under the integration lock before connecting."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        actor = instance_actor(self)
        with database_context(using), transaction.atomic(using=using):
            with system_context(reason="integrate.connection.resume"):
                row = self.lock_capability()
            if actor is not None:
                row.with_actor(actor)
            if not row.can_resume():
                raise ValidationError("This integration cannot resume in its current state.")
            row.connect()
        self.refresh_from_db(
            using=using,
        )

    def _credential_health(self) -> Credential | None:
        """Consume optimized public facts, or fetch the same narrow elevated projection."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        if self.credential_id is None:
            return None
        model = apps.get_model("integrate", "Credential")
        if "_connection_credential" in self.__dict__:
            return cast(Credential | None, self.__dict__["_connection_credential"])
        fields = ("status", "expires_at", "last_refresh_status")
        if all(f"_connection_{field}" in self.__dict__ for field in fields):
            values = {field: self.__dict__[f"_connection_{field}"] for field in fields}
            if values["status"] is None:
                return None
            if values["expires_at"] is None or values["expires_at"] > timezone.now():
                return cast(Credential, model(**values))
        return model.objects.db_manager(using).connection_health().get(pk=self.credential_id)

    @property
    def is_reconnect_required(self) -> bool:
        """Delegate consent health to the credential owner's non-secret predicate."""

        credential = self._credential_health()
        return credential is not None and credential.is_reconnect_required

    @property
    def credential_status(self) -> str:
        """Expose credential health without granting read access to its protected row."""

        if "_connection_status" in self.__dict__:
            return str(self.__dict__["_connection_status"] or "")
        credential = self._credential_health()
        return "" if credential is None else str(credential.status)

    def connection_credential(self) -> Credential | None:
        """Load this relation with its auth dependencies once; retain Django's cache."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        field = self._meta.get_field("credential")
        if not field.is_cached(self):
            if self.credential_id is None:
                return None
            field.set_cached_value(
                self,
                field.related_model._base_manager.db_manager(using, hints={"instance": self})
                .select_related("oauth_client")
                .get(
                    pk=self.credential_id,
                ),
            )
        return self.credential

    def fresh_credential(self) -> Credential | None:
        """Reload the attached credential, including changes to its FK or material."""

        # Workers must observe credential rotation or repointing between operations.
        self.refresh_from_db(fields=["credential"])
        return cast(Credential | None, self.credential)

    def concrete_capability(self) -> Self:
        """Return the installed concrete child row for this integration, or itself.

        A parent-addressed action (the console targets ``integrate.Integration``
        by public id) reaches the capability's own behavior through this: the
        child row is the same primary key with the subtype's methods. A row no
        installed child claims stays the neutral parent.
        """

        actor, unscoped = self.effective_actor(strict=True)
        declared = self.declared_concrete_model()
        if declared is not None and declared is not type(self) and issubclass(declared, type(self)):
            child = self._concrete_child(declared, actor=actor, authorized=not unscoped)
            return child if child is not None else self
        for child_model in type(self).concrete_child_models():
            child = self._concrete_child(child_model, actor=actor, authorized=not unscoped)
            if child is not None:
                return child
        return self

    def preview_push(self) -> tuple[tuple[Any, Any], ...]:
        """Preview each retained stream using one capability adapter lifetime."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        adapter = self.concrete_capability().capability_impl
        try:
            streams = apps.get_model("integrate", "SyncStream").objects.db_manager(using).current_for_bridge(self)
            return tuple((stream, preview_push(stream, adapter)) for stream in streams)
        finally:
            adapter.close()

    def request_sync(self, *, now: datetime | None = None) -> int:
        """Queue this integration's bridge capabilities and return the dispatch count."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        timestamp = now or timezone.now()
        count = 0
        for model in models_with(base=Bridge):
            for capability in (
                model._default_manager.db_manager(using).of_concrete_type().filter(pk=self.pk).order_by("pk")
            ):
                capability.request_sync(now=timestamp)
                count += 1
        return count

    def probe_credential(self) -> str:
        """Prove the attached credential is usable; raise :class:`IntegrationError` when not.

        The credential-only connection test: no vendor round-trip, just the kind
        handler producing authorization material. Returns the operator message.
        """

        credential = self.credential
        if credential is None:
            raise IntegrationError("No credential is attached.")
        try:
            credential.auth_headers()
        except Exception as error:  # noqa: BLE001 — handler diagnostics stay outside the operator message.
            raise IntegrationError("Credential is not usable.") from error
        return "Credential is usable."

    def test_connection(self) -> str:
        """Exercise this integration's connection and return the operator message.

        The parent proves only its credential; a capability child that owns a
        transport (a channel backend, a directory backend) overrides this to
        perform the vendor's real handshake. Failures raise
        :class:`IntegrationError` with the operator-safe reason.
        """

        return self.probe_credential()

    vendor = models.ForeignKey("integrate.Vendor", on_delete=models.PROTECT, related_name="integrations")
    # PROTECT: a present credential is the integration's authentication. It may
    # belong to a principal other than ``owner`` (an org/app-install credential), so
    # deleting a credential still in use is refused rather than silently breaking
    # the integration. Disconnected integrations may leave it empty.
    credential = models.ForeignKey(
        "integrate.Credential",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="integrations",
    )
    account = models.ForeignKey(
        "integrate.ExternalAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="integrations",
    )
    lifecycle = StateField(choices_enum=IntegrationLifecycle, default=IntegrationLifecycle.DISCONNECTED)
    """Declared connection intent for this integration.

    Connected means configured and enabled, paused retains configuration while
    intentionally stopped, and disconnected releases the connection. Runtime
    health lives separately on ``runtime_status``.
    """
    runtime_status = StateField(
        choices_enum=IntegrationRuntimeStatus,
        default=IntegrationRuntimeStatus.OK,
        status_display="dot",
    )
    """Observed health for the integration's last runtime interaction.

    ``PENDING`` marks discovery in flight; ``OK`` and ``ERROR`` describe the
    last credential/sync/use outcome. None moves the lifecycle journey.
    """
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_status = models.CharField(max_length=64, blank=True)
    use_count_24h = models.PositiveIntegerField(default=0)
    error_count_24h = models.PositiveIntegerField(default=0)
    last_error = DiagnosticTextField(blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    discovery_credential = models.ForeignKey(
        "integrate.Credential",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="discovered_integrations",
    )
    """The original grant retained when discovery attaches a derived credential."""
    discovery_retry_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)
    """Durable pending discovery and the earliest provider retry deadline."""
    discovery_completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    """Evidence that this connection generation applied successfully."""
    discovery_generation = models.PositiveIntegerField(default=0, editable=False)
    """Fence older callbacks when OAuth rotates material on the same credential row."""

    discovery_attempts = models.PositiveIntegerField(default=0, editable=False)
    """Remote attempts in this generation; lock contention does not consume one."""

    lifecycle_transitions = StateTransitions(
        lifecycle,
        {
            IntegrationLifecycle.DISCONNECTED: [IntegrationLifecycle.CONNECTED],
            IntegrationLifecycle.CONNECTED: [
                IntegrationLifecycle.PAUSED,
                IntegrationLifecycle.DISCONNECTED,
            ],
            IntegrationLifecycle.PAUSED: [
                IntegrationLifecycle.CONNECTED,
                IntegrationLifecycle.DISCONNECTED,
            ],
        },
    )

    objects = IntegrationManager()
    unscoped_objects = AngeeHistoryUnscopedManager()

    class Meta:
        """Django model options for integrations."""

        abstract = True
        base_manager_name = "unscoped_objects"
        ordering = ("-updated_at",)
        rebac_resource_type = "integrate/integration"

    def __str__(self) -> str:
        """Return a stable vendor-qualified integration label."""

        vendor_slug = getattr(getattr(self, "vendor", None), "slug", "?")
        return f"{vendor_slug}:{self.public_id}"

    def prepare_patch(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        """Apply connection/state/ownership verbs before ordinary native field updates.

        Values are Django-native field assignments, including FK attnames. The
        write boundary resolves public references before dispatching here. The
        caller holds the capability lock and an atomic transaction.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        fields = dict(fields)
        credential_id = fields.pop("credential_id", _UNSET)
        if credential_id is not _UNSET:
            actor = instance_actor(self)
            credentials = apps.get_model("integrate", "Credential").objects.db_manager(using).for_write()
            accounts = apps.get_model("integrate", "ExternalAccount").objects.db_manager(using).for_write()
            if actor is not None:
                credentials = credentials.with_actor(actor)
                accounts = accounts.with_actor(actor)
            credential = None if credential_id is None else credentials.get(pk=credential_id)
            account_id = fields.pop("account_id", _UNSET)
            account = account_id if account_id is _UNSET or account_id is None else accounts.get(pk=account_id)
            self.concrete_capability().attach_connection(credential, account=account)
            self.refresh_from_db(
                using=using,
            )
        if "lifecycle" in fields:
            self.set_lifecycle(fields.pop("lifecycle"))
        owner_id = fields.pop("owner_id", _UNSET)
        if owner_id is not _UNSET:
            owner = None if owner_id is None else get_user_model().objects.db_manager(using).get(pk=owner_id)
            self.transfer_ownership(owner)
        if "config" in fields:
            for name in self.apply_config_patch(fields.pop("config")):
                fields[name] = cast(models.Field, self._meta.get_field(name)).value_from_object(self)
        return fields

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep the grouping content type aligned with the row's concrete model."""

        using = write_alias(type(self), using=kwargs.get("using") or self._state.db, instance=self)
        kwargs["using"] = using

        if self.concrete_type_id is None or type(self) is not self._meta.apps.get_model("integrate", "Integration"):
            content_type = ContentType.objects.db_manager(using).get_for_model(type(self))
            changed = self.concrete_type_id != content_type.pk
            self.concrete_type = content_type
            update_fields = kwargs.get("update_fields")
            if changed and update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "concrete_type"}
        adding = self._state.adding
        super().save(*args, **kwargs)
        if adding and self.credential_id is not None and self.requires_connection_discovery:
            self.await_discovery()

    @property
    def display_label(self) -> str:
        """Return the operator label, or a vendor-derived one when none was given.

        Headers, lists, and relation pickers read this so a named integration shows
        its name and an unnamed one still reads as ``Vendor (lifecycle)``.
        """

        if self.display_name:
            return str(self.display_name)
        vendor = getattr(self, "vendor", None)
        label = str(getattr(vendor, "display_name", "") or getattr(vendor, "slug", "") or "integration")
        return f"{label} ({self.lifecycle})"

    @property
    def record_display_label(self) -> str:
        """Use the same operator label as the Integration GraphQL record."""

        return self.display_label

    @transition(
        lifecycle,
        source=[IntegrationLifecycle.DISCONNECTED, IntegrationLifecycle.PAUSED],
        target=IntegrationLifecycle.CONNECTED,
        on_success=_save_connected,
    )
    def connect(self, *, credential: Any = _UNSET, account: Any = _UNSET) -> None:
        """Mark this integration connected, optionally attaching connection rows."""

        self._transition_fields = self._set_connection_fields(credential=credential, account=account)

    def _set_connection_fields(self, *, credential: Any = _UNSET, account: Any = _UNSET) -> set[str]:
        """Attach connection rows and reset health, returning the fields changed."""

        fields: set[str] = set()
        for name, value, field in (
            ("credential", credential, type(self).credential.field),
            ("account", account, type(self).account.field),
        ):
            if value is _UNSET:
                continue
            if value is not None and value._state.adding:
                setattr(self, name, value)
            else:
                setattr(self, field.attname, None if value is None else value.pk)
                if field.is_cached(self):
                    field.delete_cached_value(self)
            fields.add(name)
            if name == "credential":
                self.discovery_credential_id = None if value is None else value.pk
                fields.add("discovery_credential")
        self.runtime_status = cast(IntegrationRuntimeStatus, IntegrationRuntimeStatus.OK)
        self.last_error = ""
        self.last_error_at = None
        fields.update({"last_error", "last_error_at", "runtime_status"})
        return fields

    def attach_connection(
        self,
        credential: Any | None,
        *,
        account: Any = _UNSET,
        connect_disconnected: bool = True,
        discovery_generation: int | None = None,
    ) -> None:
        """Own every credential attachment, including a fenced derived credential.

        ``discovery_generation`` is reserved for the apply step already holding
        this row's lock. Ordinary callers always enter connection discovery.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        resolved_account = (
            (None if credential is None else credential.external_account) if account is _UNSET else account
        )
        if self.pk is None:
            self._set_connection_fields(credential=credential, account=resolved_account)
            if credential is not None and connect_disconnected and self.lifecycle == IntegrationLifecycle.DISCONNECTED:
                self.lifecycle_transitions.force_state(
                    self,
                    IntegrationLifecycle.CONNECTED,
                    reason="unsaved integration attachment declares initial connection intent",
                )
            return
        with (
            system_context(reason="integrate.connection.attach"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            row = self.lock_capability()
            derived_pk = row.credential_id if row.discovery_credential_id not in (None, row.credential_id) else None
            if discovery_generation is not None:
                if row.discovery_generation != discovery_generation or not row.discovery_pending:
                    raise IntegrationError("Connection discovery has been superseded.")
                original_grant_id = row.discovery_credential_id
                fields = row._set_connection_fields(credential=credential, account=resolved_account)
                row.discovery_credential_id = original_grant_id
                row.save(using=row._state.db, update_fields={*fields, "updated_at"})
            elif credential is not None and connect_disconnected and row.lifecycle == IntegrationLifecycle.DISCONNECTED:
                row.connect(credential=credential, account=resolved_account)
            else:
                fields = row._set_connection_fields(credential=credential, account=resolved_account)
                row.save(using=row._state.db, update_fields={*fields, "updated_at"})
                row.await_discovery()
            apps.get_model("integrate", "Credential").objects.db_manager(using).delete_unattached_derived(derived_pk)
        self.refresh_from_db(
            using=using,
        )

    @transition(
        lifecycle,
        source=IntegrationLifecycle.CONNECTED,
        target=IntegrationLifecycle.PAUSED,
        on_success=_save_lifecycle,
    )
    def pause(self) -> None:
        """Pause this connected integration without changing its runtime health."""

    @transition(
        lifecycle,
        source=[IntegrationLifecycle.CONNECTED, IntegrationLifecycle.PAUSED],
        target=IntegrationLifecycle.DISCONNECTED,
        on_success=_save_lifecycle,
    )
    def disconnect(self) -> None:
        """Disconnect this integration without changing its runtime health."""

        self.discovery_retry_at = None
        self._transition_fields = {"discovery_retry_at"}

    def set_lifecycle(self, lifecycle: IntegrationLifecycle | str) -> None:
        """Move to one declared lifecycle state using this model's transition methods."""

        target = IntegrationLifecycle.from_value(lifecycle)
        if str(self.lifecycle) == target.value:
            return
        if target == IntegrationLifecycle.CONNECTED:
            self.connect()
        elif target == IntegrationLifecycle.PAUSED:
            self.pause()
        elif target == IntegrationLifecycle.DISCONNECTED:
            self.disconnect()
        else:
            self.lifecycle_transitions.not_allowed(self.lifecycle, target)

    @property
    def requires_connection_discovery(self) -> bool:
        """Credential-only capabilities settle attachment without a worker."""

        return False

    @property
    def discovery_pending(self) -> bool:
        """Whether this connection generation is awaiting discovery or a retry."""

        return self.discovery_retry_at is not None

    @property
    def discovery_ready(self) -> bool:
        """One admission rule for sync, workflow dispatch and outbound delivery.

        Generation zero preserves established integrations until their next
        attachment or Resume. Every new generation needs successful discovery.
        """

        return not self.discovery_pending and (
            self.discovery_generation == 0 or self.discovery_completed_at is not None
        )

    def prepare_connection_discovery(self) -> None:
        """Database-only preparation; concrete capabilities may suspend their work."""

    def connection_discovery_finished(self, *, succeeded: bool) -> None:
        """Database-only completion; concrete capabilities may arm their work."""

    def await_discovery(self) -> None:
        """Invalidate the previous result and dispatch one durable discovery attempt."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        with (
            system_context(reason="integrate.connection.await"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            row = self.lock_capability()
            source_lost = (
                row.discovery_generation > 0 and row.discovery_credential_id is None and row.credential_id is not None
            )
            if row.discovery_credential_id is not None and row.credential_id != row.discovery_credential_id:
                derived_pk = row.credential_id
                credential = (
                    apps.get_model("integrate", "Credential")
                    .objects.db_manager(using)
                    .get(pk=row.discovery_credential_id)
                )
                fields = row._set_connection_fields(credential=credential, account=row.account)
                row.save(using=row._state.db, update_fields={*fields, "updated_at"})
                type(credential).objects.db_manager(using).delete_unattached_derived(derived_pk)
            if row.discovery_credential_id is None and not source_lost:
                row.discovery_credential_id = row.credential_id
            row.discovery_generation += 1
            row.discovery_attempts = 0
            row.discovery_completed_at = None
            row.discovery_retry_at = timezone.now()
            row.prepare_connection_discovery()
            row.save(
                using=row._state.db,
                update_fields=[
                    "discovery_generation",
                    "discovery_attempts",
                    "discovery_completed_at",
                    "discovery_retry_at",
                    "discovery_credential",
                    "updated_at",
                ],
            )
            if source_lost:
                row.finish_discovery(
                    credential_pk=row.credential_id,
                    generation=row.discovery_generation,
                    error=IntegrationFailure("The source credential for connection discovery is no longer available."),
                )
            elif row.requires_connection_discovery and row.lifecycle == IntegrationLifecycle.DISCONNECTED:
                row.discovery_retry_at = None
                row.save(using=row._state.db, update_fields=["discovery_retry_at", "updated_at"])
            elif not row.requires_connection_discovery:
                row.finish_discovery(credential_pk=row.credential_id, generation=row.discovery_generation)
            elif row.credential_id is None:
                row.finish_discovery(
                    credential_pk=None,
                    generation=row.discovery_generation,
                    error=IntegrationFailure("No credential is attached."),
                )
            else:
                row.report_status(cast(IntegrationRuntimeStatus, IntegrationRuntimeStatus.PENDING))
                enqueue_task(
                    DISCOVERY_TASK,
                    kwargs={
                        "integration_pk": row.pk,
                        "credential_pk": row.credential_id,
                        "generation": row.discovery_generation,
                        "using": using,
                    },
                    expires=DISCOVERY_TASK_EXPIRES,
                    robust=True,
                    using=using,
                )
        self.refresh_from_db(
            using=using,
        )

    def retry_discovery(self) -> None:
        """Retry connection discovery, resetting the bounded attempt budget."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        actor = instance_actor(self)
        with database_context(using), transaction.atomic(using=using):
            with system_context(reason="integrate.connection.retry"):
                row = self.lock_capability()
            if actor is not None:
                row.with_actor(actor)
            if not row.can_retry_discovery():
                raise ValidationError("This integration cannot retry discovery in its current state.")
            row.await_discovery()
        self.refresh_from_db(
            using=using,
        )

    def discovery_is_pending(self, credential_pk: int | None, *, generation: int) -> bool:
        """Fence both credential replacement and rotation on the same credential row."""

        return (
            self.discovery_pending and self.credential_id == credential_pk and self.discovery_generation == generation
        )

    def defer_discovery(self, *, credential_pk: int, generation: int, retry_after: timedelta) -> bool:
        """Retain a provider deadline unless this generation exhausted its attempts."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        with (
            system_context(reason="integrate.connection.defer"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            row = self.lock_capability()
            if not row.discovery_is_pending(credential_pk, generation=generation):
                return False
            if row.discovery_attempts >= DISCOVERY_ATTEMPT_LIMIT:
                row.finish_discovery(
                    credential_pk=credential_pk,
                    generation=generation,
                    error=IntegrationFailure(_DISCOVERY_RETRY_EXHAUSTED_MESSAGE),
                )
            else:
                assert row.discovery_retry_at is not None  # discovery_is_pending checked the persisted deadline.
                row.discovery_retry_at = max(row.discovery_retry_at, timezone.now() + retry_after)
                row.save(using=row._state.db, update_fields=["discovery_retry_at", "updated_at"])
        self.refresh_from_db(
            using=using,
        )
        return True

    def apply_discovery(self, discovery: ConnectionDiscovery) -> None:
        """Apply a credential-only result; bridges add domain database work."""

        if discovery.data or discovery.credential is not None:
            raise IntegrationError("This integration does not accept external resource discovery.")

    def finish_discovery(
        self,
        *,
        credential_pk: int | None,
        generation: int,
        discovery: ConnectionDiscovery | None = None,
        error: IntegrationFailure | None = None,
    ) -> bool:
        """Apply remote results only after validating the task's original fence.

        The adapter receives the freshly locked integration. Derived material
        lands through the credential manager and ``attach_connection`` here,
        never in the adapter's network phase.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        with (
            system_context(reason="integrate.connection.finish"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            row = self.lock_capability()
            if not row.discovery_is_pending(credential_pk, generation=generation):
                return False
            if error is None:
                result = discovery or ConnectionDiscovery()
                row.apply_discovery(result)
                if result.credential is not None:
                    if credential_pk is None:
                        raise IntegrationError("No credential is attached.")
                    source = apps.get_model("integrate", "Credential").objects.db_manager(using).get(pk=credential_pk)
                    derived = result.credential
                    credential = (
                        type(source)
                        .objects.db_manager(using)
                        .create_local_credential(
                            source.user,
                            kind=derived.kind,
                            name=derived.name,
                            material=dict(derived.material),
                        )
                    )
                    row.attach_connection(
                        credential,
                        account=source.external_account,
                        connect_disconnected=False,
                        discovery_generation=generation,
                    )
            row.discovery_retry_at = None
            row.discovery_completed_at = timezone.now() if error is None else None
            row.save(using=row._state.db, update_fields=["discovery_retry_at", "discovery_completed_at", "updated_at"])
            status = IntegrationRuntimeStatus.ERROR if error else IntegrationRuntimeStatus.OK
            row.report_status(cast(IntegrationRuntimeStatus, status), error or "")
            row.connection_discovery_finished(succeeded=error is None)
            if error is None:
                transaction.on_commit(
                    lambda: discovery_finished.send(sender=type(row), instance=row),
                    robust=True,
                )
        self.refresh_from_db(
            using=using,
        )
        return True

    def attach_credential(self, credential: Any | None) -> None:
        """Route parent-addressed attachment through the concrete capability owner."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        with (
            system_context(reason="integrate.integration.attach_credential"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            self.concrete_capability().attach_connection(credential)
        self.refresh_from_db(
            using=using,
        )

    def run_discovery(self, *, credential_pk: int | None, generation: int) -> dict[str, Any]:
        """Discover in autocommit under the sync lock, then apply under the row fence."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        if connections[using].in_atomic_block:
            raise RuntimeError("Connection discovery requires autocommit.")
        target = self.concrete_capability()
        with bridge_advisory_lock(target) as acquired:
            if not acquired:
                return {"ok": False, "reason": "locked"}
            with database_context(using), transaction.atomic(using=using):
                row = target.lock_capability()
                if not row.discovery_is_pending(credential_pk, generation=generation):
                    return {"ok": False, "reason": "stale-discovery"}
                assert row.discovery_retry_at is not None  # discovery_is_pending checked the persisted deadline.
                if row.discovery_retry_at > timezone.now():
                    return {"ok": False, "reason": "deferred"}
                if row.discovery_attempts >= DISCOVERY_ATTEMPT_LIMIT:
                    row.finish_discovery(
                        credential_pk=credential_pk,
                        generation=generation,
                        error=IntegrationFailure(_DISCOVERY_RETRY_EXHAUSTED_MESSAGE),
                    )
                    return {"ok": False, "reason": "attempt-limit"}
                row.discovery_attempts += 1
                row.save(using=row._state.db, update_fields=["discovery_attempts", "updated_at"])
            target = row
            try:
                credential = row.credential
                if credential_pk is None or credential is None:
                    raise IntegrationError("No credential is attached.")
                discovery = target.discover_connection(credential)
                if not isinstance(discovery, ConnectionDiscovery):
                    raise IntegrationError("The adapter returned an invalid connection discovery result.")
                applied = target.finish_discovery(
                    credential_pk=credential_pk,
                    generation=generation,
                    discovery=discovery,
                )
                self.refresh_from_db(
                    using=using,
                )
                return {"ok": applied, "reason": "applied" if applied else "stale-discovery"}
            except Exception as error:  # noqa: BLE001 — provider diagnostics never become public telemetry.
                if not isinstance(error, IntegrationError):
                    logger.exception("Connection discovery failed for integration %s.", target.pk)
                retryable = (
                    isinstance(error, IntegrationError)
                    and error.transient
                    or isinstance(error, (httpx2.TransportError, OperationalError))
                    or isinstance(error, httpx2.HTTPStatusError)
                    and error.response.status_code >= 500
                    or isinstance(error, OAuthFlowError)
                    and error.http_status >= 500
                )
                if retryable and credential_pk is not None:
                    hint = error.retry_after if isinstance(error, IntegrationError) else None
                    backoff = min(timedelta(minutes=2 ** (target.discovery_attempts - 1)), timedelta(hours=1))
                    target.defer_discovery(
                        credential_pk=credential_pk,
                        generation=generation,
                        retry_after=max(backoff, hint) if hint is not None else backoff,
                    )
                    self.refresh_from_db(
                        using=using,
                    )
                    return {"ok": False, "reason": "deferred" if target.discovery_pending else "attempt-limit"}
                target.finish_discovery(
                    credential_pk=credential_pk,
                    generation=generation,
                    error=safe_integration_failure(error),
                )
                self.refresh_from_db(
                    using=using,
                )
                return {"ok": False, "reason": "discovery-failed"}

    def discover_connection(self, credential: Credential) -> ConnectionDiscovery:
        """Credential-only discovery is synchronous and has no external resource."""

        return ConnectionDiscovery()

    def report_status(self, status: IntegrationRuntimeStatus | str, error: str | IntegrationFailure = "") -> None:
        """Record implementation status telemetry and persist this integration.

        ``status`` is a runtime status, resolved by member name (``ERROR`` — the
        shape GraphQL serializes) or stored value (``error``); anything else
        fails at the vocabulary that could not read it. Only the runtime axis is
        written — ``lifecycle`` is the operator's to declare, and a status report
        is not the operator. ``last_used_status`` keeps what was reported
        verbatim.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        raw_status = str(getattr(status, "value", status)).strip()
        normalized = IntegrationRuntimeStatus.from_value(status)
        reported_at = timezone.now()
        self.runtime_status = normalized
        usage_fields: list[str] = []
        if normalized != IntegrationRuntimeStatus.PENDING:
            self.last_used_at = reported_at
            self.last_used_status = raw_status or normalized.value
            usage_fields = ["last_used_at", "last_used_status"]
        self.last_error = (
            error.message if isinstance(error, IntegrationFailure) else (_INTEGRATION_FAILURE_MESSAGE if error else "")
        )
        self.last_error_at = reported_at if error else None

        if self.pk is None:
            return

        with (
            system_context(reason="integrate.integration.status"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            self.save(
                using=self._state.db,
                update_fields=[
                    "last_error",
                    "last_error_at",
                    *usage_fields,
                    "runtime_status",
                    "updated_at",
                ],
            )
