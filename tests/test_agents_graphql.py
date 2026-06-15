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
import json
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

from angee.agents.context import render_view_context
from angee.agents.mcp_verifier import resolve_actor
from angee.agents.models import Agent as AbstractAgent
from angee.agents.models import MCPServer as AbstractMCPServer
from angee.agents.models import MCPTool as AbstractMCPTool
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.iam.credentials import CredentialKind
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    Credential,
    SchemaAddon,
    execute_schema,
    make_integration,
)
from tests.conftest import _create_missing_tables as _create_tables
from tests.conftest import result_data as _data
from tests.test_agents import InferenceModel, InferenceProvider, Skill
from tests.test_integrate_vcs import REPOS, VCS_TEST_MODELS, Repository, Source, Template, _vcs_integration

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


def test_provision_agent_renders_via_daemon_and_is_admin_gated(
    agents_console_tables: None, monkeypatch: Any
) -> None:
    """`provisionAgent` is one server-side flow: sync secret + drive the daemon render.

    The daemon is mocked. Asserts the credential secret is synced, the workspace and
    service are rendered from the resolved refs (the service mounts the created
    workspace), the agent records the daemon-returned instance, and it's admin-gated.
    """

    admin = _platform_admin("agt-render-admin")
    plain = User.objects.create_user(username="agt-render-plain", email="render@example.com")
    integration = make_integration("agt-render")
    vcs = _vcs_integration("agt-render-tpl", config={"stub_repos": REPOS})
    vcs.discover_repositories()
    with system_context(reason="test.agents.render.seed"):
        repository = Repository.objects.get(name="acme/widgets")
        source = Source.objects.create(repository=repository, kind="template", path="templates")
        workspace_template = Template.objects.create(
            source=source, kind="workspace", name="agent-default", path="workspaces/agent-default"
        )
        service_template = Template.objects.create(
            source=source, kind="service", name="claude-code", path="services/claude-code"
        )
        model = InferenceModel.objects.create(
            provider=InferenceProvider.objects.create(
                integration=integration, name="P", backend_class="manual"
            ),
            name="claude-opus-4-8",
        )
        agent = Agent.objects.create(
            name="Bot",
            owner=admin,
            instructions="Hi.",
            model=model,
            workspace_template=workspace_template,
            service_template=service_template,
        )
    agent_id = _gid("AgentType", agent.sqid)

    calls: list[tuple[Any, ...]] = []

    class _FakeDaemon:
        @classmethod
        def from_settings(cls) -> _FakeDaemon:
            return cls()

        def resolve_template_ref(self, *, name: str, kind: str) -> str:
            return f"ref:{name}"

        def set_secret(self, name: str, value: str) -> None:
            calls.append(("secret", name, value))

        def create_workspace(self, *, template: str, inputs: dict[str, str], name: str = "") -> str:
            calls.append(("workspace", template, inputs))
            return "ws-bot"

        def create_service(
            self, *, template: str, workspace: str, inputs: dict[str, str], start: bool = True, name: str = ""
        ) -> str:
            calls.append(("service", template, workspace, inputs))
            return "svc-bot"

        def destroy_service(self, name: str) -> None:
            calls.append(("destroy_service", name))

        def destroy_workspace(self, name: str, *, purge: bool = True) -> None:
            calls.append(("destroy", name))

    monkeypatch.setattr(agents_schema, "OperatorDaemon", _FakeDaemon)

    provision = "mutation($id: ID!){ provisionAgent(id: $id){ ok message } }"
    assert _execute(console := _schema(), provision, {"id": agent_id}, user=plain).errors is not None
    assert _data(_execute(console, provision, {"id": agent_id}, user=admin))["provisionAgent"]["ok"] is True

    with system_context(reason="test.agents.render.verify"):
        agent.refresh_from_db()
        assert (agent.workspace, agent.service, str(agent.status)) == ("ws-bot", "svc-bot", "running")

    assert [call[0] for call in calls] == ["secret", "workspace", "service"]
    assert calls[0] == ("secret", f"agent-{agent.sqid}-inference", "x")
    assert calls[1][1] == "ref:agent-default" and calls[1][2]["agent_name"] == "Bot"
    assert calls[2][1] == "ref:claude-code"
    assert calls[2][2] == "ws-bot" and calls[2][3]["auth_mode"] == "api_key"

    # Deprovision tears down the workspace via the daemon and clears the record.
    deprovision = "mutation($id: ID!){ deprovisionAgent(id: $id){ ok } }"
    assert _execute(console, deprovision, {"id": agent_id}, user=plain).errors is not None
    _data(_execute(console, deprovision, {"id": agent_id}, user=admin))
    with system_context(reason="test.agents.render.verify_cleared"):
        agent.refresh_from_db()
        assert (agent.workspace, agent.service, str(agent.status)) == ("", "", "stopped")
    assert ("destroy", "ws-bot") in calls


