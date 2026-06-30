"""GraphQL: VCS provenance contributed onto platform's ``AddonNode``.

The composer has already folded the provenance columns into the runtime
``platform.Addon`` model (see ``models.CatalogProvenance``), so they read as native
fields on the existing marketplace node — the same way ``iam_integrate_oidc`` folds
OIDC fields onto ``OAuthClientType``. The ``source`` tier (installed/local/remote)
platform already exposes; this adds the bearing directory the row was discovered at.
"""

from __future__ import annotations

import strawberry_django
from django.apps import apps
from strawberry import auto

_Addon = apps.get_model("platform", "Addon")


@strawberry_django.type(_Addon, name="AddonNode", extend=True)
class AddonVcsProvenance:
    """Contributes the VCS bearing path onto platform's ``AddonNode``."""

    vcs_path: auto


schemas = {
    "console": {
        "type_extensions": [AddonVcsProvenance],
    },
}
"""GraphQL contributions installed by the VCS marketplace addon."""
