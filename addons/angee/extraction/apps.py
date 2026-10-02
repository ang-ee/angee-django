"""Django identity of the extraction domain."""

from django.apps import AppConfig


class ExtractionConfig(AppConfig):
    """Register immutable extraction evidence models."""

    name = "angee.extraction"
