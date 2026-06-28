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

import base64
import importlib
import json
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, override_settings
from rebac import app_settings, system_context
from rebac.roles import grant

from angee.agents.context import render_view_context
from angee.agents.mcp_verifier import resolve_actor
from angee.agents.models import Agent as AbstractAgent
from angee.agents.models import MCPServer as AbstractMCPServer
from angee.agents.models import MCPTool as AbstractMCPTool
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.credentials import CredentialKind
from angee.operator.daemon import OperatorDaemonNotFound
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    Credential,
    ExternalAccount,
    Integration,
    OAuthClient,
    SchemaAddon,
    Vendor,
    execute_schema,
    make_integration,
)
from tests.conftest import _create_missing_tables as _create_tables
from tests.conftest import result_data as _data
from tests.test_agents import InferenceModel, InferenceProvider, Skill, _provider
from tests.test_integrate_vcs import REPOS, VCS_TEST_MODELS, Repository, Source, Template, _vcs_bridge

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
agents_provisioning = importlib.import_module("angee.agents.provisioning")
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


def test_agent_hasura_insert_update_and_delete(agents_console_tables: None) -> None:
    """Agent row writes use the generated Hasura mutation roots."""

    admin = _platform_admin("agt-hasura-admin")
    console = _schema()

    created = _data(
        _execute(
            console,
            """
            mutation CreateAgent($owner: ID!) {
              insert_agents_one(object: {name: "Composer", owner: $owner, lifecycle: "draft"}) {
                id
                name
                lifecycle
                is_template
                can_provision
                can_deprovision
                can_delete
                owner { username }
              }
            }
            """,
            {"owner": str(admin.pk)},
            user=admin,
        )
    )["insert_agents_one"]
    assert created == {
        "id": created["id"],
        "name": "Composer",
        "lifecycle": "DRAFT",
        "is_template": False,
        "can_provision": True,
        "can_deprovision": False,
        "can_delete": True,
        "owner": {"username": "agt-hasura-admin"},
    }

    updated = _data(
        _execute(
            console,
            """
            mutation Rename($id: String!) {
              update_agents_by_pk(pk_columns: {id: $id}, _set: {name: "Renamed", lifecycle: "deprovisioned"}) {
                name
                lifecycle
                can_provision
                can_deprovision
                can_delete
              }
            }
            """,
            {"id": created["id"]},
            user=admin,
        )
    )["update_agents_by_pk"]
    assert updated == {
        "name": "Renamed",
        "lifecycle": "DEPROVISIONED",
        "can_provision": True,
        "can_deprovision": False,
        "can_delete": True,
    }

    deleted = _data(
        _execute(
            console,
            """
            mutation Delete($id: String!) {
              delete_agents_by_pk(id: $id) { id name }
            }
            """,
            {"id": created["id"]},
            user=admin,
        )
    )["delete_agents_by_pk"]
    assert deleted == {"id": created["id"], "name": "Renamed"}
    with system_context(reason="test.agents.hasura_delete.verify"):
        assert not Agent.objects.filter(sqid=created["id"]).exists()


def test_agent_hasura_delete_blocks_rendered_agents(agents_console_tables: None) -> None:
    """Agent delete policy is enforced by the backend write owner, not only the UI."""

    admin = _platform_admin("agt-delete-block-admin")
    with system_context(reason="test.agents.delete_block.seed"):
        agent = Agent.objects.create(
            name="Rendered",
            owner=admin,
            workspace="ws-rendered",
            service="svc-rendered",
            lifecycle="ready",
        )
    agent_id = _public_id(agent.sqid)

    result = _execute(
        _schema(),
        """
        mutation Delete($id: String!) {
          delete_agents_by_pk(id: $id) { id name }
        }
        """,
        {"id": agent_id},
        user=admin,
    )

    assert result.errors is not None
    assert "Deprovision this agent before deleting it." in str(result.errors[0])
    with system_context(reason="test.agents.delete_block.verify"):
        assert Agent.objects.filter(pk=agent.pk).exists()


