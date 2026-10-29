"""Marketplace models — VCS provenance on ``platform.Addon`` + the addon source kind."""

from __future__ import annotations

from typing import Any, ClassVar

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.utils import timezone
from django_compose.models import Composable
from rebac import database_context, system_context

from angee.base.managers import AngeeManager
from angee.base.models.base import AngeeModel
from angee.integrate_vcs.registry import VcsOutput, VcsOutputManager
from angee.platform.discovery import (
    available_addons,
    installed_entry_points,
    package_identity_index,
    requirement_addons,
)
from angee.platform_integrate_vcs.catalog import parse_addon_meta


class CatalogProvenance(Composable, extends="platform.Addon"):
    """VCS provenance folded onto ``platform.Addon`` (the integrate ``Source`` extension pattern).

    Records the integrate ``Source`` a row was discovered from and its bearing
    directory within the repo. The composer folds these columns into the one
    ``Addon`` table — an installed/local row gains them once a marketplace sync has
    seen it in a repo; a ``REMOTE`` row always carries them.
    """

    hasura_readable_fields: ClassVar[tuple[str, ...]] = ("vcs_path",)

    vcs_source = models.ForeignKey(
        "integrate_vcs.Source",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="catalog_addons",
    )
    vcs_path = models.CharField(max_length=1024, blank=True, default="")

    class Meta:
        """Abstract extension base composed into ``platform.Addon``."""

        abstract = True


class AddonCatalogManager(AngeeManager, VcsOutputManager):
    """Owns the reconcile of marketplace rows from an addon ``Source``."""

    @staticmethod
    def _check_source_owner(row: Any, source_id: int) -> None:
        """Refuse another live source's claim, including after native row-lock acquisition."""

        if row.vcs_source_id not in (None, source_id) and row.state != row.AddonState.REMOVED:
            raise ImproperlyConfigured(
                f"Catalogue addon {row.name!r} is already owned by source "
                f"{row.vcs_source_id} at {row.vcs_path!r}"
            )

    def sync_from_source(self, source: Any) -> int:
        """Discover ``pyproject.toml`` under ``source`` and reconcile ``platform.Addon`` rows.

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
            repository = repository_field.related_model._base_manager.db_manager(using).select_related(
                "vcs_bridge__credential__oauth_client"
            ).get(pk=source.repository_id)
            source.repository = repository
            vcs_bridge = repository.vcs_bridge
            documents = vcs_bridge.discover(source, marker="pyproject.toml", parse=lambda blob: {"blob": blob})
            descriptors = []
            claims: dict[str, str] = {}
            for document in documents:
                facts = parse_addon_meta(document["blob"], dependencies={})
                if not facts:
                    continue
                name = facts["name"]
                if name in claims:
                    raise ImproperlyConfigured(
                        f"Duplicate catalogue addon {name!r}: {claims[name]!r} and {document['path']!r}"
                    )
                claims[name] = document["path"]
                descriptors.append({**facts, "path": document["path"]})
            available = available_addons()
            seen: set[str] = set()
            with transaction.atomic(using=using):
                known = list(rows.select_for_update().order_by("name"))
                identities = package_identity_index((
                    *((entry.dist.metadata["Name"], name) for name, entry in installed_entry_points().items()
                      if entry.dist is not None),
                    *((row.distribution, row.name) for row in known if row.distribution),
                    *((descriptor["distribution"], descriptor["name"]) for descriptor in descriptors),
                ))
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
                        # manifest metadata the parser read, never a blank-category lane.
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
                source.last_synced_at = timezone.now()
                source.save(using=using, update_fields=["last_synced_at", "updated_at"])
            return len(descriptors)


class AddonCatalog(VcsOutput, AngeeModel, concrete=True):
    """The ``addon`` source kind — discovers marketplace rows into ``platform.Addon``.

    A table-less binding (``managed = False``): its ``source_kind`` registers the
    kind with integrate's ``Source`` dispatch (so ``source.refresh()`` and a bridge
    sync route here), and its manager owns the reconcile. The rows it produces live
    in ``platform.Addon`` — the marketplace is one registry, not a parallel table.
    """

    source_kind = "addon"
    """Binds the ``addon`` source kind to this model (see ``integrate_vcs.Source``)."""

    objects = AddonCatalogManager()

    class Meta:
        """Table-less dispatch binding for the addon source kind."""

        abstract = True
        managed = False
        rebac_resource_type = "platform_integrate_vcs/catalog"
