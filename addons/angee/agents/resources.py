"""Native catalogue preparation before agent resource selections resolve."""

from typing import Any

import tablib
from django.db.models.fields import NOT_PROVIDED

from angee.resources.loader import AngeeResource


class AgentResource(AngeeResource):
    """Compose the catalogue and resource ledger before assigning named tools."""

    def before_import(self, dataset: tablib.Dataset, **kwargs: Any) -> None:
        selections = {"mcp_tools", "mcp_servers"}.intersection(dataset.headers or ())
        # Structured row omissions retain the sentinel until before_import_row.
        if any(value is not NOT_PROVIDED and value not in (None, "", [], ())
               for name in selections for value in dataset[name]):
            # Source models import this adapter before Django finishes population.
            from angee.agents.grants import sync_builtin_tool_catalogue

            sync_builtin_tool_catalogue()
        super().before_import(dataset, **kwargs)
