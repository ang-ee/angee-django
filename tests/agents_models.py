"""Shared concrete agent and inference targets for source-addon tests."""

from angee.agents.models import Agent as AbstractAgent
from angee.agents.models import AgentSession as AbstractAgentSession
from angee.agents.models import AgentTurn as AbstractAgentTurn
from angee.agents.models import InferenceModel as AbstractInferenceModel
from angee.agents.models import InferenceProvider as AbstractInferenceProvider
from angee.agents.models import MCPServer as AbstractMCPServer
from angee.agents.models import MCPTool as AbstractMCPTool
from angee.agents.models import Skill as AbstractSkill
from angee.agents.models import ToolRole as AbstractToolRole
from tests.integrate_models import Integration


class InferenceProvider(AbstractInferenceProvider, Integration):
    """Concrete inference capability over the shared integration."""

    class Meta(AbstractInferenceProvider.Meta):
        abstract = False
        app_label = "agents"
        db_table = "test_agents_inference_provider"
        rebac_resource_type = "agents/inference_provider"


class InferenceModel(AbstractInferenceModel):
    """Concrete inference catalogue row shared by evidence and agent tests."""

    class Meta(AbstractInferenceModel.Meta):
        abstract = False
        app_label = "agents"
        db_table = "test_agents_inference_model"
        rebac_resource_type = "agents/inference_model"


class Skill(AbstractSkill):
    """Concrete skill used by the agents discovery tests."""

    class Meta(AbstractSkill.Meta):
        """Django model options for the canonical test skill."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_skill"
        rebac_resource_type = "agents/skill"


class MCPServer(AbstractMCPServer):
    """Concrete MCP server used by the agents console tests."""

    class Meta(AbstractMCPServer.Meta):
        """Django model options for the canonical test MCP server."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_mcp_server"
        rebac_resource_type = "agents/mcp_server"


class MCPTool(AbstractMCPTool):
    """Concrete MCP tool used by the agents console tests."""

    class Meta(AbstractMCPTool.Meta):
        """Django model options for the canonical test MCP tool."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_mcp_tool"
        rebac_resource_type = "agents/tool_grant"


class ToolRole(AbstractToolRole):
    """Concrete, table-less runtime anchor emitted by the composer in real projects."""

    class Meta(AbstractToolRole.Meta):
        abstract = False
        managed = False
        app_label = "agents"
        rebac_resource_type = "agents/toolrole"


class Agent(AbstractAgent):
    """Concrete agent used by the agents console tests."""

    class Meta(AbstractAgent.Meta):
        """Django model options for the canonical test agent."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_agent"
        rebac_resource_type = "agents/agent"


class AgentSession(AbstractAgentSession):
    """Concrete persisted agent session used by runtime tests."""

    class Meta(AbstractAgentSession.Meta):
        abstract = False
        app_label = "agents"
        db_table = "test_agents_session"
        rebac_resource_type = "agents/session"


class AgentTurn(AbstractAgentTurn):
    """Concrete persisted agent turn used by runtime tests."""

    class Meta(AbstractAgentTurn.Meta):
        abstract = False
        app_label = "agents"
        db_table = "test_agents_turn"
        rebac_resource_type = "agents/turn"
