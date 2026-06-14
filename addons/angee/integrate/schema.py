"""GraphQL schema contributions for Angee integrations.

Owns the admin console surface for the third-party ``Vendor`` catalogue (moved
here from iam) and the first-class ``Integration`` an integration runs over. The
console is platform-admin gated, so ``Integration``'s REBAC-guarded relations
(credential/account from iam) are safe to expose — the const-admin reaches every
related row.
"""

from __future__ import annotations

import json
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import models, transaction
from django.utils import timezone
from rebac import PermissionDenied, system_context
from strawberry import auto, relay
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.base.models import instance_from_public_id
from angee.graphql.actions import ActionResult
from angee.graphql.crud import crud
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.permissions import session_user as _session_user
from angee.iam.schema import CredentialType, ExternalAccountType, UserType
from angee.integrate.registry import bridge_models

Vendor = apps.get_model("integrate", "Vendor")
Integration = apps.get_model("integrate", "Integration")
Credential = apps.get_model("iam", "Credential")
WebhookSubscription = apps.get_model("integrate", "WebhookSubscription")
VCSIntegration = apps.get_model("integrate", "VCSIntegration")
Repository = apps.get_model("integrate", "Repository")
Source = apps.get_model("integrate", "Source")
Template = apps.get_model("integrate", "Template")


@strawberry_django.type(Vendor)
class VendorType(AngeeNode):
    """GraphQL projection of an integration vendor catalogue row."""

    slug: auto
    display_name: auto
    website_url: auto
    icon: auto
    description: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Integration)
class IntegrationType(AngeeNode):
    """Admin projection of an integration.

    Exposes the catalogue/identity associations as nested relations so the
    console form's ``many2one`` pickers auto-wire (mirrors iam's
    ``CredentialType.external_account``); safe because the surface is admin-gated.
    """

    vendor: VendorType
    credential: CredentialType | None
    account: ExternalAccountType | None
    owner: UserType
    status: auto
    config: JSON
    capability_statuses: JSON
    last_used_at: auto
    last_error: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["vendor", "status"])
    def display_name(self) -> str:
        """Return a human label for the record header and relation pickers.

        Integration has no natural string column; this gives ``recordRepresentation``
        a value (vendor + status) to show.
        """

        vendor = getattr(cast(Any, self), "vendor", None)
        label = str(getattr(vendor, "display_name", "") or getattr(vendor, "slug", "") or "integration")
        return f"{label} ({cast(Any, self).status})"


@strawberry.input
class VendorInput:
    """Fields accepted when creating a vendor."""

    slug: str
    display_name: str
    website_url: str = ""
    icon: str = ""
    description: str = ""


@strawberry.input
class VendorPatch:
    """Fields accepted when updating a vendor."""

    id: relay.GlobalID
    slug: str | None = strawberry.UNSET
    display_name: str | None = strawberry.UNSET
    website_url: str | None = strawberry.UNSET
    icon: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET


@strawberry_django.type(WebhookSubscription)
class WebhookSubscriptionType(AngeeNode):
    """Admin projection of an outbound webhook subscription.

    The signing ``secret`` is deliberately omitted (write-only) — unlike
    OAuthClient's revealed ``client_secret``, a webhook secret is never read back.
    """

    owner: UserType
    integration_filter: IntegrationType | None
    target_url: auto
    event_kinds: JSON
    impl_app_filter: JSON
    enabled: auto
    last_delivery_at: auto
    last_delivery_status: auto
    last_error: auto
    consecutive_failures: auto
    created_at: auto
    updated_at: auto


@strawberry.input
class WebhookSubscriptionInput:
    """Fields accepted when creating a webhook subscription."""

    owner: relay.GlobalID
    target_url: str
    secret: str
    event_kinds: JSON | None = None
    impl_app_filter: JSON | None = None
    integration_filter: relay.GlobalID | None = None
    enabled: bool = True


@strawberry.input
class WebhookSubscriptionPatch:
    """Fields accepted when updating a webhook subscription."""

    id: relay.GlobalID
    target_url: str | None = strawberry.UNSET
    secret: str | None = strawberry.UNSET
    event_kinds: JSON | None = strawberry.UNSET
    impl_app_filter: JSON | None = strawberry.UNSET
    integration_filter: relay.GlobalID | None = strawberry.UNSET
    enabled: bool | None = strawberry.UNSET