def test_provision_agent_failure_tears_down_workspace_and_records_error(
    agents_console_tables: None, monkeypatch: Any
) -> None:
    """A service-render failure tears the orphaned workspace back down and marks error."""

    admin = _platform_admin("agt-fail-admin")
    vcs = _vcs_integration("agt-fail-tpl", config={"stub_repos": REPOS})
    vcs.discover_repositories()
    with system_context(reason="test.agents.fail.seed"):
        repository = Repository.objects.get(name="acme/widgets")
        source = Source.objects.create(repository=repository, kind="template", path="templates")
        agent = Agent.objects.create(
            name="Doomed",
            owner=admin,
            workspace_template=Template.objects.create(
                source=source, kind="workspace", name="agent-default", path="workspaces/agent-default"
            ),
            service_template=Template.objects.create(
                source=source, kind="service", name="claude-code", path="services/claude-code"
            ),
        )
    agent_id = _gid("AgentType", agent.sqid)

    destroyed: list[str] = []

    class _FailingDaemon:
        @classmethod
        def from_settings(cls) -> _FailingDaemon:
            return cls()

        def resolve_template_ref(self, *, name: str, kind: str) -> str:
            return f"ref:{name}"

        def create_workspace(self, *, template: str, inputs: dict[str, str]) -> str:
            return "ws-doomed"

        def create_service(self, *, template: str, workspace: str, inputs: dict[str, str]) -> str:
            raise RuntimeError("image build failed")

        def destroy_workspace(self, name: str) -> None:
            destroyed.append(name)

    monkeypatch.setattr(agents_schema, "OperatorDaemon", _FailingDaemon)

    result = _data(
        _execute(
            _schema(),
            "mutation($id: ID!){ provisionAgent(id: $id){ ok message } }",
            {"id": agent_id},
            user=admin,
        )
    )["provisionAgent"]
    assert result["ok"] is False and "image build failed" in result["message"]
    assert destroyed == ["ws-doomed"]  # the orphaned workspace was torn back down
    with system_context(reason="test.agents.fail.verify"):
        agent.refresh_from_db()
        assert str(agent.status) == "error"
        assert (agent.workspace, "image build failed" in agent.last_error) == ("", True)


