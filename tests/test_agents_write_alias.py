"""Agent writes retain their selected database after admission and external work."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import router, transaction
from pydantic_ai.messages import ModelRequest, UserPromptPart
from rebac import system_context

from angee.agents.models import AgentLifecycle
from angee.agents.provisioning import provision_agent
from angee.agents_integrate_anthropic.backend import AnthropicInferenceBackend
from angee.base.db import get_write_alias
from angee.graphql.ids import PublicID
from tests.test_agents import SKILL_BLOBS, SKILL_TREE, InferenceModel, Skill, _FakeAnthropicClient, _provider
from tests.test_agents import inference_http as inference_http
from tests.test_agents_graphql import (
    Agent,
    _provisionable_agent,
    agents_provisioning,
)
from tests.test_agents_graphql import (
    agents_console_tables as agents_console_tables,
)
from tests.test_integrate_vcs import REPOS, Repository, Source, _vcs_bridge
from tests.test_transitions import TransitionRouter


@pytest.fixture
def agent_write_alias(
    agents_console_tables: None, database_alias: Callable[[str], AbstractContextManager[str]]
) -> Iterator[str]:
    """Expose the agent schema through the shared connection factory."""

    with database_alias("agents_writer") as alias:
        yield alias


@pytest.mark.parametrize("entry", ["router", "manager", "using", "instance"])
def test_inference_sync_routes_initial_and_existing_model_writes(
    agent_write_alias: str, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """Both insert and update use the entry alias, including their transaction."""

    provider = _provider(
        "routed-models",
        backend_class="stub_inference",
        config={"stub_models": [{"handle": "routed", "display_name": "First"}]},
    )
    manager = InferenceModel.objects
    kwargs: dict[str, Any] = {}
    provider._state.db = None
    if entry == "manager":
        manager = manager.db_manager(agent_write_alias)
    elif entry == "using":
        kwargs["using"] = agent_write_alias
        provider._state.db = "other_persisted_database"
    elif entry == "instance":
        provider._state.db = agent_write_alias
    routing = TransitionRouter(agent_write_alias if entry == "router" else "other_writer")
    atomic = transaction.atomic
    aliases: list[str | None] = []

    def checked_atomic(using: str | None = None, **kwargs: Any) -> Any:
        aliases.append(using)
        assert using == agent_write_alias
        return atomic(using=using, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(router, "routers", [routing])
        patch.setattr(transaction, "atomic", checked_atomic)
        assert manager.sync_from_provider(provider, **kwargs) == 1
        provider.config["stub_models"][0]["display_name"] = "Updated"
        assert manager.sync_from_provider(provider, **kwargs) == 1
        with system_context(reason="test.agents.alias.catalogue"):
            row = InferenceModel.objects.using(agent_write_alias).get(provider_id=provider.pk)
            assert row.display_name == "Updated"
            assert row._state.db == agent_write_alias
    assert aliases and set(aliases) == {agent_write_alias}
    # Each separate sync operation resolves its alias; the provider is not mutated.
    assert routing.writes == ([provider, provider] if entry == "router" else [])


@pytest.mark.parametrize("entry", ["instance", "using"])
def test_agent_rename_updates_service_user_on_the_same_alias(
    agent_write_alias: str, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """The old-name lookup, row update, user lookup/update and deactivation stay bound."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(username="routed-agent-owner")
    with system_context(reason="test.agents.alias.seed"):
        agent = Agent.objects.create(name="Before", owner=owner)
    agent._state.db = agent_write_alias if entry == "instance" else "other_persisted_database"
    kwargs = {} if entry == "instance" else {"using": agent_write_alias}
    routing = TransitionRouter("other_writer")
    with monkeypatch.context() as patch, system_context(reason="test.agents.alias.rename"):
        patch.setattr(router, "routers", [routing])
        agent.name = "After"
        agent.save(update_fields=["name"], **kwargs)
        assert user_model._base_manager.using(agent_write_alias).get(pk=agent.user_id).first_name == "After"
        Agent.objects.db_manager(agent_write_alias).deactivate_service_user(agent)
        assert not user_model._base_manager.using(agent_write_alias).get(pk=agent.user_id).is_active
        agent.refresh_from_db(using=agent_write_alias)
        assert agent.name == "After"
    assert routing.writes == []


