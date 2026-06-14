"""Source models for Angee's integration runtime primitives.

This addon owns the integration layer: the third-party ``Vendor`` catalogue, the
first-class ``Integration`` an integration runs over, the abstract
``Capability``/``Bridge`` runtime, the host-agnostic VCS inventory
(``VCSIntegration`` + ``Repository``/``Source``/``Template``), and outbound
``WebhookSubscription``. It draws a ``Credential`` (and optionally an
``ExternalAccount``) from ``iam`` to authenticate; it never owns identity.
Host-specific VCS backends live in their own addons (``integrate_github``) and are
named per ``VCSIntegration`` row by ``backend_class``; this addon never imports them.
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from rebac import system_context
from rebac.managers import RebacManager

from angee.base.fields import EncryptedField, ImplClassField, SqidField, StateField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.integrate.events import EventKind
from angee.integrate.net import validate_public_url
from angee.integrate.vcs.backend import VCSBackend
from angee.integrate.vcs.templates import parse_template_meta
from angee.integrate.webhooks import PinnedWebhookClient, WebhookDeliveryError

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class CapabilityStatus(models.TextChoices):
    """Lifecycle state for a concrete integration capability."""

    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    ERROR = "error", "Error"
    DISABLED = "disabled", "Disabled"


class Vendor(SqidMixin, AuditMixin, AngeeModel):
    """Admin-managed third-party catalogue (GitHub, Google, Slack, …).

    The single source of truth for "what is this third party" — branding and
    reference metadata only. New integration addons add their own row via an
    install-tier resource seed (``adopt: slug``). The login-side ``OAuthClient``
    in ``iam`` carries its own ``slug``; that is a deliberately independent
    namespace, not a foreign key into this catalogue.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="vnd", min_length=8)
    slug = models.SlugField(unique=True)
    display_name = models.CharField(max_length=128)
    website_url = models.URLField(blank=True)
    icon = models.CharField(max_length=128, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        """Django model options for integration vendors."""

        abstract = True
        ordering = ("slug",)
        rebac_resource_type = "integrate/vendor"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the display label used by Django surfaces."""

        return self.display_name or self.slug


class IntegrationStatus(models.TextChoices):
    """Aggregate health for an integration, rolled up from its capabilities.

    Owns the rollup vocabulary that used to live on ``iam.AccountStatus``: an
    integration is the substrate capabilities run over, so it — not the identity
    account — aggregates their health.
    """

    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"
    ERROR = "error", "Error"

    @classmethod
    def from_value(cls, value: object) -> IntegrationStatus:
        """Return the member for one string or enum integration-status value."""

        raw = str(getattr(value, "value", value))
        try:
            return cast(IntegrationStatus, cls(raw))
        except ValueError as error:
            raise ValueError(f"Unsupported integration status for rollup: {raw}") from error

    @classmethod
    def from_capability(cls, status: object) -> IntegrationStatus:
        """Return the integration status one capability status contributes to the rollup."""

        raw = str(getattr(status, "value", status))
        mapping = {
            "active": cls.from_value(cls.ACTIVE),
            "paused": cls.from_value(cls.DISABLED),
            "disabled": cls.from_value(cls.DISABLED),
            "error": cls.from_value(cls.ERROR),
        }
        try:
            return mapping[raw]
        except KeyError as error:
            raise ValueError(f"Unsupported capability status for integration rollup: {raw}") from error

    @classmethod
    def rollup(cls, statuses: Iterable[object]) -> IntegrationStatus:
        """Return the most severe integration status across capability contributions."""

        members = tuple(cls.from_value(status) for status in statuses)
        return max(members, key=lambda member: member.precedence) if members else cls.from_value(cls.ACTIVE)

    @property
    def precedence(self) -> int:
        """Return rollup precedence — the highest wins when statuses combine."""

        order = (IntegrationStatus.ACTIVE, IntegrationStatus.DISABLED, IntegrationStatus.ERROR)
        return order.index(self)

    @property
    def is_error(self) -> bool:
        """Return whether this integration is in an error state."""

        return self is IntegrationStatus.ERROR


class Integration(SqidMixin, AuditMixin, AngeeModel):
    """A product/workspace integration to a vendor account.

    The first-class "what we're connected to and what runs over it": it draws a
    ``credential`` (and optionally an ``account``) from ``iam`` to authenticate,
    points at a catalogue ``vendor``, and owns the capability-health rollup. Its
    capabilities/bridges (``integrate.Capability``) point back at it.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="int", min_length=8)
    vendor = models.ForeignKey("integrate.Vendor", on_delete=models.PROTECT, related_name="integrations")
    # PROTECT: the credential is the integration's authentication. It may belong to
    # a principal other than ``owner`` (an org/app-install credential), so deleting
    # a credential still in use is refused rather than silently breaking the
    # integration. The owner does not have to own the credential.
    credential = models.ForeignKey("iam.Credential", on_delete=models.PROTECT, related_name="integrations")
    account = models.ForeignKey(
        "iam.ExternalAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="integrations",
    )
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="integrations")
    config = models.JSONField(default=dict, blank=True)
    """Integration-scoped settings (endpoints, options); per-capability settings live on ``Capability.config``."""
    status = StateField(choices_enum=IntegrationStatus, default=IntegrationStatus.ACTIVE)
    capability_statuses = models.JSONField(default=dict, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)

    objects = RebacManager()

    class Meta:
        """Django model options for integrations."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "integrate/integration"
        rebac_id_attr = "sqid"
        constraints = (
            # One integration per (owner, vendor, credential): lets the
            # self-service ``create_integration_from_credential`` upsert be a
            # race-safe ``update_or_create`` instead of a get-then-insert that
            # two concurrent calls both miss.
            models.UniqueConstraint(
                fields=("owner", "vendor", "credential"),
                name="uniq_integrate_integration_owner_vendor_credential",
            ),
        )

    def __str__(self) -> str:
        """Return a stable vendor-qualified integration label."""

        vendor_slug = getattr(getattr(self, "vendor", None), "slug", "?")
        return f"{vendor_slug}:{self.public_id}"

    # ``config`` key the self-service credential handoff stores the env var name
    # under; rendered-service consumers read it back via ``credential_env_value``.
    CREDENTIAL_ENV_CONFIG_KEY = "credential_env"

    def set_credential_env(self, env_name: str) -> None:
        """Record (or clear) the env var name the credential secret is exposed under.

        The integration owns this ``config`` key: callers pass the operator's
        chosen name and persist the row, rather than poking ``config`` directly.
        """

        config = dict(self.config or {})
        env_name = env_name.strip()
        if env_name:
            config[self.CREDENTIAL_ENV_CONFIG_KEY] = env_name
        else:
            config.pop(self.CREDENTIAL_ENV_CONFIG_KEY, None)
        self.config = config

    def credential_env_value(self) -> dict[str, str]:
        """Return the ``{env_name: secret}`` this integration exposes to services, or ``{}``.

        Owns the projection of its own ``config`` key and ``credential`` secret so
        callers (e.g. ``agents.InferenceProvider.service_environment``) never decode
        the config shape or read the credential from outside.
        """

        config = self.config if isinstance(self.config, dict) else {}
        env_name = str(config.get(self.CREDENTIAL_ENV_CONFIG_KEY) or "").strip()
        if not env_name:
            return {}
        credential = getattr(self, "credential", None)
        if credential is None:
            return {}
        secret = str(credential.secret_value() or "")
        if not secret:
            return {}
        return {env_name: secret}

    def note_capability_status(self, *, capability_key: Any, status: Any, error: str = "") -> None:
        """Record one capability contribution, recompute this integration, and persist.

        The integration owns this write: direct callers do not need an ambient
        ``system_context`` or transaction, and scheduler callers safely nest
        inside their own framework operation context. Unsaved instances are
        updated in memory only because there is no integration row to persist.
        """

        reported_at = timezone.now()
        incoming_status = IntegrationStatus.from_capability(status)
        capability_statuses = dict(self.capability_statuses or {})
        # Deleted capabilities can leave stale contributions until pruning has an owner.
        capability_statuses[str(capability_key)] = incoming_status.value
        rolled_status = IntegrationStatus.rollup(capability_statuses.values())

        self.capability_statuses = capability_statuses
        self.status = rolled_status
        self.last_used_at = reported_at
        if rolled_status.is_error:
            if error:
                self.last_error = error
            self.last_error_at = reported_at
        else:
            self.last_error = ""
            self.last_error_at = None

        if self.pk is None:
            return

        with system_context(reason="integrate.integration.rollup"), transaction.atomic():
            self.save(
                update_fields=[
                    "capability_statuses",
                    "last_error",
                    "last_error_at",
                    "last_used_at",
                    "status",
                    "updated_at",
                ]
            )


class Capability(SqidMixin, AuditMixin, AngeeModel):
    """Abstract base for domain-owned capabilities.

    The concrete domain subclass owns its ``rebac_resource_type``. This pure base
    stays out of runtime emission by leaving ``runtime`` unset.
    """

    # ``%(app_label)s_%(class)s``: every concrete Capability/Bridge subclass gets a
    # distinct reverse accessor on ``Integration`` (a literal ``capabilities`` would
    # collide once a second concrete subclass loads — e.g. ``VCSIntegration`` beside
    # the scheduler test bridge). The accessor has no readers; the scheduler
    # discovers bridges via ``registry.bridge_models()``.
    integration = models.ForeignKey(
        "integrate.Integration",
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s",
    )
    config = models.JSONField(default=dict, blank=True)
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
        """Record local status telemetry and push this capability's integration contribution.

        The caller owns persistence for this capability row. ``Integration`` owns
        its rollup write and tracks each capability by a model-qualified key, so
        concrete capability/bridge subclasses sharing one integration (their PK
        sequences are independent) never collide in the rollup.
        """

        reported_at = timezone.now()
        self.status = status  # type: ignore[assignment]  # StateField descriptor unmodeled by django-stubs
        self.last_used_at = reported_at
        self.last_used_status = str(status)
        self.last_error = error
        self.last_error_at = reported_at if error else None

        self.integration.note_capability_status(capability_key=self._capability_key, status=status, error=error)

    @property
    def _capability_key(self) -> str:
        """Return this capability's rollup key, qualified by concrete model label."""

        return f"{self._meta.label_lower}:{self.pk}"


