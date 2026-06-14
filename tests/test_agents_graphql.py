"""Tests for the agents console GraphQL surface.

The agents console references iam + integrate types, so these build one ``console``
schema folding the iam, integrate, and agents addon parts (the shape the composer
assembles) and run over the concrete test tables. `agents.schema` resolves all six
agents models by app-registry lookup at import, so the concretes are declared (or
imported) *before* that module is imported: `Skill`/`InferenceProvider`/
`InferenceModel` come from `tests.test_agents`, the integrate VCS concretes from
`tests.test_integrate_vcs`, and `Agent`/`MCPServer`/`MCPTool` are declared here.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory
from rebac import app_settings, system_context
from rebac.roles import grant
from strawberry import relay

from angee.agents.models import Agent as AbstractAgent
from angee.agents.models import MCPServer as AbstractMCPServer
from angee.agents.models import MCPTool as AbstractMCPTool
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    SchemaAddon,
    execute_schema,
    make_integration,
)
from tests.conftest import _create_missing_tables as _create_tables
from tests.conftest import result_data as _data
from tests.test_agents import InferenceModel, InferenceProvider, Skill
from tests.test_integrate_vcs import REPOS, VCS_TEST_MODELS, Repository, Source, _vcs_integration

User = get_user_model()


class MCPServer(AbstractMCPServer):
    """Concrete MCP server used by the agents console tests."""

    class Meta(AbstractMCPServer.Meta):
        """Django model options for the canonical test MCP server."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_mcp_server"
        rebac_resource_type = "agents/mcp_server"
        rebac_id_attr = "sqid"


class MCPTool(AbstractMCPTool):
    """Concrete MCP tool used by the agents console tests."""

    class Meta(AbstractMCPTool.Meta):
        """Django model options for the canonical test MCP tool."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_mcp_tool"
        rebac_resource_type = "agents/mcp_tool"
        rebac_id_attr = "sqid"


class Agent(AbstractAgent):
    """Concrete agent used by the agents console tests."""

    class Meta(AbstractAgent.Meta):
        """Django model options for the canonical test agent."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_agent"
        rebac_resource_type = "agents/agent"
        rebac_id_attr = "sqid"


# Order: leaf models before `Agent`, whose M2M through-tables reference them.
AGENTS_GRAPHQL_MODELS = (Skill, MCPServer, MCPTool, InferenceProvider, InferenceModel, Agent)

# Imported only now that every agents concrete is registered.
agents_schema = importlib.import_module("angee.agents.schema")
iam_schema = importlib.import_module("angee.iam.schema")
integrate_schema = importlib.import_module("angee.integrate.schema")


@pytest.fixture()
def agents_console_tables(transactional_db: Any) -> Iterator[None]:
    """Create the iam/integrate/VCS/agents console tables and sync REBAC."""

    del transactional_db
    created = _create_tables(
        IAM_CONNECTION_TEST_MODELS + INTEGRATE_TEST_MODELS + VCS_TEST_MODELS + AGENTS_GRAPHQL_MODELS
    )
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


def test_agent_update_sets_many_to_many_skills(agents_console_tables: None) -> None:
    """`updateAgent` with a `skills` id list replaces the agent's skill membership."""

    admin = _platform_admin("agt-m2m-admin")
    skill_a, skill_b, agent = _seed_agent_and_skills(admin)
    console = _schema()

    result = _data(
        _execute(
            console,
            """
            mutation Attach($id: ID!, $skills: [ID!]) {
              updateAgent(data: {id: $id, skills: $skills}) {
                skills { name }
              }
            }
            """,
            {
                "id": _gid("AgentType", agent.sqid),
                "skills": [_gid("SkillType", skill_a.sqid), _gid("SkillType", skill_b.sqid)],
            },
            user=admin,
        )
    )["updateAgent"]
    assert sorted(node["name"] for node in result["skills"]) == ["Alpha", "Beta"]

    with system_context(reason="test.agents.m2m.verify"):
        assert sorted(agent.skills.values_list("name", flat=True)) == ["Alpha", "Beta"]

    # An empty list clears the membership.
    _data(
        _execute(
            console,
            "mutation Clear($id: ID!) { updateAgent(data: {id: $id, skills: []}) { skills { name } } }",
            {"id": _gid("AgentType", agent.sqid)},
            user=admin,
        )
    )
    with system_context(reason="test.agents.m2m.verify_cleared"):
        assert agent.skills.count() == 0


def test_agent_update_is_platform_admin_gated(agents_console_tables: None) -> None:
    """Updating an agent through the console is platform-admin gated."""

    admin = _platform_admin("agt-crud-admin")
    plain = User.objects.create_user(username="agt-crud-plain", email="plain@example.com")
    with system_context(reason="test.agents.crud.seed"):
        agent = Agent.objects.create(name="Scratch", owner=admin)
    update = """
        mutation Rename($id: ID!) {
          updateAgent(data: {id: $id, name: "Renamed"}) { name }
        }
    """
    agent_id = _gid("AgentType", agent.sqid)

    assert _execute(console := _schema(), update, {"id": agent_id}, user=plain).errors is not None
    renamed = _data(_execute(console, update, {"id": agent_id}, user=admin))["updateAgent"]
    assert renamed == {"name": "Renamed"}