def test_agent_chat_endpoint_mints_route_token_and_is_admin_gated(
    agents_console_tables: None, monkeypatch: Any
) -> None:
    """`agentChatEndpoint` returns the routed url + per-actor route token + mcpServers.

    The daemon is mocked. Asserts the resolver looks the agent's `service` up, mints a
    route token scoped to that service, returns the routed url/token plus the agent's
    rendered `mcpServers`, and is platform-admin gated.
    """

    admin = _platform_admin("agt-chat-admin")
    plain = User.objects.create_user(username="agt-chat-plain", email="chat@example.com")
    with system_context(reason="test.agents.chat.seed"):
        agent = Agent.objects.create(name="Chatty", owner=admin, service="svc-chat")
        server = MCPServer.objects.create(name="notes", url="http://host.docker.internal:8101/mcp/notes/")
        agent.mcp_servers.add(server)
    agent_id = _gid("AgentType", agent.sqid)

    minted: list[tuple[str, str, str]] = []

    class _FakeDaemon:
        @classmethod
        def from_settings(cls) -> _FakeDaemon:
            return cls()

        def service_endpoint(self, name: str) -> dict[str, Any]:
            return {"routed": True, "url": f"wss://{name}.example.test/"}

        def mint_route_token(self, actor: str, service: str, ttl: str = "1h") -> dict[str, Any]:
            minted.append((actor, service, ttl))
            return {"token": "jwt-route", "expires_at": "2026-06-15T00:00:00Z"}

    monkeypatch.setattr(agents_schema, "OperatorDaemon", _FakeDaemon)

    query = """
        mutation Chat($id: ID!) {
          agentChatEndpoint(id: $id) { url token expiresAt mcpServers }
        }
    """
    assert _execute(console := _schema(), query, {"id": agent_id}, user=plain).errors is not None
    endpoint = _data(_execute(console, query, {"id": agent_id}, user=admin))["agentChatEndpoint"]

    assert endpoint["url"] == "wss://svc-chat.example.test/"
    assert endpoint["token"] == "jwt-route"
    assert endpoint["expiresAt"] == "2026-06-15T00:00:00Z"
    assert endpoint["mcpServers"] == {
        "notes": {"type": "http", "url": "http://host.docker.internal:8101/mcp/notes/"},
    }
    # The token is minted per actor (the session user, `auth/user:<id>`), scoped to the
    # agent's routed service, on the chat TTL — never as the operator admin bearer.
    assert len(minted) == 1
    actor, service, ttl = minted[0]
    assert actor.startswith("auth/user:") and service == "svc-chat" and ttl == "2h"


def test_agent_chat_endpoint_errors_when_agent_not_running(agents_console_tables: None) -> None:
    """`agentChatEndpoint` errors when the agent has no rendered `service`."""

    admin = _platform_admin("agt-chat-stopped-admin")
    with system_context(reason="test.agents.chat.stopped.seed"):
        agent = Agent.objects.create(name="Idle", owner=admin)
    result = _execute(
        _schema(),
        "mutation($id: ID!){ agentChatEndpoint(id: $id){ url } }",
        {"id": _gid("AgentType", agent.sqid)},
        user=admin,
    )
    assert result.errors is not None
    assert "not running" in str(result.errors[0])


def test_resolve_session_for_view_resolves_the_actors_running_agent(
    agents_console_tables: None,
) -> None:
    """`resolveSessionForView` resolves the actor's running agent for the side chatter.

    The chatter knows the view, not the agent; this returns the agent identity (the client
    mints the endpoint separately with `agentChatEndpoint`), picking the actor's RUNNING
    service-backed agent and returning null when the user has no running agent (so the
    chatter shows a call-to-action rather than erroring).
    """

    admin = _platform_admin("agt-session-admin")
    with system_context(reason="test.agents.session.seed"):
        Agent.objects.create(name="Sidekick", owner=admin, service="svc-side", status="running")
        # A draft agent for the same owner is not eligible (not running).
        Agent.objects.create(name="Draft", owner=admin)

    mutation = """
        mutation Session($view: JSON!) {
          resolveSessionForView(view: $view) { agentName status modelHandle }
        }
    """
    view = {"kind": "record", "type": "notes/note", "sqid": "nte_x"}
    session = _data(_execute(console := _schema(), mutation, {"view": view}, user=admin))["resolveSessionForView"]

    assert session["agentName"] == "Sidekick"
    assert session["status"] == "running"

    # A platform admin with no running agent gets null, not an error.
    other = _platform_admin("agt-session-none")
    none_session = _data(_execute(console, mutation, {"view": view}, user=other))["resolveSessionForView"]
    assert none_session is None