class Bridge(Capability):
    """Abstract base for capabilities that synchronize or subscribe to vendor data.

    Another pure base: a domain bridge that materializes declares
    ``runtime = True`` on that class.
    """

    cursor = models.JSONField(default=dict, blank=True)
    poll_interval = models.PositiveIntegerField(default=300)
    subscription_state = models.JSONField(default=dict, blank=True)
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


class RepoVisibility(models.TextChoices):
    """Visibility of a git remote on its host."""

    PUBLIC = "public", "Public"
    PRIVATE = "private", "Private"
    INTERNAL = "internal", "Internal"


class VCSIntegration(Bridge):
    """The VCS capability over an ``Integration`` — one table for every git host.

    A :class:`Bridge`: the scheduler refreshes its repositories' sources over the
    host REST API and an inbound push webhook triggers the same refresh. The
    host-specific wire format is a non-model :class:`~angee.integrate.vcs.backend.
    VCSBackend` named per row by ``backend_class`` (e.g. a GitHub backend) — so
    github/gitlab/bitbucket share this one table, differing only in behaviour.
    Django keeps the inventory only; the operator performs every git operation,
    consuming :meth:`Source.materialize_spec`.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="vcs", min_length=8)
    backend_class = ImplClassField(
        base_class=VCSBackend,
        registry_setting="ANGEE_VCS_BACKEND_CLASSES",
        default="none",
    )
    """The host backend this integration resolves to — an explicit per-row key into
    ``ANGEE_VCS_BACKEND_CLASSES`` (never derived from the vendor: one vendor can have
    several accounts/backends). Defaults to the ``none`` null-object backend."""
    webhook_secret = EncryptedField(blank=True)
    """Shared secret for verifying inbound push webhooks (per account, not per repo)."""

    objects = RebacManager()

    class Meta:
        """Django model options for the VCS integration capability."""

        abstract = True
        rebac_resource_type = "integrate/vcs_integration"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return a stable VCS-integration label."""

        return f"vcs:{self.public_id}"

    @property
    def backend(self) -> VCSBackend:
        """Return the host backend bound to this integration's credential/config."""

        field = cast(ImplClassField, type(self)._meta.get_field("backend_class"))
        return cast(VCSBackend, field.resolve_class(self.backend_class)(self.integration))

    def repositories_by_org(self) -> dict[str, list[Any]]:
        """Return every visible repository grouped and sorted by owning org."""

        groups: dict[str, list[Any]] = {}
        for descriptor in self.backend.ls_repos():
            groups.setdefault(descriptor.org, []).append(descriptor)
        return {org: sorted(repos, key=lambda item: item.name) for org, repos in sorted(groups.items())}

    def discover(self, source: Any, *, marker: str, parse: Callable[[bytes], dict[str, Any]]) -> list[dict[str, Any]]:
        """Return one descriptor per directory under ``source`` bearing ``marker``.

        The single enumeration walk shared by every source kind: list the source's
        subtree, read each ``marker`` blob, parse it, record the bearing directory,
        and return the descriptors in deterministic order. A source kind's output
        manager supplies only its ``marker`` filename and ``parse`` function.
        """

        backend = self.backend
        repository = source.repository
        ref = source.ref or repository.default_branch
        descriptors: list[dict[str, Any]] = []
        for entry in backend.ls_tree(repository, ref=ref, path=source.path, recursive=True):
            if entry.type != "blob" or entry.name != marker:
                continue
            descriptor = dict(parse(backend.cat_file(repository, ref=ref, path=entry.path)))
            descriptor.setdefault("path", _parent_path(entry.path))
            descriptors.append(descriptor)
        return sorted(descriptors, key=_descriptor_key)

    def sync(self) -> int:
        """Refresh every inventoried repository's sources over REST (Bridge contract).

        Repository discovery (creating rows from the account) is the explicit
        ``discoverRepositories`` action; the scheduled/webhook ``sync`` refreshes the
        content of already-inventoried repositories.
        """

        return sum(source.refresh() for repository in self.repositories.all() for source in repository.sources.all())

    def handle_webhook(self, payload: Any) -> None:
        """Re-sync this integration's inventory on an inbound push webhook."""

        del payload
        self.sync()

    def verify_webhook(self, request: Any) -> bool:
        """Return whether an inbound push webhook is authentic for this integration."""

        return self.backend.verify_webhook(self, request)

    def search_repositories(self, query: str) -> list[Any]:
        """Return host repositories whose name matches ``query`` (the add typeahead)."""

        return self.backend.search_repos(query, org=self._search_scope())

    def import_repository(self, name: str) -> Any:
        """Inventory one repository by its host ``name`` (a picked typeahead result)."""

        repository_model = apps.get_model("integrate", "Repository")
        return repository_model.objects.add(self, self.backend.get_repo(name))

    def discover_repositories(self, *, org: str = "") -> int:
        """Inventory every repository the account exposes (bulk import; prunes vanished)."""

        repository_model = apps.get_model("integrate", "Repository")
        return repository_model.objects.reconcile(self, self.backend.ls_repos(org=org))

    def _search_scope(self) -> str:
        """Return the org/user the typeahead search scopes to (from ``config.github_org``)."""

        return str(self.integration.config.get("github_org") or "")


