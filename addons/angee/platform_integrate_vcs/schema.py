"""GraphQL: VCS provenance + addon-source controls for the marketplace tier.

Two contributions onto the platform console, both one-way (this tier reaches up into
``platform`` and ``integrate``; neither references back):

- ``AddonVcsProvenance`` folds the VCS bearing path onto platform's ``AddonNode`` —
  the same way ``iam_integrate_oidc`` folds OIDC fields onto ``OAuthClientType``.
- ``MarketplaceSourceMutation`` adds the *source* controls (admin-gated): ``addSource``
  inventories a repository on an existing ``VcsBridge`` and points an addon ``Source``
  at it; ``scan`` runs the existing ``Source.refresh()`` (→ ``AddonCatalog`` reconcile)
  that discovers ``addon.toml`` rows into ``platform.Addon``. Both compose integrate's
  existing owners (``VcsBridge.import_repository``, ``Source``); neither re-implements
  bridge/repo creation or the discovery walk.
"""

from __future__ import annotations

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.actions import ActionResult, action_target
from angee.graphql.ids import PublicID
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES

_Addon = apps.get_model("platform", "Addon")
_VcsBridge = apps.get_model("integrate", "VcsBridge")
_Source = apps.get_model("integrate", "Source")

# The owner of "what string is the addon source kind" is the model that binds it to
# integrate's ``Source`` dispatch — read it off ``AddonCatalog.source_kind`` rather than
# re-stating the literal here.
_ADDON_SOURCE_KIND = apps.get_model("platform_integrate_vcs", "AddonCatalog").source_kind


@strawberry_django.type(_Addon, name="AddonNode", extend=True)
class AddonVcsProvenance:
    """Contributes the VCS bearing path onto platform's ``AddonNode``."""

    vcs_path: auto


@strawberry.input
class AddonSourceInput:
    """Fields accepted when pointing a new addon ``Source`` at a repository.

    ``vcs_bridge_id`` names an existing bridge (a local checkout in dev, or a host
    bridge); ``name`` is the repository's host path (``owner/repo``). ``ref``/``path``
    scope the source within the repo (blank ``ref`` resolves to the default branch).
    """

    vcs_bridge_id: PublicID
    name: str = ""
    ref: str = ""
    path: str = ""


@strawberry.type
class MarketplaceSourceMutation:
    """Admin actions that grow and refresh the addon marketplace from VCS sources."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def add_source(self, data: AddonSourceInput) -> ActionResult:
        """Inventory a repository on the bridge and point an addon ``Source`` at it."""

        with action_target(_VcsBridge, data.vcs_bridge_id, reason="platform_integrate_vcs.graphql.add_source") as vcs:
            repository = vcs.import_repository(data.name)
            # Idempotent on (repository, kind, ref, path): re-adding the same source
            # points at the one existing row instead of accumulating duplicate,
            # independently scannable addon sources for the same repo/ref/path.
            _Source.objects.update_or_create(
                repository=repository,
                kind=_ADDON_SOURCE_KIND,
                ref=data.ref,
                path=data.path,
            )
        return ActionResult(ok=True, message=f"Added an addon source for {repository.name}.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def scan(self, source_id: PublicID) -> ActionResult:
        """Re-enumerate one addon ``Source`` into ``platform.Addon`` marketplace rows."""

        with action_target(_Source, source_id, reason="platform_integrate_vcs.graphql.scan") as source:
            if source.kind != _ADDON_SOURCE_KIND:
                return ActionResult(ok=False, message="That source is not an addon source.")
            count = source.refresh()
        return ActionResult(ok=True, message=f"Discovered {count} addon(s).")


schemas = {
    "console": {
        "mutation": [MarketplaceSourceMutation],
        "type_extensions": [AddonVcsProvenance],
    },
}
"""GraphQL contributions installed by the VCS marketplace addon."""