def test_provision_workspace_inputs_from_agent_fields(agents_console_tables: None) -> None:
    """The workspace inputs come from the agent's structured fields (not raw JSON)."""

    owner = User.objects.create_user(username="agt-wsi-owner", email="wsi@example.com")
    with system_context(reason="test.agents.provision_inputs.workspace"):
        agent = Agent.objects.create(name="Helper Bot", owner=owner, instructions="Be terse.")
        server = MCPServer.objects.create(name="angee", url="http://host.docker.internal:8101/mcp/")
        agent.mcp_servers.add(server)
        inputs = agent.provision_workspace_inputs()

    assert inputs["agent_name"] == "Helper Bot"
    assert inputs["instructions"] == "Be terse."
    assert json.loads(inputs["mcp_json"]) == {
        "mcpServers": {"angee": {"type": "http", "url": "http://host.docker.internal:8101/mcp/"}},
    }


def test_render_agent_prompt_builds_system_context_and_is_admin_gated(
    agents_console_tables: None,
) -> None:
    """`renderAgentPrompt` returns a ``<system_context>`` block for the open view.

    Model-generic: a record view of ``agents/mcp_server`` previews the selected row
    from its public fields and points at the MCP tools, after resolving the agent
    (admin-gated).
    """

    admin = _platform_admin("agt-prompt-admin")
    plain = User.objects.create_user(username="agt-prompt-plain", email="prompt@example.com")
    with system_context(reason="test.agents.prompt.seed"):
        agent = Agent.objects.create(name="Prompted", owner=admin)
        server = MCPServer.objects.create(name="Local Notes", url="http://x/mcp/notes/")
    agent_id = _gid("AgentType", agent.sqid)
    mutation = """
        mutation Prompt($id: ID!, $view: JSON!) { renderAgentPrompt(id: $id, view: $view) }
    """
    view = {"kind": "record", "type": "agents/mcp_server", "sqid": str(server.sqid)}

    assert _execute(console := _schema(), mutation, {"id": agent_id, "view": view}, user=plain).errors is not None
    rendered = _data(_execute(console, mutation, {"id": agent_id, "view": view}, user=admin))["renderAgentPrompt"]

    assert rendered.startswith("<system_context>") and rendered.endswith("</system_context>")
    assert "record of agents/mcp_server" in rendered
    assert str(server.sqid) in rendered and "Local Notes" in rendered
    assert "MCP tool" in rendered

    # An empty envelope adds nothing.
    empty = _data(
        _execute(console, mutation, {"id": agent_id, "view": {}}, user=admin)
    )["renderAgentPrompt"]
    assert empty == ""


def test_render_view_context_never_previews_encrypted_secret(agents_console_tables: None) -> None:
    """A view of a secret-bearing model previews the row but never its EncryptedField.

    The block is sent to a third-party LLM, so a column whose Python value decrypts to a
    secret (here ``Credential.material``) must not appear even when the row itself is
    previewed — the regression guard for the field-enumeration leak.
    """

    owner = User.objects.create_user(username="ctx-secret-owner", email="ctxsecret@example.com")
    with system_context(reason="test.ctx.secret"):
        credential = Credential.objects.create_local_credential(
            owner,
            kind=CredentialKind.STATIC_TOKEN,
            name="leaky-cred",
            material={"api_key": "SUPER-SECRET-XYZ"},
        )
        view = {"kind": "record", "type": "auth/credential", "sqid": str(credential.sqid)}
        rendered = render_view_context(view)

    assert str(credential.sqid) in rendered  # the row IS previewed (name, kind, …)
    assert "leaky-cred" in rendered
    assert "SUPER-SECRET-XYZ" not in rendered  # …but the secret material is NOT
    assert "material" not in rendered