class RepositoryManager(RebacManager):
    """Manager owning the upsert/reconcile of repository rows from a host listing."""

    def reconcile(self, vcs_integration: Any, descriptors: Iterable[Any]) -> int:
        """Upsert one repository row per descriptor and prune rows that vanished.

        Bulk import for ``discoverRepositories``: prunes against the full listing,
        so the caller must pass every repository (see ``GitHubBackend.ls_repos``
        pagination), never a partial page.
        """

        descriptor_list = list(descriptors)
        seen: set[Any] = set()
        with system_context(reason="integrate.repository.reconcile"), transaction.atomic():
            for descriptor in descriptor_list:
                seen.add(self._upsert(vcs_integration, descriptor).pk)
            self.filter(vcs_integration=vcs_integration).exclude(pk__in=seen).delete()
        return len(descriptor_list)

    def add(self, vcs_integration: Any, descriptor: Any) -> Any:
        """Inventory one repository (no prune) — the typeahead "add this repo" path."""

        with system_context(reason="integrate.repository.add"), transaction.atomic():
            return self._upsert(vcs_integration, descriptor)

    def _upsert(self, vcs_integration: Any, descriptor: Any) -> Any:
        """Create or update one repository row from a host descriptor."""

        repository, _created = self.update_or_create(
            vcs_integration=vcs_integration,
            name=descriptor.name,
            defaults={
                "org": descriptor.org,
                "remote": descriptor.remote,
                "ssh_remote": descriptor.ssh_remote,
                "remote_id": descriptor.remote_id,
                "default_branch": descriptor.default_branch,
                "visibility": descriptor.visibility,
                "web_url": descriptor.web_url,
                "archived": descriptor.archived,
            },
        )
        return repository