def test_refresh_provider_models_is_admin_gated(agents_console_tables: None) -> None:
    """The `refreshProviderModels` action is platform-admin gated."""

    admin = _platform_admin("agt-refresh-admin")
    plain = User.objects.create_user(username="agt-refresh-plain", email="plain@example.com")
    integration = make_integration("agt-refresh")
    with system_context(reason="test.agents.refresh.seed"):
        provider = InferenceProvider.objects.create(integration=integration, name="P", backend_class="manual")
    provider_id = _gid("InferenceProviderType", provider.sqid)
    query = "mutation($id: ID!){ refreshProviderModels(id: $id){ ok message } }"

    assert _execute(console := _schema(), query, {"id": provider_id}, user=plain).errors is not None
    result = _data(_execute(console, query, {"id": provider_id}, user=admin))["refreshProviderModels"]
    assert result["ok"] is True


def test_create_mcp_server_keeps_defaults_for_omitted_optionals(agents_console_tables: None) -> None:
    """A create omitting optional non-null fields leaves them at the model default.

    Locks the `strawberry.UNSET` input contract: an omitted `config`/`placement` must
    fall back to the JSONField/StateField default, not be submitted as an explicit null
    that `full_clean` would reject (see docs/backend/guidelines.md Pitfalls).
    """

    admin = _platform_admin("agt-mcp-create-admin")
    created = _data(
        _execute(
            _schema(),
            'mutation { createMcpServer(data: {name: "Local MCP"}) { name placement config } }',
            user=admin,
        )
    )["createMcpServer"]
    assert created == {"name": "Local MCP", "placement": "EXTERNAL", "config": {}}


def test_provision_agent_records_instance_and_is_admin_gated(agents_console_tables: None) -> None:
    """`provisionAgent` persists the rendered instance + flips status; admin-gated.

    The browser renders the workspace/service against the daemon; this write-back is
    the only server-side step, so it must stay platform-admin gated like the rest of
    the console.
    """

    admin = _platform_admin("agt-provision-admin")
    plain = User.objects.create_user(username="agt-provision-plain", email="plain@example.com")
    with system_context(reason="test.agents.provision.seed"):
        agent = Agent.objects.create(name="Renderable", owner=admin)
    agent_id = _gid("AgentType", agent.sqid)
    provision = """
        mutation Provision($id: ID!) {
          provisionAgent(id: $id, workspace: "ws-renderable", service: "svc-renderable") { ok }
        }
    """

    assert _execute(console := _schema(), provision, {"id": agent_id}, user=plain).errors is not None
    assert _data(_execute(console, provision, {"id": agent_id}, user=admin))["provisionAgent"]["ok"] is True
    with system_context(reason="test.agents.provision.verify"):
        agent.refresh_from_db()
        assert (agent.workspace, agent.service, str(agent.status)) == (
            "ws-renderable",
            "svc-renderable",
            "running",
        )

    # Deprovision is admin-gated too, then clears the instance and stops the agent.
    deprovision = "mutation($id: ID!){ deprovisionAgent(id: $id){ ok } }"
    assert _execute(console, deprovision, {"id": agent_id}, user=plain).errors is not None
    _data(_execute(console, deprovision, {"id": agent_id}, user=admin))
    with system_context(reason="test.agents.provision.verify_cleared"):
        agent.refresh_from_db()
        assert (agent.workspace, agent.service, str(agent.status)) == ("", "", "stopped")


def _seed_agent_and_skills(owner: Any) -> tuple[Any, Any, Any]:
    """Create a skill source with two skills and an owned agent (all elevated)."""

    vcs = _vcs_integration("agt-m2m", config={"stub_repos": REPOS})
    vcs.discover_repositories()
    with system_context(reason="test.agents.m2m.seed"):
        repository = Repository.objects.get(name="acme/widgets")
        source = Source.objects.create(repository=repository, kind="skill", path="skills")
        skill_a = Skill.objects.create(source=source, name="Alpha", path="skills/alpha")
        skill_b = Skill.objects.create(source=source, name="Beta", path="skills/beta")
        agent = Agent.objects.create(name="Composer", owner=owner)
    return skill_a, skill_b, agent


def _schema() -> Any:
    """Build the merged iam + integrate + agents ``console`` schema for these tests."""

    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in (iam_schema, integrate_schema, agents_schema)
    ]
    return GraphQLSchemas(addons).build("console")


def _execute(schema: Any, query: str, variables: dict[str, Any] | None = None, *, user: Any | None = None) -> Any:
    """Execute one GraphQL operation against the merged console schema."""

    return execute_schema(schema, query, variables, request=_request(user or AnonymousUser()))


def _request(user: Any) -> Any:
    """Return a console-shaped POST request bound to ``user``."""

    request = RequestFactory().post("/graphql/console/")
    request.user = user
    return request


def _platform_admin(username: str) -> Any:
    """Create a superuser holding the platform-admin role tuple."""

    admin = User.objects.create_superuser(username=username, email=f"{username}@example.com", password="admin")
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    return admin


def _gid(typename: str, sqid: str) -> str:
    """Return the relay global id for a console node."""

    with system_context(reason="test.agents.global_id"):
        return relay.to_base64(typename, sqid)
