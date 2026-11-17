"""Native catalogue preparation before agent resource selections resolve, and generated MCP keys."""

import secrets
from collections.abc import Mapping, Sequence
from typing import Any

import tablib
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models.fields import NOT_PROVIDED
from rebac import system_context

from angee.integrate.credentials import CredentialKind
from angee.resources.exceptions import ResourceLoadError
from angee.resources.kinds import ResourceEntryKind
from angee.resources.loader import AngeeResource
from angee.resources.widgets import resolve_ledger_xref

MCP_SERVER_KEY_KIND = "agents.mcpserverkey"
"""Resource kind that gives a loaded internal MCP server a generated key."""


class MCPServerKeyResourceKind(ResourceEntryKind):
    """Generate an internal MCP server's HMAC key at load, so no key is ever committed.

    Each declaration names a loaded server (``server``: its name) and the user who owns
    its key credential (``user``: a resource handle). A server without a credential gets
    a fresh static-token key from :func:`secrets.token_urlsafe` (43 characters, above
    ``MCPServer.internal_key_min_length``) that nothing else references. A server that
    already holds a credential keeps it, so a reload never re-keys provisioned agents.
    """

    key = MCP_SERVER_KEY_KIND

    @classmethod
    def read(cls, entry: Any) -> tuple[Mapping[str, str], ...]:
        """Parse ``server`` and ``user`` from each declaration without writing."""

        declarations: list[Mapping[str, str]] = []
        for index, row in enumerate(entry.read_declarations(), start=1):
            server, user = row.get("server"), row.get("user")
            if not (isinstance(server, str) and server and isinstance(user, str) and user):
                raise ResourceLoadError(f"{entry.display} row {index}: declare the server name and its key owner.")
            declarations.append({"server": server, "user": user})
        return tuple(declarations)

    @classmethod
    def after_load(cls, declarations: Sequence[Any], *, loaded_groups: Any, dry_run: bool) -> None:
        """Attach a generated key to each declared server that has no credential."""

        del loaded_groups
        if dry_run:
            return
        server_model = apps.get_model("agents", "MCPServer")
        credential_model = apps.get_model("integrate", "Credential")
        with system_context(reason="agents.resources.mcp_server_key"):
            for declaration in declarations:
                server = server_model._base_manager.filter(name=declaration["server"]).first()
                if server is None:
                    raise ResourceLoadError(f"MCP server {declaration['server']!r} was not loaded.")
                if server.credential_id is not None:
                    continue
                owner = resolve_ledger_xref(declaration["user"], model=get_user_model())
                if owner is None:
                    raise ResourceLoadError(f"MCP key owner {declaration['user']!r} does not resolve.")
                server.credential = credential_model.objects.create_local_credential(
                    owner,
                    kind=CredentialKind.STATIC_TOKEN,
                    name=f"{server.name} MCP key",
                    material={"api_key": secrets.token_urlsafe(32)},
                )
                server.full_clean()
                server.save(update_fields=["credential", "updated_at"])


class AgentResource(AngeeResource):
    """Compose the catalogue and resource ledger before assigning named tools."""

    def before_import(self, dataset: tablib.Dataset, **kwargs: Any) -> None:
        selections = {"mcp_tools", "mcp_servers"}.intersection(dataset.headers or ())
        # Structured row omissions retain the sentinel until before_import_row.
        if any(value is not NOT_PROVIDED and value not in (None, "", [], ())
               for name in selections for value in dataset[name]):
            apps.get_model("agents", "MCPTool").objects.sync_builtin()
        super().before_import(dataset, **kwargs)