class Repository(SqidMixin, AuditMixin, AngeeModel):
    """Inventory of one git remote, reached through its ``VCSIntegration``.

    A plain noun: Django records the remote; the operator clones it. ``org`` groups
    the account's repositories in the browse list.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="repo", min_length=8)
    vcs_integration = models.ForeignKey(
        "integrate.VCSIntegration",
        on_delete=models.CASCADE,
        related_name="repositories",
    )
    org = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=255)
    """The repository's ``owner/repo`` path on its remote host."""
    remote = models.CharField(max_length=512)
    """The HTTPS remote URL the operator clones."""
    ssh_remote = models.CharField(max_length=255, blank=True)
    remote_id = models.CharField(max_length=128, blank=True)
    default_branch = models.CharField(max_length=255, default="main")
    visibility = StateField(choices_enum=RepoVisibility, default=RepoVisibility.PRIVATE)
    web_url = models.URLField(blank=True)
    archived = models.BooleanField(default=False)

    objects = RepositoryManager()

    class Meta:
        """Django model options for repository inventory."""

        abstract = True
        ordering = ("org", "name")
        rebac_resource_type = "integrate/repository"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("vcs_integration", "name"),
                name="uniq_integrate_repository_name",
            ),
        )

    def __str__(self) -> str:
        """Return the repository's host path."""

        return self.name


