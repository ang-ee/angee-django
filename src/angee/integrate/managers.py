"""Native managers owners for integration contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import copy, deepcopy
from dataclasses import replace
from datetime import datetime
from itertools import batched
from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connections, models, transaction
from django.db.models import Q
from django.utils import timezone
from rebac import (
    PermissionDenied,
    database_context,
    is_sudo,
    system_context,
)
from rebac.database import write_alias
from simple_history.signals import post_create_historical_record

from angee.base.models.merge_references import references
from angee.base.refs import generic_pointer_target
from angee.integrate.choices import (
    AccountStatus,
    CredentialStatus,
    IntegrationLifecycle,
)
from angee.integrate.credentials import CredentialKind
from angee.integrate.oauth.errors import INVALID_STATE, OAuthFlowError
from angee.integrate.querysets import (
    CredentialQuerySet,
    ExternalAccountQuerySet,
    IntegrationQuerySet,
    OAuthClientQuerySet,
    RecordLinkQuerySet,
    SyncStreamQuerySet,
    WebhookSubscriptionQuerySet,
)
from angee.integrate.states import (
    UNSET,
    LinkStatus,
    StreamDefinition,
    StreamDirection,
    StreamKind,
    StreamPhase,
    StreamState,
)
from angee.provenance.context import current_changeset
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.statements import (
    Statement,
)

if TYPE_CHECKING:
    from angee.integrate.models.connections import Credential, ExternalAccount, OAuthClient
    from angee.integrate.models.integrations import Integration, Vendor
    from angee.integrate.models.sync import RecordLink, SyncStream
    from angee.integrate.models.webhooks import WebhookSubscription


_OAuthClientManagerBase = AngeeHistoryManager.from_queryset(OAuthClientQuerySet)


class OAuthClientManager[OAuthClientT: OAuthClient](_OAuthClientManagerBase[OAuthClientT]):
    """Manager for settings-sourced OAuth client registration.

    OAuth only: settings entries carry the OAuth base fields. The OIDC refinement
    (issuer/JWKS/discovery + login policy) is owned by the ``iam_integrate_oidc``
    addon and seeded there, never from here.
    """

    seed_fields = frozenset(
        {
            "display_name",
            "provider_type",
            "icon",
            "client_id",
            "discovery_url",
            "authorize_endpoint",
            "token_endpoint",
            "revoke_endpoint",
            "token_request_format",
            "is_enabled",
            "scopes_catalogue",
            "default_scopes",
            "supports_refresh",
            "refresh_rotates",
            "supports_pkce",
            "max_refresh_age_seconds",
            "authorize_params",
            "token_params",
            "manual_redirect_uri",
            "loopback_redirect_path",
        }
    )
    setting_fields = seed_fields | frozenset({"slug", "environment", "client_secret"})
    required_setting_fields = frozenset({"slug", "display_name", "client_id"})

    def sync_from_settings(
        self, entries: Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | None = None
    ) -> tuple[Any, ...]:
        """Create or update OAuth clients declared in ``settings.ANGEE_INTEGRATE_OAUTH_CLIENTS``.

        The host owns reading environment variables. Integrate reads only Django
        settings and keeps secrets out of resource files.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db))

        synced: list[Any] = []
        with (
            system_context(reason="integrate.oauth_clients.seed"),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            for index, entry in enumerate(bound_manager._setting_entries(entries), start=1):
                synced.append(bound_manager._sync_setting_entry(index, entry))
        return tuple(synced)

    def _setting_entries(
        self,
        entries: Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return normalized setting entries from an explicit value or settings."""

        raw_entries = getattr(settings, "ANGEE_INTEGRATE_OAUTH_CLIENTS", ()) if entries is None else entries
        if not raw_entries:
            return ()
        if isinstance(raw_entries, Mapping):
            values = tuple(raw_entries.values())
        elif isinstance(raw_entries, Iterable) and not isinstance(raw_entries, str | bytes):
            values = tuple(raw_entries)
        else:
            raise ValueError("ANGEE_INTEGRATE_OAUTH_CLIENTS must be a sequence or mapping of OAuth client entries.")
        for index, entry in enumerate(values, start=1):
            if not isinstance(entry, Mapping):
                raise ValueError(f"ANGEE_INTEGRATE_OAUTH_CLIENTS entry {index} must be a mapping.")
        return values

    def _sync_setting_entry(self, index: int, entry: Mapping[str, Any]) -> Any:
        """Upsert one settings-authored OAuth client."""

        bound_manager = self.db_manager(write_alias(self.model, using=self._db))

        bound_manager._validate_setting_entry(index, entry)
        slug = str(entry["slug"])
        environment = str(entry.get("environment") or "prod")
        defaults = {field: entry[field] for field in sorted(bound_manager.seed_fields) if field in entry}
        if "client_secret" in entry:
            defaults["client_secret"] = str(entry.get("client_secret") or "")
        oauth_client, _created = bound_manager.update_or_create(
            slug=slug,
            environment=environment,
            defaults=defaults,
        )
        return oauth_client

    def _validate_setting_entry(self, index: int, entry: Mapping[str, Any]) -> None:
        """Raise a clear error for malformed OAuth client seed settings."""

        unknown = set(entry) - self.setting_fields
        if unknown:
            names = ", ".join(sorted(str(name) for name in unknown))
            raise ValueError(f"ANGEE_INTEGRATE_OAUTH_CLIENTS entry {index} has unknown field(s): {names}")
        missing = {field for field in self.required_setting_fields if not entry.get(field)}
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"ANGEE_INTEGRATE_OAUTH_CLIENTS entry {index} is missing required field(s): {names}")


_ExternalAccountManagerBase = AngeeHistoryManager.from_queryset(ExternalAccountQuerySet)


class ExternalAccountManager[ExternalAccountT: ExternalAccount](_ExternalAccountManagerBase[ExternalAccountT]):
    """Manager for idempotent external account linking.

    Actor-less framework writes run under ``system_context``; update paths do not maintain ``updated_by``.
    """

    caller_fields = frozenset(
        {
            "email",
            "display_name",
            "avatar_url",
            "status",
            "identity_claims",
            "last_error",
            "last_error_at",
        }
    )

    def connect_oauth(
        self,
        oauth_client: Any,
        *,
        user: Any,
        external_id: str,
        tokens: dict[str, Any],
        claims: dict[str, Any],
        reason: str,
    ) -> ExternalAccountT:
        """Link a verified identity and its credential in one transaction."""

        bound_manager = self.db_manager(write_alias(self.model, using=self._db))

        email = oauth_client.email_from_claims(claims) or ""
        with (
            system_context(reason=reason),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            account = bound_manager.link(
                oauth_client,
                external_id,
                owner=user,
                email=email,
                identity_claims=claims,
                display_name=oauth_client.display_name_from_claims(claims, email),
                avatar_url=oauth_client.avatar_url_from_claims(claims),
            )
            credential = (
                apps.get_model("integrate", "Credential")
                .objects.db_manager(bound_manager.db, hints={"instance": account})
                .upsert_for_user(
                    user,
                    oauth_client,
                    CredentialKind.OAUTH,
                    tokens,
                    external_account=account,
                )
            )
            account.credential = credential
            account.save(using=account._state.db, update_fields=["credential", "updated_at"])
        return account

    def link(
        self,
        oauth_client: Any,
        external_id: str,
        *,
        owner: Any | None = None,
        **identity: Any,
    ) -> Any:
        """Create or update one ``(oauth_client, external_id)`` external account.

        ``owner`` owns a new account and claims an unowned one: the caller has
        verified that the user controls the identity. An account claimed by another
        owner is refused before any identity update.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db))

        reason = "integrate.connections.link"
        update_values = _validated_manager_values(
            bound_manager.model,
            identity,
            allowed=bound_manager.caller_fields,
        )
        create_values = {
            "email": "",
            "display_name": "",
            "avatar_url": "",
            "status": AccountStatus.ACTIVE,
            "identity_claims": {},
            "last_error": "",
            "last_error_at": None,
            "last_used_at": None,
            "owner": owner,
            **update_values,
        }
        with (
            system_context(reason=reason),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            instance, created = bound_manager.select_for_update().get_or_create(
                oauth_client_id=oauth_client.pk,
                external_id=external_id,
                defaults=create_values,
            )
            if owner is not None and instance.owner_id not in (None, owner.pk):
                raise ValidationError(
                    "This external account is already linked to another user.",
                    code="account_already_linked",
                )
            if not created:
                for name, value in update_values.items():
                    setattr(instance, name, value)
                if owner is not None and instance.owner_id is None:
                    instance.owner = owner
                    update_values["owner"] = owner
                if update_values:
                    instance.save(using=instance._state.db, update_fields=[*update_values, "updated_at"])

        return instance


_CredentialManagerBase = AngeeHistoryManager.from_queryset(CredentialQuerySet)


class CredentialManager[CredentialT: Credential](_CredentialManagerBase[CredentialT]):
    """Manager for idempotent per-user credential writes.

    Actor-less framework writes run under ``system_context``; update paths do not maintain ``updated_by``.
    """

    caller_fields = frozenset(
        {
            "external_account",
            "status",
            "expires_at",
            "granted_scopes",
            "last_refresh_at",
            "last_refresh_status",
        }
    )
    operation_fields = frozenset({"kind", "material"})

    _REASON = "integrate.connections.credential"

    def disconnect_account(self, user: Any, external_account_sqid: str) -> bool:
        """Release this user's account and delete its credential in one admitted transaction.

        prepare_disconnect owns vetoes, derived releases and after-commit remote
        revocation; native owner release and selected credential deletion retain
        their own persistence contracts. An absent user/account link is false.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or user._state.db, instance=user))

        candidates = bound_manager.select_related("oauth_client", "external_account").select_for_update(of=("self",))
        using = candidates.db
        with (
            system_context(reason="integrate.credential.disconnect"),
            database_context(using),
            transaction.atomic(using=using),
        ):
            credential = candidates.filter(user=user, external_account__sqid=external_account_sqid).first()
            if credential is None:
                return False
            account = credential.external_account
            assert account is not None
            bound_manager.prepare_disconnect(credential)
            type(account).objects.db_manager(using).filter(pk=account.pk).release(user)
            deleted, _details = bound_manager.filter(pk=credential.pk).with_action("delete").delete()
        return deleted > 0

    def prepare_disconnect(self, credential: Any) -> None:
        """Validate a disconnect and schedule remote revocation after commit."""

        bound_manager = self.db_manager(
            write_alias(self.model, using=self._db or credential._state.db, instance=credential)
        )

        credential.check_disconnect()
        # Revoking the source grant also releases the resource-specific material
        # it discovered. Direct attachments retain their existing PROTECT policy.
        integrations = (
            apps.get_model("integrate", "Integration")
            .objects.db_manager(bound_manager.db)
            .filter(
                discovery_credential=credential,
            )
            .exclude(credential=credential)
            .order_by("pk")
        )
        for integration in integrations:
            target = integration.concrete_capability()
            target.set_lifecycle(IntegrationLifecycle.DISCONNECTED)
            target.attach_credential(None)
        transaction.on_commit(copy(credential).revoke_remote, using=credential._state.db, robust=True)

    def delete_unattached_derived(self, credential_pk: int | None) -> None:
        """Dispose of discovery-created material after its last attachment releases it."""

        bound_manager = self.db_manager(write_alias(self.model, using=self._db))

        if credential_pk is not None:
            bound_manager.filter(
                pk=credential_pk,
                integrations__isnull=True,
                discovered_integrations__isnull=True,
            ).delete()

    def live_oauth_for_user(self, user: Any, oauth_client: Any) -> Any | None:
        """Reuse a credential whenever its owner does not require fresh consent."""

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or user._state.db, instance=user))

        with system_context(reason="integrate.connections.credential.live"):
            row = bound_manager.filter(user=user, oauth_client=oauth_client, kind=CredentialKind.OAUTH).first()
            return row if row is not None and not row.is_reconnect_required else None

    def upsert_for_user(
        self,
        user: Any,
        oauth_client: Any,
        kind: str,
        material: dict[str, Any],
        /,
        *,
        external_account: Any | None = None,
        reconnect: bool = True,
        **fields: Any,
    ) -> Any:
        """Create or update one ``(user, oauth_client)`` OAuth credential (connect/login flow)."""

        bound_manager = self.db_manager(write_alias(self.model, using=self._db))

        handler = CredentialKind(kind).handler
        operation_values, update_values = bound_manager._assemble_values(handler, material, fields)
        if external_account is not None:
            update_values["external_account_id"] = external_account.pk
        create_values = {
            "external_account_id": external_account.pk if external_account is not None else None,
            **bound_manager._blank_create_values(),
            **operation_values,
            **update_values,
        }
        # Give the credential a human label on create (OAuth rows carry no name of their
        # own). Create-only so an admin rename, and token refreshes, are preserved.
        if not str(create_values.get("name") or ""):
            create_values["name"] = bound_manager._oauth_credential_name(oauth_client, external_account)
        with (
            system_context(reason=bound_manager._REASON),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            existing = bound_manager.filter(user_id=user.pk, oauth_client_id=oauth_client.pk).only("pk").first()
            integrations = []
            attached = False
            if existing is not None:
                affected = (
                    apps.get_model("integrate", "Integration")
                    .objects.db_manager(bound_manager.db)
                    .filter(
                        Q(credential_id=existing.pk) | Q(discovery_credential_id=existing.pk),
                    )
                    .order_by("pk")
                )
                if reconnect:
                    # Match Retry/Resume: concrete integration and ancestors,
                    # then the source credential. Refresh-only writes never
                    # touch integrations and may already hold the credential lock.
                    integrations = [integration.lock_capability() for integration in affected]
                existing = bound_manager.lock_if_supported().get(pk=existing.pk)
                # An attachment cannot commit while the credential is locked (its
                # foreign-key check needs the row), so this read is final. A late
                # arrival attached between the two reads; it is locked after the
                # credential, as its re-attachment below would lock it anyway.
                if reconnect:
                    locked = {integration.pk for integration in integrations}
                    integrations += [
                        integration.lock_capability() for integration in affected if integration.pk not in locked
                    ]
                    attached = bool(integrations)
                else:
                    attached = affected.exists()
            if (
                existing is not None
                and external_account is not None
                and existing.external_account_id not in (None, external_account.pk)
                and attached
            ):
                raise OAuthFlowError("account_switch_refused", 409)
            instance, _created = bound_manager.update_or_create(
                user_id=user.pk,
                oauth_client_id=oauth_client.pk,
                defaults={**operation_values, **update_values},
                create_defaults=create_values,
            )
            if reconnect and existing is not None:
                for integration in integrations:
                    if (
                        existing.external_account_id == instance.external_account_id
                        and integration.credential_id == instance.pk
                    ):
                        continue
                    # Re-consent invalidates resource tokens derived from the old grant.
                    integration.attach_connection(instance, connect_disconnected=False)
        return instance

    @staticmethod
    def _oauth_credential_name(oauth_client: Any, external_account: Any | None) -> str:
        """Return a default label for an OAuth credential: the provider, plus its subject."""

        label = oauth_client.provider_label
        subject = external_account.subject_label if external_account is not None else ""
        return f"{label} ({subject})" if subject else label

    def create_local_credential(
        self,
        user: Any,
        *,
        kind: str,
        name: str,
        material: dict[str, Any],
        **fields: Any,
    ) -> Any:
        """Create or update one provider-less (static/ssh) credential, keyed by ``(user, name)``.

        The provider-less counterpart to :meth:`upsert_for_user`: ``oauth_client`` is
        ``NULL`` (the kind/provider invariant forbids one), and ``name`` is the
        credential's identity. OAuth credentials are minted by the connect/login flow only.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or user._state.db, instance=user))

        operation_values, update_values = bound_manager._local_credential_values(
            kind=kind,
            name=name,
            material=material,
            fields=fields,
        )
        create_values = {
            "oauth_client": None,
            "external_account": None,
            **bound_manager._blank_create_values(),
            **operation_values,
            **update_values,
        }
        with (
            system_context(reason=bound_manager._REASON),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            instance, _created = bound_manager.update_or_create(
                user_id=user.pk,
                name=name,
                oauth_client=None,
                defaults={**operation_values, **update_values},
                create_defaults=create_values,
            )
        return instance

    def prepare_local_credential(
        self,
        user: Any,
        *,
        kind: str,
        name: str,
        material: dict[str, Any],
        **fields: Any,
    ) -> Any:
        """Return a validated, unsaved provider-less credential for external preflight."""

        operation_values, update_values = self._local_credential_values(
            kind=kind,
            name=name,
            material=material,
            fields=fields,
        )
        create_values = {
            "external_account": None,
            **self._blank_create_values(),
            **operation_values,
            **update_values,
        }
        return self.model(
            user=user,
            name=name,
            oauth_client=None,
            **create_values,
        )

    def _local_credential_values(
        self,
        *,
        kind: str,
        name: str,
        material: dict[str, Any],
        fields: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Validate one local credential and return its owned operation/update values."""

        handler = CredentialKind(kind).handler
        if handler.kind == CredentialKind.OAUTH:
            raise ValueError("OAuth credentials are minted by the connect/login flow, not create_local_credential().")
        if not name:
            raise ValidationError({"name": "This field is required."})
        return self._assemble_values(handler, material, fields)

    def _assemble_values(
        self,
        handler: Any,
        material: dict[str, Any],
        fields: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return ``(operation_values, update_values)`` for both create paths."""

        handler.validate(material)
        if owned := self.operation_fields & fields.keys():
            names = ", ".join(sorted(owned))
            raise ValueError(f"Credential field(s) are owned by the manager: {names}")
        update_values = handler.upsert_fields(material)
        update_values.update(_validated_manager_values(self.model, fields, allowed=self.caller_fields))
        update_values["status"] = CredentialStatus.ACTIVE
        material_value = self.model.encode_material(material)
        operation_values = {"kind": handler.kind, "material": material_value}
        return operation_values, update_values

    @staticmethod
    def _blank_create_values() -> dict[str, Any]:
        """Return the inactive-refresh defaults a fresh credential row starts from."""

        return {
            "status": CredentialStatus.ACTIVE,
            "expires_at": None,
            "granted_scopes": [],
            "last_refresh_at": None,
            "last_refresh_status": "",
        }


def _validated_manager_values(
    model: type[models.Model],
    values: dict[str, Any],
    *,
    allowed: frozenset[str],
) -> dict[str, Any]:
    """Return caller values validated against one manager-owned field set."""

    unknown = set(values) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown {model.__name__} field(s): {names}")
    result = dict(values)
    if "status" in result:
        status_field = model._meta.get_field("status")
        assert isinstance(status_field, models.Field)
        result["status"] = status_field.to_python(result["status"])
    return result


class VendorManager[VendorT: Vendor](AngeeHistoryManager[VendorT]):
    """Manager for the resource-seeded third-party catalogue."""

    def seeded(self, slug: str) -> Any:
        """Return the seeded vendor named by ``slug`` or raise with load guidance."""

        try:
            return self.get(slug=slug)
        except self.model.DoesNotExist as error:
            raise ImproperlyConfigured(
                f"Vendor {slug!r} is missing. Run `manage.py resources load` before "
                "connecting integrations that use it."
            ) from error


_IntegrationManagerBase = AngeeHistoryManager.from_queryset(IntegrationQuerySet)


class IntegrationManager[IntegrationT: Integration](_IntegrationManagerBase[IntegrationT]):
    """Own integration connection writes and concrete capability creation."""

    def attach_completed_connect(
        self,
        integration_sqid: str,
        user: Any,
        credential: Any,
        *,
        exposed_model_labels: set[str],
    ) -> None:
        """Attach completed OAuth state only to its one owned, exposed concrete capability."""

        bound_manager = self.db_manager(
            write_alias(self.model, using=self._db or credential._state.db, instance=credential)
        )

        if not integration_sqid:
            return
        with system_context(reason="integrate.integration.connect.complete"):
            integration = bound_manager.filter(sqid=integration_sqid).first()
        if integration is None or integration.owner_id != user.pk:
            raise OAuthFlowError(INVALID_STATE, 400)
        if credential.user_id != user.pk:
            raise PermissionDenied("Credential does not belong to the current user.")
        integrity, _authorized = integration.concrete_children(actor=user, exposed_model_labels=exposed_model_labels)
        if len(integrity) != 1 or integrity[0]._meta.label not in exposed_model_labels:
            raise OAuthFlowError(INVALID_STATE, 400)
        child = integrity[0]
        try:
            # Keep the caller's ambient native write-query scope; OAuth state
            # ownership must not replace the request actor's read admission.
            target = type(child).objects.db_manager(bound_manager.db).for_write().filter(pk=child.pk).first()
            if target is None or target.owner_id != user.pk:
                raise OAuthFlowError(INVALID_STATE, 400)
        except (PermissionDenied, ValueError) as error:
            raise OAuthFlowError(INVALID_STATE, 400) from error
        if credential.pk != target.credential_id or target.lifecycle == IntegrationLifecycle.DISCONNECTED:
            target.attach_credential(credential)

    def create_capability(self, **fields: Any) -> IntegrationT:
        """Prepare model-owned defaults and connection identity before native validation."""

        instance = self.model(**fields)
        actor, _sudo = self.effective_actor(strict=True)
        if actor is not None:
            instance.with_actor(actor)
        instance.prepare_impl_defaults()
        credential = instance.credential
        if "account" not in fields and "account_id" not in fields and credential is not None:
            instance.account = credential.external_account
        instance.full_clean()
        return self.get_queryset().insert(instance)


_WebhookSubscriptionManagerBase = AngeeHistoryManager.from_queryset(WebhookSubscriptionQuerySet)


class WebhookSubscriptionManager[WebhookSubscriptionT: WebhookSubscription](
    _WebhookSubscriptionManagerBase[WebhookSubscriptionT]
):
    """Manager for webhook subscriptions."""


_SyncStreamManagerBase = AngeeHistoryManager.from_queryset(SyncStreamQuerySet)


class SyncStreamManager[SyncStreamT: SyncStream](_SyncStreamManagerBase[SyncStreamT]):
    """Serialize stream discovery and epoch changes on the integration row."""

    def lock_current(self, stream: Any, *, fenced_to: Any = None) -> Any:
        """Fence one page against epoch changes inside the caller's transaction.

        The stream row lock is shared with epoch retirement. A stale extracted
        page must fail before any domain records or cursor state are applied.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or stream._state.db, instance=stream))

        if not connections[bound_manager.db].in_atomic_block:
            raise RuntimeError("Locking a stream requires the page transaction.")
        with system_context(reason="integrate.stream.lock_current"):
            locked = bound_manager.filter(pk=stream.pk).lock_if_supported().get()
            if bound_manager.filter(
                integration_id=locked.integration_id,
                key=locked.key,
                partition=locked.partition,
                generation__gt=locked.generation,
            ).exists():
                raise RuntimeError("The extracted page belongs to a retired stream generation.")
            if fenced_to is not None and locked.operation_fence != fenced_to.operation_fence:
                raise RuntimeError("Stream state changed during external work; retry the operation.")
            return locked

    def current(
        self,
        bridge: models.Model,
        definition: StreamDefinition,
    ) -> Any:
        """Return the latest epoch, creating its baseline once per partition.

        Cursor and policy arguments seed only the first epoch; subsequent calls
        preserve persisted progress and policy. Kind is immutable; direction or
        snapshot changes start a new baseline generation with the persisted policy.
        ``authoritative`` is the declaration's fact: a changed declaration flips the
        current generation through :meth:`set_authoritative`, so an adapter never
        flips it from inside a page.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or bridge._state.db, instance=bridge))

        if definition.absence_threshold < 1:
            raise ValidationError("A stream absence threshold must be positive.")
        integration_model = bound_manager.model.integration.field.related_model
        if bridge._meta.proxy or integration_model not in bridge._meta.get_parent_list():
            raise ValidationError("A stream bridge must be a concrete Integration child.")
        with (
            system_context(reason="integrate.stream.current"),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            cast(Any, bridge).lock_capability()
            identity = {
                "integration_id": bridge.pk,
                "key": definition.key,
                "partition": definition.partition,
            }
            stream = bound_manager.filter(**identity).order_by("-generation").first()
            if stream is None:
                return bound_manager.create(integration_id=bridge.pk, **definition.stream_fields())
            if stream.kind != definition.kind:
                raise ValidationError("A stream's kind cannot change between declarations.")
            if stream.direction != definition.direction or stream.snapshot != definition.snapshot:
                stream = bound_manager.bump_generation(
                    stream,
                    direction=definition.direction,
                    snapshot=definition.snapshot,
                )
            change_authority = stream.authoritative != definition.authoritative
        return bound_manager.set_authoritative(stream, definition.authoritative) if change_authority else stream

    def bump_generation(
        self,
        stream: Any,
        *,
        cursor: dict[str, Any] | None = None,
        direction: StreamDirection | None = None,
        snapshot: bool | None = None,
    ) -> Any:
        """Create one new baseline while retaining identities and history.

        Concurrent requests against a retired epoch return its successor. Links
        follow the current epoch without changing their verification marker.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or stream._state.db, instance=stream))

        with (
            system_context(reason="integrate.stream.bump_generation"),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            latest = bound_manager._lock_latest(stream)
            if latest.pk != stream.pk:
                return latest
            definition = replace(
                StreamDefinition.from_stream(latest),
                cursor={} if cursor is None else cursor,
                direction=latest.direction if direction is None else direction,
                snapshot=latest.snapshot if snapshot is None else snapshot,
            )
            successor = bound_manager.create(
                integration_id=latest.integration_id,
                **definition.stream_fields(),
                generation=latest.generation + 1,
                last_advanced_at=timezone.now() if cursor else None,
                inbound=deepcopy(latest.inbound),
            )
            latest.links.model.objects.db_manager(bound_manager.db).filter(stream=latest).update(stream=successor)
            latest.state = StreamState.CLOSED
            latest.inbound = []
            latest.save(using=latest._state.db, update_fields=["state", "inbound", "updated_at"])
            return successor

    def request_resync(self, stream: Any) -> Any:
        """Request a baseline on the next cycle, even from a retained old epoch.

        The driver owns the generation bump. Serialize this intent with that
        transition so a concurrent bump cannot lose the request on the old row.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or stream._state.db, instance=stream))

        with (
            system_context(reason="integrate.stream.request_resync"),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            latest = bound_manager._lock_latest(stream)
            if not latest.resync_required:
                latest.resync_required = True
                latest.save(using=latest._state.db, update_fields=["resync_required", "updated_at"])
            return latest

    def set_authoritative(self, stream: Any, authoritative: bool) -> Any:
        """Flip whether the stream's source is the system of record, then re-derive its subjects.

        The knob lives on the stream's current generation. Statements are retained,
        so the flip is a re-derivation: survivorship re-runs for every subject with
        a current statement linked through this stream, under the caller's changeset.
        The caller authorizes the flip on the stream's integration.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or stream._state.db, instance=stream))

        derivations = []
        with system_context(reason="integrate.stream.set_authoritative"):
            with database_context(bound_manager.db), transaction.atomic(using=bound_manager.db):
                latest = bound_manager._lock_latest(stream)
                if latest.authoritative == authoritative:
                    return latest
                for reference in references(latest.links.model):
                    if not issubclass(reference.model, Statement):
                        continue
                    linked = reference.model._base_manager.db_manager(bound_manager.db).filter(
                        **{f"{reference.name}__stream": latest}, superseded__isnull=True
                    )
                    subject_ids = linked.order_by("subject_id").values_list("subject_id", flat=True).distinct()
                    derivations.append((reference.model, reference.model.statement_subject_model, subject_ids))
                latest.authoritative = authoritative
                latest.save(using=latest._state.db, update_fields=["authoritative", "updated_at"])
            for statement_model, subject_model, subject_ids in derivations:
                for chunk in batched(subject_ids.iterator(chunk_size=500), 500):
                    for subject in (
                        subject_model._base_manager.db_manager(bound_manager.db).filter(pk__in=chunk).order_by("pk")
                    ):
                        statement_model.objects.db_manager(bound_manager.db).resolve(subject=subject)
            return latest

    def _lock_latest(self, stream: Any) -> Any:
        """Lock the integration and its latest epoch in generation-change order."""

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or stream._state.db, instance=stream))

        stream.integration.lock_capability()
        return (
            bound_manager.filter(
                integration_id=stream.integration_id,
                key=stream.key,
                partition=stream.partition,
            )
            .order_by("-generation")
            .lock_if_supported()
            .first()
        )

    def advance(
        self,
        stream: Any,
        cursor: Any,
        *,
        exhausted: bool = False,
        cursor_expires_at: datetime | None = None,
    ) -> Any:
        """Persist an opaque page cursor inside the caller's apply transaction."""

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or stream._state.db, instance=stream))

        with (
            system_context(reason="integrate.stream.advance"),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            locked = bound_manager.lock_current(stream)
            if locked.state == StreamState.CLOSED:
                raise ValidationError("A closed stream requires a new generation before advancement.")
            locked.cursor = cursor
            locked.cursor_expires_at = cursor_expires_at
            locked.last_advanced_at = timezone.now()
            if exhausted:
                locked.phase = StreamPhase.DELTA
                if locked.snapshot and locked.reconcile_interval is None:
                    locked.state = StreamState.CLOSED
            fields = ["cursor", "cursor_expires_at", "last_advanced_at", "phase", "state"]
            locked.save(using=locked._state.db, update_fields=[*fields, "updated_at"])
            for field in fields:
                setattr(stream, field, getattr(locked, field))
            return stream


_RecordLinkManagerBase = AngeeHistoryManager.from_queryset(RecordLinkQuerySet)


class RecordLinkManager[RecordLinkT: RecordLink](_RecordLinkManagerBase[RecordLinkT]):
    """Own replica identity, applied bases and count-based absence policy."""

    def observe(
        self,
        stream: Any,
        external_key: str,
        *,
        remote_version: str | None = None,
        source_payload: Any = UNSET,
        source_hash: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        parent: Any = UNSET,
        target: Any = UNSET,
    ) -> Any:
        """Retain identity without advancing applied bases.

        A child belongs to one root link in the same stream. Omitted bindings
        remain unchanged; ``target=None`` withdraws the projection explicitly.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or stream._state.db, instance=stream))

        with (
            system_context(reason="integrate.record.observe"),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            stream = type(stream).objects.db_manager(bound_manager.db).lock_current(stream)
            if stream.kind != StreamKind.RECORD_REPLICA:
                raise ValidationError("Event feeds do not create record links.")
            link, _ = bound_manager.lock_if_supported().get_or_create(stream=stream, external_key=external_key)
            if parent is not UNSET:
                if parent is not None:
                    parent = bound_manager.filter(pk=parent.pk).lock_if_supported().get()
                    if parent.stream_id != stream.pk or parent.parent_id is not None or parent.pk == link.pk:
                        raise ValidationError("A record parent must be another root link in the same stream.")
                    if bound_manager.filter(parent=link).exists():
                        raise ValidationError("A root with children cannot become a child link.")
                parent_id = None if parent is None else parent.pk
                if link.parent_id is not None and link.parent_id != parent_id:
                    raise ValidationError("A record link's aggregate parent is immutable.")
                link.parent_id = parent_id
            if (
                remote_version is not None
                and remote_version != link.remote_version
                and link.status != LinkStatus.FAILED
            ):
                link.status = LinkStatus.OBSERVED
            if remote_version is not None:
                link.remote_version = remote_version
            if source_payload is not UNSET:
                link.source_payload = source_payload
            if source_hash is not None:
                link.source_hash = source_hash
            # The application facts and protected base never change here.
            link.last_seen_at = timezone.now()
            link.last_verified_generation = stream.generation
            link.absence_count = 0
            if metadata is not None:
                link.metadata = dict(metadata)
            if target is not UNSET:
                bound_manager._set_target(link, target)
                if target is None:
                    link.status = LinkStatus.WITHDRAWN
            link.update_sync_status()
            link.save(
                using=link._state.db,
                update_fields=[
                    "status",
                    "source_payload",
                    "source_hash",
                    "remote_version",
                    "last_seen_at",
                    "last_verified_generation",
                    "absence_count",
                    "metadata",
                    "parent_id",
                    "target_content_type_id",
                    "target_object_id",
                    "updated_at",
                ],
            )
            return link

    def release_target(self, link: Any) -> Any:
        """Let the bound row go while its domain owner rebuilds it under the same identity.

        A retained reference blocks the row's deletion (declared edge teardown), so an
        adapter whose owner deletes and recreates rows releases the pointer first and
        binds the rebuilt row through ``observe``. Status, evidence and history stay:
        this is not a withdrawal, the identity is still current.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or link._state.db, instance=link))

        with (
            system_context(reason="integrate.record.release_target"),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            locked = bound_manager.filter(pk=link.pk).lock_if_supported().get()
            bound_manager._set_target(locked, None)
            locked.save(
                using=locked._state.db, update_fields=["target_content_type_id", "target_object_id", "updated_at"]
            )
            link.target_content_type_id = link.target_object_id = None
            return locked

    def promote(
        self,
        link: Any,
        *,
        source_payload: Any,
        source_hash: str,
        mapped_payload: Any,
        mapping_version: int = 1,
        dependency_digest: str = "",
        target: Any = UNSET,
        remote_version: str = "",
        origin: str = "remote",
        tombstone: bool = False,
    ) -> Any:
        """Append applied evidence and pin its base; the driver owns promotion.

        Adapters return their evidence to the driver, never promote its link
        themselves. Omit ``target`` to retain its binding; pass ``None`` to clear
        it and retain a WITHDRAWN identity.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or link._state.db, instance=link))

        with (
            system_context(reason="integrate.record.promote"),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            locked = bound_manager.filter(pk=link.pk).lock_if_supported().get()
            previous = locked.applied_base
            if mapped_payload is UNSET:
                mapped_payload = previous.mapped_payload if previous is not None else {}
            locked.source_payload = {} if source_payload is None else source_payload
            locked.source_hash, locked.mapped_payload = source_hash, mapped_payload
            locked.mapping_version, locked.dependency_digest = mapping_version, dependency_digest
            locked.applied_at = timezone.now()
            if target is not UNSET:
                bound_manager._set_target(locked, target)
            locked.remote_version, locked.origin, locked.status = remote_version, origin, LinkStatus.CURRENT
            if target is None:
                locked.status = LinkStatus.WITHDRAWN
            locked.tombstoned_at = timezone.now() if tombstone else None
            if tombstone:
                locked.status = LinkStatus.TOMBSTONE
            fields = [
                "source_payload",
                "source_hash",
                "mapped_payload",
                "mapping_version",
                "dependency_digest",
                "applied_at",
                "target_content_type_id",
                "target_object_id",
                "remote_version",
                "origin",
                "status",
                "tombstoned_at",
            ]
            locked.update_sync_status()
            application = None
            context = current_changeset()

            def pin_application(instance: Any, history_instance: Any, **kwargs: Any) -> None:
                nonlocal application
                if instance.pk == locked.pk and current_changeset() is context:
                    application = history_instance
                    locked.applied_base = history_instance
                    # This pointer completes the application save, not a new observation.
                    locked.save_without_historical_record(using=locked._state.db, update_fields=["applied_base"])

            sender = locked.history.model
            post_create_historical_record.connect(pin_application, sender=sender, weak=False)
            try:
                locked.save(using=locked._state.db, update_fields=[*fields, "updated_at"])
            finally:
                post_create_historical_record.disconnect(pin_application, sender=sender)
            if application is None:
                raise RuntimeError("Application save did not create its historical evidence.")
            bound_manager._set_target(link, locked.target if target is UNSET else target)
            for field in [*fields, "applied_base"]:
                setattr(link, field, getattr(locked, field))
            return application

    def _set_target(self, link: Any, target: models.Model | None) -> None:
        if target is not None and target.pk is None:
            raise ValidationError("A record target must be saved.")
        link.target = target
        if target is not None:
            # Provenance names any applied row, a plain domain sink included.
            link.target_content_type, object_id = generic_pointer_target(target)
            link.target_object_id = str(object_id)

    def bind_target(self, link: Any, target: models.Model) -> None:
        """Bind the source before assertion admission inside its application transaction.

        The driver's promotion records this binding in the application snapshot.
        Like applied_base, this completes that write without a pointer-only snapshot.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or link._state.db, instance=link))

        if not transaction.get_connection(bound_manager.db).in_atomic_block or not is_sudo():
            raise PermissionDenied("Target binding requires the trusted application transaction.")
        bound_manager._set_target(link, target)
        link.save_without_historical_record(
            using=bound_manager.db, update_fields=("target_content_type", "target_object_id")
        )

    def revalidate(self, link: Any, *, target: Any = UNSET) -> Any:
        """Restore a verified identity without advancing its application base.

        A link already current on the same target is left unsaved: the delivery's
        observation recorded it, and a save would repeat that snapshot in history.
        """
        fields = ("target_content_type_id", "target_object_id", "status", "tombstoned_at")
        locked = self.lock_if_supported().get(pk=link.pk)
        before = [getattr(locked, name) for name in fields]
        if target is not UNSET:
            self._set_target(locked, target)
        locked.status = LinkStatus.CURRENT
        locked.tombstoned_at = None
        locked.update_sync_status()
        if [getattr(locked, name) for name in fields] != before:
            locked.save(
                using=locked._state.db,
                update_fields=["target_content_type", "target_object_id", "status", "tombstoned_at", "updated_at"],
            )
        self._set_target(link, locked.target if target is UNSET else target)
        for name in fields:
            setattr(link, name, getattr(locked, name))
        return locked

    def mark_absent(self, stream: Any, keys: Iterable[str]) -> int:
        """Count a bounded batch of missing keys, retaining tombstones.

        Children may be included only after their parent has absence evidence;
        the sweep supplies bounded child batches after marking missing roots.
        """

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or stream._state.db, instance=stream))

        with (
            system_context(reason="integrate.record.mark_absent"),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            stream = type(stream).objects.db_manager(bound_manager.db).lock_current(stream)
            rows = (
                bound_manager.filter(stream=stream, external_key__in=tuple(keys))
                .absence_eligible()
                .order_by("pk")
                .lock_if_supported()
            )
            count = 0
            for link in rows:
                link.absence_count += 1
                if link.status != LinkStatus.FAILED:
                    link.status = LinkStatus.UNAVAILABLE
                    if link.absence_count >= stream.absence_threshold:
                        link.status, link.tombstoned_at = LinkStatus.TOMBSTONE, timezone.now()
                link.save(
                    using=link._state.db, update_fields=["absence_count", "status", "tombstoned_at", "updated_at"]
                )
                count += 1
            return count

    def tombstone(self, link: Any) -> Any:
        """Retain a confirmed remote deletion; never delete the identity."""

        bound_manager = self.db_manager(write_alias(self.model, using=self._db or link._state.db, instance=link))

        with (
            system_context(reason="integrate.record.tombstone"),
            database_context(bound_manager.db),
            transaction.atomic(using=bound_manager.db),
        ):
            locked = bound_manager.filter(pk=link.pk).lock_if_supported().get()
            if locked.status != LinkStatus.TOMBSTONE:
                locked.status, locked.tombstoned_at = LinkStatus.TOMBSTONE, timezone.now()
                locked.save(using=locked._state.db, update_fields=["status", "tombstoned_at", "updated_at"])
            link.status, link.tombstoned_at = locked.status, locked.tombstoned_at
            return link
