"""Native querysets owners for integration contracts."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Self, cast

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import connections, models
from django.db.models import OuterRef, Q, Subquery
from django.db.models.query import ModelIterable
from django.utils import timezone
from rebac import to_subject_ref

from angee.base.refs import generic_pointer_target
from angee.iam.models.ownership import OwnerQuerySet
from angee.integrate.choices import (
    IntegrationLifecycle,
    IntegrationRuntimeStatus,
)
from angee.integrate.states import (
    LinkStatus,
    SyncFailure,
    SyncFailureKind,
)
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet

if TYPE_CHECKING:
    from angee.integrate.models.bridges import Bridge
    from angee.integrate.models.connections import Credential, ExternalAccount, OAuthClient
    from angee.integrate.models.integrations import Integration
    from angee.integrate.models.sync import RecordLink, SyncStream
    from angee.integrate.models.webhooks import WebhookSubscription


class OAuthClientQuerySet[OAuthClientT: OAuthClient](AngeeHistoryQuerySet[OAuthClientT]):
    """REBAC-scoped reads for OAuth client registration."""

    def preferred(self, *, environment: str = "prod") -> Self:
        """Select the first registration per slug under a deterministic priority.

        Choose the environment before testing readiness: a disabled production
        registration must not silently fall back to a development registration.
        """

        alternatives = (
            self.model.objects.db_manager(self.db)
            .sudo(reason="integrate.oauth.environment_preference")
            .filter(
                slug=OuterRef("slug"),
            )
            .order_by(
                models.Case(models.When(environment=environment, then=0), default=1),
                "environment",
                "pk",
            )
        )
        return self.filter(pk=Subquery(alternatives.values("pk")[:1]))

    def ready_registrations(self) -> tuple[OAuthClientT, ...]:
        """Evaluate the one model readiness rule on preferred public registrations."""

        return tuple(
            row for row in self.preferred().only(*self.model.readiness_fields) if row.configuration_state == "ready"
        )

    def connectable(self) -> Self:
        """Use the model's readiness rule for the public connection picker."""

        public = self.system_context(reason="integrate.oauth.connectable")
        return public.filter(pk__in=[row.pk for row in public.ready_registrations()])

    def enabled_for_slug(self, slug: str, *, environment: str = "prod") -> OAuthClientT | None:
        """Resolve the same ready registration the picker and projection offer."""

        client = self.filter(slug=slug).preferred(environment=environment).first()
        return client if client is not None and client.configuration_state == "ready" else None


class ExternalAccountQuerySet[ExternalAccountT: ExternalAccount](
    OwnerQuerySet[ExternalAccountT], AngeeHistoryQuerySet[ExternalAccountT]
):
    """REBAC-scoped reads and verb-authorized ownership release for external accounts."""

    def console_external_accounts(self) -> Self:
        """Return admin-visible external accounts with guarded FK joins."""

        return self.rebac_select_related("oauth_client", "credential")


class CredentialQuerySet[CredentialT: Credential](AngeeHistoryQuerySet[CredentialT]):
    """REBAC-scoped reads for credential health and connected accounts."""

    def connection_health(self) -> Self:
        """Load current health and refresh evidence for an integration-owned projection.

        Native prefetch hints batch non-secret facts. Only an expired OAuth row
        that needs refreshability loads its deferred material through its handler.
        """

        return (
            self.sudo(reason="integrate.connection.health")
            .select_related("oauth_client")
            .only(
                "status",
                "expires_at",
                "last_refresh_status",
                "kind",
                "oauth_client__supports_refresh",
            )
        )

    def connected_for(self, user: Any) -> Self:
        """Return ``user``'s external-account-backed credentials.

        Each row is a credential whose ``external_account`` (and that account's
        ``credential``) is preloaded under the ambient actor's scope. Owns the
        "what counts as a connected account" predicate for the self-service
        connected-accounts surface.
        """

        return self.filter(
            user=user,
            external_account__isnull=False,
        ).rebac_select_related("external_account", "external_account__credential")

    def console_credentials(self) -> Self:
        """Return admin-visible credential health with guarded FK joins."""

        return self.rebac_select_related(
            "oauth_client",
            "external_account",
        )


