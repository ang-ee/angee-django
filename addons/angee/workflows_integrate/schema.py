"""Workflow-owned public run identity on the integration console surface."""

from typing import cast

import strawberry_django
from django.apps import apps

from angee.graphql.ids import PublicID, to_public_id
from angee.integrate.models import Bridge
from angee.integrate.models import Integration as IntegrationModel

Integration = apps.get_model("integrate", "Integration")
WorkflowRun = apps.get_model("workflows", "WorkflowRun")


@strawberry_django.type(Integration, name="IntegrationType", extend=True)
class IntegrationSyncRunExtension:
    """Keep workflow identity conversion outside the integration owner."""

    @strawberry_django.field(only=["id"])
    def sync_run(self) -> PublicID | None:
        """Expose the dispatched run through the readable concrete bridge."""

        bridge = cast(IntegrationModel, self).concrete_capability()
        return to_public_id(WorkflowRun, bridge.sync_run_id) if isinstance(bridge, Bridge) else None


schemas = {"console": {"type_extensions": [IntegrationSyncRunExtension]}}