class Source(SqidMixin, AuditMixin, AngeeModel):
    """A pointer into a ``Repository`` at a ``ref`` and ``path``, with a ``kind``.

    One noun for every source kind. ``kind`` binds the source to an output model
    (``Template``/``Skill``) whose manager reconciles its rows; :meth:`refresh`
    dispatches there. The operator materializes a source from
    :meth:`materialize_spec`.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="src", min_length=8)
    repository = models.ForeignKey("integrate.Repository", on_delete=models.CASCADE, related_name="sources")
    kind = models.CharField(max_length=64)
    """The source kind (e.g. ``template``, ``skill``); resolves to an output model."""
    ref = models.CharField(max_length=255, blank=True)
    """Branch, tag, or commit oid; blank resolves to the repository's default branch."""
    path = models.CharField(max_length=1024, blank=True)
    """Pathspec of the subtree this source points at within the repository."""
    last_synced_at = models.DateTimeField(null=True, blank=True)

    objects = RebacManager()

    class Meta:
        """Django model options for source inventory."""

        abstract = True
        ordering = ("kind", "path")
        rebac_resource_type = "integrate/source"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return a kind-qualified source label."""

        return f"{self.kind}:{self.path or '/'}"

    @classmethod
    def kind_models(cls) -> tuple[type[models.Model], ...]:
        """Return the output models that declare a ``source_kind`` (e.g. ``Template``).

        ``Source`` owns "what a kind resolves to": an output model binds itself to a
        kind with a ``source_kind`` class attribute, discovered through the app
        registry so a new addon adds a kind without ``integrate`` changing.
        """

        return tuple(model for model in apps.get_models() if getattr(model, "source_kind", ""))

    @classmethod
    def available_kinds(cls) -> tuple[str, ...]:
        """Return the source kinds any installed addon contributes an output model for."""

        return tuple(sorted({str(model.source_kind) for model in cls.kind_models()}))

    @classmethod
    def target_for_kind(cls, kind: str) -> type[models.Model]:
        """Return the output model bound to one source ``kind`` or raise."""

        for model in cls.kind_models():
            if model.source_kind == kind:
                return model
        known = ", ".join(cls.available_kinds()) or "none registered"
        raise ValueError(f"No output model for source kind {kind!r} (known: {known}).")

    def refresh(self) -> int:
        """Re-enumerate over REST into the kind's output rows; return the row count."""

        return int(type(self).target_for_kind(self.kind).objects.sync_from_source(self))

    def materialize_spec(self) -> dict[str, str]:
        """Return the operator handoff coordinates to clone and check out this source."""

        repository = self.repository
        return {
            "remote": str(repository.remote),
            "ssh_remote": str(repository.ssh_remote),
            "ref": str(self.ref or repository.default_branch),
            "path": str(self.path),
        }


