"""Marketplace models — VCS provenance on ``platform.Addon`` + the addon source kind."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from rebac import system_context

from angee.addons import available_addons
from angee.base.models import AngeeManager, AngeeModel
from angee.platform_integrate_vcs.catalog import parse_addon_meta


class CatalogProvenance(AngeeModel):
    """VCS provenance folded onto ``platform.Addon`` (the integrate ``Source`` extension pattern).

    Records the integrate ``Source`` a row was discovered from and its bearing
    directory within the repo. The composer folds these columns into the one
    ``Addon`` table — an installed/local row gains them once a marketplace sync has
    seen it in a repo; a ``REMOTE`` row always carries them.
    """

    extends = "platform.Addon"
    hasura_readable_fields = ("vcs_path",)

    vcs_source = models.ForeignKey(
        "integrate.Source",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="catalog_addons",
    )
    vcs_path = models.CharField(max_length=1024, blank=True, default="")

    class Meta:
        """Abstract extension base composed into ``platform.Addon``."""

        abstract = True


class AddonCatalogManager(AngeeManager):
    """Owns the reconcile of marketplace rows from an addon ``Source``."""

    def sync_from_source(self, source: Any) -> int:
        """Discover ``addon.toml`` under ``source`` and reconcile ``platform.Addon`` rows.

        Mirrors ``TemplateManager.sync_from_source`` but writes into ``platform.Addon``
        (the one marketplace registry), tier-scoped so it never fights platform's own
        reconcile: an addon already materialised (in ``available_addons``) gets only
        its provenance set — platform owns that row's ``source``/``state``. One not
        materialised becomes a ``REMOTE``/``DISABLED`` row. ``REMOTE`` rows from this
        source no longer discovered are marked ``REMOVED``. Runs under ``system_context``.
        """

        addon = apps.get_model("platform", "Addon")
        vcs_bridge = source.repository.vcs_bridge
        descriptors = vcs_bridge.discover(source, marker="addon.toml", parse=parse_addon_meta)
        available = available_addons(getattr(settings, "ANGEE_ADDON_DIRS", ()))
        seen: set[str] = set()
        with system_context(reason="platform_integrate_vcs.catalog.sync"), transaction.atomic():
            for descriptor in descriptors:
                name = str(descriptor.get("name", ""))
                if not name:
                    continue
                seen.add(name)
                provenance = {"vcs_source": source, "vcs_path": str(descriptor.get("path", ""))}
                if name in available:
                    addon.objects.filter(name=name).update(**provenance)
                    continue
                addon.objects.update_or_create(
                    name=name,
                    defaults={
                        "label": str(descriptor.get("label", "")),
                        "namespace": str(descriptor.get("namespace", "")),
                        # The board groups by ``category`` and renders ``description``/
                        # ``keywords`` — a discovered marketplace row carries the same
                        # manifest metadata the parser read, never a blank-category lane.
                        "description": str(descriptor.get("description", "")),
                        "keywords": list(descriptor.get("keywords", [])),
                        "category": str(descriptor.get("category", "")),
                        "kind": addon.Kind.REQUIRED,
                        "source": addon.Source.REMOTE,
                        "state": addon.State.DISABLED,
                        "depends_on": list(descriptor.get("depends_on", [])),
                        **provenance,
                    },
                )
            (
                addon.objects.filter(vcs_source=source, source=addon.Source.REMOTE)
                .exclude(name__in=seen)
                .update(state=addon.State.REMOVED)
            )
            source.last_synced_at = timezone.now()
            source.save(update_fields=["last_synced_at", "updated_at"])
        return len(descriptors)


class AddonCatalog(AngeeModel):
    """The ``addon`` source kind — discovers marketplace rows into ``platform.Addon``.

    A table-less binding (``managed = False``): its ``source_kind`` registers the
    kind with integrate's ``Source`` dispatch (so ``source.refresh()`` and a bridge
    sync route here), and its manager owns the reconcile. The rows it produces live
    in ``platform.Addon`` — the marketplace is one registry, not a parallel table.
    """

    runtime = True
    source_kind = "addon"
    """Binds the ``addon`` source kind to this model (see ``integrate.Source``)."""

    objects = AddonCatalogManager()

    class Meta:
        """Table-less dispatch binding for the addon source kind."""

        abstract = True
        managed = False
        rebac_resource_type = "platform_integrate_vcs/catalog"
