"""Tests for the agents console GraphQL surface.

The agents console references iam + integrate types, so these build one ``console``
schema folding the iam, integrate, and agents addon parts (the shape the composer
assembles) and run over the shared concrete test tables.
"""

from __future__ import annotations

import base64
import importlib
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.models.signals import pre_delete
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from rebac import (
    RelationshipTuple,
    actor_context,
    current_actor,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.agents import signals as agent_signals
from angee.agents.context import render_view_context
from angee.agents.models import MCPPlacement
from angee.agents.testing.models import (
    Agent,
    AgentSession,
    AgentTurn,
    InferenceModel,
    InferenceProvider,
    MCPServer,
    Skill,
)
from angee.base.transitions import TransitionNotAllowed
from angee.graphql.deletion import DeletePreview
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.credentials import CredentialKind
from angee.jobs.locks import task_lock
from angee.operator.daemon import OperatorDaemonConflict, OperatorDaemonNotFound, OperatorInstanceKind, WorkspaceStatus
from tests.conftest import (
    Credential,
    ExternalAccount,
    Integration,
    OAuthClient,
    SchemaAddon,
    Vendor,
    execute_schema,
    make_integration,
)
from tests.conftest import create_platform_admin as _platform_admin
from tests.conftest import result_data as _data
from tests.test_agents import _provider
from tests.test_integrate_vcs import REPOS, Repository, Source, Template, _vcs_bridge

User = get_user_model()


# Imported only now that every agents concrete is registered.
agents_provisioning = importlib.import_module("angee.agents.provisioning")
agents_schema = importlib.import_module("angee.agents.schema")
iam_schema = importlib.import_module("angee.iam.schema")
integrate_schema = importlib.import_module("angee.integrate.schema")

_DELETE_AGENT_PREVIEW = """
    mutation DeleteAgent($id: ID!, $confirm: Boolean! = false) {
      delete_agent(id: $id, confirm: $confirm) {
        total_deleted_count
        has_blockers
        refusals
        deleted { label count }
        blocked { label count }
        root {
          object_id object_label
          children {
            label object_id object_label
            children { object_id object_label }
          }
        }
      }
    }
"""


def test_agent_hasura_insert_accepts_enum_member_names(composed_tables: None) -> None:
    """A console read→write round-trip posts choices columns by member NAME.

    Reads project ``TextChoices`` enums serialized by name (``"PYDANTIC"``)
    while the Hasura insert input carries ``String``; the write backend
    translates the name onto the stored value so the console's create form —
    which sources its vocabulary from the read surface — can insert what it
    read. Regression for the first console-created agent failing full_clean
    with ``"PYDANTIC" must be a subclass of AgentRuntimeImpl``.
    """

    admin = _platform_admin("agt-enum-admin")
    console = _schema()

    created = _data(
        _execute(
            console,
            """
            mutation CreateAgent($owner: ID!) {
              insert_agents_one(
                object: {name: "InProcess", owner: $owner, runtime_class: "PYDANTIC"}
              ) {
                id
                runtime_class
                expects_service
                lifecycle
              }
            }
            """,
            {"owner": str(admin.sqid)},
            user=admin,
        )
    )["insert_agents_one"]
    assert created["runtime_class"] == "PYDANTIC"
    assert created["expects_service"] is False
    assert created["lifecycle"] == "DRAFT"
    with system_context(reason="test.agents.enum_wire.verify"):
        row = Agent.objects.get(name="InProcess")
        assert row.runtime_class == "pydantic"
        assert row.lifecycle == "draft"


@pytest.mark.parametrize(
    ("runtime_class", "runtime_status", "service", "expected", "expects_service"),
    [
        ("claude_code", "running", "", False, True),
        ("claude_code", "running", "agent-service", True, True),
        ("opencode", "running", "", False, True),
        ("opencode", "running", "agent-service", True, True),
        ("pydantic", "running", "", True, False),
        ("pydantic", "stopped", "", False, False),
        ("claude_code", "stopped", "agent-service", False, True),
        ("none", "running", "", False, False),
    ],
)
def test_agent_chat_readiness_requires_its_runtime_transport(
    runtime_class: str, runtime_status: str, service: str, expected: bool, expects_service: bool
) -> None:
    """Only in-process runtimes may chat without a rendered operator service."""

    agent = Agent(runtime_class=runtime_class, runtime_status=runtime_status, service=service)
    assert agent.can_chat is expected
    assert agent.expects_service is expects_service


def test_agent_hasura_insert_update_and_delete(composed_tables: None) -> None:
    """Agent row writes use the generated Hasura mutation roots."""

    admin = _platform_admin("agt-hasura-admin")
    console = _schema()

    created = _data(
        _execute(
            console,
            """
            mutation CreateAgent($owner: ID!) {
              insert_agents_one(object: {name: "Composer", owner: $owner}) {
                id
                name
                lifecycle
                is_template
                can_chat
                expects_service
                can_provision
                can_deprovision
                can_delete
                owner { username }
              }
            }
            """,
            {"owner": str(admin.sqid)},
            user=admin,
        )
    )["insert_agents_one"]
    assert created == {
        "id": created["id"],
        "name": "Composer",
        "lifecycle": "DRAFT",
        "is_template": False,
        "can_chat": False,
        "expects_service": False,
        "can_provision": True,
        "can_deprovision": False,
        "can_delete": True,
        "owner": {"username": "agt-hasura-admin"},
    }

    rejected_insert = _execute(
        console,
        """
        mutation SeedState($owner: ID!) {
          insert_agents_one(object: {name: "Bypass", owner: $owner, lifecycle: "ready"}) { id }
        }
        """,
        {"owner": str(admin.sqid)},
        user=admin,
    )
    assert rejected_insert.errors
    assert "lifecycle" in rejected_insert.errors[0].message

    rejected = _execute(
        console,
        """
        mutation ResetState($id: String!) {
          update_agents_by_pk(pk_columns: {id: $id}, _set: {lifecycle: "deprovisioned"}) { id }
        }
        """,
        {"id": created["id"]},
        user=admin,
    )
    assert rejected.errors
    assert "lifecycle" in rejected.errors[0].message
    with system_context(reason="test.agents.hasura_update.transition"):
        agent = Agent.objects.get(sqid=created["id"])
        assert str(agent.lifecycle) == "draft"
        agent.mark_deprovisioned()

    updated = _data(
        _execute(
            console,
            """
            mutation Rename($id: String!) {
              update_agents_by_pk(pk_columns: {id: $id}, _set: {name: "Renamed"}) {
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


def test_agent_resource_exposes_authored_delete_preview() -> None:
    """Final resource metadata enables the shared UI delete action for agents."""

    resource = next(item for item in _schema().angee_resources if item.model_label == "agents.Agent")
    assert resource.roots.delete_preview_name == "delete_agent"
    assert resource.type_names.delete_payload == "DeletePreview"
    assert "deletePreview" in resource.capabilities
    assert resource.as_wire(schema_name="console")["roots"]["deletePreview"] == "delete_agent"


def test_agent_delete_preview_hides_other_users_transcripts_and_confirms(composed_tables: None) -> None:
    """An owner sees cascade counts but only readable session and turn identities."""

    owner = User.objects.create_user(username="agt-preview-owner")
    other = User.objects.create_user(username="agt-preview-other")
    with system_context(reason="test.agents.delete_preview.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner, lifecycle="deprovisioned")
        visible_session = AgentSession.objects.create(agent=agent, owner=owner, title="Own conversation")
        hidden_session = AgentSession.objects.create(agent=agent, owner=other, title="Secret conversation")
        visible_turn = AgentTurn.objects.create(
            session=visible_session,
            index=1,
            prompt="Own prompt",
            status="completed",
        )
        hidden_turn = AgentTurn.objects.create(
            session=hidden_session,
            index=1,
            prompt="Secret prompt",
            status="completed",
        )
    console = _schema()
    preview = _data(_execute(console, _DELETE_AGENT_PREVIEW, {"id": str(agent.sqid)}, user=owner))["delete_agent"]
    assert preview["has_blockers"] is False
    assert preview["blocked"] == []
    assert preview["refusals"] == []
    deleted = {group["label"]: group["count"] for group in preview["deleted"]}
    assert deleted["agents"] == 1
    assert deleted["agent sessions"] == deleted["agent turns"] == 2
    assert preview["total_deleted_count"] == sum(deleted.values())
    assert preview["root"]["object_id"] == str(agent.sqid)
    groups = {group["label"]: group for group in preview["root"]["children"]}
    for label, visible in (("agent sessions", visible_session), ("agent turns", visible_turn)):
        group = groups[label]
        assert group["object_label"] == f"2 {label}"
        assert group["children"] == [
            {"object_id": str(visible.sqid), "object_label": str(visible)},
            {"object_id": None, "object_label": "1 more records"},
        ]
    serialized = json.dumps(preview)
    for secret in (str(hidden_session.sqid), str(hidden_turn.sqid), "Secret conversation", "Secret prompt"):
        assert secret not in serialized
    with system_context(reason="test.agents.delete_preview.verify_preview"):
        assert Agent.objects.filter(pk=agent.pk).exists()
        assert AgentSession.objects.filter(agent=agent).count() == 2
        assert AgentTurn.objects.filter(session__agent=agent).count() == 2
    confirmed = _data(
        _execute(
            console,
            _DELETE_AGENT_PREVIEW,
            {"id": str(agent.sqid), "confirm": True},
            user=owner,
        )
    )["delete_agent"]
    assert confirmed == preview
    with system_context(reason="test.agents.delete_preview.verify_confirm"):
        assert not Agent.objects.filter(pk=agent.pk).exists()
        assert not AgentSession.objects.filter(pk__in=[visible_session.pk, hidden_session.pk]).exists()
        assert not AgentTurn.objects.filter(pk__in=[visible_turn.pk, hidden_turn.pk]).exists()


@pytest.mark.parametrize("confirm", [False, True])
def test_agent_delete_preview_reports_provisioned_blocker(composed_tables: None, confirm: bool) -> None:
    """A provisioned agent reports its owning refusal without attempting deletion."""

    owner = User.objects.create_user(username="agt-provisioned-preview-owner")
    with system_context(reason="test.agents.provisioned_preview.seed"):
        agent = Agent.objects.create(name="Provisioned", owner=owner, lifecycle="ready", service="svc-agent")
    preview = _data(
        _execute(
            _schema(),
            _DELETE_AGENT_PREVIEW,
            {"id": str(agent.sqid), "confirm": confirm},
            user=owner,
        )
    )["delete_agent"]
    assert preview["has_blockers"] is True
    assert preview["blocked"] == []
    assert preview["refusals"] == ["Deprovision this agent before deleting it."]
    with system_context(reason="test.agents.provisioned_preview.verify"):
        assert Agent.objects.filter(pk=agent.pk).exists()


@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("status", ["running", "awaiting_approval"])
def test_agent_delete_preview_reports_hidden_active_turn_blockers(
    composed_tables: None,
    confirm: bool,
    status: str,
) -> None:
    """Agent and turn refusals are readable even when the active transcript is hidden."""

    owner = User.objects.create_user(username="agt-active-preview-owner")
    other = User.objects.create_user(username="agt-active-preview-other")
    with system_context(reason="test.agents.active_preview.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner)
        session = AgentSession.objects.create(agent=agent, owner=other, title="Secret conversation")
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Secret prompt", status=status)
    preview = _data(
        _execute(
            _schema(),
            _DELETE_AGENT_PREVIEW,
            {"id": str(agent.sqid), "confirm": confirm},
            user=owner,
        )
    )["delete_agent"]
    assert preview["has_blockers"] is True
    assert preview["blocked"] == []
    messages = set(preview["refusals"])
    assert messages == {
        "Stop all active turns before deleting this agent.",
        "An agent turn is still running or awaiting approval; stop it first.",
    }
    serialized = json.dumps(preview)
    for secret in (str(session.sqid), str(turn.sqid), "Secret conversation", "Secret prompt"):
        assert secret not in serialized
    with system_context(reason="test.agents.active_preview.verify"):
        assert Agent.objects.filter(pk=agent.pk).exists()
        assert AgentSession.objects.filter(pk=session.pk).exists()
        assert AgentTurn.objects.filter(pk=turn.pk, status=status).exists()


def test_agent_delete_preview_has_constant_query_count_across_sessions(composed_tables: None) -> None:
    """Collecting 1 or 50 private active sessions adds no per-session blocker queries."""

    owner = User.objects.create_user(username="agt-preview-budget-owner")
    other = User.objects.create_user(username="agt-preview-budget-session-owner")
    agents = []
    with system_context(reason="test.agents.preview_budget.seed"):
        for size in (1, 50):
            agent = Agent.objects.create(name=f"Assistant {size}", owner=owner, lifecycle="deprovisioned")
            agents.append(agent)
            for _ in range(size):
                session = AgentSession.objects.create(agent=agent, owner=other)
                AgentTurn.objects.create(session=session, index=1, prompt="Private question", status="running")
    console = _schema()
    _data(_execute(console, _DELETE_AGENT_PREVIEW, {"id": str(agents[0].sqid)}, user=owner))
    counts = []
    for agent in agents:
        with CaptureQueriesContext(connection) as captured:
            result = _execute(console, _DELETE_AGENT_PREVIEW, {"id": str(agent.sqid)}, user=owner)
            preview = _data(result)["delete_agent"]
        assert preview["blocked"] == []
        assert preview["refusals"] == [
            "Stop all active turns before deleting this agent.",
            "An agent turn is still running or awaiting approval; stop it first.",
        ]
        counts.append(len(captured))
    assert counts[0] == counts[1], counts


@pytest.mark.parametrize("surface", ["agent", "user"])
def test_graphql_delete_receivers_refuse_after_preview_race(
    composed_tables: None, monkeypatch: pytest.MonkeyPatch, surface: str,
) -> None:
    """Authored and IAM Hasura deletes return readable late refusals and retain all rows."""

    owner = User.objects.create_user(username="agt-preview-race-owner")
    admin = _platform_admin("agt-preview-race-admin")
    with system_context(reason="test.agents.preview_race.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner, lifecycle="deprovisioned")
        session = AgentSession.objects.create(agent=agent, owner=owner)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Retained", status="running")
        principal = agent.user
    from_instance = DeletePreview.from_instance

    def unblocked_preview(instance: Any) -> DeletePreview:
        preview = from_instance(instance)
        preview.refusals = []
        preview.has_blockers = False
        return preview

    monkeypatch.setattr(DeletePreview, "from_instance", unblocked_preview)
    mutation = (
        _DELETE_AGENT_PREVIEW if surface == "agent"
        else "mutation Delete($id: String!) { delete_users_by_pk(id: $id) { id } }"
    )
    result = _execute(
        _schema(), mutation,
        {"id": str(agent.sqid), "confirm": True} if surface == "agent" else {"id": str(owner.sqid)},
        user=admin,
    )
    message = "An agent turn is still running or awaiting approval; stop it first."
    if surface == "agent":
        preview = _data(result)["delete_agent"]
        assert preview["has_blockers"] is True
        assert preview["refusals"] == [message]
        assert preview["blocked"] == []
    else:
        assert result.errors is not None
        assert result.errors[0].message == message
        assert result.errors[0].extensions == {"code": "BAD_USER_INPUT"}
    with system_context(reason="test.agents.preview_race.verify"):
        assert User.objects.filter(pk=owner.pk).exists()
        assert User.objects.filter(pk=principal.pk, is_active=True).exists()
        assert Agent.objects.filter(pk=agent.pk).exists()
        assert AgentSession.objects.filter(pk=session.pk).exists()
        assert AgentTurn.objects.filter(pk=turn.pk, status="running").exists()


def test_agent_delete_capability_has_constant_query_count(composed_tables: None) -> None:
    """A narrow can_delete selection batches active turns and lifecycle facts."""

    owner = User.objects.create_user(username="agt-delete-budget-owner")
    other = User.objects.create_user(username="agt-delete-budget-session-owner")
    with system_context(reason="test.agents.delete_capability.seed"):
        idle = Agent.objects.create(name="A idle", owner=owner)
        AgentSession.objects.create(agent=idle, owner=other)
        active = Agent.objects.create(name="B active", owner=owner)
        session = AgentSession.objects.create(agent=active, owner=other)
        AgentTurn.objects.create(session=session, index=1, prompt="Private question", status="running")
        Agent.objects.create(name="C rendered", owner=owner, lifecycle="ready", service="agent-service")
        waiting = Agent.objects.create(name="D waiting", owner=owner)
        session = AgentSession.objects.create(agent=waiting, owner=other)
        AgentTurn.objects.create(session=session, index=1, prompt="Private approval", status="awaiting_approval")
    query = """
        query Capabilities($limit: Int!) {
          agents(limit: $limit, order_by: {name: asc}) { can_delete }
        }
    """
    console = _schema()
    _data(_execute(console, query, {"limit": 1}, user=owner))
    counts = []
    for size in (1, 4):
        with CaptureQueriesContext(connection) as captured:
            rows = _data(_execute(console, query, {"limit": size}, user=owner))["agents"]
        assert [row["can_delete"] for row in rows] == [True, False, False, False][:size]
        counts.append(len(captured))
    assert counts[0] == counts[1], counts


def test_a_shared_agents_owner_shows_their_name_without_restricted_fields(composed_tables: None) -> None:
    """The non-null owner resolves for a reader who is not a people manager, restricted fields withheld."""

    owner = User.objects.create_user(username="agt-owner-name", email="agt-owner@example.com", first_name="Owen")
    reader = User.objects.create_user(username="agt-owner-reader")
    with system_context(reason="test.agents.owner_name.seed"):
        agent = Agent.objects.create(name="Shared agent", owner=owner)
    write_relationships([RelationshipTuple(to_object_ref(agent), "reader", to_subject_ref(reader))])

    query = "{ agents { name owner { display_name username email is_active } } }"
    rows = _data(_execute(_schema(), query, user=reader))

    assert rows["agents"] == [{
        "name": "Shared agent",
        "owner": {"display_name": "Owen", "username": None, "email": None, "is_active": None},
    }]


def test_agent_hasura_delete_blocks_rendered_agents(composed_tables: None) -> None:
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


def test_owner_deletes_agent_with_idle_sessions_from_several_users(composed_tables: None) -> None:
    """Agent delete authority derives to private transcripts without granting read."""

    owner = User.objects.create_user(username="agt-cascade-owner")
    others = [User.objects.create_user(username=f"agt-cascade-user-{index}") for index in range(2)]
    with system_context(reason="test.agents.cascade.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner, lifecycle="deprovisioned")
        sessions = [
            AgentSession.objects.create(agent=agent, owner=user, title=f"Private {user.username}")
            for user in (owner, *others)
        ]
        turns = [
            AgentTurn.objects.create(session=session, index=index, prompt=f"Private {status}", status=status)
            for session in sessions
            for index, status in enumerate(("completed", "failed", "canceled", "pending"), 1)
        ]
        unrelated = Agent.objects.create(name="Unrelated", owner=others[0])
        unrelated_session = AgentSession.objects.create(agent=unrelated, owner=others[0])
        unrelated_turn = AgentTurn.objects.create(session=unrelated_session, index=1, prompt="Keep", status="completed")
    with actor_context(owner):
        assert Agent.objects.get(pk=agent.pk).can_delete
        assert not AgentSession.objects.filter(pk__in=[session.pk for session in sessions[1:]]).exists()
        assert not AgentTurn.objects.filter(session_id__in=[session.pk for session in sessions[1:]]).exists()
        assert AgentSession.objects.get(pk=sessions[0].pk).has_access("delete")
    console = _schema()
    before = _data(_execute(
        console,
        "{ agents { can_delete } agent_sessions { id title } agent_turns { id prompt } }",
        user=owner,
    ))
    assert before["agents"] == [{"can_delete": True}]
    assert before["agent_sessions"] == [{"id": str(sessions[0].sqid), "title": f"Private {owner.username}"}]
    assert {row["id"] for row in before["agent_turns"]} == {
        str(turn.sqid) for turn in turns if turn.session_id == sessions[0].pk
    }
    deleted = _data(_execute(
        console,
        "mutation Delete($id: String!) { delete_agents_by_pk(id: $id) { id name } }",
        {"id": str(agent.sqid)},
        user=owner,
    ))["delete_agents_by_pk"]
    assert deleted == {"id": str(agent.sqid), "name": "Assistant"}
    with system_context(reason="test.agents.cascade.verify"):
        assert not Agent.objects.filter(pk=agent.pk).exists()
        assert not AgentSession.objects.filter(pk__in=[session.pk for session in sessions]).exists()
        assert not AgentTurn.objects.filter(pk__in=[turn.pk for turn in turns]).exists()
        assert Agent.objects.filter(pk=unrelated.pk).exists()
        assert AgentSession.objects.filter(pk=unrelated_session.pk).exists()
        assert AgentTurn.objects.filter(pk=unrelated_turn.pk).exists()


@pytest.mark.parametrize("delete_path", ["instance", "queryset", "session"])
def test_agent_transcript_delete_keeps_owner_authority_throughout(
    composed_tables: None, delete_path: str,
) -> None:
    """Private sessions and turns delete through their agent without any elevation."""

    owner = User.objects.create_user(username="agt-cascade-binding-owner")
    other = User.objects.create_user(username="agt-cascade-binding-session-owner")
    with system_context(reason="test.agents.cascade_binding.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner, lifecycle="deprovisioned")
        session = AgentSession.objects.create(agent=agent, owner=other)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Private", status="completed")
    seen: set[type[models.Model]] = set()

    def check_authority(sender: type[models.Model], instance: Any, **kwargs: Any) -> None:
        assert current_actor() == to_subject_ref(owner)
        assert instance.effective_actor() == (to_subject_ref(owner), False)
        seen.add(sender)

    for model in (Agent, AgentSession, AgentTurn):
        pre_delete.connect(check_authority, sender=model)
    try:
        with actor_context(owner):
            if delete_path == "queryset":
                Agent.objects.filter(pk=agent.pk).delete()
            elif delete_path == "session":
                AgentSession.objects.with_action("delete").get(pk=session.pk).delete()
            else:
                Agent.objects.get(pk=agent.pk).delete()
    finally:
        for model in (Agent, AgentSession, AgentTurn):
            pre_delete.disconnect(check_authority, sender=model)
    assert seen == ({AgentSession, AgentTurn} if delete_path == "session" else {Agent, AgentSession, AgentTurn})
    with system_context(reason="test.agents.cascade_binding.verify"):
        assert Agent.objects.filter(pk=agent.pk).exists() == (delete_path == "session")
        assert not AgentSession.objects.filter(pk=session.pk).exists()
        assert not AgentTurn.objects.filter(pk=turn.pk).exists()


@pytest.mark.parametrize("actor", ["owner", "admin"])
def test_agent_owner_or_admin_deletes_a_session_directly(composed_tables: None, actor: str) -> None:
    """Session deletion derives through the agent and retains the platform admin arm."""

    owner = User.objects.create_user(username="agt-direct-session-owner")
    admin = _platform_admin("agt-direct-session-admin")
    with system_context(reason="test.agents.direct_session_delete.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner)
        session = AgentSession.objects.create(agent=agent, owner=owner)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Question", status="completed")
    console = _schema()
    delete_session = "mutation Delete($id: String!) { delete_agent_sessions_by_pk(id: $id) { id } }"
    deleted = _data(_execute(
        console, delete_session, {"id": str(session.sqid)}, user=owner if actor == "owner" else admin,
    ))["delete_agent_sessions_by_pk"]
    assert deleted == {"id": str(session.sqid)}
    with system_context(reason="test.agents.direct_session_delete.verify"):
        assert Agent.objects.filter(pk=agent.pk).exists()
        assert not AgentSession.objects.filter(pk=session.pk).exists()
        assert not AgentTurn.objects.filter(pk=turn.pk).exists()


def test_session_owner_without_agent_ownership_cannot_delete_transcripts(composed_tables: None) -> None:
    """Session write permission grants neither session nor turn deletion."""

    owner = User.objects.create_user(username="agt-session-agent-owner")
    other = User.objects.create_user(username="agt-session-only-owner")
    with system_context(reason="test.agents.session_owner_delete.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner)
        session = AgentSession.objects.create(agent=agent, owner=other)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Question", status="completed")
    with actor_context(other):
        for model, pk in ((AgentSession, session.pk), (AgentTurn, turn.pk)):
            target = model.objects.get(pk=pk)
            assert target.has_access("write")
            assert not target.has_access("delete")
            with pytest.raises(PermissionDenied):
                target.delete()
    refused = _execute(
        _schema(),
        "mutation Delete($id: String!) { delete_agent_sessions_by_pk(id: $id) { id } }",
        {"id": str(session.sqid)}, user=other,
    )
    assert refused.errors is not None
    assert refused.errors[0].extensions == {"code": "PERMISSION_DENIED"}
    with system_context(reason="test.agents.session_owner_delete.verify"):
        assert Agent.objects.filter(pk=agent.pk).exists()
        assert AgentSession.objects.filter(pk=session.pk).exists()
        assert AgentTurn.objects.filter(pk=turn.pk).exists()


@pytest.mark.parametrize("status", ["running", "awaiting_approval"])
def test_agent_delete_refuses_active_turns_in_unreadable_sessions(composed_tables: None, status: str) -> None:
    """The projection and delete verb refuse all active work without disclosing its transcript."""

    owner = User.objects.create_user(username="agt-active-agent-owner")
    other = User.objects.create_user(username="agt-active-agent-session-owner")
    with system_context(reason="test.agents.active_agent_delete.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner)
        # Turn execution owns eligibility, even if the session projection is stale.
        session = AgentSession.objects.create(agent=agent, owner=other)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Private question", status=status)
    with actor_context(owner):
        target = Agent.objects.get(pk=agent.pk)
        assert not target.can_delete
        assert target.delete_blocker() == "Stop all active turns before deleting this agent."
        with pytest.raises(ValidationError, match="An agent turn is still running or awaiting approval; stop it first"):
            target.delete()
        assert not target.is_sudo()
    console = _schema()
    query = "{ agents { can_delete } agent_sessions { id title } agent_turns { id prompt } }"
    assert _data(_execute(console, query, user=owner)) == {
        "agents": [{"can_delete": False}], "agent_sessions": [], "agent_turns": [],
    }
    mutation = "mutation Delete($id: String!) { delete_agents_by_pk(id: $id) { id } }"
    refused = _execute(console, mutation, {"id": str(agent.sqid)}, user=owner)
    assert refused.errors is not None
    assert refused.errors[0].extensions == {"code": "BAD_USER_INPUT"}
    assert "Stop all active turns before deleting this agent." in refused.errors[0].message
    assert "An agent turn is still running or awaiting approval; stop it first." in refused.errors[0].message
    with system_context(reason="test.agents.active_agent_delete.verify_and_stop"):
        assert Agent.objects.filter(pk=agent.pk).exists()
        assert AgentSession.objects.filter(pk=session.pk).exists()
        assert AgentTurn.objects.filter(pk=turn.pk, status=status).exists()
        turn.mark_canceled()
    with actor_context(owner):
        assert Agent.objects.get(pk=agent.pk).can_delete
    assert _data(_execute(console, query, user=owner)) == {
        "agents": [{"can_delete": True}], "agent_sessions": [], "agent_turns": [],
    }
    assert _data(_execute(console, mutation, {"id": str(agent.sqid)}, user=owner))["delete_agents_by_pk"] == {
        "id": str(agent.sqid),
    }
    with system_context(reason="test.agents.active_agent_delete.verify_deleted"):
        assert not Agent.objects.filter(pk=agent.pk).exists()
        assert not AgentSession.objects.filter(pk=session.pk).exists()
        assert not AgentTurn.objects.filter(pk=turn.pk).exists()


@pytest.mark.parametrize("relation", ["reader", "editor"])
@pytest.mark.parametrize(
    "operation",
    [
        "mutation Delete($id: String!) { delete_agents_by_pk(id: $id) { id } }",
        "mutation Delete($id: ID!) { delete_agent(id: $id) { total_deleted_count } }",
        "mutation Delete($id: ID!) { delete_agent(id: $id, confirm: true) { total_deleted_count } }",
    ],
)
def test_agent_delete_denies_before_locking_or_disclosing_session_counts(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
    relation: str,
    operation: str,
) -> None:
    """Read/write reach alone cannot lock a delete target or inspect its blockers."""

    owner = User.objects.create_user(username="agt-delete-owner")
    reader = User.objects.create_user(username="agt-delete-reader")
    with system_context(reason="test.agents.delete_permission.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner)
        AgentSession.objects.create(agent=agent, owner=owner)
        write_relationships(
            [
                RelationshipTuple(resource=to_object_ref(agent), relation=relation, subject=to_subject_ref(reader)),
            ]
        )
    with actor_context(reader):
        readable = Agent.objects.get(pk=agent.pk)
        assert readable.has_access("read")
        assert not readable.has_access("delete")
        with pytest.raises(PermissionDenied, match="cannot delete"):
            readable.delete()
        assert not readable.is_sudo()
    locks: list[type[models.Model]] = []
    previews: list[Any] = []
    select_for_update = models.QuerySet.select_for_update
    preview_from_instance = DeletePreview.from_instance

    def record_lock(queryset: Any, *args: Any, **kwargs: Any) -> Any:
        locks.append(queryset.model)
        return select_for_update(queryset, *args, **kwargs)

    def record_preview(instance: Any, *args: Any, **kwargs: Any) -> DeletePreview:
        previews.append(instance)
        return preview_from_instance(instance, *args, **kwargs)

    monkeypatch.setattr(models.QuerySet, "select_for_update", record_lock)
    monkeypatch.setattr(DeletePreview, "from_instance", record_preview)
    result = _execute(
        _schema(),
        operation,
        {"id": str(agent.sqid)},
        user=reader,
    )
    assert result.errors is not None
    assert result.errors[0].extensions == {"code": "PERMISSION_DENIED"}
    assert "agent sessions" not in result.errors[0].message
    assert Agent not in locks
    assert previews == []


@pytest.mark.parametrize("status", ["running", "awaiting_approval"])
def test_agent_session_delete_refuses_an_active_turn(composed_tables: None, status: str) -> None:
    """Even an admin, who may delete sessions, must Stop an active turn first."""

    owner = User.objects.create_user(username="agt-active-session-owner")
    admin = _platform_admin(f"agt-active-session-admin-{status}")
    with system_context(reason="test.agents.active_session_delete.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner)
        session = AgentSession.objects.create(agent=agent, owner=owner, status=status)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Question", status=status)
    result = _execute(
        _schema(),
        "mutation Delete($id: String!) { delete_agent_sessions_by_pk(id: $id) { id } }",
        {"id": str(session.sqid)},
        user=admin,
    )
    assert result.errors is not None
    assert result.errors[0].extensions == {"code": "BAD_USER_INPUT"}
    assert result.errors[0].message == "An agent turn is still running or awaiting approval; stop it first."
    with system_context(reason="test.agents.active_session_delete.verify"):
        assert AgentSession.objects.filter(pk=session.pk).exists()
        assert AgentTurn.objects.filter(pk=turn.pk, status=status).exists()


@pytest.mark.parametrize("actor", ["owner", "admin", "system"])
@pytest.mark.parametrize("status", ["running", "awaiting_approval"])
def test_agent_queryset_delete_refuses_private_active_turns(
    composed_tables: None, actor: str, status: str,
) -> None:
    """Bulk deletion refuses hidden active work without exposing a turn identity."""

    owner = User.objects.create_user(username="agt-bulk-active-owner")
    other = User.objects.create_user(username="agt-bulk-active-session-owner")
    admin = _platform_admin("agt-bulk-active-admin")
    with system_context(reason="test.agents.bulk_active.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner, lifecycle="deprovisioned")
        idle = Agent.objects.create(name="Idle", owner=owner, lifecycle="deprovisioned")
        session = AgentSession.objects.create(agent=agent, owner=other)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Private question", status=status)
        principal = agent.user
    targets = (
        Agent.system_queryset() if actor == "system" else Agent.objects.with_actor(owner if actor == "owner" else admin)
    )
    with pytest.raises(ValidationError) as refused:
        targets.filter(pk__in=[agent.pk, idle.pk]).delete()
    assert refused.value.messages == ["An agent turn is still running or awaiting approval; stop it first."]
    assert f"agents/turn:{turn.pk}" not in str(refused.value)
    assert str(turn.sqid) not in str(refused.value)
    with system_context(reason="test.agents.bulk_active.verify"):
        assert Agent.objects.filter(pk__in=[agent.pk, idle.pk]).count() == 2
        assert AgentSession.objects.filter(pk=session.pk).exists()
        assert AgentTurn.objects.filter(pk=turn.pk, status=status).exists()
        assert User.objects.filter(pk=principal.pk, is_active=True).exists()


@pytest.mark.parametrize("actor", ["owner", "admin", "system"])
@pytest.mark.parametrize(
    "state",
    [
        {"lifecycle": "ready", "workspace": "ws-agent", "service": "svc-agent"},
        {"lifecycle": "ready", "runtime_class": "pydantic"},
        {"lifecycle": "provisioning"},
        {"lifecycle": "deprovisioning"},
        {"lifecycle": "deprovisioned", "service": "svc-leftover"},
    ],
)
def test_agent_queryset_delete_requires_deprovisioning(
    composed_tables: None, actor: str, state: dict[str, str],
) -> None:
    """The collector enforces teardown even when no active turn retains the agent."""

    owner = User.objects.create_user(username="agt-bulk-provisioned-owner")
    admin = _platform_admin("agt-bulk-provisioned-admin")
    with system_context(reason="test.agents.bulk_provisioned.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner, **state)
        session = AgentSession.objects.create(agent=agent, owner=owner)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Retained", status="completed")
        principal = agent.user
    targets = (
        Agent.system_queryset() if actor == "system" else Agent.objects.with_actor(owner if actor == "owner" else admin)
    )
    with pytest.raises(ValidationError, match="Deprovision this agent before deleting it"):
        targets.filter(pk=agent.pk).delete()
    with system_context(reason="test.agents.bulk_provisioned.verify"):
        assert Agent.objects.filter(pk=agent.pk).exists()
        assert AgentSession.objects.filter(pk=session.pk).exists()
        assert AgentTurn.objects.filter(pk=turn.pk).exists()
        assert User.objects.filter(pk=principal.pk, is_active=True).exists()


@pytest.mark.parametrize("model", [AgentSession, AgentTurn])
@pytest.mark.parametrize("status", ["running", "awaiting_approval"])
def test_transcript_queryset_delete_refuses_active_turns(
    composed_tables: None, model: type[models.Model], status: str,
) -> None:
    """A system delete beginning below the agent still cannot erase claimed work."""

    owner = User.objects.create_user(username="agt-transcript-bulk-owner")
    with system_context(reason="test.agents.transcript_bulk.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner)
        session = AgentSession.objects.create(agent=agent, owner=owner)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Retained", status=status)
    with pytest.raises(ValidationError, match="An agent turn is still running or awaiting approval; stop it first"):
        model.system_queryset().filter(pk=session.pk if model is AgentSession else turn.pk).delete()
    with system_context(reason="test.agents.transcript_bulk.verify"):
        assert Agent.objects.filter(pk=agent.pk).exists()
        assert AgentSession.objects.filter(pk=session.pk).exists()
        assert AgentTurn.objects.filter(pk=turn.pk, status=status).exists()


@pytest.mark.parametrize("cascade", ["agent_owner", "session_owner"])
@pytest.mark.parametrize("status", ["running", "awaiting_approval"])
@pytest.mark.parametrize(
    ("mutation", "is_preview"),
    [
        ("mutation Delete($id: String!) { delete_users_by_pk(id: $id) { id } }", False),
        (
            "mutation Delete($id: ID!) { delete_user(id: $id, confirm: true) { "
            "has_blockers refusals blocked { label count } } }",
            True,
        ),
    ],
)
def test_user_delete_refuses_active_turn_cascades(
    composed_tables: None,
    cascade: str,
    status: str,
    mutation: str,
    is_preview: bool,
) -> None:
    """IAM cannot delete an active turn through either user-owned cascade."""

    owner = User.objects.create_user(username="agt-user-cascade-agent-owner")
    other = User.objects.create_user(username="agt-user-cascade-session-owner")
    admin = _platform_admin("agt-user-cascade-admin")
    with system_context(reason="test.agents.user_cascade.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner, lifecycle="deprovisioned")
        session = AgentSession.objects.create(agent=agent, owner=other)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Retained", status=status)
        principal = agent.user
    target = owner if cascade == "agent_owner" else other
    result = _execute(_schema(), mutation, {"id": str(target.sqid)}, user=admin)
    turn_message = "An agent turn is still running or awaiting approval; stop it first."
    agent_message = "Stop all active turns before deleting this agent."
    if is_preview:
        preview = _data(result)["delete_user"]
        assert preview["has_blockers"] is True
        assert preview["blocked"] == []
        assert preview["refusals"] == ([agent_message, turn_message] if cascade == "agent_owner" else [turn_message])
    else:
        assert result.errors is not None
        assert result.errors[0].extensions == {"code": "BAD_USER_INPUT"}
        assert result.errors[0].message == (
            f"{agent_message} {turn_message}" if cascade == "agent_owner" else turn_message
        )
    with system_context(reason="test.agents.user_cascade.verify"):
        assert User.objects.filter(pk__in=[owner.pk, other.pk, principal.pk]).count() == 3
        assert User.objects.filter(pk=principal.pk, is_active=True).exists()
        assert Agent.objects.filter(pk=agent.pk).exists()
        assert AgentSession.objects.filter(pk=session.pk).exists()
        assert AgentTurn.objects.filter(pk=turn.pk, status=status).exists()


@pytest.mark.parametrize("model", [Agent, AgentTurn])
def test_instance_delete_uses_current_lifecycle_state(composed_tables: None, model: type[models.Model]) -> None:
    """A stale draft agent or pending turn cannot bypass a later lifecycle change."""

    owner = User.objects.create_user(username="agt-stale-delete-owner")
    with system_context(reason="test.agents.stale_delete.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner)
        session = AgentSession.objects.create(agent=agent, owner=owner)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Retained")
    with actor_context(owner):
        stale = model.objects.get(pk=agent.pk if model is Agent else turn.pk)
        if model is Agent:
            Agent.objects.get(pk=agent.pk).mark_provisioning()
        else:
            AgentTurn.objects.get(pk=turn.pk).mark_running()
        with pytest.raises(ValidationError):
            stale.delete()
    with system_context(reason="test.agents.stale_delete.verify"):
        assert Agent.objects.filter(pk=agent.pk).exists()
        assert AgentSession.objects.filter(pk=session.pk).exists()
        assert AgentTurn.objects.filter(pk=turn.pk).exists()


@pytest.mark.parametrize("status", ["running", "awaiting_approval"])
def test_agent_owner_cannot_delete_session_with_hidden_active_turn(composed_tables: None, status: str) -> None:
    """Delete authority over a private session never bypasses hidden active work."""

    owner = User.objects.create_user(username="agt-hidden-active-owner")
    other = User.objects.create_user(username="agt-hidden-active-session-owner")
    with system_context(reason="test.agents.hidden_active.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner)
        session = AgentSession.objects.create(agent=agent, owner=other)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Private", status=status)
    with actor_context(owner):
        target = AgentSession.objects.with_action("delete").get(pk=session.pk)
        assert not target.turns.active().exists()
        with pytest.raises(ValidationError) as refused:
            target.delete()
    assert refused.value.messages == ["An agent turn is still running or awaiting approval; stop it first."]
    with system_context(reason="test.agents.hidden_active.verify"):
        assert AgentSession.objects.filter(pk=session.pk).exists()
        assert AgentTurn.objects.filter(pk=turn.pk, status=status).exists()


@pytest.mark.parametrize("model", [Agent, AgentSession, AgentTurn])
def test_instance_delete_tolerates_a_vanished_row(composed_tables: None, model: type[models.Model]) -> None:
    """A second deletion of an already collected row does not raise DoesNotExist."""

    owner = User.objects.create_user(username="agt-vanished-delete-owner")
    with system_context(reason="test.agents.vanished_delete"):
        agent = Agent.objects.create(name="Assistant", owner=owner, lifecycle="deprovisioned")
        session = AgentSession.objects.create(agent=agent, owner=owner)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Retained", status="completed")
        target_pk = {Agent: agent.pk, AgentSession: session.pk, AgentTurn: turn.pk}[model]
        stale = model.objects.get(pk=target_pk)
        model.objects.filter(pk=target_pk).delete()
        assert stale.delete()[0] == 0
        assert not model.objects.filter(pk=target_pk).exists()


@pytest.mark.parametrize("delete_path", ["instance", "queryset"])
def test_fifty_turn_agent_cascade_has_bounded_guard_queries(composed_tables: None, delete_path: str) -> None:
    """Guards add constant agent work, two reads per session, and native bypass audits."""

    owner = User.objects.create_user(username="agt-delete-query-owner")
    with system_context(reason="test.agents.delete_queries.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner, lifecycle="deprovisioned")
        sessions = [AgentSession.objects.create(agent=agent, owner=owner) for _ in range(3)]
        for index in range(50):
            AgentTurn.objects.create(
                session=sessions[index % len(sessions)], index=index + 1, prompt="Retained", status="completed",
            )
    guards = {
        Agent: (agent_signals.refuse_agent_delete, "angee.agents.agent.delete_guard"),
        AgentSession: (agent_signals.refuse_session_delete, "angee.agents.session.delete_guard"),
        AgentTurn: (agent_signals.refuse_active_turn_delete, "angee.agents.turn.delete_guard"),
    }
    guard_queries: list[dict[str, Any]] = []
    turn_calls = 0
    turn_queries = 0

    def count_guard_queries(sender: type[models.Model], **kwargs: Any) -> None:
        nonlocal turn_calls, turn_queries
        if sender is AgentTurn:
            turn_calls += 1
        with CaptureQueriesContext(connection) as queries:
            guards[sender][0](sender=sender, **kwargs)
        if sender is AgentTurn:
            turn_queries += len(queries)
        guard_queries.extend(queries.captured_queries)

    try:
        for model, (_receiver, uid) in guards.items():
            dispatch_uid = f"{uid}.{model._meta.label_lower}"
            pre_delete.disconnect(sender=model, dispatch_uid=dispatch_uid)
            pre_delete.connect(count_guard_queries, sender=model, dispatch_uid=dispatch_uid)
        with actor_context(owner):
            if delete_path == "instance":
                Agent.objects.get(pk=agent.pk).delete()
            else:
                Agent.objects.filter(pk=agent.pk).delete()
    finally:
        for model, (receiver, uid) in guards.items():
            dispatch_uid = f"{uid}.{model._meta.label_lower}"
            pre_delete.disconnect(sender=model, dispatch_uid=dispatch_uid)
            pre_delete.connect(receiver, sender=model, dispatch_uid=dispatch_uid)
    assert turn_calls == 50
    assert turn_queries == 0
    reads = [query for query in guard_queries if query["sql"].startswith("SELECT")]
    assert len(reads) <= 2 + 2 * len(sessions), reads
    assert len(guard_queries) <= 4 + 4 * len(sessions), guard_queries
    with system_context(reason="test.agents.delete_queries.verify"):
        assert not Agent.objects.filter(pk=agent.pk).exists()
        assert not AgentSession.objects.filter(pk__in=[session.pk for session in sessions]).exists()
        assert not AgentTurn.objects.filter(session_id__in=[session.pk for session in sessions]).exists()


def test_agent_delete_after_stop_uses_current_capability(composed_tables: None) -> None:
    """A capability projection from before Stop cannot retain an inactive agent."""

    owner = User.objects.create_user(username="agt-stale-capability-owner")
    with system_context(reason="test.agents.stale_capability.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner, lifecycle="deprovisioned")
        session = AgentSession.objects.create(agent=agent, owner=owner)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Retained", status="running")
    with actor_context(owner):
        target = Agent.objects.annotate(_has_active_turns=Agent.has_active_turns_expression()).get(pk=agent.pk)
        assert not target.can_delete
        turn.with_actor(owner).mark_canceled()
        target.delete()
    with system_context(reason="test.agents.stale_capability.verify"):
        assert not Agent.objects.filter(pk=agent.pk).exists()
        assert not AgentSession.objects.filter(pk=session.pk).exists()
        assert not AgentTurn.objects.filter(pk=turn.pk).exists()


def test_user_delete_requires_owned_agent_deprovisioning(composed_tables: None) -> None:
    """Deleting an owner cannot bypass teardown merely because their agent is idle."""

    owner = User.objects.create_user(username="agt-user-provisioned-owner")
    admin = _platform_admin("agt-user-provisioned-admin")
    with system_context(reason="test.agents.user_provisioned.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner, lifecycle="ready", workspace="ws-agent")
        principal = agent.user
    result = _execute(
        _schema(), "mutation Delete($id: String!) { delete_users_by_pk(id: $id) { id } }",
        {"id": str(owner.sqid)}, user=admin,
    )
    assert result.errors is not None
    assert result.errors[0].extensions == {"code": "BAD_USER_INPUT"}
    assert result.errors[0].message == "Deprovision this agent before deleting it."
    with system_context(reason="test.agents.user_provisioned.verify"):
        assert User.objects.filter(pk__in=[owner.pk, principal.pk]).count() == 2
        assert User.objects.filter(pk=principal.pk, is_active=True).exists()
        assert Agent.objects.filter(pk=agent.pk).exists()


def test_agent_hasura_update_sets_many_to_many_skills(composed_tables: None) -> None:
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


def test_agent_update_is_platform_admin_gated(composed_tables: None) -> None:
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


def test_refresh_provider_models_is_admin_gated(composed_tables: None) -> None:
    """The `refreshProviderModels` action is platform-admin gated."""

    admin = _platform_admin("agt-refresh-admin")
    plain = User.objects.create_user(username="agt-refresh-plain", email="plain@example.com")
    provider = _provider("agt-refresh", name="P")
    provider_id = _public_id(provider.sqid)
    query = "mutation($id: ID!){ refresh_provider_models(id: $id){ ok message } }"

    assert _execute(console := _schema(), query, {"id": provider_id}, user=plain).errors is not None
    result = _data(_execute(console, query, {"id": provider_id}, user=admin))["refresh_provider_models"]
    assert result["ok"] is True


def test_inference_models_query_accepts_provider_sqid_filter(composed_tables: None) -> None:
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
    composed_tables: None,
) -> None:
    """The model catalogue exposes grouped buckets for list/board views."""

    admin = _platform_admin("agt-model-groups-admin")
    provider_a = _provider("agt-model-groups-a", name="Anthropic")
    provider_b = _provider("agt-model-groups-b", name="Manual")
    with system_context(reason="test.agents.model_groups.seed"):
        InferenceModel.objects.create(provider=provider_a, name="claude-sonnet-4-6", model_use="chat")
        InferenceModel.objects.create(provider=provider_a, name="claude-embed-4-6", model_use="embedding")
        InferenceModel.objects.create(provider=provider_b, name="manual-model", model_use="chat")

    schema = _schema()
    resources = {item.model_label: item for item in schema.angee_resources}
    group_by_type = resources["agents.InferenceModel"].type_names.group_by_spec
    assert group_by_type is not None
    grouped = _data(
        _execute(
            schema,
            """
            query InferenceModelGroups(
              $byUse: [GROUP_BY_SPEC!]!
              $byProvider: [GROUP_BY_SPEC!]!
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
            """.replace("GROUP_BY_SPEC", group_by_type),
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
    provider_groups = {row["key"]["provider_id"]: row for row in grouped["byProvider"]}
    assert provider_groups == {
        str(provider_a.sqid): {
            "key": {
                "provider_id": str(provider_a.sqid),
                "provider__name": "Anthropic",
            },
            "aggregate": {"count": 2},
        },
        str(provider_b.sqid): {
            "key": {
                "provider_id": str(provider_b.sqid),
                "provider__name": "Manual",
            },
            "aggregate": {"count": 1},
        },
    }


def test_create_inference_provider_creates_child_row(composed_tables: None) -> None:
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
            lifecycle
            runtime_status
          }
        }
    """

    created = _data(
        _execute(
            console,
            mutation,
            {
                "vendor": _public_id(seed.vendor.sqid),
                "owner": str(seed.owner.sqid),
            },
            user=admin,
        )
    )["create_inference_provider"]
    assert created == {
        "name": "Provider",
        "base_url": "https://api.example.test",
        "backend_class": "MANUAL",
        "lifecycle": "DISCONNECTED",
        "runtime_status": "OK",
    }
    with system_context(reason="test.agents.provider_mti.verify"):
        provider = InferenceProvider.objects.get(name="Provider")
        integration = Integration.objects.get(pk=provider.pk)
        assert provider.owner_id == integration.owner_id
        assert provider.vendor_id == integration.vendor_id
        assert provider.backend_class == "manual"


def test_update_inference_provider_merges_config_and_removes_null_keys(composed_tables: None) -> None:
    """Untyped provider patches preserve unsent keys and delete explicit null values."""

    provider = _provider(
        "agt-provider-config-patch",
        config={"endpoint": "https://example.test", "retries": 1, "obsolete": True},
    )
    result = _data(
        _execute(
            _schema(),
            """
            mutation UpdateConfig($id: ID!) {
              update_inference_provider(data: {id: $id, config: {retries: 3, obsolete: null}}) { config }
            }
            """,
            {"id": _public_id(provider.sqid)},
            user=_platform_admin("agt-provider-config-patch-admin"),
        )
    )["update_inference_provider"]

    expected = {"endpoint": "https://example.test", "retries": 3}
    assert result == {"config": expected}
    with system_context(reason="test.agents.provider_config_patch.verify"):
        provider.refresh_from_db()
        assert provider.config == expected


def test_update_inference_provider_backend_is_create_only(composed_tables: None) -> None:
    """A saved provider cannot switch implementation or absorb another backend's defaults."""

    admin = _platform_admin("agt-provider-update-admin")
    with system_context(reason="test.agents.provider_update.seed"):
        Vendor.objects.create(slug="anthropic", display_name="Anthropic")
    provider = _provider(
        "agt-provider-update",
        backend_class="manual",
        name="Custom",
    )
    original_vendor_id = provider.vendor_id
    original_account_id = provider.account_id
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

    result = _execute(
        _schema(),
        mutation,
        {"id": _public_id(provider.sqid), "account": _public_id(account.sqid)},
        user=admin,
    )
    assert result.errors is not None
    assert result.errors[0].extensions == {
        "code": "VALIDATION",
        "validationErrors": {"backend_class": ["Implementation selection is create-only."]},
        "formErrors": [],
    }
    provider.refresh_from_db()
    assert provider.backend_class == "manual"
    assert provider.name == "Custom"
    assert provider.vendor_id == original_vendor_id
    assert provider.account_id == original_account_id


def test_connect_inference_provider_uses_provider_backend_oauth_client(composed_tables: None) -> None:
    """Provider connect resolves OAuth from provider.backend."""

    del composed_tables
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
            str(CredentialKind.OAUTH),
            {"access_token": "anthropic-token"},
        )
    mutation = """
        mutation ConnectProvider($id: ID!) {
          connect_inference_provider(id: $id) {
            attached
            error
            integration { lifecycle credential { display_name } }
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
            "lifecycle": "CONNECTED",
            "credential": {"display_name": "Anthropic Personal"},
        },
    }
    provider.refresh_from_db()
    assert provider.credential_id == credential.pk


def test_connect_inference_provider_uses_shared_oauth_client_error_code(
    composed_tables: None,
) -> None:
    """Provider connect reports the shared OAuth-client lookup error code."""

    del composed_tables
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
        "error": "This connection is not available.",
        "error_code": "oauth_client_not_connectable",
    }


def test_create_mcp_server_keeps_defaults_for_omitted_optionals(composed_tables: None) -> None:
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


def test_provision_agent_renders_via_daemon_and_is_admin_gated(composed_tables: None, monkeypatch: Any) -> None:
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
            assert start is False
            with system_context(reason="test.agents.render.verify_workspace_recorded"):
                agent.refresh_from_db()
                calls.append(("recorded_workspace", agent.workspace, str(agent.lifecycle)))
            calls.append(("service", template, workspace, inputs))
            return "svc-bot"

        def start_service(self, name: str) -> None:
            calls.append(("start_service", name))

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
        "start_service",
        "recorded_service",
    ]
    assert calls[0] == ("secret", f"agent-{agent.sqid}-inference", "x")
    assert calls[1][1] == "ref:agent-default" and calls[1][2]["agent_name"] == "Bot"
    assert calls[2] == ("recorded_workspace", "ws-bot", "provisioning")
    assert calls[3][1] == "ref:claude-code"
    assert calls[3][2] == "ws-bot"
    assert calls[3][3]["auth_env"] == f'      ANTHROPIC_API_KEY: "${{secret.agent-{agent.sqid}-inference}}"'
    assert calls[4] == ("start_service", "svc-bot")
    assert calls[5] == ("recorded_service", "svc-bot", "provisioning")

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


def test_mark_provisioning_never_starts_over_a_provision_under_way(composed_tables: None) -> None:
    """``PROVISIONING`` is not a source: a second provision cannot run over the first."""

    admin = _platform_admin("agt-reenter-admin")
    agent = _provisionable_agent(admin, "Reenter", slug="agt-reenter-tpl", lifecycle="provisioning")

    with system_context(reason="test.agents.reenter"), pytest.raises(TransitionNotAllowed):
        agent.mark_provisioning()


def test_provision_agent_refuses_while_a_provision_is_under_way(composed_tables: None, monkeypatch: Any) -> None:
    """A provision that finds the row ``PROVISIONING`` refuses without touching the daemon or the row.

    The lock is free here — a stalled provision looks like this — so the refusal comes from
    the lifecycle, which stays authoritative; Deprovision is the way out.
    """

    admin = _platform_admin("agt-double-provision-admin")
    agent = _provisionable_agent(admin, "Double", slug="agt-double-provision-tpl", lifecycle="provisioning")
    operator = _Operator().install(monkeypatch)

    result = _data(_run_action("provision_agent", agent, admin))["provision_agent"]

    assert result["ok"] is False and "deprovision it to recover" in result["message"]
    assert operator.calls == []
    assert _agent_state(agent, admin)["lifecycle"] == "PROVISIONING"


def test_deprovision_agent_from_empty_provisioning_row_is_idempotent(composed_tables: None, monkeypatch: Any) -> None:
    """A teardown retry for a stuck PROVISIONING row with no daemon names clears locally."""

    admin = _platform_admin("agt-empty-deprov-admin")
    agent = _provisionable_agent(
        admin,
        "Empty Deprovision",
        slug="agt-empty-deprov-tpl",
        lifecycle="provisioning",
        runtime_status="stopped",
    )
    operator = _Operator().install(monkeypatch)

    result = _data(_run_action("deprovision_agent", agent, admin))["deprovision_agent"]

    assert result == {"ok": True, "message": "Deprovisioned.", "code": None}
    assert operator.calls == []
    with system_context(reason="test.agents.empty_deprov.verify"):
        agent.refresh_from_db()
        assert (agent.workspace, agent.service, str(agent.lifecycle), str(agent.runtime_status)) == (
            "",
            "",
            "deprovisioned",
            "stopped",
        )


def test_provision_agent_reports_racing_deprovision_without_clobbering_state(
    composed_tables: None, monkeypatch: Any
) -> None:
    """If teardown advances the row before final record, provision reports failure and leaves it."""

    admin = _platform_admin("agt-race-admin")
    agent = _provisionable_agent(admin, "Race", slug="agt-race-tpl")
    agent_id = _public_id(agent.sqid)

    def render_after_racing_deprovision(*args: Any, **kwargs: Any) -> dict[str, str]:
        del args, kwargs
        with system_context(reason="test.agents.race.concurrent_deprovision"):
            Agent.objects.filter(pk=agent.pk).update(lifecycle="deprovisioning")
        return {"workspace": "ws-race", "service": "svc-race"}

    monkeypatch.setattr(agents_provisioning, "_render_agent", render_after_racing_deprovision)

    result = _data(
        _execute(
            _schema(),
            "mutation($id: ID!){ provision_agent(id: $id){ ok message } }",
            {"id": agent_id},
            user=admin,
        )
    )["provision_agent"]

    assert result["ok"] is False
    assert "Provisioning failed" in result["message"]
    with system_context(reason="test.agents.race.verify"):
        agent.refresh_from_db()
        assert (agent.workspace, agent.service, str(agent.lifecycle)) == ("", "", "deprovisioning")


def test_provision_agent_failure_tears_down_service_then_workspace_and_records_error(
    composed_tables: None, monkeypatch: Any
) -> None:
    """A service-start failure removes its persisted entry before the workspace."""

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

    destroyed: list[tuple[str, str]] = []
    recorded: list[tuple[str, str]] = []

    class _FailingDaemon:
        @classmethod
        def from_settings(cls) -> _FailingDaemon:
            return cls()

        def resolve_template_ref(self, *, name: str, kind: str) -> str:
            return f"ref:{name}"

        def create_workspace(self, *, template: str, inputs: dict[str, str]) -> str:
            return "ws-doomed"

        def create_service(self, *, template: str, workspace: str, inputs: dict[str, str], start: bool = True) -> str:
            assert start is False
            with system_context(reason="test.agents.fail.verify_workspace_recorded"):
                agent.refresh_from_db()
                recorded.append((agent.workspace, str(agent.lifecycle)))
            return "svc-doomed"

        def start_service(self, name: str) -> None:
            assert name == "svc-doomed"
            with system_context(reason="test.agents.fail.verify_service_recorded"):
                agent.refresh_from_db()
                recorded.append((agent.service, str(agent.lifecycle)))
            raise RuntimeError("image build failed")

        def destroy_service(self, name: str) -> None:
            # Already gone: the daemon client reports a by-name absent instance as destroyed.
            destroyed.append(("service", name))

        def destroy_workspace(self, name: str) -> None:
            destroyed.append(("workspace", name))

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
    assert recorded == [
        ("ws-doomed", "provisioning"),
        ("svc-doomed", "provisioning"),
    ]
    assert destroyed == [
        ("service", "svc-doomed"),
        ("workspace", "ws-doomed"),
    ]
    with system_context(reason="test.agents.fail.verify"):
        agent.refresh_from_db()
        # Run state errored; the rolled-back workspace leaves the lifecycle a clean DRAFT.
        assert (str(agent.runtime_status), str(agent.lifecycle)) == ("error", "draft")
        assert (agent.workspace, "image build failed" in agent.last_error) == ("", True)


def test_deprovision_agent_keeps_the_names_when_the_daemon_answers_a_plain_404(
    composed_tables: None, monkeypatch: Any
) -> None:
    """A not-found that does not name the instance (a proxy, a wrong mount) is a failure, not a destroy.

    The daemon client absorbs only a not-found naming the asked-for instance; anything else
    reaches the teardown, which keeps every name it could not confirm destroyed.
    """

    admin = _platform_admin("agt-deprov-plain404-admin")
    agent = _provisionable_agent(
        admin,
        "Gonebot",
        slug="agt-deprov-plain404-tpl",
        workspace="ws-gone",
        service="svc-gone",
        lifecycle="ready",
        runtime_status="running",
    )
    operator = _Operator(
        workspaces={"ws-gone": ("ref:agent-default", {"agent_name": "Gonebot"})},
        mounts={"svc-gone": "ws-gone"},
        fail={"destroy_service": OperatorDaemonNotFound("operator POST destroy: HTTP 404: 404 page not found")},
    ).install(monkeypatch)

    result = _data(_run_action("deprovision_agent", agent, admin))["deprovision_agent"]

    assert result["ok"] is False and "404 page not found" in result["message"]
    assert [name for name, _ in operator.calls] == ["destroy_service"]
    with system_context(reason="test.agents.deprov_plain404.verify"):
        agent.refresh_from_db()
    assert (agent.workspace, agent.service, str(agent.lifecycle), str(agent.runtime_status)) == (
        "ws-gone",
        "svc-gone",
        "ready",
        "error",
    )


def test_provision_agent_records_error_when_plan_resolution_fails(composed_tables: None, monkeypatch: Any) -> None:
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


def test_reprovision_agent_recreates_service_over_existing_workspace(composed_tables: None, monkeypatch: Any) -> None:
    """`reprovisionAgent` destroys the old service and recreates it over the kept workspace."""

    admin = _platform_admin("agt-reprov-admin")
    plain = User.objects.create_user(username="agt-reprov-plain", email="reprov@example.com")
    agent = _provisionable_agent(
        admin,
        "Rebot",
        slug="agt-reprov-tpl",
        workspace="ws-keep",
        service="svc-old",
        lifecycle="ready",
        runtime_status="running",
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
            assert start is False
            calls.append(("create_service", template, workspace))
            return "svc-new"

        def start_service(self, name: str) -> None:
            calls.append(("start_service", name))

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
    composed_tables: None, monkeypatch: Any
) -> None:
    """When the old service is destroyed but the recreate fails, the stale name is cleared.

    The workspace (and its files) is preserved; the service name is blanked so a later
    deprovision doesn't try to tear down a service the daemon already removed (a 409).
    """

    admin = _platform_admin("agt-reprovfail-admin")
    agent = _provisionable_agent(
        admin,
        "ReDoomed",
        slug="agt-reprovfail-tpl",
        workspace="ws-keep",
        service="svc-old",
        lifecycle="ready",
        runtime_status="running",
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


@dataclass
class _Operator:
    """In-memory operator daemon that owns instance naming, as the real daemon does.

    A workspace is named ``ws-<agent name slug>`` and its service ``agent-<workspace>``,
    so a second render of the same agent collides with what is already there. Destroying
    an absent instance succeeds and a missing workspace has no status — the daemon
    client's contract for a not-found that names the instance. ``fail`` maps a daemon
    call to the error it raises; ``calls`` records every call in order.
    """

    workspaces: dict[str, tuple[str, dict[str, str]]] = field(default_factory=dict)
    """Workspace name -> (template ref it was rendered from, the inputs it recorded)."""
    mounts: dict[str, str] = field(default_factory=dict)
    """Service name -> the workspace it mounts."""
    running: set[str] = field(default_factory=set)
    fail: dict[str, Exception] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def install(self, monkeypatch: Any) -> _Operator:
        monkeypatch.setattr(agents_provisioning, "OperatorDaemon", SimpleNamespace(from_settings=lambda: self))
        return self

    def _call(self, name: str, argument: str) -> None:
        self.calls.append((name, argument))
        if name in self.fail:
            raise self.fail[name]

    def resolve_template_ref(self, *, name: str, kind: str) -> str:
        return f"ref:{name}"

    def set_secret(self, name: str, value: str) -> None:
        self._call("set_secret", name)

    def create_workspace(self, *, template: str, inputs: dict[str, str]) -> str:
        name = "ws-" + inputs["agent_name"].lower().replace(" ", "-")
        self._call("create_workspace", name)
        if name in self.workspaces:
            raise OperatorDaemonConflict(
                f"operator POST workspaces: HTTP 409: workspace {name} conflicts: already exists",
                status_code=409,
                kind=OperatorInstanceKind.WORKSPACE,
                name=name,
            )
        self.workspaces[name] = (template, dict(inputs))
        return name

    def create_service(self, *, template: str, workspace: str, inputs: dict[str, str], start: bool = True) -> str:
        name = f"agent-{workspace}"
        self._call("create_service", name)
        if name in self.mounts:
            raise OperatorDaemonConflict(
                f"operator POST create: HTTP 409: service {name} conflicts: already exists",
                status_code=409,
                kind=OperatorInstanceKind.SERVICE,
                name=name,
            )
        self.mounts[name] = workspace
        return name

    def start_service(self, name: str) -> None:
        self._call("start_service", name)
        self.running.add(name)

    def service_status(self, name: str) -> str | None:
        self._call("service_status", name)
        if name not in self.mounts:
            return None
        return "running" if name in self.running else "exited"

    def destroy_service(self, name: str) -> None:
        self._call("destroy_service", name)
        self.mounts.pop(name, None)
        self.running.discard(name)

    def destroy_workspace(self, name: str) -> None:
        self._call("destroy_workspace", name)
        self.workspaces.pop(name, None)

    def workspace_status(self, name: str) -> WorkspaceStatus | None:
        self._call("workspace_status", name)
        if name not in self.workspaces:
            return None
        template, inputs = self.workspaces[name]
        services = tuple(sorted(service for service, mounted in self.mounts.items() if mounted == name))
        return WorkspaceStatus(name=name, template=template, inputs=inputs, services=services)


_AGENT_STATE_QUERY = """
query($id: String!) {
  agents_by_pk(id: $id) {
    lifecycle runtime_status workspace service conflict_kind conflict_name last_error
    can_provision can_adopt can_replace can_reprovision can_deprovision can_delete
  }
}
"""


def _agent_state(agent: Any, admin: Any) -> dict[str, Any]:
    """Read an agent's lifecycle facts and verb eligibility through the console schema."""

    return dict(_data(_execute(_schema(), _AGENT_STATE_QUERY, {"id": agent.sqid}, user=admin))["agents_by_pk"])


def _run_action(field_name: str, agent: Any, user: Any) -> Any:
    """Run one agent action mutation and return its execution result."""

    return _execute(
        _schema(),
        f"mutation($id: ID!){{ {field_name}(id: $id){{ ok message code }} }}",
        {"id": _public_id(agent.sqid)},
        user=user,
    )


def _action(field_name: str, agent: Any, user: Any) -> dict[str, Any]:
    """Run one agent action mutation and return its ``ActionResult``."""

    return dict(_data(_run_action(field_name, agent, user))[field_name])


def _agent_holding(owner: Any, *, workspace: str = "", service: str = "") -> Any:
    """Seed another, provisioned agent that records ``workspace``/``service`` as its own."""

    with system_context(reason="test.agents.holding.seed"):
        return Agent.objects.create(name="Owner", owner=owner, workspace=workspace, service=service, lifecycle="ready")


def _operator_holding(
    monkeypatch: Any,
    *,
    workspace: str,
    agent_name: str,
    template: str = "ref:agent-default",
    services: tuple[str, ...] | None = None,
    running: bool = True,
) -> _Operator:
    """Install an operator that already holds a workspace and the services mounting it."""

    mounted = (f"agent-{workspace}",) if services is None else services
    operator = _Operator(
        workspaces={workspace: (template, {"agent_name": agent_name})},
        mounts={service: workspace for service in mounted},
        running=set(mounted) if running else set(),
    )
    return operator.install(monkeypatch)


def _conflicted_agent(
    admin: Any, monkeypatch: Any, *, slug: str, name: str = "Taken", **operator: Any
) -> tuple[Any, _Operator]:
    """Seed an agent and an operator holding its workspace, then provision into the conflict."""

    agent = _provisionable_agent(admin, name, slug=slug)
    workspace = "ws-" + name.lower().replace(" ", "-")
    daemon = _operator_holding(monkeypatch, workspace=workspace, agent_name=name, **operator)
    assert _action("provision_agent", agent, admin)["ok"] is False
    daemon.calls.clear()
    return agent, daemon


def test_provision_agent_records_the_conflicting_workspace_and_offers_adopt_and_replace(
    composed_tables: None, monkeypatch: Any
) -> None:
    """A 409 over an unrecorded workspace is a recorded outcome; Provision then stays hidden."""

    admin = _platform_admin("agt-conflict-admin")
    agent = _provisionable_agent(admin, "Taken", slug="agt-conflict-tpl")
    operator = _operator_holding(monkeypatch, workspace="ws-taken", agent_name="Taken")

    result = _action("provision_agent", agent, admin)

    assert result["ok"] is False and result["code"] is None
    assert "workspace ws-taken conflicts" in result["message"]
    state = _agent_state(agent, admin)
    assert {key: state[key] for key in state if key != "last_error"} == {
        "lifecycle": "DRAFT",
        "runtime_status": "ERROR",
        "workspace": "",
        "service": "",
        "conflict_kind": "WORKSPACE",
        "conflict_name": "ws-taken",
        "can_provision": False,
        "can_adopt": True,
        "can_replace": True,
        "can_reprovision": False,
        "can_deprovision": True,
        "can_delete": False,
    }
    # Nothing was created, so nothing was rolled back: the conflicting instance is untouched.
    assert [name for name, _ in operator.calls] == ["create_workspace"]
    assert operator.mounts == {"agent-ws-taken": "ws-taken"}


def test_provision_agent_conflict_over_another_agents_instance_names_it_only_to_the_admin(
    composed_tables: None, monkeypatch: Any
) -> None:
    """A conflict with an instance another agent records is never recorded for adoption.

    The admin-only result names the other agent; the persisted error, readable by any
    reader of this agent, does not. A rename then provisions under a fresh name.
    """

    admin = _platform_admin("agt-recorded-elsewhere-admin")
    _agent_holding(admin, workspace="ws-shared")
    agent = _provisionable_agent(admin, "Shared", slug="agt-recorded-elsewhere-tpl")
    _operator_holding(monkeypatch, workspace="ws-shared", agent_name="Shared")

    result = _action("provision_agent", agent, admin)

    assert result["ok"] is False
    assert "recorded by agent “Owner”" in result["message"] and "rename this agent" in result["message"]
    state = _agent_state(agent, admin)
    assert (state["conflict_kind"], state["can_provision"], state["can_adopt"]) == (None, True, False)
    assert "Owner" not in state["last_error"] and "another agent" in state["last_error"]


def test_renaming_an_agent_clears_its_conflicting_workspace(composed_tables: None, monkeypatch: Any) -> None:
    """The conflicting workspace's name derived from the old agent name; a rename makes it stale."""

    admin = _platform_admin("agt-rename-admin")
    agent, _ = _conflicted_agent(admin, monkeypatch, slug="agt-rename-tpl")

    renamed = _data(
        _execute(
            _schema(),
            """
            mutation($id: String!) {
              update_agents_by_pk(pk_columns: {id: $id}, _set: {name: "Renamed"}) {
                conflict_kind can_provision can_adopt
              }
            }
            """,
            {"id": agent.sqid},
            user=admin,
        )
    )["update_agents_by_pk"]

    assert renamed == {"conflict_kind": None, "can_provision": True, "can_adopt": False}


@pytest.mark.parametrize(
    ("failing_destroy", "kept", "destroys"),
    [
        ("destroy_workspace", ("ws-doomed", "", "ready"), ["destroy_service", "destroy_workspace"]),
        ("destroy_service", ("ws-doomed", "agent-ws-doomed", "ready"), ["destroy_service"]),
    ],
)
def test_provision_agent_keeps_the_names_whose_rollback_destroy_failed(
    composed_tables: None, monkeypatch: Any, failing_destroy: str, kept: tuple[str, str, str], destroys: list[str]
) -> None:
    """A failed rollback leaves its instance recorded, so the agent never forgets it.

    The workspace is never destroyed under a service whose destroy failed.
    """

    admin = _platform_admin(f"agt-rollback-{failing_destroy}-admin")
    agent = _provisionable_agent(admin, "Doomed", slug=f"agt-rollback-{failing_destroy}-tpl")
    failures = {"start_service": RuntimeError("image build failed"), failing_destroy: RuntimeError("unreachable")}
    operator = _Operator(fail=failures).install(monkeypatch)

    result = _action("provision_agent", agent, admin)

    assert result["ok"] is False and "image build failed" in result["message"]
    assert [name for name, _ in operator.calls if name.startswith("destroy")] == destroys
    with system_context(reason="test.agents.rollback.verify"):
        agent.refresh_from_db()
    assert (agent.workspace, agent.service, str(agent.lifecycle)) == kept
    assert str(agent.runtime_status) == "error" and "image build failed" in agent.last_error


@pytest.mark.parametrize(
    "verb", ["provision_agent", "adopt_agent", "replace_agent", "reprovision_agent", "deprovision_agent"]
)
def test_a_lifecycle_verb_is_refused_while_another_holds_the_agent(
    composed_tables: None, monkeypatch: Any, verb: str
) -> None:
    """The five verbs share one advisory lock per agent; a refused verb touches neither daemon nor row."""

    admin = _platform_admin(f"agt-locked-{verb}-admin")
    agent = _provisionable_agent(
        admin,
        "Locked",
        slug=f"agt-locked-{verb}-tpl",
        workspace="ws-locked",
        lifecycle="ready",
        runtime_status="running",
        conflict_kind="service",
        conflict_name="agent-ws-locked",
    )
    operator = _Operator().install(monkeypatch)
    before = _agent_state(agent, admin)

    with task_lock(agent.provisioning_lock_key()) as held:
        assert held
        result = _action(verb, agent, admin)

    assert result == {
        "ok": False,
        "message": "Another provisioning action is running for this agent; try again when it finishes.",
        "code": None,
    }
    assert operator.calls == []
    assert _agent_state(agent, admin) == before


def test_adopt_agent_keeps_a_running_container_as_it_is(composed_tables: None, monkeypatch: Any) -> None:
    """Adopt records the workspace and its running service and changes nothing on the daemon."""

    admin = _platform_admin("agt-adopt-admin")
    plain = User.objects.create_user(username="agt-adopt-plain", email="adopt@example.com")
    agent, operator = _conflicted_agent(admin, monkeypatch, slug="agt-adopt-tpl")

    assert _run_action("adopt_agent", agent, plain).errors is not None
    result = _action("adopt_agent", agent, admin)

    assert result == {"ok": True, "message": "Adopted “agent-ws-taken”.", "code": None}
    # Read only: no secret sync, no render, no start of a running service.
    assert operator.calls == [("workspace_status", "ws-taken"), ("service_status", "agent-ws-taken")]
    state = _agent_state(agent, admin)
    assert (state["lifecycle"], state["runtime_status"], state["workspace"], state["service"]) == (
        "READY",
        "RUNNING",
        "ws-taken",
        "agent-ws-taken",
    )
    assert (state["conflict_kind"], state["can_adopt"], state["can_replace"], state["can_reprovision"]) == (
        None,
        False,
        False,
        True,
    )


def test_adopt_agent_starts_a_stopped_service(composed_tables: None, monkeypatch: Any) -> None:
    """A stopped adopted service is brought up; it is not re-rendered."""

    admin = _platform_admin("agt-adopt-start-admin")
    agent, operator = _conflicted_agent(admin, monkeypatch, slug="agt-adopt-start-tpl", running=False)

    assert _action("adopt_agent", agent, admin)["ok"] is True

    assert [name for name, _ in operator.calls] == ["workspace_status", "service_status", "start_service"]
    assert operator.running == {"agent-ws-taken"}


def test_adopt_agent_without_a_mounting_service_records_the_workspace_and_asks_for_reprovision(
    composed_tables: None, monkeypatch: Any
) -> None:
    """Adopt renders nothing: a workspace with no service is recorded, and Reprovision renders one."""

    admin = _platform_admin("agt-adopt-bare-admin")
    agent, operator = _conflicted_agent(admin, monkeypatch, slug="agt-adopt-bare-tpl", services=())

    result = _action("adopt_agent", agent, admin)

    assert result["ok"] is True and "reprovision to render one" in result["message"]
    assert operator.mounts == {}
    state = _agent_state(agent, admin)
    assert (state["lifecycle"], state["runtime_status"], state["workspace"], state["can_reprovision"]) == (
        "READY",
        "ERROR",
        "ws-taken",
        True,
    )


@pytest.mark.parametrize(
    ("operator_state", "holder", "refusal"),
    [
        ({"template": "ref:other-template"}, {}, "was rendered from template “ref:other-template”"),
        ({"agent_name": "Someone Else"}, {}, "was rendered with agent_name “Someone Else”, not “Taken”"),
        ({"services": ("agent-ws-taken", "sidecar")}, {}, "more services than this agent's runtime renders"),
        ({}, {"service": "agent-ws-taken"}, "service “agent-ws-taken” is recorded by agent “Owner”"),
    ],
)
def test_adopt_agent_refuses_an_instance_that_does_not_verify_as_this_agents(
    composed_tables: None, monkeypatch: Any, operator_state: dict[str, Any], holder: dict[str, str], refusal: str
) -> None:
    """Adopt verifies template, identity inputs, mounting services and records before changing anything."""

    admin = _platform_admin(f"agt-adopt-refuse-{len(refusal)}-admin")
    agent = _provisionable_agent(admin, "Taken", slug=f"agt-adopt-refuse-{len(refusal)}-tpl")
    operator = _operator_holding(
        monkeypatch,
        workspace="ws-taken",
        agent_name=operator_state.get("agent_name", "Taken"),
        template=operator_state.get("template", "ref:agent-default"),
        services=operator_state.get("services"),
    )
    _action("provision_agent", agent, admin)
    if holder:
        _agent_holding(admin, **holder)
    operator.calls.clear()

    result = _action("adopt_agent", agent, admin)

    assert result["ok"] is False and refusal in result["message"]
    assert [name for name, _ in operator.calls] == ["workspace_status"]
    state = _agent_state(agent, admin)
    assert (state["lifecycle"], state["workspace"], state["conflict_name"], state["can_replace"]) == (
        "DRAFT",
        "",
        "ws-taken",
        True,
    )


def test_adopt_agent_turns_a_record_race_into_the_other_agents_name(composed_tables: None, monkeypatch: Any) -> None:
    """The unique instance constraint refuses a record another agent won meanwhile."""

    admin = _platform_admin("agt-adopt-race-admin")
    agent, _ = _conflicted_agent(admin, monkeypatch, slug="agt-adopt-race-tpl")
    _agent_holding(admin, workspace="ws-taken")
    # The holder records the workspace after the verification read: let the check pass.
    monkeypatch.setattr(type(agent), "conflicting_instance_blocker", lambda self, status, *, template_ref: None)

    result = _action("adopt_agent", agent, admin)

    assert result["ok"] is False and "workspace “ws-taken” is recorded by agent “Owner”" in result["message"]
    assert _agent_state(agent, admin)["conflict_name"] == "ws-taken"


def test_adopt_agent_failing_after_the_record_keeps_the_instance(composed_tables: None, monkeypatch: Any) -> None:
    """Once recorded, a failed start leaves the workspace and service on the agent, never forgotten."""

    admin = _platform_admin("agt-adopt-startfail-admin")
    agent, operator = _conflicted_agent(admin, monkeypatch, slug="agt-adopt-startfail-tpl", running=False)
    operator.fail["start_service"] = RuntimeError("image missing")

    result = _action("adopt_agent", agent, admin)

    assert result["ok"] is False and "image missing" in result["message"]
    state = _agent_state(agent, admin)
    assert (state["lifecycle"], state["runtime_status"], state["workspace"], state["service"]) == (
        "READY",
        "ERROR",
        "ws-taken",
        "agent-ws-taken",
    )
    assert (state["conflict_kind"], state["can_reprovision"], state["can_deprovision"]) == (None, True, True)


@pytest.mark.parametrize("verb", ["adopt_agent", "replace_agent", "deprovision_agent"])
def test_an_unreachable_operator_during_the_conflict_read_changes_nothing(
    composed_tables: None, monkeypatch: Any, verb: str
) -> None:
    """The verification read failing refuses the verb; nothing is recorded or destroyed."""

    admin = _platform_admin(f"agt-unreachable-{verb}-admin")
    agent, operator = _conflicted_agent(admin, monkeypatch, slug=f"agt-unreachable-{verb}-tpl")
    operator.fail["workspace_status"] = RuntimeError("operator unreachable")
    before = _agent_state(agent, admin)

    result = _action(verb, agent, admin)

    assert result["ok"] is False and "operator unreachable" in result["message"]
    assert [name for name, _ in operator.calls] == ["workspace_status"]
    assert _agent_state(agent, admin) == before


def test_replace_agent_destroys_the_verified_conflicting_instance_then_provisions_afresh(
    composed_tables: None, monkeypatch: Any
) -> None:
    """Replace records the conflicting instance, destroys it, and renders anew."""

    admin = _platform_admin("agt-replace-admin")
    plain = User.objects.create_user(username="agt-replace-plain", email="replace@example.com")
    agent, operator = _conflicted_agent(admin, monkeypatch, slug="agt-replace-tpl")

    assert _run_action("replace_agent", agent, plain).errors is not None
    result = _action("replace_agent", agent, admin)

    assert result == {"ok": True, "message": "Provisioned “agent-ws-taken”.", "code": None}
    assert operator.calls == [
        ("workspace_status", "ws-taken"),
        ("workspace_status", "ws-taken"),
        ("destroy_service", "agent-ws-taken"),
        ("destroy_workspace", "ws-taken"),
        ("create_workspace", "ws-taken"),
        ("create_service", "agent-ws-taken"),
        ("start_service", "agent-ws-taken"),
    ]
    state = _agent_state(agent, admin)
    assert (state["lifecycle"], state["workspace"], state["service"], state["conflict_kind"]) == (
        "READY",
        "ws-taken",
        "agent-ws-taken",
        None,
    )


def test_replace_agent_refuses_to_destroy_a_workspace_this_agent_did_not_render(
    composed_tables: None, monkeypatch: Any
) -> None:
    """A conflicting workspace from another template is never destroyed by Replace."""

    admin = _platform_admin("agt-replace-foreign-admin")
    agent, operator = _conflicted_agent(
        admin, monkeypatch, slug="agt-replace-foreign-tpl", template="ref:stale-template"
    )

    result = _action("replace_agent", agent, admin)

    assert result["ok"] is False and "Deprovision to clear the record without destroying it" in result["message"]
    assert [name for name, _ in operator.calls] == ["workspace_status"]
    assert "ws-taken" in operator.workspaces and operator.mounts == {"agent-ws-taken": "ws-taken"}


def test_replace_agent_refuses_before_destroying_when_it_could_not_provision(
    composed_tables: None, monkeypatch: Any
) -> None:
    """A replace that could not rebuild the agent never destroys the conflicting instance."""

    admin = _platform_admin("agt-replace-refuse-admin")
    with system_context(reason="test.agents.replace_refuse.seed"):
        agent = Agent.objects.create(
            name="Templateless",
            owner=admin,
            runtime_class="claude_code",
            conflict_kind="workspace",
            conflict_name="ws-templateless",
            runtime_status="error",
        )
    operator = _operator_holding(monkeypatch, workspace="ws-templateless", agent_name="Templateless")

    result = _action("replace_agent", agent, admin)

    assert result["ok"] is False and "Set a workspace template" in result["message"]
    assert operator.calls == []


def test_a_service_conflict_keeps_the_new_workspace_and_replace_clears_both(
    composed_tables: None, monkeypatch: Any
) -> None:
    """A 409 on the service keeps the workspace this provision created; Replace then rebuilds both."""

    admin = _platform_admin("agt-service-conflict-admin")
    agent = _provisionable_agent(admin, "Svc Taken", slug="agt-service-conflict-tpl")
    # The service survived a workspace destroyed outside this agent.
    operator = _Operator(mounts={"agent-ws-svc-taken": "ws-svc-taken"}).install(monkeypatch)

    provisioned = _action("provision_agent", agent, admin)

    assert provisioned["ok"] is False and "service agent-ws-svc-taken conflicts" in provisioned["message"]
    state = _agent_state(agent, admin)
    assert (state["lifecycle"], state["workspace"], state["conflict_kind"], state["conflict_name"]) == (
        "READY",
        "ws-svc-taken",
        "SERVICE",
        "agent-ws-svc-taken",
    )
    assert (state["can_provision"], state["can_reprovision"], state["can_adopt"], state["can_replace"]) == (
        False,
        False,
        True,
        True,
    )
    operator.calls.clear()

    replaced = _action("replace_agent", agent, admin)

    assert replaced == {"ok": True, "message": "Provisioned “agent-ws-svc-taken”.", "code": None}
    assert [name for name, _ in operator.calls][:4] == [
        "workspace_status",
        "workspace_status",
        "destroy_service",
        "destroy_workspace",
    ]
    assert _agent_state(agent, admin)["conflict_kind"] is None


def test_reprovision_agent_records_a_service_conflict(composed_tables: None, monkeypatch: Any) -> None:
    """A 409 while recreating the service is recorded, keeping the workspace and forgetting the destroyed service."""

    admin = _platform_admin("agt-reprov-conflict-admin")
    agent = _provisionable_agent(
        admin,
        "Rebot",
        slug="agt-reprov-conflict-tpl",
        workspace="ws-keep",
        service="svc-old",
        lifecycle="ready",
        runtime_status="running",
    )
    operator = _Operator(
        workspaces={"ws-keep": ("ref:agent-default", {"agent_name": "Rebot"})},
        mounts={"svc-old": "ws-keep", "agent-ws-keep": "ws-keep"},
    ).install(monkeypatch)

    result = _action("reprovision_agent", agent, admin)

    assert result["ok"] is False and "service agent-ws-keep conflicts" in result["message"]
    assert ("destroy_service", "svc-old") in operator.calls
    state = _agent_state(agent, admin)
    assert (state["lifecycle"], state["workspace"], state["service"]) == ("READY", "ws-keep", "")
    assert (state["conflict_kind"], state["conflict_name"], state["can_adopt"]) == (
        "SERVICE",
        "agent-ws-keep",
        True,
    )


def _reprovisionable(admin: Any, monkeypatch: Any, *, slug: str) -> tuple[Any, _Operator]:
    """Seed a provisioned agent whose service the daemon will recreate under the same name."""

    agent = _provisionable_agent(
        admin,
        "Rebot",
        slug=slug,
        workspace="ws-keep",
        service="agent-ws-keep",
        lifecycle="ready",
        runtime_status="running",
    )
    operator = _Operator(
        workspaces={"ws-keep": ("ref:agent-default", {"agent_name": "Rebot"})},
        mounts={"agent-ws-keep": "ws-keep"},
    ).install(monkeypatch)
    return agent, operator


def _service_conflict(name: str) -> OperatorDaemonConflict:
    """A classified 409 naming the service ``name``."""

    return OperatorDaemonConflict(
        f"operator POST up: HTTP 409: service {name} conflicts: already exists",
        status_code=409,
        kind=OperatorInstanceKind.SERVICE,
        name=name,
    )


def test_reprovision_agent_never_forgets_the_new_service_on_a_later_conflict(
    composed_tables: None, monkeypatch: Any
) -> None:
    """The old service is forgotten when destroyed; the new one, same name, stays recorded."""

    admin = _platform_admin("agt-reprov-keep-new-admin")
    agent, operator = _reprovisionable(admin, monkeypatch, slug="agt-reprov-keep-new-tpl")
    operator.fail["start_service"] = _service_conflict("agent-sidecar")

    result = _action("reprovision_agent", agent, admin)

    assert result["ok"] is False and "agent-sidecar conflicts" in result["message"]
    assert operator.mounts == {"agent-ws-keep": "ws-keep"}  # destroyed, then created again
    state = _agent_state(agent, admin)
    assert (state["workspace"], state["service"], state["conflict_kind"], state["conflict_name"]) == (
        "ws-keep",
        "agent-ws-keep",
        "SERVICE",
        "agent-sidecar",
    )


def test_a_409_over_an_instance_this_agent_records_is_a_plain_failure(composed_tables: None, monkeypatch: Any) -> None:
    """It is never recorded as a conflict the agent "does not record"; the verb's own creation is undone."""

    admin = _platform_admin("agt-own-409-admin")
    agent, operator = _reprovisionable(admin, monkeypatch, slug="agt-own-409-tpl")
    operator.fail["start_service"] = _service_conflict("agent-ws-keep")

    result = _action("reprovision_agent", agent, admin)

    assert result["ok"] is False and "service agent-ws-keep conflicts" in result["message"]
    state = _agent_state(agent, admin)
    assert (state["conflict_kind"], state["service"], state["workspace"]) == (None, "", "ws-keep")
    assert "does not record" not in state["last_error"]
    assert operator.mounts == {}  # the new service this verb created was rolled back


@pytest.mark.parametrize(
    ("recorded", "conflict", "mounts", "refusal"),
    [
        (
            {"workspace": "ws-own"},
            ("workspace", "ws-taken"),
            {"agent-ws-taken": "ws-taken"},
            "already records workspace “ws-own”, so it cannot also take over “ws-taken”",
        ),
        (
            {"workspace": "ws-own", "service": "svc-own"},
            ("service", "agent-ws-own"),
            {"agent-ws-own": "ws-own"},
            "already records service “svc-own”, so it cannot also take over “agent-ws-own”",
        ),
    ],
)
def test_a_conflicting_instance_never_overwrites_one_this_agent_records(
    composed_tables: None,
    monkeypatch: Any,
    recorded: dict[str, str],
    conflict: tuple[str, str],
    mounts: dict[str, str],
    refusal: str,
) -> None:
    """Adopt refuses, and Deprovision destroys only the agent's own instances, leaving the other in place."""

    admin = _platform_admin(f"agt-own-overwrite-{conflict[0]}-admin")
    agent = _provisionable_agent(
        admin,
        "Taken",
        slug=f"agt-own-overwrite-{conflict[0]}-tpl",
        lifecycle="ready",
        runtime_status="error",
        conflict_kind=conflict[0],
        conflict_name=conflict[1],
        **recorded,
    )
    identity = {"agent_name": "Taken"}
    operator = _Operator(
        workspaces={"ws-own": ("ref:agent-default", identity), "ws-taken": ("ref:agent-default", identity)},
        mounts=mounts,
    ).install(monkeypatch)

    adopted = _action("adopt_agent", agent, admin)

    assert adopted["ok"] is False and refusal in adopted["message"]
    assert _agent_state(agent, admin)["conflict_name"] == conflict[1]

    deprovisioned = _action("deprovision_agent", agent, admin)

    assert deprovisioned["ok"] is True and f"Left “{conflict[1]}” in place" in deprovisioned["message"]
    destroyed = {name for verb, name in operator.calls if verb.startswith("destroy")}
    assert destroyed == set(recorded.values())
    assert conflict[1] in {*operator.workspaces, *operator.mounts}


@pytest.mark.parametrize("verb", ["adopt_agent", "replace_agent", "deprovision_agent"])
def test_a_service_conflict_without_a_recorded_workspace_cannot_be_verified(
    composed_tables: None, monkeypatch: Any, verb: str
) -> None:
    """The daemon is never asked about an empty workspace name; only Deprovision proceeds, destroying nothing."""

    admin = _platform_admin(f"agt-unverifiable-{verb}-admin")
    agent = _provisionable_agent(
        admin,
        "Orphan",
        slug=f"agt-unverifiable-{verb}-tpl",
        runtime_status="error",
        conflict_kind="service",
        conflict_name="agent-ws-orphan",
    )
    operator = _Operator(mounts={"agent-ws-orphan": "ws-orphan"}).install(monkeypatch)

    result = _action(verb, agent, admin)

    assert "cannot be verified" in result["message"]
    assert operator.calls == []
    state = _agent_state(agent, admin)
    if verb == "deprovision_agent":
        assert result["ok"] is True
        assert (state["lifecycle"], state["conflict_kind"], state["can_provision"]) == ("DEPROVISIONED", None, True)
    else:
        assert result["ok"] is False
        assert state["conflict_name"] == "agent-ws-orphan"


def test_deprovision_agent_destroys_the_verified_conflicting_instance(composed_tables: None, monkeypatch: Any) -> None:
    """Deprovision records the conflicting instance as the agent's own, then destroys it."""

    admin = _platform_admin("agt-deprov-conflict-admin")
    agent, operator = _conflicted_agent(admin, monkeypatch, slug="agt-deprov-conflict-tpl")

    result = _action("deprovision_agent", agent, admin)

    assert result == {"ok": True, "message": "Deprovisioned.", "code": None}
    assert operator.calls == [
        ("workspace_status", "ws-taken"),
        ("destroy_service", "agent-ws-taken"),
        ("destroy_workspace", "ws-taken"),
    ]
    assert (operator.workspaces, operator.mounts) == ({}, {})
    state = _agent_state(agent, admin)
    assert (state["lifecycle"], state["conflict_kind"], state["can_provision"], state["can_delete"]) == (
        "DEPROVISIONED",
        None,
        True,
        True,
    )


def test_deprovision_agent_keeps_a_recorded_instance_whose_destroy_failed(
    composed_tables: None, monkeypatch: Any
) -> None:
    """After the record, a refused workspace destroy keeps that workspace on the agent for a retry."""

    admin = _platform_admin("agt-deprov-retry-admin")
    agent, operator = _conflicted_agent(admin, monkeypatch, slug="agt-deprov-retry-tpl")
    operator.fail["destroy_workspace"] = RuntimeError("workspace has unpushed work")

    result = _action("deprovision_agent", agent, admin)

    assert result["ok"] is False and "unpushed work" in result["message"]
    assert operator.mounts == {}
    state = _agent_state(agent, admin)
    assert (state["lifecycle"], state["workspace"], state["service"], state["conflict_kind"]) == (
        "READY",
        "ws-taken",
        "",
        None,
    )
    assert state["can_deprovision"] is True
    del operator.fail["destroy_workspace"]
    assert _action("deprovision_agent", agent, admin)["ok"] is True
    assert operator.workspaces == {}


@pytest.mark.parametrize(
    ("operator_state", "holder"),
    [({"template": "ref:stale-template"}, False), ({}, True)],
)
def test_deprovision_agent_clears_a_conflict_it_cannot_verify_without_destroying_anything(
    composed_tables: None, monkeypatch: Any, operator_state: dict[str, Any], holder: bool
) -> None:
    """The non-destructive way out: an instance that is not this agent's stays; the record is cleared."""

    admin = _platform_admin(f"agt-deprov-foreign-{holder}-admin")
    agent, operator = _conflicted_agent(admin, monkeypatch, slug=f"agt-deprov-foreign-{holder}-tpl", **operator_state)
    if holder:
        owner = _agent_holding(admin, workspace="ws-taken")

    result = _action("deprovision_agent", agent, admin)

    assert result["ok"] is True and "Left “ws-taken” in place" in result["message"]
    assert [name for name, _ in operator.calls] == ["workspace_status"]
    assert "ws-taken" in operator.workspaces and operator.mounts == {"agent-ws-taken": "ws-taken"}
    state = _agent_state(agent, admin)
    assert (state["lifecycle"], state["conflict_kind"], state["can_provision"]) == ("DEPROVISIONED", None, True)
    if holder:
        with system_context(reason="test.agents.deprov_foreign.verify"):
            owner.refresh_from_db()
        assert owner.workspace == "ws-taken"


def test_two_agents_cannot_record_the_same_operator_instance(composed_tables: None) -> None:
    """The partial unique constraints allow any number of blank names, and one agent per instance."""

    admin = _platform_admin("agt-unique-admin")
    _agent_holding(admin, workspace="ws-once", service="svc-once")
    with system_context(reason="test.agents.unique.seed"):
        Agent.objects.create(name="Blank A", owner=admin)
        Agent.objects.create(name="Blank B", owner=admin)
        for duplicate in ({"workspace": "ws-once"}, {"service": "svc-once"}):
            with pytest.raises(IntegrityError), transaction.atomic():
                Agent.objects.create(name="Copy", owner=admin, **duplicate)


def test_agent_verb_eligibility_selects_without_per_row_queries(composed_tables: None) -> None:
    """``can_*`` answer from each row's own columns and annotations, so a list costs the same at any size."""

    admin = _platform_admin("agt-eligibility-admin")
    query = """
    { agents { can_provision can_adopt can_replace can_reprovision can_deprovision can_delete } }
    """

    def queries_for(count: int) -> int:
        with system_context(reason="test.agents.eligibility.seed"):
            existing = Agent.objects.count()
            for index in range(existing, count):
                agent = Agent.objects.create(
                    name=f"Row {index}",
                    owner=admin,
                    runtime_class="claude_code",
                    conflict_kind="workspace" if index % 2 else None,
                    conflict_name=f"ws-row-{index}" if index % 2 else "",
                )
                if index % 3 == 0:
                    AgentSession.objects.create(agent=agent, owner=admin)
        with CaptureQueriesContext(connection) as captured:
            rows = _data(_execute(_schema(), query, user=admin))["agents"]
        assert len(rows) == count
        return len(captured.captured_queries)

    assert queries_for(1) == queries_for(3) == queries_for(6)


def test_provision_agent_refuses_when_inference_credential_has_no_secret(
    composed_tables: None, monkeypatch: Any
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
    operator = _Operator().install(monkeypatch)

    result = _action("provision_agent", agent, admin)

    assert result["ok"] is False and "inference credential" in result["message"]
    assert operator.calls == []  # refused before any daemon call or render
    with system_context(reason="test.agents.nokey.verify"):
        agent.refresh_from_db()
        # Never flipped to PROVISIONING; the lifecycle stays a fresh DRAFT and nothing rendered.
        assert str(agent.lifecycle) == "draft"
        assert (agent.workspace, agent.service) == ("", "")


def test_agent_inference_credential_override_wins_over_model_chain(composed_tables: None) -> None:
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


def test_agent_chat_endpoint_mints_route_token_and_is_admin_gated(composed_tables: None, monkeypatch: Any) -> None:
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

    monkeypatch.setattr("angee.agents.runtimes.OperatorDaemon", _FakeDaemon)

    query = """
        mutation Chat($id: ID!) {
          agent_chat_endpoint(id: $id) { url token expires_at mcp_servers model_handle protocol_version }
        }
    """
    assert _execute(console := _schema(), query, {"id": agent_id}, user=plain).errors is not None
    endpoint = _data(_execute(console, query, {"id": agent_id}, user=admin))["agent_chat_endpoint"]

    assert endpoint["url"] == "wss://svc-chat.example.test/"
    assert endpoint["token"] == "jwt-route"
    assert endpoint["expires_at"] == "2026-06-15T00:00:00Z"
    assert endpoint["model_handle"] == "claude-opus-4-8"
    assert endpoint["protocol_version"] == 1
    assert endpoint["mcp_servers"] == {
        "notes": {"type": "http", "url": "http://host.docker.internal:8101/mcp/notes/"},
    }
    # The token is minted per actor (the session user, `auth/user:<id>`), scoped to the
    # agent's routed service, on the chat TTL — never as the operator admin bearer.
    assert len(minted) == 1
    actor, service, ttl = minted[0]
    assert actor.startswith("auth/user:") and service == "svc-chat" and ttl == "2h"


@pytest.mark.parametrize("secure", [False, True])
def test_in_process_chat_endpoint_uses_cookie_origin_and_call_permission(
    composed_tables: None,
    secure: bool,
    settings: Any,
) -> None:
    settings.ALLOWED_HOSTS = ["localhost"]
    owner = User.objects.create_user(username="agt-endpoint-owner")
    stranger = User.objects.create_user(username="agt-endpoint-stranger")
    with system_context(reason="test.agents.in_process_endpoint"):
        agent = Agent.objects.create(name="Assistant", owner=owner, runtime_class="pydantic", runtime_status="running")
    query = """
        mutation Chat($id: ID!) {
          agent_chat_endpoint(id: $id) { url token expires_at protocol_version mcp_servers model_handle }
        }
    """
    request = RequestFactory().post("/graphql/console/", secure=secure, HTTP_HOST="localhost:5173")
    request.user = owner
    console = _schema()
    endpoint = _data(execute_schema(console, query, {"id": str(agent.sqid)}, request=request))["agent_chat_endpoint"]
    assert endpoint == {
        "url": f"{'wss' if secure else 'ws'}://localhost:5173/acp/agents/{agent.sqid}/",
        "token": "",
        "expires_at": "",
        "protocol_version": 2,
        "mcp_servers": {},
        "model_handle": "",
    }
    assert _execute(console, query, {"id": str(agent.sqid)}, user=stranger).errors
    with system_context(reason="test.agents.unavailable_endpoint"):
        Agent.objects.filter(pk=agent.pk).update(runtime_status="stopped")
    assert _execute(console, query, {"id": str(agent.sqid)}, user=owner).errors


def test_container_chat_endpoint_still_requires_platform_admin_for_its_owner(composed_tables: None) -> None:
    owner = User.objects.create_user(username="agt-container-owner")
    with system_context(reason="test.agents.container_endpoint"):
        agent = Agent.objects.create(name="Container", owner=owner, runtime_class="claude_code", service="svc")
    result = _execute(
        _schema(),
        "mutation($id: ID!) { agent_chat_endpoint(id: $id) { url } }",
        {"id": str(agent.sqid)},
        user=owner,
    )
    assert result.errors
    assert "Platform admin" in result.errors[0].message


def test_agent_chat_endpoint_errors_when_agent_not_running(composed_tables: None) -> None:
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
    composed_tables: None,
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
          resolve_session_for_view(view: $view) { agent_name status model_handle runs_in_process }
        }
    """
    view = {"kind": "record", "type": "notes/note", "sqid": "nte_x"}
    session = _data(_execute(console := _schema(), query, {"view": view}, user=admin))["resolve_session_for_view"]

    assert session["agent_name"] == "Sidekick"
    assert session["status"] == "running"
    assert session["model_handle"] == "claude-opus-4-8"
    assert session["runs_in_process"] is False

    # A platform admin with no running agent gets null, not an error.
    other = _platform_admin("agt-session-none")
    none_session = _data(_execute(console, query, {"view": view}, user=other))["resolve_session_for_view"]
    assert none_session is None

    # The side chatter is actor-scoped, not admin-scoped: a normal user with no
    # running agent also gets null instead of a permission error.
    viewer = User.objects.create_user(username="agt-session-viewer", email="viewer@example.com")
    viewer_session = _data(_execute(console, query, {"view": view}, user=viewer))["resolve_session_for_view"]
    assert viewer_session is None


def test_agent_session_reads_and_view_resolution(composed_tables: None, capture_tasks: list[Any]) -> None:
    """The GraphQL read side retains caller-scoped sessions, turns and view resolution."""

    owner = User.objects.create_user(username="agt-chat-owner")
    reader = User.objects.create_user(username="agt-chat-reader")
    with system_context(reason="test.agents.chat.seed"):
        agent = Agent.objects.create(name="Assistant", owner=owner, runtime_class="pydantic", runtime_status="running")
        write_relationships(
            [
                RelationshipTuple(resource=to_object_ref(agent), relation="reader", subject=to_subject_ref(reader)),
            ]
        )
    with actor_context(owner):
        session = AgentSession.objects.start(agent, owner=owner, context={"kind": "list"})
        turn = session.post("Private question", context={"kind": "record"})
    console = _schema()
    target = _data(
        _execute(
            console,
            "{ resolve_session_for_view(view: {}) { runs_in_process session_id } }",
            user=owner,
        )
    )["resolve_session_for_view"]
    assert target == {"runs_in_process": True, "session_id": str(session.sqid)}
    query = """
        query Chat($id: String!) {
          agent_sessions { id context agent { permissions } }
          agent_turns { id prompt context }
          agent_turns_by_pk(id: $id) { id prompt context }
        }
    """
    mine = _data(_execute(console, query, {"id": str(turn.sqid)}, user=owner))
    assert mine == {
        "agent_sessions": [{"id": str(session.sqid), "context": {"kind": "list"}, "agent": {"permissions": ["call"]}}],
        "agent_turns": [{"id": str(turn.sqid), "prompt": "Private question", "context": {"kind": "record"}}],
        "agent_turns_by_pk": {"id": str(turn.sqid), "prompt": "Private question", "context": {"kind": "record"}},
    }
    hidden = _data(_execute(console, query, {"id": str(turn.sqid)}, user=reader))
    assert hidden == {"agent_sessions": [], "agent_turns": [], "agent_turns_by_pk": None}
    mutation_fields = _data(_execute(console, "{ __schema { mutationType { fields { name } } } }", user=owner))
    assert not any("agent_turns" in field["name"] for field in mutation_fields["__schema"]["mutationType"]["fields"])


def test_deprovision_in_process_agent_closes_open_sessions(composed_tables: None) -> None:
    """In-process teardown closes persisted sessions through their cancellation verb."""

    admin = _platform_admin("agt-chat-deprovision")
    with system_context(reason="test.agents.deprovision_sessions.seed"):
        agent = Agent.objects.create(
            name="Assistant",
            owner=admin,
            runtime_class="pydantic",
            runtime_status="running",
            lifecycle="ready",
        )
        for status in ("running", "awaiting_approval"):
            session = AgentSession.objects.create(agent=agent, owner=admin, status=status)
            AgentTurn.objects.create(session=session, index=1, prompt="Message", status=status)
    result = _data(
        _execute(
            _schema(),
            "mutation Deprovision($id: ID!) { deprovision_agent(id: $id) { ok message } }",
            {"id": str(agent.sqid)},
            user=admin,
        )
    )["deprovision_agent"]
    assert result == {"ok": True, "message": "Deprovisioned."}
    with system_context(reason="test.agents.deprovision_sessions.verify"):
        assert set(agent.sessions.values_list("status", flat=True)) == {"closed"}
        assert set(AgentTurn.objects.filter(session__agent=agent).values_list("status", flat=True)) == {"canceled"}


def test_provision_workspace_inputs_from_agent_fields(composed_tables: None) -> None:
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
    composed_tables: None,
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


def test_render_view_context_never_previews_encrypted_secret(composed_tables: None) -> None:
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
    composed_tables: None,
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


def test_mcp_secrets_derive_per_agent_bearer_for_internal_server(composed_tables: None) -> None:
    """An internal server syncs the per-agent derived bearer, not the raw credential secret."""

    owner = User.objects.create_user(username="agt-mcpint-owner", email="mcpint@example.com")
    with system_context(reason="test.agents.mcp_secrets.internal"):
        credential = Credential.objects.create_local_credential(
            owner, kind=str(CredentialKind.STATIC_TOKEN), name="angee-bearer", material={"api_key": "tok-internal"}
        )
        agent = Agent.objects.create(name="Internal Cfg", owner=owner)
        server = MCPServer.objects.create(
            name="angee",
            url="http://host.docker.internal:8111/mcp/",
            credential=credential,
            placement=MCPPlacement.INTERNAL,
        )
        agent.mcp_servers.add(server)
        secrets = agent.mcp_secrets()
        secret_name = agent.mcp_secret_name(server)
        bearer = server.bearer_for(agent)

    assert secrets[secret_name] == bearer
    assert bearer.startswith(f"{agent.sqid}.")
    assert bearer != "tok-internal"  # the raw credential secret never leaves the platform


def test_mcp_config_resolves_builtin_server_from_settings(
    composed_tables: None,
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


def test_provision_service_inputs_credential_drives_auth_env(composed_tables: None) -> None:
    """The provider backend maps credential kind to service auth env."""

    owner = User.objects.create_user(username="agt-svci-owner", email="svci@example.com")
    static_provider = _provider("agt-svc-static", backend_class="anthropic", name="S")
    oauth_provider = _provider("agt-svc-oauth", backend_class="anthropic", kind=CredentialKind.OAUTH, name="O")
    with system_context(reason="test.agents.provision_inputs.service"):
        static_model = InferenceModel.objects.create(
            provider=static_provider,
            name="claude-3",
        )
        static_agent = Agent.objects.create(name="Static", owner=owner, model=static_model, runtime_class="claude_code")
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


def test_provision_service_inputs_allow_an_unauthenticated_backend(composed_tables: None) -> None:
    """Only an in-process runtime is ready for a no-auth backend."""

    owner = User.objects.create_user(username="agt-svc-ollama-agent", email="ollama@example.com")
    provider = _provider("agt-svc-ollama", backend_class="ollama", name="Ollama")
    with system_context(reason="test.agents.provision_inputs.ollama"):
        provider.credential = None
        provider.save(update_fields=["credential"])
        model = InferenceModel.objects.create(provider=provider, name="ollama/llama3.2:latest")
        agent = Agent.objects.create(name="Local", owner=owner, model=model, runtime_class="pydantic")
        service_agent = Agent.objects.create(name="Local service", owner=owner, model=model, runtime_class="opencode")

        assert agent.inference_credential_ready() is True
        assert agent.provision_service_inputs() == {"model": "ollama/llama3.2:latest"}
        assert agent.provision_inference_secret() == ""
        assert service_agent.inference_credential_ready() is False


def test_provision_service_inputs_opencode_refuses_oauth_credential(composed_tables: None) -> None:
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
def test_provision_service_inputs_opencode_oauth_when_enabled(composed_tables: None) -> None:
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
def test_provision_service_inputs_opencode_oauth_requires_refresh_token(composed_tables: None) -> None:
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


def test_provision_service_inputs_workspace_only_runtime_skips_service_auth(composed_tables: None) -> None:
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


def test_claude_code_service_inputs_use_provider_model_name(composed_tables: None) -> None:
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


def test_opencode_service_inputs_keep_selected_broker_model(composed_tables: None) -> None:
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


def _public_id(sqid: str) -> str:
    """Return the public id for a console node."""

    return str(sqid)


def test_inference_provider_resolver_remains_an_object() -> None:
    """Computed object projections must not become default relation reads/pickers."""

    resources = {item.model_label: item for item in _schema().angee_resources}
    for label, names in {"integrate.Integration": ["inference_provider"]}.items():
        resource = resources[label]
        fields = {field.name: field for field in resource.fields}
        for name in names:
            assert fields[name].kind == "object"
            assert fields[name].scalar is None
            assert not fields[name].relation_object
            assert resource.query.fields[name].kind == "object"
            assert resource.query.fields[name].relation is None
            assert resource.query.fields[name].row is None
