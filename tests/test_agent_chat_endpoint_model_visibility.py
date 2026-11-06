"""The chat endpoint exposes a selected model only to its readers."""

from typing import Any

from django.test import RequestFactory
from rebac import system_context

from tests.conftest import execute_schema
from tests.test_agents_graphql import Agent, InferenceModel, User, _data, _provider, _schema


def test_owner_without_model_read_gets_no_handle_from_chat_endpoint(composed_tables: None, settings: Any) -> None:
    settings.ALLOWED_HOSTS = ["localhost"]
    owner = User.objects.create_user(username="private-model-owner")
    provider = _provider("private-provider")
    with system_context(reason="test.private_model.seed"):
        model = InferenceModel.objects.create(provider=provider, name="private-runtime-model")
        agent = Agent.objects.create(
            name="Private model agent", owner=owner, model=model, runtime_class="pydantic", runtime_status="running",
        )
    assert not InferenceModel.objects.with_actor(owner).filter(pk=model.pk).exists()
    request = RequestFactory().post("/graphql/console/", HTTP_HOST="localhost:5173")
    request.user = owner
    endpoint = _data(execute_schema(
        _schema(), "mutation($id: ID!) { agent_chat_endpoint(id: $id) { model_handle } }",
        {"id": str(agent.sqid)}, request=request,
    ))["agent_chat_endpoint"]
    assert endpoint["model_handle"] == ""