def test_provisioning_keeps_alias_through_external_work_callbacks_and_final_state(
    agent_write_alias: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Callbacks and completion persist on the admitted alias despite another writer router."""

    owner = get_user_model().objects.create_user(username="routed-provision-agent-owner")
    agent = _provisionable_agent(owner, "Routed provision", slug="routed-provision")
    observed: list[tuple[str, str]] = []

    def render(plan: Any, *, on_workspace_created: Any, on_service_created: Any) -> dict[str, str]:
        del plan
        on_workspace_created("routed-workspace")
        with system_context(reason="test.agents.alias.workspace"):
            row = Agent.objects.using(agent_write_alias).get(pk=agent.pk)
            observed.append((row.workspace, str(row.lifecycle)))
        on_service_created("routed-service")
        with system_context(reason="test.agents.alias.service"):
            row = Agent.objects.using(agent_write_alias).get(pk=agent.pk)
            observed.append((row.service, str(row.lifecycle)))
        return {"workspace": "routed-workspace", "service": "routed-service"}

    routing = TransitionRouter("other_writer")
    with monkeypatch.context() as patch:
        patch.setattr(router, "routers", [routing])
        patch.setattr(agents_provisioning, "_render_agent", render)
        result = provision_agent(PublicID(str(agent.sqid)), using=agent_write_alias)
        assert result.ok, result.message
        with system_context(reason="test.agents.alias.completed"):
            agent.refresh_from_db(using=agent_write_alias)
    assert observed == [
        ("routed-workspace", AgentLifecycle.PROVISIONING),
        ("routed-service", AgentLifecycle.PROVISIONING),
    ]
    assert agent.lifecycle == AgentLifecycle.READY
    assert agent.workspace == "routed-workspace"
    assert agent.service == "routed-service"
    assert routing.writes == []


def test_skill_sync_keeps_alias_through_upsert_prune_and_source_timestamp(
    agent_write_alias: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery results, stale-row pruning and source acknowledgement share one alias."""

    vcs = _vcs_bridge("routed-skills", config={"stub_repos": REPOS, "stub_tree": SKILL_TREE, "stub_blobs": SKILL_BLOBS})
    vcs.discover_repositories()
    with system_context(reason="test.agents.alias.skill.seed"):
        repository = Repository.objects.get(name="acme/widgets")
        source = Source.objects.create(repository=repository, kind="skill", path="skills")
    routing = TransitionRouter("other_writer")
    with monkeypatch.context() as patch, system_context(reason="test.agents.alias.skill.sync"):
        patch.setattr(router, "routers", [routing])
        manager = Skill.objects.db_manager(agent_write_alias)
        assert manager.sync_from_source(source) == 2
        type(vcs)._base_manager.using(agent_write_alias).filter(pk=vcs.pk).update(
            config={"stub_repos": REPOS, "stub_tree": SKILL_TREE[:1], "stub_blobs": SKILL_BLOBS}
        )
        assert manager.sync_from_source(source) == 1
        assert list(
            Skill.objects.using(agent_write_alias).filter(source_id=source.pk).values_list("name", flat=True)
        ) == ["Calculator"]
        source.refresh_from_db(using=agent_write_alias)
        assert source.last_synced_at is not None
    assert routing.writes == []


@pytest.mark.parametrize("operation", ["catalogue", "model_bind", "agent_bind", "infer", "provider_chat"])
@pytest.mark.parametrize("deferred", [False, True])
def test_inference_binding_reaches_sdk_credential_mutation(
    agent_write_alias: str, monkeypatch: pytest.MonkeyPatch, operation: str, inference_http: Any, deferred: bool
) -> None:
    """Catalogue and invocation bindings retain the alias through credential writes."""

    provider = _provider("routed-sdk", backend_class="anthropic", material={"api_key": "before"})
    credential_class = type(provider.credential)
    credential_id = provider.credential_id
    with system_context(reason="test.agents.alias.sdk.seed"):
        model = InferenceModel.objects.create(provider=provider, name="claude-sonnet-4-6")
        agent = Agent.objects.create(name="SDK binding", owner=provider.owner, model=model, runtime_class="pydantic")
        if deferred:
            model = InferenceModel.objects.using("default").only("pk").get(pk=model.pk)
            provider = type(provider).objects.using("default").only("pk").get(pk=provider.pk)
    refreshed: list[str] = []

    def refresh(credential: Any, *, using: str | None = None) -> None:
        using = get_write_alias(type(credential), using=using, instance=credential)
        assert using == agent_write_alias
        credential.update_material(api_key="after", using=using)
        refreshed.append(using)

    monkeypatch.setattr(credential_class, "ensure_fresh", refresh)
    monkeypatch.setattr(AnthropicInferenceBackend, "client_class", _FakeAnthropicClient)
    routing = TransitionRouter("other_writer")
    with monkeypatch.context() as patch, system_context(reason="test.agents.alias.sdk"):
        patch.setattr(router, "routers", [routing])
        if operation == "catalogue":
            assert InferenceModel.objects.db_manager(agent_write_alias).sync_from_provider(provider) == 4
            assert InferenceModel.objects.using(agent_write_alias).filter(provider_id=provider.pk).count() == 4
            assert _FakeAnthropicClient.instances[-1].kwargs == {"api_key": "after"}
        elif operation == "model_bind":
            assert model.bind(using=agent_write_alias) is not None
        elif operation == "agent_bind":
            assert agent.inference_model(using=agent_write_alias) is not None
        elif operation == "infer":
            result = model.infer([ModelRequest(parts=[UserPromptPart("Ping")])], using=agent_write_alias)
            assert result.response.text == "pong"
            assert result.usage["requests"] == 1
        else:
            response = provider.chat(
                model="claude-sonnet-4-6",
                messages=[ModelRequest(parts=[UserPromptPart("Ping")])],
                using=agent_write_alias,
            )
            assert response.text == "pong"
        stored = credential_class._base_manager.using(agent_write_alias).get(pk=credential_id)
        assert stored.reveal()["api_key"] == "after"
    assert refreshed == [agent_write_alias]
    assert routing.writes == []


def test_deployment_identity_refreshes_deferred_handle_on_selected_alias(agent_write_alias, monkeypatch):
    provider = _provider("identity-alias", backend_class="ollama")
    with system_context(reason="test.agents.alias.identity"):
        model = InferenceModel.objects.create(provider=provider, name="catalogue", config={"provider_model": "native"})
        model = InferenceModel.objects.using("default").only("pk").get(pk=model.pk)
    monkeypatch.setattr(router, "routers", [TransitionRouter("other_writer")])
    with system_context(reason="test.agents.alias.identity.read"):
        identity = model.deployment_identity(using=agent_write_alias)
    assert identity["native_model"] == "native"
    assert identity["endpoint"] == "http://localhost:11434/v1"


@pytest.mark.parametrize("has_model", [False, True])
def test_agent_error_classifier_binds_nullable_model_and_provider_to_alias(agent_write_alias, monkeypatch, has_model):
    """Deferred relations ignore another read router while missing models are terminal."""

    provider = _provider("error-alias", backend_class="anthropic")
    with system_context(reason="test.agents.alias.error.seed"):
        model = InferenceModel.objects.create(provider=provider, name="claude") if has_model else None
        agent = Agent.objects.create(name="Error classifier", owner=provider.owner, model=model)
        agent = Agent.objects.using("default").only("pk").get(pk=agent.pk)
    seen: list[str] = []

    def classify(backend, error):
        seen.append(backend.provider._state.db)
        return isinstance(error, TimeoutError)

    monkeypatch.setattr(AnthropicInferenceBackend, "is_transient_error", classify)
    monkeypatch.setattr(router, "routers", [TransitionRouter("other_writer")])
    assert agent.is_transient_inference_error(TimeoutError(), using=agent_write_alias) is has_model
    assert seen == ([agent_write_alias] if has_model else [])


@pytest.mark.parametrize("has_credential", [False, True])
def test_direct_provision_inputs_and_readiness_bind_uncached_relations(
    agent_write_alias: str, monkeypatch: pytest.MonkeyPatch, has_credential: bool
) -> None:
    """Direct model entrypoints work without the GraphQL admission's eager relations."""

    provider = _provider("routed-inputs", backend_class="ollama")
    owner = get_user_model().objects.create_user(username="routed-inputs-agent-owner")
    with system_context(reason="test.agents.alias.inputs.seed"):
        if not has_credential:
            provider.credential = None
            provider.save(update_fields=["credential"])
        model = InferenceModel.objects.create(provider=provider, name="ollama/routed")
        agent = Agent.objects.create(name="Inputs", owner=owner, model=model, runtime_class="pydantic")
        agent = Agent.objects.using(agent_write_alias).get(pk=agent.pk)
    routing = TransitionRouter("other_writer")
    with monkeypatch.context() as patch, system_context(reason="test.agents.alias.inputs"):
        patch.setattr(router, "routers", [routing])
        assert agent.inference_credential_ready(using=agent_write_alias)
        assert agent.provision_service_inputs(using=agent_write_alias) == {"model": "ollama/routed"}
    assert routing.writes == []