@strawberry.input
class IntegrationInput:
    """Fields accepted when creating an integration.

    FK GlobalIDs resolve to instances via strawberry-django (like storage's
    ``DriveInput.backend``); ``owner`` is field-backed REBAC, so writing it
    derives the owner tuple.
    """

    vendor: relay.GlobalID
    credential: relay.GlobalID
    owner: relay.GlobalID
    account: relay.GlobalID | None = strawberry.UNSET
    # UNSET (not None): an omitted field must fall back to the model default, not
    # overwrite it with null — `status`/`config` are non-null columns.
    config: JSON | None = strawberry.UNSET
    status: str | None = strawberry.UNSET


@strawberry.input
class IntegrationPatch:
    """Fields accepted when updating an integration."""

    id: relay.GlobalID
    vendor: relay.GlobalID | None = strawberry.UNSET
    credential: relay.GlobalID | None = strawberry.UNSET
    account: relay.GlobalID | None = strawberry.UNSET
    owner: relay.GlobalID | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET
    status: str | None = strawberry.UNSET


@strawberry.type
class IntegrateConsoleQuery:
    """Admin integration catalogue and integration queries."""

    vendors: OffsetPaginated[VendorType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    vendor: VendorType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    integrations: OffsetPaginated[IntegrationType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    integration: IntegrationType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    webhook_subscriptions: OffsetPaginated[WebhookSubscriptionType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    webhook_subscription: WebhookSubscriptionType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )


_VENDOR_MUTATION = crud(
    VendorType,
    create=VendorInput,
    update=VendorPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="vendor",
    write_context="integrate.graphql.vendor",
)
"""Admin vendor CRUD: const-admin gated by ``PlatformAdminPermission``, written elevated."""

_INTEGRATION_MUTATION = crud(
    IntegrationType,
    create=IntegrationInput,
    update=IntegrationPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="integration",
    write_context="integrate.graphql.integration",
)
"""Admin integration CRUD: FK inputs resolve via strawberry-django; written elevated."""

_WEBHOOK_MUTATION = crud(
    WebhookSubscriptionType,
    create=WebhookSubscriptionInput,
    update=WebhookSubscriptionPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="webhook_subscription",
    write_context="integrate.graphql.webhook_subscription",
)
"""Admin outbound-webhook CRUD: secret is write-only; written elevated."""


@strawberry.type
class RotatedSecret:
    """A freshly rotated webhook signing secret, returned once for display."""

    ok: bool
    secret: str


def _resolve(model: type[models.Model], gid: relay.GlobalID, *, reason: str) -> Any:
    """Return the elevated instance addressed by ``gid`` for an action write.

    Admin authorization is enforced by the field's ``permission_classes`` (with
    the request actor) before the resolver runs; the row read/write then runs
    elevated, the same shape as ``crud(..., write_context=…)``.
    """

    with system_context(reason=reason):
        instance = instance_from_public_id(model, gid.node_id, queryset=model._default_manager.all())
    if instance is None:
        raise ValueError(f"{model._meta.object_name} {gid.node_id!r} was not found.")
    return instance


def _vendor_by_slug(slug: str) -> Any:
    """Return a vendor catalogue row by slug, or raise."""

    with system_context(reason="integrate.graphql.vendor_slug"):
        vendor = Vendor.objects.filter(slug=slug).first()
    if vendor is None:
        raise ValueError(f"Vendor {slug!r} was not found.")
    return vendor


@strawberry.type
class IntegrationCredentialMutation:
    """Self-service integration creation from connected credentials."""

    @strawberry.mutation
    def create_integration_from_credential(
        self,
        info: strawberry.Info,
        credential: relay.GlobalID,
        vendor_slug: str,
        credential_env: str = "",
    ) -> IntegrationType:
        """Create or update this user's integration from a connected credential.

        Self-service, not platform-admin: the authorization is *ownership of the
        credential*. ``_resolve`` reads the credential elevated, then the
        ``user_id`` check below is the actual gate. This deliberately bypasses the
        ``create = admin->member`` arm in ``integrate/permissions.zed`` (which
        governs the admin-console Integration CRUD), so a credential owner can wire
        up their own integration without an admin.
        """

        user = _session_user(info)
        oauth_credential = _resolve(
            Credential,
            credential,
            reason="integrate.graphql.integration_from_credential.credential",
        )
        if oauth_credential.user_id != user.pk:
            raise PermissionDenied("Credential does not belong to the current user.")
        vendor = _vendor_by_slug(vendor_slug)
        with system_context(reason="integrate.graphql.integration_from_credential"), transaction.atomic():
            # Race-safe upsert keyed by the (owner, vendor, credential) unique
            # constraint: two concurrent submits converge on the one row instead
            # of both missing a get-then-insert and creating duplicates.
            integration, _created = Integration.objects.update_or_create(
                owner=user,
                vendor=vendor,
                credential=oauth_credential,
                defaults={"account": oauth_credential.external_account},
            )
            if credential_env:
                integration.set_credential_env(credential_env)
                integration.save(update_fields=["config", "updated_at"])
        return cast(IntegrationType, integration)


@strawberry.type
class IntegrationActionMutation:
    """Operational actions on an integration (sync, connection test)."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def sync_integration(self, id: relay.GlobalID) -> ActionResult:
        """Run every bridge of one integration now (eager variant of the scheduler)."""

        integration = _resolve(Integration, id, reason="integrate.graphql.sync_integration")
        now = timezone.now()
        ran = 0
        errors = 0
        items = 0
        with system_context(reason="integrate.graphql.sync_integration"):
            for model in bridge_models():
                for bridge in model._default_manager.filter(integration=integration).order_by("pk"):
                    ran += 1
                    bridge.mark_sync_started(now=now)
                    try:
                        result = bridge.sync()
                    except Exception as error:  # noqa: BLE001 — report any bridge failure as telemetry
                        bridge.record_sync_error(error, now=now)
                        errors += 1
                    else:
                        bridge.record_sync(result, now=now)
                        items += result
        if ran == 0:
            return ActionResult(ok=True, message="No bridges to sync.")
        return ActionResult(
            ok=errors == 0,
            message=f"Synced {items} item(s) across {ran} bridge(s); {errors} error(s).",
        )

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def test_connection(self, id: relay.GlobalID) -> ActionResult:
        """Probe the integration's credential so the operator sees it is usable."""

        integration = _resolve(Integration, id, reason="integrate.graphql.test_connection")
        with system_context(reason="integrate.graphql.test_connection"):
            credential = integration.credential
            if credential is None:
                return ActionResult(ok=False, message="No credential is attached.")
            try:
                credential.auth_headers()
            except Exception as error:  # noqa: BLE001 — surface any handler failure to the operator
                return ActionResult(ok=False, message=f"Credential is not usable: {error}")
        return ActionResult(ok=True, message="Credential is usable.")


@strawberry.type
class WebhookActionMutation:
    """Operational actions on an outbound webhook subscription."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def test_webhook_delivery(self, id: relay.GlobalID) -> ActionResult:
        """Send a test event to one subscription and report the delivery outcome."""

        subscription = _resolve(WebhookSubscription, id, reason="integrate.graphql.test_webhook_delivery")
        body = json.dumps(
            {"type": "test", "subscription": subscription.public_id},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        with system_context(reason="integrate.graphql.test_webhook_delivery"):
            try:
                status = subscription.deliver(body)
            except Exception as error:  # noqa: BLE001 — delivery failure is the result, not a 500
                message = "; ".join(error.messages) if hasattr(error, "messages") else str(error)
                subscription.record_delivery_failure(status="", error=message)
                return ActionResult(ok=False, message=f"Delivery failed: {message}")
            subscription.record_delivery(status)
        return ActionResult(ok=True, message=f"Delivered (status {status}).")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def rotate_webhook_secret(self, id: relay.GlobalID) -> RotatedSecret:
        """Roll one subscription's signing secret and return the new value once."""

        subscription = _resolve(WebhookSubscription, id, reason="integrate.graphql.rotate_webhook_secret")
        with system_context(reason="integrate.graphql.rotate_webhook_secret"):
            secret = subscription.rotate_secret()
        return RotatedSecret(ok=True, secret=secret)


# --- VCS inventory: integrations, repositories, sources, templates ----------


@strawberry_django.type(VCSIntegration)
class VCSIntegrationType(AngeeNode):
    """Admin projection of a VCS integration (the git host capability)."""

    integration: IntegrationType
    backend_class: auto
    status: auto
    config: JSON
    last_sync_completed_at: auto
    last_sync_status: auto
    last_error: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["backend_class", "status"])
    def display_name(self) -> str:
        """Return a human label for the record header and relation pickers."""

        return f"{cast(Any, self).backend_class} ({cast(Any, self).status})"


@strawberry_django.type(Repository)
class RepositoryType(AngeeNode):
    """Admin projection of one inventoried repository."""

    vcs_integration: VCSIntegrationType
    org: auto
    name: auto
    remote: auto
    ssh_remote: auto
    remote_id: auto
    default_branch: auto
    visibility: auto
    web_url: auto
    archived: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Source)