def _concrete_page_groups(queryset: models.QuerySet[Any], rows: list[Any]) -> dict[type[models.Model], list[Any]]:
    """Group materialized base rows by their saved concrete ContentType."""

    if not rows:
        return {}
    if any("concrete_type_id" not in row.__dict__ for row in rows):
        kinds = dict(
            queryset.model._base_manager.db_manager(queryset.db)
            .filter(pk__in=[row.pk for row in rows])
            .values_list("pk", "concrete_type_id")
        )
        for row in rows:
            row.concrete_type_id = kinds.get(row.pk)

    by_model: dict[type[models.Model], list[Any]] = {}
    for row in rows:
        model = row.declared_concrete_model()
        if model is not None and model is not type(row):
            by_model.setdefault(model, []).append(row)
    return by_model


def _prepare_concrete_capabilities(queryset: models.QuerySet[Any], rows: list[Any]) -> None:
    """Fetch only the concrete connector types present in this selected page."""

    groups = _concrete_page_groups(queryset, rows)
    for model, parents in sorted(groups.items(), key=lambda item: item[0]._meta.label_lower):
        actor, unscoped = parents[0].effective_actor(strict=True)
        source = model._base_manager.db_manager(queryset.db) if unscoped else (
            cast(Any, model).objects.db_manager(queryset.db).with_actor(actor)
        )
        children = {child.pk: child for child in source.filter(pk__in=[row.pk for row in parents])}
        for parent in parents:
            parent.__dict__["_concrete_capability"] = (actor, unscoped, children.get(parent.pk, parent))


class IntegrationQuerySet[IntegrationT: Integration](AngeeHistoryQuerySet[IntegrationT]):
    """Chainable collection scopes for integration and bridge rows."""

    def with_vendor_label(self) -> Self:
        """Carry the two public vendor-name columns used by Integration.display_label."""

        return self.annotate(
            _vendor_display_name=models.F("vendor__display_name"),
            _vendor_slug=models.F("vendor__slug"),
        )

    def with_concrete_capabilities(self) -> Self:
        """Batch actor-readable concrete connectors only for types on the page."""

        return self.with_page_hook(_prepare_concrete_capabilities)

    def of_concrete_type(self) -> Self:
        """Keep rows whose declared final MTI kind is this queryset's model."""

        return self.filter(concrete_type=ContentType.objects.db_manager(self.db).get_for_model(self.model))

    def ready_for_work(self) -> Self:
        """Exclude pending or unsuccessful discovery through one admission predicate."""

        return self.filter(discovery_retry_at__isnull=True).filter(
            Q(discovery_generation=0) | Q(discovery_completed_at__isnull=False),
        )

    def due_for_enqueue(self, *, timestamp: datetime, stale_before: datetime) -> Self:
        """Return bridge rows due for a new queue attempt or stale recovery."""

        return self.ready_for_work().filter(
            Q(lifecycle=IntegrationLifecycle.CONNECTED, next_sync_at__lte=timestamp)
            | Q(
                lifecycle__in=(IntegrationLifecycle.CONNECTED, IntegrationLifecycle.PAUSED),
                sync_stage="queued",
                updated_at__lte=stale_before,
            )
        )

    def pending_discoveries(self, *, now: datetime) -> Self:
        """Select due connection discovery, including paused rows."""

        return (
            self.filter(discovery_retry_at__lte=now)
            .exclude(
                lifecycle=IntegrationLifecycle.DISCONNECTED,
            )
            .order_by("pk")
        )

    def live_session_candidates(self) -> Self:
        """Select connected healthy concrete bridges using declared live backends."""

        model = cast("type[Bridge]", self.model)
        field = model.live_implementation_field()
        keys = model.live_implementation_keys()
        if field is None or not keys:
            return self.none()
        return (
            self.of_concrete_type()
            .ready_for_work()
            .filter(
                **{f"{field.name}__in": keys},
                lifecycle=self.model.Lifecycle.CONNECTED,
                runtime_status=IntegrationRuntimeStatus.OK,
            )
            .order_by("pk")
        )

    def live_account_owners(
        self,
        bridge: Any,
        impl_key: str,
        external_id: str,
        *,
        identity_key: str,
    ) -> Self:
        """Return other rows retaining the same backend account claim."""

        return (
            self.filter(
                **{
                    bridge.live_impl_field: impl_key,
                    f"subscription_state__{identity_key}": external_id,
                    "lifecycle__in": (str(bridge.Lifecycle.CONNECTED), str(bridge.Lifecycle.PAUSED)),
                }
            )
            .exclude(pk=bridge.pk)
            .order_by("pk")
        )

    @staticmethod
    def prime_concrete_children(rows: list[Integration], *, actor: Any, using: str) -> None:
        """Fill the owner's integrity and actor-readable child caches for this page."""

        if not rows:
            return
        direct = type(rows[0]).concrete_child_models()
        ids = {row.pk for row in rows}
        probes = [
            model._base_manager.using(using).filter(pk__in=ids).order_by()
            .annotate(_child_kind=models.Value(index, output_field=models.IntegerField()))
            .values_list("pk", "_child_kind")
            for index, model in enumerate(direct)
        ]
        present = list(probes[0].union(*probes[1:], all=True).order_by()) if probes else []
        by_pk = {row.pk: row for row in rows}
        for row in rows:
            row._concrete_child_actor = to_subject_ref(actor)
            for model in direct:
                for authorized in (False, True):
                    setattr(row, row.concrete_child_cache_attr(model, authorized=authorized), [])
        by_model: dict[tuple[type[Integration], type[Integration]], set[Any]] = {}
        for pk, index in present:
            row = by_pk[pk]
            direct_model = direct[index]
            declared = row.declared_concrete_model()
            model = declared if declared is not None and issubclass(declared, direct_model) else direct_model
            by_model.setdefault((model, direct_model), set()).add(pk)
        for (model, direct_model), keys in by_model.items():
            integrity = {child.pk: child for child in model._base_manager.using(using).filter(pk__in=keys)}
            readable_children = {
                child.pk: child for child in model.objects.db_manager(using).with_actor(actor).filter(pk__in=keys)
            }
            for pk in keys:
                row = by_pk[pk]
                if model is not direct_model:
                    direct_attr = row.concrete_child_cache_attr(direct_model, authorized=False)
                    child = integrity.get(pk)
                    setattr(row, direct_attr, [child] if child else [])
                for scope, found in ((False, integrity.get(pk)), (True, readable_children.get(pk))):
                    setattr(row, row.concrete_child_cache_attr(model, authorized=scope), [found] if found else [])

    def with_concrete_children(self, *, actor: Any) -> Self:
        """Prime child caches only for a selected concrete target or source label."""

        return self.only("concrete_type_id").with_page_hook(
            lambda queryset, rows: IntegrationQuerySet.prime_concrete_children(rows, actor=actor, using=queryset.db)
        )