def test_mcp_config_emits_secret_ref_auth_header_for_credentialed_server(
    agents_console_tables: None,
) -> None:
    """A credentialed MCP server renders a ``${<env>}`` Authorization header.

    The bearer rides through the operator secret store, never the rendered file: the
    header references the container env var (:meth:`Agent.mcp_bearer_env`), which the
    service env sets from the operator secret (the operator resolves ``${secret.<name>}``
    in a service's env, not in file content); :meth:`Agent.mcp_secrets` carries the value
    for the provision flow to sync.
    """

    owner = User.objects.create_user(username="agt-mcpcfg-owner", email="mcpcfg@example.com")
    with system_context(reason="test.agents.mcp_config"):
        credential = Credential.objects.create_local_credential(
            owner, kind=CredentialKind.STATIC_TOKEN, name="notes-bearer", material={"api_key": "tok-notes"}
        )
        agent = Agent.objects.create(name="Cfg", owner=owner)
        plain = MCPServer.objects.create(name="public", url="http://host.docker.internal:8101/mcp/public/")
        secured = MCPServer.objects.create(
            name="notes", url="http://host.docker.internal:8101/mcp/notes/", credential=credential
        )
        agent.mcp_servers.add(plain, secured)
        config = agent.mcp_config()
        secrets = agent.mcp_secrets()
        secret_name = agent.mcp_secret_name(secured)
        service_inputs = agent.provision_service_inputs()

    servers = config["mcpServers"]
    assert "headers" not in servers["public"]  # no credential → no auth header
    assert servers["notes"]["headers"] == {"Authorization": f"Bearer ${{{agent.mcp_bearer_env(secured)}}}"}
    assert secret_name == f"agent-{agent.sqid}-mcp-{credential.sqid}"
    # The bearer reaches the container via the service env, set from the operator secret.
    assert f'{agent.mcp_bearer_env(secured)}: "${{secret.{secret_name}}}"' in service_inputs["mcp_env"]
    assert secrets == {secret_name: "tok-notes"}  # synced server-side, never in the file


def test_mcp_actor_verifier_resolves_bearer_to_the_credential_owner(agents_console_tables: None) -> None:
    """The agents bearer verifier maps an MCP-server credential to its owning user.

    Interim model (option A): an agent acts with the identity of the user who owns the
    credential it presents, so it gets that user's notes CRUD with correct attribution.
    An unknown bearer resolves to nothing, so the runtime denies it with no fallback.
    """

    owner = User.objects.create_user(username="agt-verify-owner", email="verify@example.com")
    with system_context(reason="test.agents.mcp_verify"):
        credential = Credential.objects.create_local_credential(
            owner, kind=CredentialKind.STATIC_TOKEN, name="mcp-bearer", material={"api_key": "tok-secret"}
        )
        MCPServer.objects.create(name="notes", url="http://x/mcp/notes/", credential=credential)

    actor = resolve_actor("tok-secret")
    assert actor is not None
    assert actor.subject_type == "auth/user"
    assert actor.subject_id == str(owner.pk)  # the agent runs as the credential's owner
    assert resolve_actor("wrong-token") is None
    assert resolve_actor("") is None


def test_provision_service_inputs_credential_drives_auth_mode(agents_console_tables: None) -> None:
    """The credential kind picks the auth mode (prefer OAuth) and the model rides along."""

    owner = User.objects.create_user(username="agt-svci-owner", email="svci@example.com")
    static_integration = make_integration("agt-svc-static")
    oauth_integration = make_integration("agt-svc-oauth", kind=CredentialKind.OAUTH)
    with system_context(reason="test.agents.provision_inputs.service"):
        static_model = InferenceModel.objects.create(
            provider=InferenceProvider.objects.create(
                integration=static_integration, name="S", backend_class="manual"
            ),
            name="claude-3",
        )
        static_agent = Agent.objects.create(name="Static", owner=owner, model=static_model)
        static_inputs = static_agent.provision_service_inputs()

        oauth_model = InferenceModel.objects.create(
            provider=InferenceProvider.objects.create(
                integration=oauth_integration, name="O", backend_class="manual"
            ),
            name="claude-opus-4-8",
        )
        oauth_agent = Agent.objects.create(name="OAuth", owner=owner, model=oauth_model)
        oauth_inputs = oauth_agent.provision_service_inputs()

    assert static_inputs == {
        "auth_mode": "api_key",
        "model": "claude-3",
        "secret_name": f"agent-{static_agent.sqid}-inference",
    }
    assert oauth_inputs["auth_mode"] == "oauth"
    assert oauth_inputs["model"] == "claude-opus-4-8"


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
