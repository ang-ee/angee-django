"""Django checks for provenance attribution boundaries."""

from collections.abc import Sequence

from django.apps import AppConfig
from django.conf import settings
from django.core import checks
from rebac import app_settings
from simple_history.models import registered_models

from angee.provenance.context import changeset_model_label


def check_changeset_middleware(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Require strict HTTP attribution to follow REBAC's actor resolution."""

    del app_configs, kwargs
    if not app_settings.REBAC_STRICT_MODE or not changeset_model_label():
        return []
    if not registered_models:
        return []
    middleware = settings.MIDDLEWARE
    actor = "rebac.middleware.ActorMiddleware"
    boundary = "angee.provenance.middleware.ChangesetMiddleware"
    if middleware.count(boundary) == 1 and actor in middleware and middleware.index(actor) < middleware.index(boundary):
        return []
    return [
        checks.Error(
            "Strict changeset attribution requires one ChangesetMiddleware after REBAC's ActorMiddleware.",
            hint=f"Add '{boundary}' after '{actor}' in MIDDLEWARE.",
            id="angee.E038",
        )
    ]
