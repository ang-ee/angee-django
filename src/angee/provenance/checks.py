"""Django checks for provenance attribution boundaries."""

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig, apps
from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from simple_history.models import registered_models

from angee.provenance.context import changeset_model_label
from angee.provenance.engine.base import AbstractChangeset
from angee.provenance.engine.policy import get_policy


def check_configuration(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
    """Validate the one storage identity, policy, and strictness without database access."""

    del app_configs, kwargs
    errors: list[checks.CheckMessage] = []
    if not hasattr(settings, "ANGEE_CHANGESET_MODEL"):
        errors.append(
            checks.Error(
                "Declare ANGEE_CHANGESET_MODEL for Django's swappable migrations.",
                hint='Use ANGEE_CHANGESET_MODEL = "provenance.Changeset" for default storage.',
                id="provenance.E004",
            )
        )
    label = getattr(settings, "ANGEE_CHANGESET_MODEL", "provenance.Changeset")
    try:
        model = apps.get_model(label)
        if not issubclass(model, AbstractChangeset):
            raise ImproperlyConfigured("Changeset storage must subclass AbstractChangeset.")
    except (ValueError, LookupError, ImproperlyConfigured, TypeError) as error:
        errors.append(checks.Error(str(error), id="provenance.E001"))
    try:
        get_policy()
    except (ImportError, AttributeError, ImproperlyConfigured, TypeError) as error:
        errors.append(checks.Error(str(error), id="provenance.E002"))
    if not isinstance(getattr(settings, "PROVENANCE_STRICT", True), bool):
        errors.append(checks.Error("PROVENANCE_STRICT must be boolean.", id="provenance.E003"))
    if hasattr(settings, "PROVENANCE_CHANGESET_MODEL"):
        errors.append(
            checks.Error(
                "PROVENANCE_CHANGESET_MODEL is retired; use ANGEE_CHANGESET_MODEL only.",
                id="provenance.E005",
            )
        )
    return errors


def check_changeset_middleware(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Require one HTTP attribution boundary and REBAC actor resolution."""

    from rebac import app_settings

    del app_configs, kwargs
    if not app_settings.REBAC_STRICT_MODE or not changeset_model_label():
        return []
    if not registered_models:
        return []
    middleware = settings.MIDDLEWARE
    actor = "rebac.middleware.ActorMiddleware"
    boundary = "angee.provenance.middleware.ChangesetMiddleware"
    if middleware.count(boundary) == 1 and actor in middleware:
        return []
    return [
        checks.Error(
            "Strict changeset attribution requires one ChangesetMiddleware and REBAC's ActorMiddleware.",
            hint=f"Add one '{boundary}' and '{actor}' in MIDDLEWARE.",
            id="angee.E038",
        )
    ]
