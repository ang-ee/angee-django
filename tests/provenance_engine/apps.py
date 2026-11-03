"""Isolated native engine host with the provenance addon as the sole app label owner."""

from importlib import import_module

from django.apps import AppConfig
from django.core import checks


class ProvenanceEngineTestConfig(AppConfig):
    """Use the addon label with the original plain concrete storage fixture."""

    name = "angee.provenance"
    label = "provenance"
    default_auto_field = "django.db.models.BigAutoField"

    def import_models(self):
        """Load only the plain fixture model for this isolated test registry."""

        self.models = self.apps.all_models[self.label]
        self.models_module = import_module("tests.provenance_engine.storage.models")

    def ready(self):
        """Register the addon's one configuration sequence in the fixture host."""

        from angee.provenance.checks import check_configuration

        checks.register(check_configuration, checks.Tags.models)
