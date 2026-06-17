"""Integration implementation descriptors.

An ``Integration`` row stores the registry key for the implementation that owns
its behavior. Concrete addons contribute subclasses through
``ANGEE_INTEGRATION_IMPLS``; this base keeps only the shared catalogue/connect
metadata and the optional one-to-one companion binding.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist


class IntegrationImpl:
    """Base descriptor for one row-selected integration implementation."""

    category = "none"
    companion_model: str | None = None
    companion_create_fields: tuple[str, ...] = ()
    label = "Integration"
    icon = ""
    oauth_client = ""

    def __init__(self, integration: Any, companion: Any | None = None) -> None:
        """Bind this implementation to its owning integration and companion row."""

        self.integration = integration
        self.companion = companion

    @classmethod
    def companion_model_class(cls) -> type | None:
        """Return this implementation's companion model class, when declared."""

        if not cls.companion_model:
            return None
        app_label, model_name = cls.companion_model.split(".", 1)
        return apps.get_model(app_label, model_name)

    @classmethod
    def companion_for(cls, integration: Any) -> Any | None:
        """Return this implementation's declared companion row when it exists."""

        model = cls.companion_model_class()
        if model is None:
            return None
        related_name = f"{model._meta.app_label}_{model._meta.model_name}"
        try:
            return getattr(integration, related_name)
        except ObjectDoesNotExist:
            return None

    @classmethod
    def create_companion(cls, integration: Any, values: dict[str, Any]) -> Any | None:
        """Create this implementation's companion row from declared create values."""

        model = cls.companion_model_class()
        if model is None:
            return None
        attrs = cls.companion_create_values(integration, values)
        return model.objects.create(integration=integration, **attrs)

    @classmethod
    def companion_create_values(cls, integration: Any, values: dict[str, Any]) -> dict[str, Any]:
        """Return the subset of create values owned by this companion model."""

        del integration
        return {field: values[field] for field in cls.companion_create_fields if field in values}


class NullIntegrationImpl(IntegrationImpl):
    """Neutral implementation for draft rows with no capability companion."""
