"""Tests for the agents addon — skill discovery, inference model sync, and the
``SKILL.md`` parser.

Skill discovery reuses the integrate VCS inventory: the concrete
``VcsBridge``/``Repository``/``Source`` models and the ``stub`` backend live in
``tests.test_integrate_vcs``/``tests.conftest``, so this module imports them (a
second concrete ``Source`` for ``app_label="integrate"`` would collide in the
registry) and declares only the agents concretes. Inference sync rides on the
``stub_inference`` ``InferenceBackend`` whose canned models ride on ``provider.config``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.agents.models import Agent as AbstractAgent
from angee.agents.models import InferenceModel as AbstractInferenceModel
from angee.agents.models import InferenceProvider as AbstractInferenceProvider
from angee.agents.models import Skill as AbstractSkill
from angee.agents.skills import parse_skill_meta
from angee.agents_integrate_anthropic.backend import AnthropicInferenceBackend
from angee.agents_integrate_openai.backend import OpenAIInferenceBackend
from angee.integrate.credentials import CredentialKind
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    Credential,
    Integration,
    OAuthClient,
    _create_missing_tables,
    make_integration,
)
from tests.test_integrate_vcs import (
    REPOS,
    VCS_TEST_MODELS,
    Repository,
    Source,
    _vcs_bridge,
)


class Skill(AbstractSkill):
    """Concrete skill used by the agents discovery tests."""

    class Meta(AbstractSkill.Meta):
        """Django model options for the canonical test skill."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_skill"
        rebac_resource_type = "agents/skill"
        rebac_id_attr = "sqid"


class InferenceProvider(Integration, AbstractInferenceProvider):
    """Concrete inference provider (capability over an integration) used by tests."""

    class Meta(AbstractInferenceProvider.Meta):
        """Django model options for the canonical test inference provider."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_inference_provider"
        rebac_resource_type = "agents/inference_provider"
        rebac_id_attr = "sqid"


class InferenceModel(AbstractInferenceModel):
    """Concrete inference model catalogue row used by tests."""

    class Meta(AbstractInferenceModel.Meta):
        """Django model options for the canonical test inference model."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_inference_model"
        rebac_resource_type = "agents/inference_model"
        rebac_id_attr = "sqid"


AGENTS_TEST_MODELS = (Skill, InferenceProvider, InferenceModel)


def _provider(
    slug: str,
    *,
    backend_class: str = "manual",
    name: str = "Provider",
    kind: Any = CredentialKind.STATIC_TOKEN,
    material: dict[str, Any] | None = None,
    **attrs: Any,
) -> Any:
    """Create an inference provider child row with inherited integration fields."""

    return make_integration(
        slug,
        kind=kind,
        material=material,
        model=InferenceProvider,
        backend_class=backend_class,
        name=name,
        **attrs,
    )


SKILL_TREE = [
    {"path": "skills/calc/SKILL.md", "type": "blob", "oid": "a"},
    {"path": "skills/search/SKILL.md", "type": "blob", "oid": "b"},
    {"path": "skills/calc/README.md", "type": "blob", "oid": "c"},
]
SKILL_BLOBS = {
    "skills/calc/SKILL.md": "---\nname: Calculator\ndescription: arithmetic\n---\nbody",
    "skills/search/SKILL.md": "---\nname: Web Search\ndescription: search the web\n---\nbody",
}


@pytest.fixture()
def agents_tables(transactional_db: Any) -> Iterator[None]:
    """Create the iam/integrate/VCS/agents test tables and sync the REBAC schema."""

    del transactional_db
    created = _create_missing_tables(
        IAM_CONNECTION_TEST_MODELS + INTEGRATE_TEST_MODELS + VCS_TEST_MODELS + AGENTS_TEST_MODELS
    )
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


# --- parse_skill_meta (pure) --------------------------------------------------