class WebhookSubscriptionQuerySet[WebhookSubscriptionT: WebhookSubscription](
    AngeeHistoryQuerySet[WebhookSubscriptionT]
):
    """Chainable reads owned by WebhookSubscription."""

    def matching(self, *, kind: str, impl_app: str, integration_pk: Any | None) -> Self:
        """Return the narrowest portable candidate queryset for one webhook event."""

        queryset = self.filter(enabled=True)
        if integration_pk is None:
            queryset = queryset.filter(integration_filter__isnull=True)
        else:
            queryset = queryset.filter(Q(integration_filter__isnull=True) | Q(integration_filter_id=integration_pk))
        if connections[self.db].features.supports_json_field_contains:
            queryset = queryset.filter(event_kinds__contains=[kind])
            queryset = queryset.filter(Q(impl_app_filter=[]) | Q(impl_app_filter__contains=[impl_app]))
        return queryset.order_by("pk")


class SyncStreamQuerySet[SyncStreamT: SyncStream](AngeeHistoryQuerySet[SyncStreamT]):
    """Chainable reads owned by SyncStream."""

    def current_for_bridge(self, bridge: models.Model, key: str | None = None) -> Self:
        """Return one current epoch per partition of the bridge's named stream, or of every stream.

        Without ``key`` this is one query over every (key, partition) the bridge
        has opened, so a caller resolving across streams reads them once.
        """

        rows = self.sudo(reason="integrate.stream.current_for_bridge").filter(integration_id=bridge.pk)
        if key is not None:
            rows = rows.filter(key=key)
        latest = (
            rows.filter(key=OuterRef("key"), partition=OuterRef("partition"))
            .order_by("-generation")
            .values("generation")[:1]
        )
        return rows.filter(generation=Subquery(latest)).order_by("key", "partition")