class TemplateManager(RebacManager):
    """Manager owning the reconcile of template rows from a template source."""

    def sync_from_source(self, source: Any) -> int:
        """Walk the source for ``copier.yml`` and upsert/prune ``Template`` rows."""

        vcs_integration = source.repository.vcs_integration
        descriptors = vcs_integration.discover(source, marker="copier.yml", parse=parse_template_meta)
        seen: set[Any] = set()
        with system_context(reason="integrate.template.sync"), transaction.atomic():
            for descriptor in descriptors:
                template, _created = self.update_or_create(
                    source=source,
                    path=str(descriptor.get("path", "")),
                    defaults={
                        "name": str(descriptor.get("name", "")),
                        "kind": str(descriptor.get("kind", "")),
                        "inputs": list(descriptor.get("inputs", [])),
                    },
                )
                seen.add(template.pk)
            self.filter(source=source).exclude(pk__in=seen).delete()
            source.last_synced_at = timezone.now()
            source.save(update_fields=["last_synced_at", "updated_at"])
        return len(descriptors)


class Template(SqidMixin, AuditMixin, AngeeModel):
    """One Copier template discovered under a ``Source`` (``source_kind="template"``).

    The operator renders these; the kind here is the *template* kind from the
    manifest's ``_angee.kind`` (stack/workspace/service).
    """

    runtime = True
    source_kind = "template"
    """Binds the ``template`` source kind to this output model (see ``registry``)."""

    sqid = SqidField(real_field_name="id", prefix="tpl", min_length=8)
    source = models.ForeignKey("integrate.Source", on_delete=models.CASCADE, related_name="templates")
    name = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=64, blank=True)
    """The template kind from ``_angee.kind`` (stack/workspace/service)."""
    path = models.CharField(max_length=1024, blank=True)
    inputs = models.JSONField(default=list, blank=True)

    objects = TemplateManager()

    class Meta:
        """Django model options for discovered templates."""

        abstract = True
        ordering = ("kind", "name")
        rebac_resource_type = "integrate/template"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("source", "path"),
                name="uniq_integrate_template_path",
            ),
        )

    def __str__(self) -> str:
        """Return a kind-qualified template label."""

        return f"{self.kind}:{self.name or self.path}"


def _parent_path(path: str) -> str:
    """Return the directory containing ``path`` (empty string at the root)."""

    return path.rsplit("/", 1)[0] if "/" in path else ""


def _descriptor_key(descriptor: dict[str, Any]) -> tuple[str, str]:
    """Return a stable ``(kind, name|path)`` sort key for one discovered descriptor."""

    return (str(descriptor.get("kind", "")), str(descriptor.get("name") or descriptor.get("path", "")))


class WebhookSubscriptionManager(RebacManager):
    """Manager for webhook subscriptions."""

    def deliver_event(
        self,
        *,
        kind: EventKind,
        payload: Any,
        impl_app: str = "",
        integration: Any | None = None,
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
                if not subscription.matches(kind=kind, impl_app=impl_app, integration=integration):
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

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="whs", min_length=8)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="webhook_subscriptions",
    )
    target_url = models.URLField(max_length=2048, validators=(validate_public_url,))
    secret = EncryptedField()
    event_kinds = models.JSONField(default=list, blank=True)
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

    def matches(self, *, kind: str, impl_app: str, integration: Any | None) -> bool:
        """Return whether this subscription should receive one event."""

        if kind not in {str(value) for value in self.event_kinds or ()}:
            return False
        impl_app_filter = tuple(str(value) for value in self.impl_app_filter or ())
        if impl_app_filter and impl_app not in impl_app_filter:
            return False
        if self.integration_filter_id is None:
            return True
        return integration is not None and self.integration_filter_id == getattr(integration, "pk", None)

    def deliver(self, body: bytes) -> str:
        """POST one signed event body to this subscription's pinned target; raise on non-2xx."""

        return PinnedWebhookClient(str(self.target_url)).post(secret=str(self.secret), body=body)

    def rotate_secret(self) -> str:
        """Generate a new signing secret, persist it, and return the plaintext once.

        The subscription owns its signing material: a console action calls this to
        roll the secret. The plaintext is returned only here (for one-time display);
        reads never expose it.
        """

        new_secret = secrets.token_urlsafe(32)
        self.secret = new_secret  # type: ignore[assignment]  # EncryptedField descriptor unmodeled by django-stubs
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
