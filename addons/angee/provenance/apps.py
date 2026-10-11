"""Django identity for the provenance addon."""

from django.apps import AppConfig


class ProvenanceConfig(AppConfig):
    """Compose the provenance source model through the ordinary addon lifecycle."""

    default = True
    name = "angee.provenance"
