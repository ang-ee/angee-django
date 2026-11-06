"""Marketplace models — VCS provenance on ``platform.Addon`` + the addon source kind."""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django_compose.models import Composable

from angee.iam.models.base import ProtectedModel
from angee.integrate_vcs.registry import VcsOutput
from angee.platform_integrate_vcs.managers import AddonCatalogManager


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




class AddonCatalog(VcsOutput, ProtectedModel, concrete=True):
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
