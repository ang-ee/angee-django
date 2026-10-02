"""Generic workflow bridge shared by admission and stream recovery proofs."""

from typing import Any

from angee.integrate.models import Bridge
from angee.integrate.testing.integration import Integration
from angee.workflows_integrate.sync import SyncCycleBridge


class SyncCycleTestBridge(SyncCycleBridge, Bridge, Integration):
    """Snapshot plain configured input with no additional scope to lock."""

    sync_workflow_key = "test_sync_cycle"

    class Meta(Bridge.Meta):
        abstract = False
        app_label = "integrate"
        db_table = "test_workflows_integrate_bridge"
        rebac_resource_type = "integrate/integration"

    def sync_workflow_input(self) -> Any:
        return self.config.get("input", {})

    def lock_sync_scope(self) -> None:
        """This fixture has no upstream scope records."""