class SourceType(AngeeNode):
    """Admin projection of one source (a ref+path pointer into a repository)."""

    repository: RepositoryType
    kind: auto
    ref: auto
    path: auto
    last_synced_at: auto
    created_at: auto
    updated_at: auto


@strawberry_django.filter_type(Source, lookups=True)
class SourceFilter:
    """Field lookups accepted when filtering the sources list.

    ``kind`` lets a consumer surface scope a list to its own source kind (e.g. the
    agents console filtering to ``skill`` sources).
    """

    kind: auto
    ref: auto
    repository: auto


@strawberry_django.type(Template)
class TemplateType(AngeeNode):
    """Admin projection of one discovered template."""

    source: SourceType
    name: auto
    kind: auto
    path: auto
    inputs: JSON
    created_at: auto
    updated_at: auto


@strawberry.type
class RepoCandidate:
    """A repository the host returns for the add typeahead (not yet inventoried)."""

    name: str
    org: str
    remote: str
    ssh_remote: str
    default_branch: str
    visibility: str
    web_url: str
    archived: bool


@strawberry.input
class VCSIntegrationInput:
    """Fields accepted when creating a VCS integration.

    ``backend_class`` is an ``ImplClassField`` enum key (e.g. ``github``);
    ``webhook_secret`` is write-only (never read back).
    """

    integration: relay.GlobalID
    backend_class: str = "none"
    # UNSET (not None): an omitted JSON config falls back to the non-null model
    # default rather than overwriting it with null.
    config: JSON | None = strawberry.UNSET
    webhook_secret: str = ""


