"""Agent deferred inputs."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context

from angee.agents_integrate_anthropic.backend import AnthropicInferenceBackend
from tests.test_agents import InferenceModel, _provider
from tests.test_agents_graphql import Agent
from tests.test_agents_graphql import agents_console_tables as agents_console_tables


def test_deployment_identity_refreshes_deferred_handle(agents_console_tables: None, monkeypatch):
    provider = _provider("deferred-identity", backend_class="ollama")
    with system_context(reason="test.agents.identity"):
        model = InferenceModel.objects.create(provider=provider, name="catalogue", config={"provider_model": "native"})
        model = InferenceModel.objects.only("pk").get(pk=model.pk)
    with system_context(reason="test.agents.identity.read"):
        identity = model.deployment_identity()
    assert identity["native_model"] == "native"
    assert identity["endpoint"] == "http://localhost:11434/v1"


@pytest.mark.parametrize("has_model", [False, True])
def test_agent_error_classifier_loads_nullable_deferred_model(agents_console_tables: None, monkeypatch, has_model):
    """Agent error classifier loads nullable deferred model."""
    provider = _provider("deferred-error", backend_class="anthropic")
    with system_context(reason="test.agents.error.seed"):
        model = InferenceModel.objects.create(provider=provider, name="claude") if has_model else None
        agent = Agent.objects.create(name="Error classifier", owner=provider.owner, model=model)
        agent = Agent.objects.only("pk").get(pk=agent.pk)
    seen: list[str] = []

    def classify(backend, error):
        seen.append(backend.provider._state.db)
        return isinstance(error, TimeoutError)

    monkeypatch.setattr(AnthropicInferenceBackend, "is_transient_error", classify)
    assert agent.is_transient_inference_error(TimeoutError()) is has_model
    assert seen == (["default"] if has_model else [])


@pytest.mark.parametrize("has_credential", [False, True])
def test_provision_inputs_and_readiness_load_uncached_relations(
    agents_console_tables: None, monkeypatch: pytest.MonkeyPatch, has_credential: bool
) -> None:
    """Provision inputs and readiness load uncached relations."""
    provider = _provider("routed-inputs", backend_class="ollama")
    owner = get_user_model().objects.create_user(username="routed-inputs-agent-owner")
    with system_context(reason="test.agents.inputs.seed"):
        if not has_credential:
            provider.credential = None
            provider.save(update_fields=["credential"])
        model = InferenceModel.objects.create(provider=provider, name="ollama/routed")
        agent = Agent.objects.create(name="Inputs", owner=owner, model=model, runtime_class="pydantic")
        agent = Agent.objects.get(pk=agent.pk)
    with system_context(reason="test.agents.inputs"):
        assert agent.inference_credential_ready()
        assert agent.provision_service_inputs() == {"model": "ollama/routed"}
