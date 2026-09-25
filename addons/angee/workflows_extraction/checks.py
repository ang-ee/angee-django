"""Django system checks for document extraction configuration."""

from collections.abc import Sequence

from django.apps import AppConfig
from django.conf import settings
from django.core import checks


def check_extraction_settings(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Reject retired engine settings instead of silently ignoring them."""

    del app_configs, kwargs
    if hasattr(settings, "ANGEE_EXTRACTION_ENGINE_CLASSES"):
        return [
            checks.Error(
                "ANGEE_EXTRACTION_ENGINE_CLASSES has been removed.",
                hint="Remove it and register extraction profiles in ANGEE_EXTRACTION_PROFILE_CLASSES.",
                id="angee.workflows_extraction.E001",
            )
        ]
    return []
