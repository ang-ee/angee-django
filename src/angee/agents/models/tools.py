"""Agent tools owners and their native model contracts."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from angee.agents.constants import BUILTIN_MCP_ANGEE as BUILTIN_MCP_ANGEE
from angee.agents.managers import MCPServerManager, MCPToolManager
from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.roles import role_anchor
from angee.resources.mixins import ResourceLoadMixin

if TYPE_CHECKING:
    from angee.integrate.models import Credential

from angee.agents.models.choices import MCPPlacement, MCPTransport

if TYPE_CHECKING:
    from angee.agents.models.agents import Agent


ToolRole = role_anchor("agents/toolrole", name="ToolRole")


class MCPServer(ResourceLoadMixin, AuditMixin, ProtectedDataModel, concrete=True):
    """An MCP server an agent can reach — internal to the platform or external.

    An external server authenticates with an ``integrate.Credential``; the operator renders
    the selected servers and their authorized tools into an agent's MCP config.
    """

    catalogue = True
    catalogue_tier = "demo"

    sqid_prefix = "mcp_"
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    placement = StateField(choices_enum=MCPPlacement, default=MCPPlacement.EXTERNAL)
    transport = StateField(choices_enum=MCPTransport, default=MCPTransport.HTTP)
    url = models.URLField(blank=True)
    # Expected to be a non-rotating credential (e.g. a static token). For an internal
    # server this credential's secret is the HMAC key from which each agent's per-agent
    # bearer is derived (see ``bearer_for``); the raw secret never leaves the platform.
    # Rotating the credential re-keys the derivation, so every provisioned agent's bearer
    # stops verifying and its MCP calls 401 until reprovisioned. Constrain to static
    # credentials at the catalogue level.
    credential = models.ForeignKey(
        "integrate.Credential",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mcp_servers",
    )
    config = models.JSONField(default=dict, blank=True)

    objects = MCPServerManager()

    class Meta:
        """Django model options for MCP servers."""

        abstract = True
        ordering = ("name",)
        rebac_resource_type = "agents/mcp_server"

    def __str__(self) -> str:
        """Return the server's name."""

        return self.name

    @property
    def builtin(self) -> str:
        """Return the built-in MCP server key this row targets, if any."""

        config = self.config if isinstance(self.config, Mapping) else {}
        return str(config.get("builtin") or "").strip()

    @property
    def resolved_url(self) -> str:
        """Return the container-reachable URL this MCP server renders for agents.

        Explicit ``url`` wins for external/custom servers. The built-in Angee MCP
        server is modelled as ``config = {"builtin": "angee"}`` with no row-owned
        URL; the stack supplies the concrete container-reachable URL through
        ``ANGEE_BUILTIN_MCP_URL`` so demo/catalogue data never bakes in a dev port.
        """

        if self.url:
            return str(self.url)
        if self.builtin == BUILTIN_MCP_ANGEE:
            return str(getattr(settings, "ANGEE_BUILTIN_MCP_URL", "") or "").strip()
        return ""

    @property
    def is_addressable(self) -> bool:
        """Whether a rendered container can reach this server — i.e. it has a URL.

        A stdio server is a local command with no URL and isn't rendered into an
        agent's ``.mcp.json``. A built-in Angee server is addressable when the stack
        supplied ``ANGEE_BUILTIN_MCP_URL``.
        """

        return bool(self.resolved_url)

    def config_entry(self, bearer_env: str | None) -> dict[str, Any]:
        """Return this server's ``.mcp.json`` entry, given an optional bearer env var.

        A credentialed server carries an ``Authorization: Bearer`` header whose value is
        the agent runtime's ``${<bearer_env>}`` expansion — the bearer rides the *container
        env* (set from the operator secret in the service env, like the inference token),
        never the file or browser. The operator only resolves ``${secret.<name>}`` in a
        service's env, not in file content, so a bearer placed literally in ``.mcp.json``
        would never resolve. Pass ``None`` for an uncredentialed server.
        """

        entry: dict[str, Any] = {"type": str(self.transport), "url": self.resolved_url}
        if bearer_env is not None:
            entry["headers"] = {"Authorization": f"Bearer ${{{bearer_env}}}"}
        return entry

    def require_credential(self) -> Credential:
        """Return the configured credential for operations that require authentication."""

        credential = self.credential
        if credential is None:
            raise ValidationError({"credential": "This MCP server has no authentication credential."})
        return credential

    def bearer_for(self, agent: Agent) -> str:
        """Return the bearer ``agent`` presents to this MCP server.

        For an ``INTERNAL`` (platform-verified) server this is a *per-agent* bearer
        ``"<agent sqid>.<hmac>"`` derived from the server credential (see
        :meth:`_bearer_digest`): each agent gets a distinct value, an agent holding only
        its own bearer cannot forge a peer's (it never sees the server secret), and the
        derivation is deterministic, so a reprovision re-mints the same value while the
        credential is unchanged. For any other placement the server verifies the raw token
        itself, so it receives the credential's ``secret_value()`` unchanged. Callers
        refresh the credential (``ensure_fresh()``) first, as :meth:`Agent.mcp_secrets` does.
        """

        if self.placement == MCPPlacement.INTERNAL:
            return f"{agent.sqid}.{self._bearer_digest(agent)}"
        return str(self.require_credential().secret_value())

    def accepts_bearer_digest(self, agent: Agent, digest: str) -> bool:
        """Whether ``digest`` is the per-agent bearer digest this server mints for ``agent``.

        Only meaningful for an ``INTERNAL`` server (an external one verifies its own raw
        token, not a derived digest), so any other placement returns ``False``. Compared in
        constant time against the freshly recomputed digest so a mismatch leaks no timing.
        """

        if self.placement != MCPPlacement.INTERNAL:
            return False
        return hmac.compare_digest(self._bearer_digest(agent), digest)

    def _bearer_digest(self, agent: Agent) -> str:
        """Return the HMAC digest binding ``agent`` to this server's credential.

        The single home of the derivation, so minting (:meth:`bearer_for`) and verifying
        (:meth:`accepts_bearer_digest`) cannot drift: the credential secret keys an
        HMAC-SHA256 over the agent's stable public sqid.
        """

        secret = str(self.require_credential().secret_value())
        return hmac.new(secret.encode(), agent.sqid.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def parse_bearer(bearer: str) -> tuple[str, str] | None:
        """Split a platform bearer into its ``(agent sqid, digest)`` parts, or ``None``.

        The internal-server bearer minted by :meth:`bearer_for` is
        ``"<agent sqid>.<hmac>"``. Agent sqids are a prefix plus base-alphabet characters
        and never contain ``.``, so a single partition recovers the parts. The digest must
        be the 64-character lowercase hexadecimal output of HMAC-SHA256; rejecting every
        other shape here also keeps arbitrary Unicode away from ``hmac.compare_digest``.
        """

        sqid, dot, digest = bearer.partition(".")
        if (
            not dot
            or not sqid
            or len(digest) != hashlib.sha256().digest_size * 2
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return None
        return sqid, digest


class MCPTool(AuditMixin, ProtectedDataModel, concrete=True):
    """One tool an MCP server exposes; agents select the tools they may call."""


    sqid_prefix = "mct_"
    objects = MCPToolManager()
    server = models.ForeignKey("agents.MCPServer", on_delete=models.CASCADE, related_name="tools")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    input_schema = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    requires_approval = models.BooleanField(default=False)
    """Whether invoking this tool must suspend for an owner decision."""

    class Meta:
        """Django model options for MCP tools."""

        abstract = True
        ordering = ("server", "name")
        rebac_resource_type = "agents/tool_grant"
        constraints = (models.UniqueConstraint(fields=("server", "name"), name="uniq_agents_mcp_tool_name"),)

    def __str__(self) -> str:
        """Return the tool's name."""

        return self.name
