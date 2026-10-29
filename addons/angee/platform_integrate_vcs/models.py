"""Marketplace models — VCS provenance on ``platform.Addon`` + the addon source kind."""

from __future__ import annotations

from typing import Any, ClassVar

from django.apps import apps
from django.db import models, transaction
from django.utils import timezone
from rebac import system_context

from angee.addons import addon_entry_points
from angee.base.managers import AngeeManager
from angee.base.models.base import AngeeModel
from angee.integrate_vcs.registry import VcsOutput, VcsOutputManager
from angee.platform.discovery import available_addons
from angee.platform_integrate_vcs.catalog import parse_addon_meta


class CatalogProvenance(models.Model):
    """VCS provenance folded onto ``platform.Addon`` (the integrate ``Source`` extension pattern).

    Records the integrate ``Source`` a row was discovered from and its bearing
    directory within the repo. The composer folds these columns into the one
    ``Addon`` table — an installed/local row gains them once a marketplace sync has
    seen it in a repo; a ``REMOTE`` row always carries them.
    """

    extends: ClassVar[str | None] = "platform.Addon"
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
        repository_field = source._meta.get_field("repository")
        repository = repository_field.related_model._base_manager.select_related(
            "vcs_bridge__credential__oauth_client"
        ).get(pk=source.repository_id)
        source.repository = repository
        vcs_bridge = repository.vcs_bridge
        documents = vcs_bridge.discover(source, marker="pyproject.toml", parse=lambda blob: {"blob": blob})
        dependencies = {
            entry.dist.metadata["Name"]: name for name, entry in addon_entry_points().items()
            if entry.dist is not None
        }
        for document in documents:
            facts = parse_addon_meta(document["blob"], dependencies={})
            if facts:
                dependencies.setdefault(facts["distribution"], facts["name"])
        descriptors = [
            {**facts, "path": document["path"]} for document in documents
            if (facts := parse_addon_meta(document["blob"], dependencies=dependencies))
        ]
        available = available_addons()
        seen: set[str] = set()
        with system_context(reason="platform_integrate_vcs.catalog.sync"), transaction.atomic():
            for descriptor in descriptors:
                name = str(descriptor.get("name", ""))
                if not name:
                    continue
                seen.add(name)
                provenance = {"vcs_source_id": source.pk, "vcs_path": str(descriptor.get("path", ""))}
                if name in available:
                    addon.objects.filter(name=name).update(**provenance)
                    continue
                addon.objects.update_or_create(
                    name=name,
                    defaults={
                        "label": "",
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
                        "depends_on": list(descriptor.get("depends_on", [])),
                        **addon.reset_runtime_facts(),
                        **provenance,
                    },
                )
            (
                addon.objects.filter(vcs_source=source, source=addon.AddonSource.REMOTE)
                .exclude(name__in=seen)
                .update(state=addon.AddonState.REMOVED, **addon.reset_runtime_facts())
            )
            addon.objects.reconcile_dependencies()
            source.last_synced_at = timezone.now()
            source.save(update_fields=["last_synced_at", "updated_at"])
        return len(descriptors)


class AddonCatalog(VcsOutput, AngeeModel):
    """The ``addon`` source kind — discovers marketplace rows into ``platform.Addon``.

    A table-less binding (``managed = False``): its ``source_kind`` registers the
    kind with integrate's ``Source`` dispatch (so ``source.refresh()`` and a bridge
    sync route here), and its manager owns the reconcile. The rows it produces live
    in ``platform.Addon`` — the marketplace is one registry, not a parallel table.
    """

    runtime = True
    source_kind = "addon"
    """Binds the ``addon`` source kind to this model (see ``integrate_vcs.Source``)."""

    objects = AddonCatalogManager()

    class Meta:
        """Table-less dispatch binding for the addon source kind."""

        abstract = True
        managed = False
        rebac_resource_type = "platform_integrate_vcs/catalog"