@strawberry.input
class VCSIntegrationPatch:
    """Fields accepted when updating a VCS integration."""

    id: relay.GlobalID
    backend_class: str | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET
    webhook_secret: str | None = strawberry.UNSET
    status: str | None = strawberry.UNSET


@strawberry.input
class SourceInput:
    """Fields accepted when creating a source."""

    repository: relay.GlobalID
    kind: str
    ref: str = ""
    path: str = ""


@strawberry.input
class SourcePatch:
    """Fields accepted when updating a source."""

    id: relay.GlobalID
    kind: str | None = strawberry.UNSET
    ref: str | None = strawberry.UNSET
    path: str | None = strawberry.UNSET


def _repo_candidate(descriptor: Any) -> RepoCandidate:
    """Project a host ``RepoDescriptor`` into a typeahead candidate."""

    return RepoCandidate(
        name=str(descriptor.name),
        org=str(descriptor.org),
        remote=str(descriptor.remote),
        ssh_remote=str(descriptor.ssh_remote),
        default_branch=str(descriptor.default_branch),
        visibility=str(descriptor.visibility),
        web_url=str(descriptor.web_url),
        archived=bool(descriptor.archived),
    )


@strawberry.type
class VCSConsoleQuery:
    """Admin VCS inventory queries."""

    vcs_integrations: OffsetPaginated[VCSIntegrationType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    vcs_integration: VCSIntegrationType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    repositories: OffsetPaginated[RepositoryType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    repository: RepositoryType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    sources: OffsetPaginated[SourceType] = strawberry_django.offset_paginated(
        filters=SourceFilter,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    source: SourceType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    templates: OffsetPaginated[TemplateType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    template: TemplateType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def search_repositories(self, vcs_integration_id: relay.GlobalID, query: str) -> list[RepoCandidate]:
        """Return host repositories matching ``query`` for the add typeahead."""

        vcs = _resolve(VCSIntegration, vcs_integration_id, reason="integrate.graphql.search_repositories")
        with system_context(reason="integrate.graphql.search_repositories"):
            return [_repo_candidate(descriptor) for descriptor in vcs.search_repositories(query)]


_VCS_INTEGRATION_MUTATION = crud(
    VCSIntegrationType,
    create=VCSIntegrationInput,
    update=VCSIntegrationPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="vcs_integration",
    write_context="integrate.graphql.vcs_integration",
)
"""Admin VCS-integration CRUD: webhook_secret is write-only; written elevated."""

_SOURCE_MUTATION = crud(
    SourceType,
    create=SourceInput,
    update=SourcePatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="source",
    write_context="integrate.graphql.source",
)
"""Admin source CRUD: FK input resolves via strawberry-django; written elevated."""

_REPOSITORY_MUTATION = crud(
    RepositoryType,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="repository",
    write_context="integrate.graphql.repository",
)
"""Admin repository delete: rows arrive via ``addRepository``/``discoverRepositories``."""


@strawberry.type
class VCSActionMutation:
    """Operational actions on a VCS integration and its inventory."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def add_repository(self, vcs_integration_id: relay.GlobalID, name: str) -> RepositoryType:
        """Inventory one repository by its host ``name`` (a picked typeahead result)."""

        vcs = _resolve(VCSIntegration, vcs_integration_id, reason="integrate.graphql.add_repository")
        with system_context(reason="integrate.graphql.add_repository"):
            return cast(RepositoryType, vcs.import_repository(name))

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def discover_repositories(self, vcs_integration_id: relay.GlobalID, org: str = "") -> ActionResult:
        """Inventory every repository the account exposes (bulk import; prunes vanished)."""

        vcs = _resolve(VCSIntegration, vcs_integration_id, reason="integrate.graphql.discover_repositories")
        with system_context(reason="integrate.graphql.discover_repositories"):
            count = vcs.discover_repositories(org=org)
        return ActionResult(ok=True, message=f"Inventoried {count} repository(ies).")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def sync_vcs_integration(self, id: relay.GlobalID) -> ActionResult:
        """Refresh every repository's sources for one VCS integration now."""

        vcs = _resolve(VCSIntegration, id, reason="integrate.graphql.sync_vcs_integration")
        now = timezone.now()
        with system_context(reason="integrate.graphql.sync_vcs_integration"):
            vcs.mark_sync_started(now=now)
            try:
                result = vcs.sync()
            except Exception as error:  # noqa: BLE001 — sync failure is the result, not a 500
                vcs.record_sync_error(error, now=now)
                return ActionResult(ok=False, message=f"Sync failed: {error}")
            vcs.record_sync(result, now=now)
        return ActionResult(ok=True, message=f"Synced {result} item(s).")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def refresh_source(self, id: relay.GlobalID) -> ActionResult:
        """Re-enumerate one source's output rows now."""

        source = _resolve(Source, id, reason="integrate.graphql.refresh_source")
        with system_context(reason="integrate.graphql.refresh_source"):
            count = source.refresh()
        return ActionResult(ok=True, message=f"Synced {count} item(s).")


# Extracted with an explicit annotation: a bare homogeneous list of two
# AngeeNode-decorated types infers as ``list[type[AngeeNode]]`` and trips mypy's
# invariance check; ``list[type]`` widens it. (iam's inline lists are heterogeneous,
# so they don't hit this.)
_CONSOLE_TYPES: list[type] = [
    VendorType,
    IntegrationType,
    WebhookSubscriptionType,
    VCSIntegrationType,
    RepositoryType,
    SourceType,
    TemplateType,
    RepoCandidate,
]

schemas = {
    "public": {
        "mutation": [IntegrationCredentialMutation],
        "types": [
            VendorType,
            IntegrationType,
            CredentialType,
            ExternalAccountType,
            UserType,
        ],
    },
    "console": {
        "query": [IntegrateConsoleQuery, VCSConsoleQuery],
        "mutation": [
            _VENDOR_MUTATION,
            _INTEGRATION_MUTATION,
            _WEBHOOK_MUTATION,
            _VCS_INTEGRATION_MUTATION,
            _SOURCE_MUTATION,
            _REPOSITORY_MUTATION,
            IntegrationCredentialMutation,
            IntegrationActionMutation,
            WebhookActionMutation,
            VCSActionMutation,
        ],
        "subscription": [changes(Integration, field="integrationChanged")],
        "types": _CONSOLE_TYPES,
    },
}
"""GraphQL contributions installed by the integrate addon."""