def test_agent_hasura_update_sets_many_to_many_skills(agents_console_tables: None) -> None:
    """Generated Hasura updates replace agent skill membership through relation arrays."""

    admin = _platform_admin("agt-m2m-admin")
    skill_a, skill_b, agent = _seed_agent_and_skills(admin)
    console = _schema()

    result = _data(
        _execute(
            console,
            """
            mutation Attach($id: String!, $skills: [ID!]) {
              update_agents_by_pk(pk_columns: {id: $id}, _set: {skills: $skills}) {
                skills { name }
              }
            }
            """,
            {
                "id": _public_id(agent.sqid),
                "skills": [_public_id(skill_a.sqid), _public_id(skill_b.sqid)],
            },
            user=admin,
        )
    )["update_agents_by_pk"]
    assert sorted(node["name"] for node in result["skills"]) == ["Alpha", "Beta"]

    with system_context(reason="test.agents.m2m.verify"):
        assert sorted(agent.skills.values_list("name", flat=True)) == ["Alpha", "Beta"]

    _data(
        _execute(
            console,
            """
            mutation Clear($id: String!) {
              update_agents_by_pk(pk_columns: {id: $id}, _set: {skills: []}) {
                skills { name }
              }
            }
            """,
            {"id": _public_id(agent.sqid)},
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
        mutation Rename($id: String!) {
          update_agents_by_pk(pk_columns: {id: $id}, _set: {name: "Renamed"}) { name }
        }
    """
    agent_id = _public_id(agent.sqid)

    assert _execute(console := _schema(), update, {"id": agent_id}, user=plain).errors is not None
    renamed = _data(_execute(console, update, {"id": agent_id}, user=admin))["update_agents_by_pk"]
    assert renamed == {"name": "Renamed"}


def test_refresh_provider_models_is_admin_gated(agents_console_tables: None) -> None:
    """The `refreshProviderModels` action is platform-admin gated."""

    admin = _platform_admin("agt-refresh-admin")
    plain = User.objects.create_user(username="agt-refresh-plain", email="plain@example.com")
    provider = _provider("agt-refresh", name="P")
    provider_id = _public_id(provider.sqid)
    query = "mutation($id: ID!){ refresh_provider_models(id: $id){ ok message } }"

    assert _execute(console := _schema(), query, {"id": provider_id}, user=plain).errors is not None
    result = _data(_execute(console, query, {"id": provider_id}, user=admin))["refresh_provider_models"]
    assert result["ok"] is True


def test_inference_models_query_accepts_provider_sqid_filter(agents_console_tables: None) -> None:
    """The model catalogue list supports native provider relation filters."""

    admin = _platform_admin("agt-model-filter-admin")
    provider_a = _provider("agt-model-filter-a", name="Anthropic")
    provider_b = _provider("agt-model-filter-b", name="Manual")
    with system_context(reason="test.agents.model_filter.seed"):
        InferenceModel.objects.create(provider=provider_a, name="claude-sonnet-4-6")
        InferenceModel.objects.create(provider=provider_a, name="claude-opus-4-8")
        InferenceModel.objects.create(provider=provider_b, name="manual-model")

    result = _data(
        _execute(
            _schema(),
            """
            query ModelsForProvider($provider: String!) {
              rows: inference_models(where: {provider: {_eq: $provider}}) {
                name
                provider { name }
              }
            }
            """,
            {"provider": _public_id(provider_a.sqid)},
            user=admin,
        )
    )["rows"]

    assert sorted(row["name"] for row in result) == ["claude-opus-4-8", "claude-sonnet-4-6"]
    assert {row["provider"]["name"] for row in result} == {"Anthropic"}


def test_inference_model_groups_aggregate_runs_for_provider_and_capability(
    agents_console_tables: None,
) -> None:
    """The model catalogue exposes grouped buckets for list/board views."""

    admin = _platform_admin("agt-model-groups-admin")
    provider_a = _provider("agt-model-groups-a", name="Anthropic")
    provider_b = _provider("agt-model-groups-b", name="Manual")
    with system_context(reason="test.agents.model_groups.seed"):
        InferenceModel.objects.create(provider=provider_a, name="claude-sonnet-4-6", model_use="chat")
        InferenceModel.objects.create(provider=provider_a, name="claude-embed-4-6", model_use="embedding")
        InferenceModel.objects.create(provider=provider_b, name="manual-model", model_use="chat")

    grouped = _data(
        _execute(
            _schema(),
            """
            query InferenceModelGroups(
              $byUse: [InferenceModelTypeGroupBySpec!]!
              $byProvider: [InferenceModelTypeGroupBySpec!]!
            ) {
              byUse: inference_models_groups(group_by: $byUse, limit: 10) {
                key { model_use }
                aggregate { count }
              }
              byProvider: inference_models_groups(group_by: $byProvider, limit: 10) {
                key { provider_id provider__name }
                aggregate { count }
              }
            }
            """,
            {
                "byUse": [{"field": "MODEL_USE"}],
                "byProvider": [{"field": "PROVIDER"}, {"field": "PROVIDER__NAME"}],
            },
            user=admin,
        )
    )

    assert sorted(grouped["byUse"], key=lambda row: row["key"]["model_use"]) == [
        {"key": {"model_use": "CHAT"}, "aggregate": {"count": 2}},
        {"key": {"model_use": "EMBEDDING"}, "aggregate": {"count": 1}},
    ]
    provider_groups = {
        row["key"]["provider_id"]: row
        for row in grouped["byProvider"]
    }
    assert provider_groups == {
        str(provider_a.pk): {
            "key": {
                "provider_id": str(provider_a.pk),
                "provider__name": "Anthropic",
            },
            "aggregate": {"count": 2},
        },
        str(provider_b.pk): {
            "key": {
                "provider_id": str(provider_b.pk),
                "provider__name": "Manual",
            },
            "aggregate": {"count": 1},
        },
    }


def test_create_inference_provider_creates_child_row(agents_console_tables: None) -> None:
    """InferenceProvider create writes the provider child row directly."""

    admin = _platform_admin("agt-provider-create-admin")
    seed = make_integration("agt-provider-manual")
    console = _schema()
    mutation = """
        mutation CreateProvider($vendor: ID!, $owner: ID!) {
          create_inference_provider(
            data: {
              vendor: $vendor
              owner: $owner
              backend_class: "manual"
              name: "Provider"
              base_url: "https://api.example.test"
            }
          ) {
            name
            base_url
            backend_class
            status
          }
        }
    """

    created = _data(
        _execute(
            console,
            mutation,
            {
                "vendor": _public_id(seed.vendor.sqid),
                "owner": str(seed.owner.pk),
            },
            user=admin,
        )
    )["create_inference_provider"]
    assert created == {
        "name": "Provider",
        "base_url": "https://api.example.test",
        "backend_class": "MANUAL",
        "status": "DRAFT",
    }
    with system_context(reason="test.agents.provider_mti.verify"):
        provider = InferenceProvider.objects.get(name="Provider")
        integration = Integration.objects.get(pk=provider.pk)
        assert provider.owner_id == integration.owner_id
        assert provider.vendor_id == integration.vendor_id
        assert provider.backend_class == "manual"


def test_update_inference_provider_backend_rematerializes_defaults(agents_console_tables: None) -> None:
    """Changing a provider backend applies the new backend defaults on the owner row."""

    admin = _platform_admin("agt-provider-update-admin")
    with system_context(reason="test.agents.provider_update.seed"):
        anthropic = Vendor.objects.create(slug="anthropic", display_name="Anthropic")
    provider = _provider(
        "agt-provider-update",
        backend_class="manual",
        name="Custom",
    )
    with system_context(reason="test.agents.provider_update.account"):
        oauth_client = OAuthClient.objects.create(
            slug="agt-provider-update-account",
            display_name="Provider Update Account",
        )
        account = ExternalAccount.objects.link(
            oauth_client,
            "agt-provider-update-ext",
            owner=provider.owner,
            email="provider-update@example.test",
        )
    mutation = """
        mutation UpdateProvider($id: ID!, $account: ID!) {
          update_inference_provider(data: {id: $id, backend_class: "anthropic", account: $account}) {
            backend_class
            name
            vendor { slug }
            account { external_id }
          }
        }
    """

    updated = _data(
        _execute(
            _schema(),
            mutation,
            {"id": _public_id(provider.sqid), "account": _public_id(account.sqid)},
            user=admin,
        )
    )["update_inference_provider"]

    assert updated == {
        "backend_class": "ANTHROPIC",
        "name": "Anthropic",
        "vendor": {"slug": "anthropic"},
        "account": {"external_id": "agt-provider-update-ext"},
    }
    provider.refresh_from_db()
    assert provider.vendor_id == anthropic.pk
    assert provider.account_id == account.pk


def test_connect_inference_provider_uses_provider_backend_oauth_client(agents_console_tables: None) -> None:
    """Provider connect resolves OAuth from provider.backend, not Integration.impl."""

    del agents_console_tables
    provider = _provider("agt-provider-connect", backend_class="anthropic", name="Anthropic")
    provider_id = _public_id(provider.sqid)
    with system_context(reason="test.agents.provider_connect.seed"):
        oauth_client = OAuthClient.objects.create(
            slug="anthropic-personal",
            display_name="Anthropic Personal",
            client_id="anthropic-client",
        )
        credential = Credential.objects.upsert_for_user(
            provider.owner,
            oauth_client,
            CredentialKind.OAUTH,
            {"access_token": "anthropic-token"},
        )
    mutation = """
        mutation ConnectProvider($id: ID!) {
          connect_inference_provider(id: $id) {
            attached
            error
            integration { status credential { display_name } }
          }
        }
    """

    result = _data(_execute(_schema(), mutation, {"id": provider_id}, user=provider.owner))[
        "connect_inference_provider"
    ]

    assert result == {
        "attached": True,
        "error": None,
        "integration": {
            "status": "ACTIVE",
            "credential": {"display_name": "Anthropic Personal"},
        },
    }
    provider.refresh_from_db()
    assert provider.credential_id == credential.pk


def test_connect_inference_provider_uses_shared_oauth_client_error_code(
    agents_console_tables: None,
) -> None:
    """Provider connect reports the shared OAuth-client lookup error code."""

    del agents_console_tables
    provider = _provider("agt-provider-missing-oauth", backend_class="anthropic", name="Anthropic")
    mutation = """
        mutation ConnectProvider($id: ID!) {
          connect_inference_provider(id: $id) {
            attached
            error
            error_code
          }
        }
    """

    result = _data(_execute(_schema(), mutation, {"id": _public_id(provider.sqid)}, user=provider.owner))[
        "connect_inference_provider"
    ]

    assert result == {
        "attached": False,
        "error": "Inference provider has no enabled OAuth client.",
        "error_code": "oauth_client_not_connectable",
    }


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
            'mutation { insert_mcp_servers_one(object: {name: "Local MCP"}) { name placement config } }',
            user=admin,
        )
    )["insert_mcp_servers_one"]
    assert created == {"name": "Local MCP", "placement": "EXTERNAL", "config": {}}


def test_provision_agent_renders_via_daemon_and_is_admin_gated(agents_console_tables: None, monkeypatch: Any) -> None:
    """`provisionAgent` syncs secrets, drives the daemon render, and records names.

    The daemon is mocked. Asserts the credential secret is synced, the workspace and
    service are rendered from the resolved refs (the service mounts the created
    workspace), the agent records the daemon-returned instance, and it is admin-gated.
    """

    admin = _platform_admin("agt-render-admin")
    plain = User.objects.create_user(username="agt-render-plain", email="render@example.com")
    provider = _provider("agt-render", backend_class="anthropic", name="P")
    vcs = _vcs_bridge("agt-render-tpl", config={"stub_repos": REPOS})
    vcs.discover_repositories()
    with system_context(reason="test.agents.render.seed"):
        repository = Repository.objects.get(name="acme/widgets")
        source = Source.objects.create(repository=repository, kind="template", path="templates")
        workspace_template = Template.objects.create(
            source=source, kind="workspace", name="agent-default", path="workspaces/agent-default"
        )
        model = InferenceModel.objects.create(
            provider=provider,
            name="claude-opus-4-8",
        )
        agent = Agent.objects.create(
            name="Bot",
            owner=admin,
            instructions="Hi.",
            model=model,
            workspace_template=workspace_template,
            runtime_class="claude_code",
        )
    agent_id = _public_id(agent.sqid)

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
            with system_context(reason="test.agents.render.verify_workspace_recorded"):
                agent.refresh_from_db()
                calls.append(("recorded_workspace", agent.workspace, str(agent.lifecycle)))
            calls.append(("service", template, workspace, inputs))
            return "svc-bot"

        def destroy_service(self, name: str) -> None:
            calls.append(("destroy_service", name))

        def destroy_workspace(self, name: str, *, purge: bool = True) -> None:
            calls.append(("destroy", name))

    monkeypatch.setattr(agents_provisioning, "OperatorDaemon", _FakeDaemon)
    original_mark_provisioned = Agent.mark_provisioned

    def mark_provisioned_with_recorded_service(self: Agent, *, workspace: str, service: str = "") -> None:
        with system_context(reason="test.agents.render.verify_service_recorded"):
            persisted = Agent.objects.get(pk=self.pk)
            calls.append(("recorded_service", persisted.service, str(persisted.lifecycle)))
        original_mark_provisioned(self, workspace=workspace, service=service)

    monkeypatch.setattr(Agent, "mark_provisioned", mark_provisioned_with_recorded_service)

    provision = "mutation($id: ID!){ provision_agent(id: $id){ ok message } }"
    assert _execute(console := _schema(), provision, {"id": agent_id}, user=plain).errors is not None
    result = _data(_execute(console, provision, {"id": agent_id}, user=admin))["provision_agent"]
    assert result == {"ok": True, "message": "Provisioned “svc-bot”."}
    with system_context(reason="test.agents.render.verify_rendered"):
        agent.refresh_from_db()
        assert (agent.workspace, agent.service, str(agent.lifecycle), str(agent.runtime_status)) == (
            "ws-bot",
            "svc-bot",
            "ready",
            "running",
        )

    assert [call[0] for call in calls] == [
        "secret",
        "workspace",
        "recorded_workspace",
        "service",
        "recorded_service",
    ]
    assert calls[0] == ("secret", f"agent-{agent.sqid}-inference", "x")
    assert calls[1][1] == "ref:agent-default" and calls[1][2]["agent_name"] == "Bot"
    assert calls[2] == ("recorded_workspace", "ws-bot", "provisioning")
    assert calls[3][1] == "ref:claude-code"
    assert calls[3][2] == "ws-bot"
    assert calls[3][3]["auth_env"] == f'      ANTHROPIC_API_KEY: "${{secret.agent-{agent.sqid}-inference}}"'
    assert calls[4] == ("recorded_service", "svc-bot", "provisioning")

    # Deprovision tears down the workspace via the daemon and clears the record.
    deprovision = "mutation($id: ID!){ deprovision_agent(id: $id){ ok message } }"
    assert _execute(console, deprovision, {"id": agent_id}, user=plain).errors is not None
    result = _data(_execute(console, deprovision, {"id": agent_id}, user=admin))["deprovision_agent"]
    assert result == {"ok": True, "message": "Deprovisioned."}
    with system_context(reason="test.agents.render.verify_deprovisioned"):
        agent.refresh_from_db()
        assert (agent.workspace, agent.service, str(agent.lifecycle), str(agent.runtime_status)) == (
            "",
            "",
            "deprovisioned",
            "stopped",
        )
    assert ("destroy", "ws-bot") in calls


def test_provision_agent_failure_tears_down_workspace_and_records_error(
    agents_console_tables: None, monkeypatch: Any
) -> None:
    """A service-render failure tears the orphaned workspace down and marks error."""

    admin = _platform_admin("agt-fail-admin")
    vcs = _vcs_bridge("agt-fail-tpl", config={"stub_repos": REPOS})
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
            runtime_class="claude_code",
        )
    agent_id = _public_id(agent.sqid)

    destroyed: list[str] = []
    recorded: list[tuple[str, str]] = []

    class _FailingDaemon:
        @classmethod
        def from_settings(cls) -> _FailingDaemon:
            return cls()

        def resolve_template_ref(self, *, name: str, kind: str) -> str:
            return f"ref:{name}"

        def create_workspace(self, *, template: str, inputs: dict[str, str]) -> str:
            return "ws-doomed"

        def create_service(self, *, template: str, workspace: str, inputs: dict[str, str]) -> str:
            with system_context(reason="test.agents.fail.verify_workspace_recorded"):
                agent.refresh_from_db()
                recorded.append((agent.workspace, str(agent.lifecycle)))
            raise RuntimeError("image build failed")

        def destroy_workspace(self, name: str) -> None:
            destroyed.append(name)

    monkeypatch.setattr(agents_provisioning, "OperatorDaemon", _FailingDaemon)

    result = _data(
        _execute(
            _schema(),
            "mutation($id: ID!){ provision_agent(id: $id){ ok message } }",
            {"id": agent_id},
            user=admin,
        )
    )["provision_agent"]
    assert result["ok"] is False and "image build failed" in result["message"]
    assert recorded == [("ws-doomed", "provisioning")]
    assert destroyed == ["ws-doomed"]  # the orphaned workspace was torn back down
    with system_context(reason="test.agents.fail.verify"):
        agent.refresh_from_db()
        # Run state errored; the rolled-back workspace leaves the lifecycle a clean DRAFT.
        assert (str(agent.runtime_status), str(agent.lifecycle)) == ("error", "draft")
        assert (agent.workspace, "image build failed" in agent.last_error) == ("", True)


def test_deprovision_agent_treats_missing_operator_instances_as_gone(
    agents_console_tables: None, monkeypatch: Any
) -> None:
    """A deprovision retry clears stale names when the daemon says they are already gone."""

    admin = _platform_admin("agt-deprov-missing-admin")
    agent = _provisionable_agent(
        admin,
        "Gonebot",
        slug="agt-deprov-missing-tpl",
        workspace="ws-gone",
        service="svc-gone",
        lifecycle="ready",
        runtime_status="error",
        last_error='Teardown failed: operator POST destroy: HTTP 404: service "svc-gone" is not declared',
    )
    agent_id = _public_id(agent.sqid)
    calls: list[tuple[str, str]] = []

    class _MissingDaemon:
        @classmethod
        def from_settings(cls) -> _MissingDaemon:
            return cls()

        def destroy_service(self, name: str) -> None:
            calls.append(("destroy_service", name))
            raise OperatorDaemonNotFound(f'operator POST destroy: HTTP 404: service "{name}" is not declared')

        def destroy_workspace(self, name: str) -> None:
            calls.append(("destroy_workspace", name))
            raise OperatorDaemonNotFound(f'operator POST destroy?purge=true: HTTP 404: workspace "{name}" is not found')

    monkeypatch.setattr(agents_provisioning, "OperatorDaemon", _MissingDaemon)

    result = _data(
        _execute(
            _schema(),
            "mutation($id: ID!){ deprovision_agent(id: $id){ ok message } }",
            {"id": agent_id},
            user=admin,
        )
    )["deprovision_agent"]

    assert result == {"ok": True, "message": "Deprovisioned."}
    assert calls == [("destroy_service", "svc-gone"), ("destroy_workspace", "ws-gone")]
    with system_context(reason="test.agents.deprov_missing.verify"):
        agent.refresh_from_db()
        assert (agent.workspace, agent.service, str(agent.lifecycle), str(agent.runtime_status), agent.last_error) == (
            "",
            "",
            "deprovisioned",
            "stopped",
            "",
        )


def test_provision_agent_records_error_when_plan_resolution_fails(
    agents_console_tables: None, monkeypatch: Any
) -> None:
    """A plan-resolution failure records ERROR — the agent never strands in PROVISIONING.

    `_render_plan` reads the credential chain and agent inputs before the daemon render; a
    failure there (a missing/undecryptable credential, a bad MCP config) must route through
    the same failure handler as a daemon render failure, not flip the agent to PROVISIONING
    and then raise an unhandled 500 that leaves it stuck.
    """

    admin = _platform_admin("agt-planfail-admin")
    agent = _provisionable_agent(admin, "PlanFail", slug="agt-planfail-tpl")
    agent_id = _public_id(agent.sqid)

    def _boom(_agent: Any) -> Any:
        raise RuntimeError("credential is unreadable")

    monkeypatch.setattr(agents_provisioning, "_render_plan", _boom)

    result = _data(
        _execute(
            _schema(),
            "mutation($id: ID!){ provision_agent(id: $id){ ok message } }",
            {"id": agent_id},
            user=admin,
        )
    )["provision_agent"]

    assert result["ok"] is False and "credential is unreadable" in result["message"]
    with system_context(reason="test.agents.planfail.verify"):
        agent.refresh_from_db()
        # Run state ERROR with the lifecycle reset to DRAFT — not stranded in PROVISIONING
        # — and no instance names recorded.
        assert (str(agent.runtime_status), str(agent.lifecycle)) == ("error", "draft")
        assert (agent.workspace, agent.service) == ("", "")
        assert "credential is unreadable" in agent.last_error


def test_reprovision_agent_recreates_service_over_existing_workspace(
    agents_console_tables: None, monkeypatch: Any
) -> None:
    """`reprovisionAgent` destroys the old service and recreates it over the kept workspace."""

    admin = _platform_admin("agt-reprov-admin")
    plain = User.objects.create_user(username="agt-reprov-plain", email="reprov@example.com")
    agent = _provisionable_agent(
        admin, "Rebot", slug="agt-reprov-tpl", workspace="ws-keep", service="svc-old",
        lifecycle="ready", runtime_status="running",
    )
    agent_id = _public_id(agent.sqid)

    calls: list[tuple[Any, ...]] = []

    class _FakeDaemon:
        @classmethod
        def from_settings(cls) -> _FakeDaemon:
            return cls()

        def resolve_template_ref(self, *, name: str, kind: str) -> str:
            return f"ref:{name}"

        def set_secret(self, name: str, value: str) -> None:
            calls.append(("secret", name))

        def destroy_service(self, name: str) -> None:
            calls.append(("destroy_service", name))

        def create_service(
            self, *, template: str, workspace: str, inputs: dict[str, str], start: bool = True, name: str = ""
        ) -> str:
            calls.append(("create_service", template, workspace))
            return "svc-new"

    monkeypatch.setattr(agents_provisioning, "OperatorDaemon", _FakeDaemon)

    reprovision = "mutation($id: ID!){ reprovision_agent(id: $id){ ok message } }"
    assert _execute(console := _schema(), reprovision, {"id": agent_id}, user=plain).errors is not None
    result = _data(_execute(console, reprovision, {"id": agent_id}, user=admin))["reprovision_agent"]

    assert result == {"ok": True, "message": "Recreated service “svc-new”."}
    # The old service is torn down before the recreate over the preserved workspace.
    assert ("destroy_service", "svc-old") in calls
    assert ("create_service", "ref:claude-code", "ws-keep") in calls
    with system_context(reason="test.agents.reprov.verify"):
        agent.refresh_from_db()
        assert (agent.workspace, agent.service, str(agent.lifecycle), str(agent.runtime_status)) == (
            "ws-keep",
            "svc-new",
            "ready",
            "running",
        )


def test_reprovision_agent_failure_clears_destroyed_service_but_keeps_workspace(
    agents_console_tables: None, monkeypatch: Any
) -> None:
    """When the old service is destroyed but the recreate fails, the stale name is cleared.

    The workspace (and its files) is preserved; the service name is blanked so a later
    deprovision doesn't try to tear down a service the daemon already removed (a 409).
    """

    admin = _platform_admin("agt-reprovfail-admin")
    agent = _provisionable_agent(
        admin, "ReDoomed", slug="agt-reprovfail-tpl", workspace="ws-keep", service="svc-old",
        lifecycle="ready", runtime_status="running",
    )
    agent_id = _public_id(agent.sqid)

    destroyed: list[str] = []

    class _FailingDaemon:
        @classmethod
        def from_settings(cls) -> _FailingDaemon:
            return cls()

        def resolve_template_ref(self, *, name: str, kind: str) -> str:
            return f"ref:{name}"

        def set_secret(self, name: str, value: str) -> None:
            pass

        def destroy_service(self, name: str) -> None:
            destroyed.append(name)

        def create_service(
            self, *, template: str, workspace: str, inputs: dict[str, str], start: bool = True, name: str = ""
        ) -> str:
            raise RuntimeError("service recreate failed")

    monkeypatch.setattr(agents_provisioning, "OperatorDaemon", _FailingDaemon)

    result = _data(
        _execute(
            _schema(),
            "mutation($id: ID!){ reprovision_agent(id: $id){ ok message } }",
            {"id": agent_id},
            user=admin,
        )
    )["reprovision_agent"]

    assert result["ok"] is False and "service recreate failed" in result["message"]
    assert destroyed == ["svc-old"]  # destroyed before the recreate failed
    with system_context(reason="test.agents.reprovfail.verify"):
        agent.refresh_from_db()
        # Run state errored; the preserved workspace keeps the lifecycle at READY.
        assert (str(agent.runtime_status), str(agent.lifecycle)) == ("error", "ready")
        # Workspace preserved; the destroyed service name is cleared, not left dangling.
        assert (agent.workspace, agent.service) == ("ws-keep", "")
        assert "service recreate failed" in agent.last_error


def test_provision_agent_refuses_when_inference_credential_has_no_secret(
    agents_console_tables: None, monkeypatch: Any
) -> None:
    """A model-backed agent whose credential yields no secret is refused before any render.

    A placeholder inference credential (empty api_key) would render a service with a bogus
    key (the ANTHROPIC_API_KEY=REPLACE_ME footgun), so the flow refuses up front — no
    lifecycle flip, no daemon work — rather than bringing up an agent that can never authenticate.
    """

    admin = _platform_admin("agt-nokey-admin")
    provider = _provider("agt-nokey", material={"api_key": ""}, name="P")
    with system_context(reason="test.agents.nokey.seed"):
        model = InferenceModel.objects.create(
            provider=provider,
            name="claude-opus-4-8",
        )
    agent = _provisionable_agent(admin, "NoKey", slug="agt-nokey-tpl", model=model)
    agent_id = _public_id(agent.sqid)

    called: list[str] = []

    class _UnusedDaemon:
        @classmethod
        def from_settings(cls) -> _UnusedDaemon:
            called.append("from_settings")
            return cls()

    monkeypatch.setattr(agents_provisioning, "OperatorDaemon", _UnusedDaemon)

    result = _data(
        _execute(
            _schema(),
            "mutation($id: ID!){ provision_agent(id: $id){ ok message } }",
            {"id": agent_id},
            user=admin,
        )
    )["provision_agent"]

    assert result["ok"] is False and "inference credential" in result["message"]
    assert called == []  # refused before constructing the daemon / any render
    with system_context(reason="test.agents.nokey.verify"):
        agent.refresh_from_db()
        # Never flipped to PROVISIONING; the lifecycle stays a fresh DRAFT and nothing rendered.
        assert str(agent.lifecycle) == "draft"
        assert (agent.workspace, agent.service) == ("", "")


def test_agent_inference_credential_override_wins_over_model_chain(agents_console_tables: None) -> None:
    """A per-agent ``inference_credential`` overrides the model's integration credential.

    Pointing the agent at a connected OAuth credential makes inference authenticate with that
    token without touching the model's provider integration — whose own
    credential here is an empty placeholder that otherwise refuses provisioning.
    """

    owner = User.objects.create_user(username="agt-ov-owner", email="ov@example.com")
    provider = _provider("agt-ov-model", backend_class="anthropic", material={"api_key": ""}, name="P")
    oauth_integration = make_integration("agt-ov-oauth", kind=CredentialKind.OAUTH)
    with system_context(reason="test.agents.override.seed"):
        model = InferenceModel.objects.create(
            provider=provider,
            name="claude-opus-4-8",
        )
        # Without an override the model's empty placeholder credential is unusable.
        plain = Agent.objects.create(name="Plain", owner=owner, model=model, runtime_class="claude_code")
        assert plain.inference_secret() == ""
        assert plain.inference_credential_ready() is False

        # The per-agent override points at the connected OAuth credential and wins.
        agent = Agent.objects.create(
            name="Override",
            owner=owner,
            model=model,
            inference_credential=oauth_integration.credential,
            runtime_class="claude_code",
        )
        assert agent.inference_secret() == "token"
        assert agent.inference_credential_ready() is True
        service_inputs = agent.provision_service_inputs()
        assert service_inputs["auth_env"] == (
            f'      ANTHROPIC_AUTH_TOKEN: "${{secret.agent-{agent.sqid}-inference}}"\n'
            f'      CLAUDE_CODE_OAUTH_TOKEN: "${{secret.agent-{agent.sqid}-inference}}"\n'
            '      ANTHROPIC_CUSTOM_HEADERS: "anthropic-beta: oauth-2025-04-20"'
        )
        assert service_inputs["model"] == "claude-opus-4-8"


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
    provider = _provider("agt-chat-provider", name="P")
    with system_context(reason="test.agents.chat.seed"):
        model = InferenceModel.objects.create(
            provider=provider,
            name="anthropic/claude-opus-4-8",
            config={"provider_model": "claude-opus-4-8", "broker_name": "anthropic"},
        )
    agent = _provisionable_agent(
        admin,
        "Chatty",
        slug="agt-chat-tpl",
        service="svc-chat",
        lifecycle="ready",
        runtime_status="running",
        model=model,
    )
    with system_context(reason="test.agents.chat.mcp.seed"):
        server = MCPServer.objects.create(name="notes", url="http://host.docker.internal:8101/mcp/notes/")
        agent.mcp_servers.add(server)
    agent_id = _public_id(agent.sqid)

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
          agent_chat_endpoint(id: $id) { url token expires_at mcp_servers model_handle }
        }
    """
    assert _execute(console := _schema(), query, {"id": agent_id}, user=plain).errors is not None
    endpoint = _data(_execute(console, query, {"id": agent_id}, user=admin))["agent_chat_endpoint"]

    assert endpoint["url"] == "wss://svc-chat.example.test/"
    assert endpoint["token"] == "jwt-route"
    assert endpoint["expires_at"] == "2026-06-15T00:00:00Z"
    assert endpoint["model_handle"] == "claude-opus-4-8"
    assert endpoint["mcp_servers"] == {
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
        "mutation($id: ID!){ agent_chat_endpoint(id: $id){ url } }",
        {"id": _public_id(agent.sqid)},
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
    provider = _provider("agt-session-provider", name="P")
    with system_context(reason="test.agents.session.seed"):
        model = InferenceModel.objects.create(
            provider=provider,
            name="anthropic/claude-opus-4-8",
            config={"provider_model": "claude-opus-4-8", "broker_name": "anthropic"},
        )
    _provisionable_agent(
        admin,
        "Sidekick",
        slug="agt-session-tpl",
        service="svc-side",
        lifecycle="ready",
        runtime_status="running",
        model=model,
    )
    with system_context(reason="test.agents.session.draft.seed"):
        # A draft agent for the same owner is not eligible (not running).
        Agent.objects.create(name="Draft", owner=admin)

    query = """
        query Session($view: JSON!) {
          resolve_session_for_view(view: $view) { agent_name status model_handle }
        }
    """
    view = {"kind": "record", "type": "notes/note", "sqid": "nte_x"}
    session = _data(_execute(console := _schema(), query, {"view": view}, user=admin))["resolve_session_for_view"]

    assert session["agent_name"] == "Sidekick"
    assert session["status"] == "running"
    assert session["model_handle"] == "claude-opus-4-8"

    # A platform admin with no running agent gets null, not an error.
    other = _platform_admin("agt-session-none")
    none_session = _data(_execute(console, query, {"view": view}, user=other))["resolve_session_for_view"]
    assert none_session is None

    # The side chatter is actor-scoped, not admin-scoped: a normal user with no
    # running agent also gets null instead of a permission error.
    viewer = User.objects.create_user(username="agt-session-viewer", email="viewer@example.com")
    viewer_session = _data(_execute(console, query, {"view": view}, user=viewer))["resolve_session_for_view"]
    assert viewer_session is None


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
    agent_id = _public_id(agent.sqid)
    mutation = """
        mutation Prompt($id: ID!, $view: JSON!) { render_agent_prompt(id: $id, view: $view) }
    """
    view = {"kind": "record", "type": "agents/mcp_server", "sqid": str(server.sqid)}

    assert _execute(console := _schema(), mutation, {"id": agent_id, "view": view}, user=plain).errors is not None
    rendered = _data(_execute(console, mutation, {"id": agent_id, "view": view}, user=admin))["render_agent_prompt"]

    assert rendered.startswith("<system_context>") and rendered.endswith("</system_context>")
    assert "record of agents/mcp_server" in rendered
    assert str(server.sqid) in rendered and "Local Notes" in rendered
    assert "MCP tool" in rendered

    # An empty envelope adds nothing.
    empty = _data(_execute(console, mutation, {"id": agent_id, "view": {}}, user=admin))["render_agent_prompt"]
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
            kind=str(CredentialKind.STATIC_TOKEN),
            name="leaky-cred",
            material={"api_key": "SUPER-SECRET-XYZ"},
        )
        view = {"kind": "record", "type": "integrate/credential", "sqid": str(credential.sqid)}
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
            owner, kind=str(CredentialKind.STATIC_TOKEN), name="notes-bearer", material={"api_key": "tok-notes"}
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


def test_mcp_config_resolves_builtin_server_from_settings(
    agents_console_tables: None,
    settings: Any,
) -> None:
    """The built-in Angee MCP server is selected by model config, not seeded URL."""

    settings.ANGEE_BUILTIN_MCP_URL = "http://host.docker.internal:8111/mcp"
    owner = User.objects.create_user(username="agt-builtin-mcp-owner", email="builtin-mcp@example.com")
    with system_context(reason="test.agents.builtin_mcp_config"):
        agent = Agent.objects.create(name="Built-in MCP", owner=owner)
        builtin = MCPServer.objects.create(
            name="angee",
            placement="internal",
            transport="http",
            config={"builtin": "angee"},
        )
        agent.mcp_servers.add(builtin)
        config = agent.mcp_config()

    assert builtin.url == ""
    assert config == {
        "mcpServers": {
            "angee": {"type": "http", "url": "http://host.docker.internal:8111/mcp"},
        },
    }


def test_mcp_actor_verifier_resolves_bearer_to_the_credential_owner(agents_console_tables: None) -> None:
    """The agents bearer verifier maps an MCP-server credential to its owning user.

    Interim model (option A): an agent acts with the identity of the user who owns the
    credential it presents, so it gets that user's notes CRUD with correct attribution.
    An unknown bearer resolves to nothing, so the runtime denies it with no fallback.
    """

    owner = User.objects.create_user(username="agt-verify-owner", email="verify@example.com")
    with system_context(reason="test.agents.mcp_verify"):
        credential = Credential.objects.create_local_credential(
            owner, kind=str(CredentialKind.STATIC_TOKEN), name="mcp-bearer", material={"api_key": "tok-secret"}
        )
        MCPServer.objects.create(name="notes", url="http://x/mcp/notes/", credential=credential)

    actor = resolve_actor("tok-secret")
    assert actor is not None
    assert actor.subject_type == "auth/user"
    assert actor.subject_id == str(owner.pk)  # the agent runs as the credential's owner
    assert resolve_actor("wrong-token") is None
    assert resolve_actor("") is None


def test_provision_service_inputs_credential_drives_auth_env(agents_console_tables: None) -> None:
    """The provider backend maps credential kind to service auth env."""

    owner = User.objects.create_user(username="agt-svci-owner", email="svci@example.com")
    static_provider = _provider("agt-svc-static", backend_class="anthropic", name="S")
    oauth_provider = _provider("agt-svc-oauth", backend_class="anthropic", kind=CredentialKind.OAUTH, name="O")
    with system_context(reason="test.agents.provision_inputs.service"):
        static_model = InferenceModel.objects.create(
            provider=static_provider,
            name="claude-3",
        )
        static_agent = Agent.objects.create(
            name="Static", owner=owner, model=static_model, runtime_class="claude_code"
        )
        static_inputs = static_agent.provision_service_inputs()

        oauth_model = InferenceModel.objects.create(
            provider=oauth_provider,
            name="claude-opus-4-8",
        )
        oauth_agent = Agent.objects.create(name="OAuth", owner=owner, model=oauth_model, runtime_class="claude_code")
        oauth_inputs = oauth_agent.provision_service_inputs()

    assert static_inputs == {
        "auth_env": f'      ANTHROPIC_API_KEY: "${{secret.agent-{static_agent.sqid}-inference}}"',
        "model": "claude-3",
    }
    assert oauth_inputs["auth_env"] == (
        f'      ANTHROPIC_AUTH_TOKEN: "${{secret.agent-{oauth_agent.sqid}-inference}}"\n'
        f'      CLAUDE_CODE_OAUTH_TOKEN: "${{secret.agent-{oauth_agent.sqid}-inference}}"\n'
        '      ANTHROPIC_CUSTOM_HEADERS: "anthropic-beta: oauth-2025-04-20"'
    )
    assert oauth_inputs["model"] == "claude-opus-4-8"


def test_provision_service_inputs_opencode_refuses_oauth_credential(agents_console_tables: None) -> None:
    """OpenCode OAuth is off by default (no plugin in the image), so the pairing is refused."""

    oauth_provider = _provider("agt-oc-oauth", backend_class="anthropic", kind=CredentialKind.OAUTH, name="O")
    owner = oauth_provider.owner
    with system_context(reason="test.agents.provision_inputs.opencode_oauth"):
        model = InferenceModel.objects.create(provider=oauth_provider, name="anthropic/claude-opus-4-8")
        agent = Agent.objects.create(name="OpenCode OAuth", owner=owner, model=model, runtime_class="opencode")
        assert agent.inference_credential_ready() is False
        with pytest.raises(ValueError, match="cannot use a"):
            agent.provision_service_inputs()


@override_settings(ANGEE_OPENCODE_OAUTH_ENABLED=True)
def test_provision_service_inputs_opencode_oauth_when_enabled(agents_console_tables: None) -> None:
    """With the opt-in on, OpenCode OAuth syncs a base64 auth.json and the decode env var."""

    oauth_provider = _provider(
        "agt-oc-oauth-on",
        backend_class="anthropic",
        kind=CredentialKind.OAUTH,
        material={"access_token": "token", "refresh_token": "refresh"},
        name="O",
    )
    owner = oauth_provider.owner
    with system_context(reason="test.agents.provision_inputs.opencode_oauth_on"):
        model = InferenceModel.objects.create(provider=oauth_provider, name="anthropic/claude-opus-4-8")
        agent = Agent.objects.create(name="OC OAuth On", owner=owner, model=model, runtime_class="opencode")
        assert agent.inference_credential_ready() is True
        inputs = agent.provision_service_inputs()
        payload = agent.provision_inference_secret()

    # The service env carries only the base64 blob placeholder (the JSON never appears here).
    assert inputs["auth_env"] == f'      ANGEE_OPENCODE_AUTH_B64: "${{secret.agent-{agent.sqid}-inference}}"'
    # The synced secret is base64 of OpenCode's auth.json for the Anthropic OAuth credential.
    decoded = json.loads(base64.b64decode(payload))
    assert decoded == {"anthropic": {"type": "oauth", "refresh": "refresh", "access": "token", "expires": 0}}


@override_settings(ANGEE_OPENCODE_OAUTH_ENABLED=True)
def test_provision_service_inputs_opencode_oauth_requires_refresh_token(agents_console_tables: None) -> None:
    """An OAuth credential with no refresh token can't be refreshed in-container, so it's refused."""

    oauth_provider = _provider(
        "agt-oc-oauth-norefresh",
        backend_class="anthropic",
        kind=CredentialKind.OAUTH,
        material={"access_token": "token"},
        name="O",
    )
    owner = oauth_provider.owner
    with system_context(reason="test.agents.provision_inputs.opencode_oauth_norefresh"):
        model = InferenceModel.objects.create(provider=oauth_provider, name="anthropic/claude-opus-4-8")
        agent = Agent.objects.create(name="OC No Refresh", owner=owner, model=model, runtime_class="opencode")
        assert agent.inference_credential_ready() is False
        with pytest.raises(ValueError, match="cannot use a"):
            agent.provision_service_inputs()


def test_provision_service_inputs_workspace_only_runtime_skips_service_auth(agents_console_tables: None) -> None:
    """A model-backed workspace-only (``none``) agent is ready and renders no service auth.

    The readiness gate allows a workspace-only runtime regardless of credential kind, so the
    plan builder must agree: it emits no ``auth_env`` and syncs no inference secret (there is
    no service container to consume them) rather than raising at plan time.
    """

    oauth_provider = _provider("agt-none-oauth", backend_class="anthropic", kind=CredentialKind.OAUTH, name="O")
    owner = oauth_provider.owner
    with system_context(reason="test.agents.provision_inputs.workspace_only"):
        model = InferenceModel.objects.create(provider=oauth_provider, name="anthropic/claude-opus-4-8")
        # runtime_class defaults to "none" (workspace-only) — an OAuth credential no service
        # runtime could consume must not strand provisioning.
        agent = Agent.objects.create(name="Workspace Only", owner=owner, model=model)
        assert agent.inference_credential_ready() is True
        inputs = agent.provision_service_inputs()
        assert agent.provision_inference_secret() == ""

    assert "auth_env" not in inputs


def test_claude_code_service_inputs_use_provider_model_name(agents_console_tables: None) -> None:
    """Claude Code talks to Anthropic directly, so broker aliases render as provider ids."""

    owner = User.objects.create_user(username="agt-cc-model-agent-owner", email="cc-model@example.com")
    provider = _provider("agt-cc-model", backend_class="anthropic", name="Anthropic")
    with system_context(reason="test.agents.provision_inputs.claude_code_model.seed"):
        model = InferenceModel.objects.create(
            provider=provider,
            name="anthropic/claude-opus-4-8",
            config={"provider_model": "claude-opus-4-8", "broker_name": "anthropic"},
        )
    agent = _provisionable_agent(
        owner,
        "Claude Code",
        slug="agt-cc-model-tpl",
        model=model,
    )

    with system_context(reason="test.agents.provision_inputs.claude_code_model"):
        assert agent.provision_service_inputs()["model"] == "claude-opus-4-8"


def test_opencode_service_inputs_keep_selected_broker_model(agents_console_tables: None) -> None:
    """OpenCode expects the provider/model handle, so the selected catalogue row renders as-is."""

    owner = User.objects.create_user(username="agt-oc-model-agent-owner", email="oc-model@example.com")
    provider = _provider("agt-oc-model", backend_class="anthropic", name="Anthropic")
    with system_context(reason="test.agents.provision_inputs.opencode_model"):
        model = InferenceModel.objects.create(
            provider=provider,
            name="anthropic/claude-opus-4-8",
            config={"provider_model": "claude-opus-4-8", "broker_name": "anthropic"},
        )
        agent = Agent.objects.create(name="OpenCode", owner=owner, runtime_class="opencode", model=model)

        assert agent.provision_service_inputs()["model"] == "anthropic/claude-opus-4-8"


def _provisionable_agent(owner: Any, name: str, *, slug: str, **agent_fields: Any) -> Any:
    """Seed an agent with a workspace template and a service runtime for provisioning tests.

    Defaults to the ``claude_code`` runtime (overridable via ``agent_fields``); the runtime
    declares the service template the daemon resolves by name. ``agent_fields`` set the
    starting instance state (e.g. ``workspace``/``service``/``lifecycle``/``runtime_status``)
    so a reprovision/deprovision test can begin from an already-provisioned row.
    """

    agent_fields.setdefault("runtime_class", "claude_code")
    vcs = _vcs_bridge(slug, config={"stub_repos": REPOS})
    vcs.discover_repositories()
    with system_context(reason="test.agents.provisionable.seed"):
        repository = Repository.objects.get(name="acme/widgets")
        source = Source.objects.create(repository=repository, kind="template", path="templates")
        workspace_template = Template.objects.create(
            source=source, kind="workspace", name="agent-default", path="workspaces/agent-default"
        )
        return Agent.objects.create(
            name=name,
            owner=owner,
            workspace_template=workspace_template,
            **agent_fields,
        )


def _seed_agent_and_skills(owner: Any) -> tuple[Any, Any, Any]:
    """Create a skill source with two skills and an owned agent (all elevated)."""

    vcs = _vcs_bridge("agt-m2m", config={"stub_repos": REPOS})
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


def _public_id(sqid: str) -> str:
    """Return the public id for a console node."""

    return str(sqid)