def test_parse_skill_meta_reads_frontmatter() -> None:
    """The parser lifts name/description and keeps the rest as metadata."""

    descriptor = parse_skill_meta(b"---\nname: Calculator\ndescription: arithmetic\nversion: 2\n---\nbody")
    assert descriptor["name"] == "Calculator"
    assert descriptor["description"] == "arithmetic"
    assert descriptor["metadata"] == {"version": 2}


def test_parse_skill_meta_coerces_typed_values_json_safe() -> None:
    """An unquoted date becomes a ``date`` YAML-side; the parser keeps metadata JSON-safe."""

    descriptor = parse_skill_meta(b"---\nname: Dated\nreleased: 2024-01-15\n---\n")
    assert descriptor["metadata"] == {"released": "2024-01-15"}
    json.dumps(descriptor["metadata"])  # must not raise


def test_parse_skill_meta_tolerates_missing_or_malformed_frontmatter() -> None:
    """No frontmatter (or an unterminated block) yields an empty descriptor, not an error."""

    assert parse_skill_meta(b"# just a heading")["name"] == ""
    assert parse_skill_meta(b"---\nnot: [valid")["metadata"] == {}


# --- skill discovery ----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_skill_source_refresh_materializes_and_prunes(agents_tables: None) -> None:
    """A skill source refresh walks the tree for ``SKILL.md`` and upserts/prunes rows."""

    del agents_tables
    vcs = _vcs_bridge("skills", config={"stub_repos": REPOS, "stub_tree": SKILL_TREE, "stub_blobs": SKILL_BLOBS})
    vcs.discover_repositories()
    with system_context(reason="test"):
        repository = Repository.objects.get(name="acme/widgets")
        source = Source.objects.create(repository=repository, kind="skill", path="skills")

    # README.md is ignored — only the two SKILL.md markers materialize.
    assert source.refresh() == 2
    with system_context(reason="test read"):
        skills = [(skill.name, skill.path) for skill in Skill.objects.filter(source=source).order_by("name")]
    assert skills == [("Calculator", "skills/calc"), ("Web Search", "skills/search")]

    # Drop the search skill from the tree → the next refresh prunes its row. Reload
    # the source so it reads the updated config fresh (the action path loads it anew),
    # rather than the related chain cached on this Python object.
    with system_context(reason="test"):
        vcs.config = {"stub_repos": REPOS, "stub_tree": SKILL_TREE[:1], "stub_blobs": SKILL_BLOBS}
        vcs.save(update_fields=["config", "updated_at"])
        source = Source.objects.get(pk=source.pk)
    assert source.refresh() == 1
    with system_context(reason="test read"):
        assert [skill.name for skill in Skill.objects.filter(source=source)] == ["Calculator"]


# --- inference catalogue sync -------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_inference_provider_refresh_upserts_models(agents_tables: None) -> None:
    """Refreshing a provider upserts one ``InferenceModel`` per advertised spec."""

    del agents_tables
    provider = _provider(
        "anthropic",
        backend_class="stub_inference",
        name="Anthropic",
        config={
            "stub_models": [
                {
                    "handle": "claude-opus-4-8",
                    "display_name": "Claude Opus 4.8",
                    "model_use": "chat",
                    "context_window": 200000,
                },
                {"handle": "claude-haiku-4-5", "model_use": "chat"},
            ]
        },
    )

    assert provider.refresh_models() == 2
    with system_context(reason="test read"):
        models = {model.name: model for model in InferenceModel.objects.filter(provider=provider)}
    assert set(models) == {"claude-opus-4-8", "claude-haiku-4-5"}
    assert models["claude-opus-4-8"].display_name == "Claude Opus 4.8"
    assert models["claude-opus-4-8"].context_window == 200000
    # The spec omits display_name → it defaults to the wire handle.
    assert models["claude-haiku-4-5"].display_name == "claude-haiku-4-5"


@pytest.mark.django_db(transaction=True)
def test_inference_provider_materializes_backend_defaults(agents_tables: None) -> None:
    """Provider backend defaults land on direct child-row creates."""

    del agents_tables
    provider = make_integration(
        "provider-defaults",
        model=InferenceProvider,
        backend_class="anthropic",
    )

    assert provider.name == "Anthropic"
    assert provider.credential_env == "ANTHROPIC_API_KEY"


