"""Django identity for the optional provenance test composition."""

from django.apps import AppConfig


class ProvenanceTestingConfig(AppConfig):
    """Load concrete attribution after its source addon."""

    name = "angee.provenance.testing"
    label = "provenance_testing"
