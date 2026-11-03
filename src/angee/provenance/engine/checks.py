"""Validate public configuration without database access."""

from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register
from django.core.exceptions import ImproperlyConfigured

from .policy import get_policy


@register(Tags.models)
def check_configuration(app_configs: Any = None, **kwargs: Any) -> list[CheckMessage]:
    errors: list[CheckMessage] = []
    if not hasattr(settings, "PROVENANCE_CHANGESET_MODEL"):
        errors.append(
            Error(
                "Declare PROVENANCE_CHANGESET_MODEL for Django's swappable migrations.",
                hint='Use PROVENANCE_CHANGESET_MODEL = "provenance.Changeset" for default storage.',
                id="provenance.E004",
            )
        )
    label = getattr(settings, "PROVENANCE_CHANGESET_MODEL", "provenance.Changeset")
    try:
        model = apps.get_model(label)
        from .base import AbstractChangeset

        if not issubclass(model, AbstractChangeset):
            raise ImproperlyConfigured("Changeset storage must subclass AbstractChangeset.")
    except (ValueError, LookupError, ImproperlyConfigured, TypeError) as error:
        errors.append(Error(str(error), id="provenance.E001"))
    try:
        get_policy()
    except (ImportError, AttributeError, ImproperlyConfigured, TypeError) as error:
        errors.append(Error(str(error), id="provenance.E002"))
    if not isinstance(getattr(settings, "PROVENANCE_STRICT", True), bool):
        errors.append(Error("PROVENANCE_STRICT must be boolean.", id="provenance.E003"))
    return errors