@pytest.mark.django_db(transaction=True)
def test_manual_backend_advertises_no_models(agents_tables: None) -> None:
    """The built-in ``manual`` backend lists nothing — its catalogue is hand-curated."""

    del agents_tables
    provider = _provider("manual-vendor", backend_class="manual", name="Manual")
    assert provider.refresh_models() == 0
    with system_context(reason="test read"):
        assert InferenceModel.objects.filter(provider=provider).count() == 0


@pytest.mark.django_db(transaction=True)
def test_inference_provider_service_environment_reads_provider_credential_env(
    agents_tables: None,
) -> None:
    """Provider service env exposes only the provider-declared credential token."""

    del agents_tables
    integration = make_integration("anthropic-env")
    with system_context(reason="test service env setup"):
        oauth_client = OAuthClient.objects.create(
            slug="anthropic-oauth",
            display_name="Anthropic OAuth",
            client_id="public-client",
        )
        credential = Credential.objects.upsert_for_user(
            integration.owner,
            oauth_client,
            CredentialKind.OAUTH,
            {"access_token": "oauth-token"},
        )
        provider = _provider(
            "anthropic-env-provider",
            backend_class="manual",
            name="Anthropic",
            credential=credential,
            credential_env="ANTHROPIC_OAUTH_TOKEN",
        )

    assert provider.service_environment() == {"ANTHROPIC_OAUTH_TOKEN": "oauth-token"}
    agent_like = SimpleNamespace(model=SimpleNamespace(provider=provider))
    assert AbstractAgent.service_environment(agent_like) == {"ANTHROPIC_OAUTH_TOKEN": "oauth-token"}

    with system_context(reason="test service env disabled"):
        provider.credential_env = ""
        provider.save(update_fields=["credential_env", "updated_at"])
        provider.refresh_from_db()

    assert provider.service_environment() == {}


class _FakeModelPage:
    """SDK-shaped iterable page over one or more model batches."""

    def __init__(self, *pages: list[Any]) -> None:
        self.pages = pages

    def __iter__(self) -> Iterator[Any]:
        for page in self.pages:
            yield from page


class _FakeAnthropicModels:
    """Small fake for the Anthropic SDK models resource."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.calls: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> _FakeModelPage:
        """Return SDK-shaped model pages."""

        self.calls.append(kwargs)
        return _FakeModelPage(
            [
                SimpleNamespace(
                    id="claude-sonnet-4-6",
                    display_name="Claude Sonnet 4.6",
                    max_input_tokens=200000,
                    max_tokens=64000,
                    capabilities={"vision": True},
                )
            ],
            [
                SimpleNamespace(
                    id="claude-opus-4-8",
                    display_name="Claude Opus 4.8",
                    max_input_tokens=200000,
                    max_tokens=32000,
                    capabilities={"vision": True},
                )
            ],
        )


class _FakeAnthropicMessages:
    """Small fake for the Anthropic SDK messages resource."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        """Record a message request and return an SDK-shaped response."""

        self.calls.append(kwargs)
        return SimpleNamespace(
            id="msg_1",
            type="message",
            content=[SimpleNamespace(type="text", text="pong")],
            usage=SimpleNamespace(input_tokens=3, output_tokens=1),
        )


class _FakeAnthropicClient:
    """Small fake for ``anthropic.Anthropic``."""

    instances: list[Any] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.models = _FakeAnthropicModels(self)
        self.messages = _FakeAnthropicMessages(self)
        self.instances.append(self)


class _FakeOpenAIModels:
    """Small fake for the OpenAI SDK models resource."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.calls: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> _FakeModelPage:
        """Return SDK-shaped model pages."""

        self.calls.append(kwargs)
        return _FakeModelPage(
            [
                SimpleNamespace(
                    id="gpt-4.1",
                    owned_by="openai",
                ),
                SimpleNamespace(id="text-embedding-3-large", owned_by="openai"),
            ],
            [
                SimpleNamespace(id="gpt-4.2", owned_by="openai"),
                SimpleNamespace(id="gpt-image-1", owned_by="openai"),
                SimpleNamespace(id="gpt-4o-transcribe", owned_by="openai"),
            ],
        )