class RecordLinkQuerySet[RecordLinkT: RecordLink](AngeeHistoryQuerySet[RecordLinkT]):
    """Load comparison evidence for a bounded collection of replica identities."""

    def with_concrete_sources(self, *, actor: Any) -> Self:
        """Prime the selected link page's readable streams and capabilities once."""

        class ConcreteSourceIterable(ModelIterable):
            def __iter__(self):
                rows = list(super().__iter__())
                if not rows:
                    return
                stream_model = apps.get_model("integrate", "SyncStream")
                integration_model = apps.get_model("integrate", "Integration")
                streams = list(
                    stream_model.objects.db_manager(self.queryset.db)
                    .with_actor(actor).filter(pk__in={row.stream_id for row in rows})
                )
                integrations = list(
                    integration_model.objects.db_manager(self.queryset.db)
                    .with_actor(actor).filter(pk__in={stream.integration_id for stream in streams})
                    .with_vendor_label()
                )
                IntegrationQuerySet.prime_concrete_children(integrations, actor=actor, using=self.queryset.db)
                by_integration = {integration.pk: integration for integration in integrations}
                by_stream = {stream.pk: stream for stream in streams}
                for stream in streams:
                    if integration := by_integration.get(stream.integration_id):
                        stream_model._meta.get_field("integration").set_cached_value(stream, integration)
                for row in rows:
                    if stream := by_stream.get(row.stream_id):
                        row._meta.get_field("stream").set_cached_value(row, stream)
                yield from rows

        queryset = self.all()
        queryset._iterable_class = ConcreteSourceIterable
        return queryset

    def current_for_bridge(self, bridge: models.Model) -> Self:
        """Keep this selection within the bridge's current stream generations."""
        streams = apps.get_model("integrate", "SyncStream").objects.db_manager(self.db).current_for_bridge(bridge)
        return self.filter(stream__in=streams)

    def for_target(self, target: models.Model) -> Self:
        """Select the canonical generic pointer identity owned by the target."""
        content_type, object_id = generic_pointer_target(target)
        return self.filter(target_content_type=content_type, target_object_id=str(object_id))

    def absence_eligible(self) -> Self:
        """Roots and children with absent parents may accumulate absence evidence."""

        parents = self.model.objects.db_manager(self.db).filter(absence_count__gt=0)
        return self.filter(Q(parent__isnull=True) | Q(parent_id__in=parents.values("pk"))).exclude(
            status=LinkStatus.TOMBSTONE,
        )

    def unseen_since(self, started: datetime) -> Self:
        """Rows not observed during this sweep's enumeration."""

        return self.filter(Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=started))

    def absence_candidates(self, *, started: datetime, children: bool) -> Self:
        """Select this sweep's root or missing-root-child batch without losing scope."""

        rows = self.absence_eligible()
        if not children:
            return rows.filter(parent__isnull=True).unseen_since(started)
        parents = self.filter(parent__isnull=True, absence_count__gt=0).unseen_since(started)
        return rows.filter(parent_id__in=parents.values("pk"))

    def with_sync_evidence(self) -> Self:
        """Load the protected application projection; state is already on the link."""
        return self.select_related("applied_base", "parent", "stream").defer(
            "source_payload", "mapped_payload", "applied_base__source_payload"
        )

    def with_open_failures(self, *, include_source: bool = False) -> Self:
        """Select open-state owners; include retained sources only for their readable labels."""
        return (
            self.filter(Q(status=LinkStatus.FAILED) | Q(outbox_state__isnull=False))
            .order_by("pk")
            .only(
                "stream_id",
                "parent_id",
                "external_key",
                "status",
                "inbound",
                "outbox_state",
                "outbox_retry_at",
                "outbox_last_error",
                *(("source_payload",) if include_source else ()),
            )
        )

    def open_failures(self) -> tuple[SyncFailure, ...]:
        """Project state after the caller's actor-scoped link selection."""
        return tuple(
            row
            for link in self.with_open_failures().select_related("stream").iterator(chunk_size=500)
            for row in link.failures(open_only=True)
        )

    def rescan(self, *, limit: int, due_by: datetime | None = None) -> tuple[SyncFailure, ...]:
        """Bound inbound failures due by ``due_by`` (now) after excluding every conflicted aggregate."""
        if limit < 1:
            raise ValueError("A failure rescan limit must be positive.")
        links = self.with_open_failures().select_related("stream")
        due_by = due_by or timezone.now()
        conflicts = set()
        candidates = []
        for link in links.iterator(chunk_size=500):
            aggregate = link.parent_id or link.pk
            failures = link.failures(open_only=True)
            if any(row.kind == SyncFailureKind.CONFLICT for row in failures):
                conflicts.add(aggregate)
            for row in failures:
                if (
                    row.slot is not None
                    and row.kind != SyncFailureKind.REMOTE_REJECTED
                    and (row.retry_at is None or row.retry_at <= due_by)
                ):
                    candidates.append((aggregate, row))
        return tuple(row for aggregate, row in candidates if aggregate not in conflicts)[:limit]
