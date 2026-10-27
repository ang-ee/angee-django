"""Native lifecycle for the provenance capability."""

from django.apps import AppConfig
from django.core import checks


class ProvenanceConfig(AppConfig):
    name = "angee.provenance"
    label = "provenance"

    def ready(self) -> None:
        from angee.provenance.checks import check_changeset_middleware
        from angee.provenance.graphql import setup_graphql
        from angee.provenance.signals import capture_merge
        from angee.signals import pre_merge

        checks.register(check_changeset_middleware, checks.Tags.security)
        setup_graphql()
        pre_merge.connect(capture_merge, dispatch_uid="provenance.capture_merge")