class _FakeOpenAICompletions:
    """Small fake for the OpenAI SDK chat completions resource."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        """Record a chat completion request and return an SDK-shaped response."""

        self.calls.append(kwargs)
        return SimpleNamespace(
            id="chatcmpl_1",
            choices=[SimpleNamespace(message=SimpleNamespace(content="pong"))],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1, total_tokens=4),
        )


class _FakeOpenAIChat:
    """Small fake for the OpenAI SDK chat resource."""

    def __init__(self, client: Any) -> None:
        self.completions = _FakeOpenAICompletions(client)


class _FakeOpenAIClient:
    """Small fake for ``openai.OpenAI``."""

    instances: list[Any] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.models = _FakeOpenAIModels(self)
        self.chat = _FakeOpenAIChat(self)
        self.instances.append(self)


@pytest.mark.django_db(transaction=True)
def test_anthropic_backend_refresh_syncs_native_and_broker_models(
    agents_tables: None,
    monkeypatch: Any,
) -> None:
    """Anthropic model sync emits native and broker-prefixed handles from the SDK."""

    del agents_tables
    _FakeAnthropicClient.instances.clear()
    monkeypatch.setattr(AnthropicInferenceBackend, "client_class", _FakeAnthropicClient)
    provider = _provider(
        "anthropic-sdk",
        backend_class="anthropic",
        name="Anthropic",
        material={"api_key": "api-key"},
    )

    assert provider.refresh_models() == 4

    client = _FakeAnthropicClient.instances[-1]
    assert client.kwargs == {"api_key": "api-key"}
    assert client.models.calls == [{"limit": 1000}]
    with system_context(reason="test read"):
        models = {model.name: model for model in InferenceModel.objects.filter(provider=provider)}
    assert set(models) == {
        "claude-sonnet-4-6",
        "anthropic/claude-sonnet-4-6",
        "claude-opus-4-8",
        "anthropic/claude-opus-4-8",
    }
    assert models["claude-sonnet-4-6"].display_name == "Claude Sonnet 4.6"
    assert models["claude-sonnet-4-6"].context_window == 200000
    assert models["claude-sonnet-4-6"].max_output_tokens == 64000
    assert models["anthropic/claude-sonnet-4-6"].display_name == "Claude Sonnet 4.6 (anthropic)"
    assert models["anthropic/claude-sonnet-4-6"].config["provider_model"] == "claude-sonnet-4-6"
    assert models["claude-opus-4-8"].display_name == "Claude Opus 4.8"


@pytest.mark.django_db(transaction=True)
def test_anthropic_model_chat_uses_sdk_messages_and_strips_broker_prefix(
    agents_tables: None,
    monkeypatch: Any,
) -> None:
    """Direct model chat uses the Anthropic SDK without provisioning an agent."""

    del agents_tables
    _FakeAnthropicClient.instances.clear()
    monkeypatch.setattr(AnthropicInferenceBackend, "client_class", _FakeAnthropicClient)
    provider = _provider(
        "anthropic-chat",
        backend_class="anthropic",
        name="Anthropic",
        material={"api_key": "api-key"},
    )
    with system_context(reason="test anthropic chat"):
        model = InferenceModel.objects.create(provider=provider, name="anthropic/claude-sonnet-4-6")

    response = model.chat(
        [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Ping"},
        ],
        system="Policy",
        max_tokens=12,
        temperature=0.2,
        options={"top_p": 0.9},
    )

    client = _FakeAnthropicClient.instances[-1]
    assert client.kwargs == {"api_key": "api-key"}
    assert client.messages.calls == [
        {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Ping"}],
            "max_tokens": 12,
            "system": "Policy\n\nBe brief.",
            "temperature": 0.2,
            "top_p": 0.9,
        }
    ]
    assert response.text == "pong"
    assert response.content == [{"type": "text", "text": "pong"}]
    assert response.usage == {"input_tokens": 3, "output_tokens": 1}


@pytest.mark.django_db(transaction=True)
def test_anthropic_backend_uses_auth_token_for_oauth_credentials(
    agents_tables: None,
    monkeypatch: Any,
) -> None:
    """OAuth Anthropic credentials bind as bearer auth, not API-key auth."""

    del agents_tables
    _FakeAnthropicClient.instances.clear()
    monkeypatch.setattr(AnthropicInferenceBackend, "client_class", _FakeAnthropicClient)
    provider = _provider(
        "anthropic-oauth-chat",
        kind=CredentialKind.OAUTH,
        backend_class="anthropic",
        name="Anthropic",
        material={"access_token": "oauth-token"},
    )
    with system_context(reason="test anthropic oauth chat"):
        model = InferenceModel.objects.create(provider=provider, name="claude-sonnet-4-6")

    assert model.chat([{"role": "user", "content": "Ping"}]).text == "pong"
    assert _FakeAnthropicClient.instances[-1].kwargs == {"auth_token": "oauth-token"}


@pytest.mark.django_db(transaction=True)
def test_anthropic_chat_rejects_options_that_override_owned_request_fields(
    agents_tables: None,
    monkeypatch: Any,
) -> None:
    """Provider-specific options cannot replace the selected catalogue model."""

    del agents_tables
    _FakeAnthropicClient.instances.clear()
    monkeypatch.setattr(AnthropicInferenceBackend, "client_class", _FakeAnthropicClient)
    provider = _provider(
        "anthropic-owned-options",
        backend_class="anthropic",
        name="Anthropic",
        material={"api_key": "api-key"},
    )
    with system_context(reason="test anthropic options guard"):
        model = InferenceModel.objects.create(provider=provider, name="claude-sonnet-4-6")

    with pytest.raises(ValueError, match="model"):
        model.chat([{"role": "user", "content": "Ping"}], options={"model": "other"})


@pytest.mark.django_db(transaction=True)
def test_openai_backend_refresh_syncs_native_and_broker_models(
    agents_tables: None,
    monkeypatch: Any,
) -> None:
    """OpenAI model sync emits native and broker-prefixed handles from the SDK."""

    del agents_tables
    _FakeOpenAIClient.instances.clear()
    monkeypatch.setattr(OpenAIInferenceBackend, "client_class", _FakeOpenAIClient)
    provider = _provider(
        "openai-sdk",
        backend_class="openai",
        name="OpenAI",
        material={"api_key": "api-key"},
    )

    assert provider.refresh_models() == 4

    client = _FakeOpenAIClient.instances[-1]
    assert client.kwargs == {"api_key": "api-key"}
    assert client.models.calls == [{}]
    with system_context(reason="test read"):
        models = {model.name: model for model in InferenceModel.objects.filter(provider=provider)}
    assert set(models) == {"gpt-4.1", "openai/gpt-4.1", "gpt-4.2", "openai/gpt-4.2"}
    assert models["gpt-4.1"].display_name == "gpt-4.1"
    assert models["gpt-4.1"].config == {
        "provider_model": "gpt-4.1",
        "source": "openai",
        "owned_by": "openai",
    }
    assert models["openai/gpt-4.1"].display_name == "gpt-4.1 (openai)"
    assert models["openai/gpt-4.1"].config["provider_model"] == "gpt-4.1"
    assert models["gpt-4.2"].display_name == "gpt-4.2"


@pytest.mark.django_db(transaction=True)
def test_openai_backend_rejects_oauth_credentials(
    agents_tables: None,
    monkeypatch: Any,
) -> None:
    """The OpenAI SDK backend is explicit about accepting static API keys only."""

    del agents_tables
    _FakeOpenAIClient.instances.clear()
    monkeypatch.setattr(OpenAIInferenceBackend, "client_class", _FakeOpenAIClient)
    provider = _provider(
        "openai-oauth-chat",
        kind=CredentialKind.OAUTH,
        backend_class="openai",
        name="OpenAI",
        material={"access_token": "oauth-token"},
    )
    with system_context(reason="test openai oauth rejection"):
        model = InferenceModel.objects.create(provider=provider, name="gpt-4.1")

    with pytest.raises(ValueError, match="does not support OAuth"):
        model.chat([{"role": "user", "content": "Ping"}])
    assert _FakeOpenAIClient.instances == []


@pytest.mark.django_db(transaction=True)
def test_openai_model_chat_uses_sdk_chat_completions_and_strips_broker_prefix(
    agents_tables: None,
    monkeypatch: Any,
) -> None:
    """Direct model chat uses the OpenAI SDK without provisioning an agent."""

    del agents_tables
    _FakeOpenAIClient.instances.clear()
    monkeypatch.setattr(OpenAIInferenceBackend, "client_class", _FakeOpenAIClient)
    provider = _provider(
        "openai-chat",
        backend_class="openai",
        name="OpenAI",
        material={"api_key": "api-key"},
    )
    with system_context(reason="test openai chat"):
        model = InferenceModel.objects.create(provider=provider, name="openai/gpt-4.1")

    response = model.chat(
        [{"role": "user", "content": "Ping"}],
        system="Policy",
        max_tokens=12,
        temperature=0.2,
        options={"top_p": 0.9},
    )

    client = _FakeOpenAIClient.instances[-1]
    assert client.kwargs == {"api_key": "api-key"}
    assert client.chat.completions.calls == [
        {
            "model": "gpt-4.1",
            "messages": [
                {"role": "system", "content": "Policy"},
                {"role": "user", "content": "Ping"},
            ],
            "max_tokens": 12,
            "temperature": 0.2,
            "top_p": 0.9,
        }
    ]
    assert response.text == "pong"
    assert response.content == [{"type": "text", "text": "pong"}]
    assert response.usage == {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}


@pytest.mark.django_db(transaction=True)
def test_openai_backend_can_configure_max_completion_tokens(
    agents_tables: None,
    monkeypatch: Any,
) -> None:
    """OpenAI owns the max-token wire field but lets provider config pick the SDK parameter."""

    del agents_tables
    _FakeOpenAIClient.instances.clear()
    monkeypatch.setattr(OpenAIInferenceBackend, "client_class", _FakeOpenAIClient)
    provider = _provider(
        "openai-max-completion",
        backend_class="openai",
        name="OpenAI",
        material={"api_key": "api-key"},
        config={"max_tokens_param": "max_completion_tokens"},
    )
    with system_context(reason="test openai max tokens"):
        model = InferenceModel.objects.create(provider=provider, name="gpt-4.1")

    assert model.chat([{"role": "user", "content": "Ping"}], max_tokens=8).text == "pong"
    assert _FakeOpenAIClient.instances[-1].chat.completions.calls[-1]["max_completion_tokens"] == 8


@pytest.mark.django_db(transaction=True)
def test_openai_chat_rejects_options_that_override_owned_request_fields(
    agents_tables: None,
    monkeypatch: Any,
) -> None:
    """Provider-specific options cannot replace the selected catalogue model."""

    del agents_tables
    _FakeOpenAIClient.instances.clear()
    monkeypatch.setattr(OpenAIInferenceBackend, "client_class", _FakeOpenAIClient)
    provider = _provider(
        "openai-owned-options",
        backend_class="openai",
        name="OpenAI",
        material={"api_key": "api-key"},
    )
    with system_context(reason="test openai options guard"):
        model = InferenceModel.objects.create(provider=provider, name="gpt-4.1")

    with pytest.raises(ValueError, match="model"):
        model.chat([{"role": "user", "content": "Ping"}], options={"model": "other"})
