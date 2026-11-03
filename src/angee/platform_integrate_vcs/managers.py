"""Marketplace models — VCS provenance on ``platform.Addon`` + the addon source kind."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from rebac import database_context, system_context

from angee.iam.managers import ProtectedManager
from angee.integrate_vcs.registry import VcsOutputManager
from angee.platform.discovery import (
    available_addons,
    installed_entry_points,
    package_identity_index,
    requirement_addons,
)
from angee.platform_integrate_vcs.catalog import source_descriptors

if TYPE_CHECKING:
    from angee.platform.models import Addon

_AddonT = TypeVar("_AddonT", bound="Addon")


class AddonCatalogManager(ProtectedManager[_AddonT], VcsOutputManager[_AddonT]):
    """Owns the reconcile of marketplace rows from an addon ``Source``."""

    @staticmethod
    def _check_source_owner(row: Any, source_id: int) -> None:
        """Refuse another live source's claim, including after native row-lock acquisition."""

        if row.vcs_source_id not in (None, source_id) and row.state != row.AddonState.REMOVED:
            raise ImproperlyConfigured(
                f"Catalogue addon {row.name!r} is already owned by source {row.vcs_source_id} at {row.vcs_path!r}"
            )

    def sync_from_source(self, source: Any) -> int:
        """Prepare source distribution metadata and reconcile ``platform.Addon`` rows.

        Mirrors ``TemplateManager.sync_from_source`` but writes into ``platform.Addon``
        (the one marketplace registry), tier-scoped so it never fights platform's own
        reconcile: an addon already materialised (in ``available_addons``) gets only
        its provenance set — platform owns that row's ``source``/``state``. One not
        materialised becomes a ``REMOTE``/``DISABLED`` row. ``REMOTE`` rows from this
        source no longer discovered are marked ``REMOVED``. Runs under ``system_context``.
        """

        addon = apps.get_model("platform", "Addon")
        using = self._db or source._state.db or self.db
        rows = addon.objects.db_manager(using)
        with database_context(using), system_context(reason="platform_integrate_vcs.catalog.sync"):
            repository_field = source._meta.get_field("repository")
            repository = (
                repository_field.related_model._base_manager.db_manager(using)
                .select_related("vcs_bridge__credential__oauth_client")
                .get(pk=source.repository_id)
            )
            source.repository = repository
            coordinates = (source.repository_id, source.kind, source.materialize_spec(), source.last_synced_at)
            descriptors = source_descriptors(source)
            claims = {descriptor["name"]: descriptor["path"] for descriptor in descriptors}
            available = available_addons()
            seen: set[str] = set()
            with transaction.atomic(using=using):
                current_source = (
                    type(source)
                    ._base_manager.db_manager(using)
                    .select_related("repository")
                    .select_for_update()
                    .get(pk=source.pk)
                )
                if (
                    current_source.repository_id,
                    current_source.kind,
                    current_source.materialize_spec(),
                    current_source.last_synced_at,
                ) != coordinates:
                    raise ImproperlyConfigured(
                        "Source changed during catalogue preparation; refresh its current coordinates"
                    )
                known = list(rows.select_for_update().order_by("name"))
                identities = package_identity_index(
                    (
                        *(
                            (entry.dist.metadata["Name"], name)
                            for name, entry in installed_entry_points().items()
                            if entry.dist is not None
                        ),
                        *((row.distribution, row.name) for row in known if row.distribution),
                        *((descriptor["distribution"], descriptor["name"]) for descriptor in descriptors),
                    )
                )
                for row in known:
                    if row.name in claims:
                        self._check_source_owner(row, source.pk)
                for descriptor in descriptors:
                    name = str(descriptor.get("name", ""))
                    if not name:
                        continue
                    seen.add(name)
                    provenance = {"vcs_source_id": source.pk, "vcs_path": str(descriptor.get("path", ""))}
                    if name in available:
                        rows.filter(name=name).update(**provenance)
                        continue
                    defaults = {
                        "label": "",
                        "distribution": descriptor["distribution"],
                        "requirements": list(descriptor["requirements"]),
                        "namespace": str(descriptor.get("namespace", "")),
                        # The board groups by ``category`` and renders ``description``/
                        # ``keywords`` — a discovered marketplace row carries the same
                        # native package metadata, never a blank-category lane.
                        "description": str(descriptor.get("description", "")),
                        "keywords": list(descriptor.get("keywords", [])),
                        "category": str(descriptor.get("category", "")),
                        "kind": addon.AddonKind.REQUIRED,
                        "source": addon.AddonSource.REMOTE,
                        "state": addon.AddonState.DISABLED,
                        "depends_on": list(requirement_addons(descriptor["requirements"], identities)),
                        **addon.reset_runtime_facts(),
                        **provenance,
                    }
                    row, created = rows.select_for_update().get_or_create(name=name, defaults=defaults)
                    if not created:
                        self._check_source_owner(row, source.pk)
                        rows.update_or_create(name=name, defaults=defaults)

                (
                    rows.filter(vcs_source=source, source=addon.AddonSource.REMOTE)
                    .exclude(name__in=seen)
                    .update(state=addon.AddonState.REMOVED, **addon.reset_runtime_facts())
                )
                rows.reconcile_dependencies()
                current_source.complete_sync()
                source.last_synced_at = current_source.last_synced_at
            return len(descriptors)
