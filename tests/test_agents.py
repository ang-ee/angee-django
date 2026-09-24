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

import httpx
import httpx2
import pytest
from anthropic.types import Message, TextBlock, Usage
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.completion_usage import CompletionUsage
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage, RunUsage
from rebac import system_context

from angee.agents.backends import InferenceBackend
from angee.agents.models import (
    INFERENCE_OUTPUT_TOOL,
    InferenceModelStatus,
    InferenceModelUse,
    InferenceOutputError,
    decode_inference_output,
    normalize_inference_usage,
)
from angee.agents.models import InferenceModel as AbstractInferenceModel
from angee.agents.models import InferenceProvider as AbstractInferenceProvider
from angee.agents.models import Skill as AbstractSkill
from angee.agents.sdk_backends import SDKInferenceBackend
from angee.agents.skills import parse_skill_meta
from angee.agents_integrate_anthropic.backend import AnthropicInferenceBackend
from angee.agents_integrate_ollama.backend import OllamaInferenceBackend
from angee.agents_integrate_openai.backend import OpenAIInferenceBackend
from angee.integrate.credentials import CredentialKind
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    Integration,
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


class InferenceProvider(AbstractInferenceProvider, Integration):
    """Concrete inference provider (capability over an integration) used by tests."""

    class Meta(AbstractInferenceProvider.Meta):
        """Django model options for the canonical test inference provider."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_inference_provider"
        rebac_resource_type = "agents/inference_provider"


class InferenceModel(AbstractInferenceModel):
    """Concrete inference model catalogue row used by tests."""

    class Meta(AbstractInferenceModel.Meta):
        """Django model options for the canonical test inference model."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_inference_model"
        rebac_resource_type = "agents/inference_model"


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


@pytest.mark.django_db(transaction=True)
def test_manual_backend_advertises_no_models(agents_tables: None) -> None:
    """The built-in ``manual`` backend lists nothing — its catalogue is hand-curated."""

    del agents_tables
    provider = _provider("manual-vendor", backend_class="manual", name="Manual")
    assert provider.refresh_models() == 0
    with system_context(reason="test read"):
        assert InferenceModel.objects.filter(provider=provider).count() == 0


def test_sdk_backend_loads_client_class_by_dotted_path() -> None:
    """SDK backends declare their client class path; the shared base imports it."""

    class DemoSDKBackend(SDKInferenceBackend):
        label = "Demo"
        client_class_path = "types.SimpleNamespace"
        sdk_package_name = "types"

    assert DemoSDKBackend(SimpleNamespace())._load_client_class() is SimpleNamespace


def test_sdk_backend_client_class_override_skips_dotted_import() -> None:
    """Tests and custom backends can still inject a client class directly."""

    class DemoSDKBackend(SDKInferenceBackend):
        label = "Demo"
        client_class = SimpleNamespace
        client_class_path = "missing_provider_sdk.Client"
        sdk_package_name = "missing-provider-sdk"

        def _client_kwargs(self, *, credential: Any | None = None, using: str | None = None) -> dict[str, Any]:
            del credential
            return {}

    assert type(DemoSDKBackend(SimpleNamespace()).client()).__name__ == "SimpleNamespace"


def test_sdk_backend_wraps_missing_client_package_error() -> None:
    """Missing SDK imports fail with the backend's install hint."""

    class MissingSDKBackend(SDKInferenceBackend):
        label = "Missing"
        client_class_path = "missing_provider_sdk.Client"
        sdk_package_name = "missing-provider-sdk"

    with pytest.raises(RuntimeError, match="missing-provider-sdk.*Missing inference backend"):
        MissingSDKBackend(SimpleNamespace())._load_client_class()


def test_sdk_backend_still_requires_a_credential_by_default() -> None:
    """The no-auth SDK seam does not weaken credential-requiring backends."""

    class RequiredSDKBackend(SDKInferenceBackend):
        label = "Required"

    with pytest.raises(ValueError, match="Required inference requires an attached credential"):
        RequiredSDKBackend(SimpleNamespace(credential=None))._credential_auth()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("extra_body", {"model": "unchecked-model"}),
        ("extra_headers", {"Authorization": "Bearer unchecked"}),
        ("extra_query", {"api-version": "unchecked"}),
    ],
)
def test_inference_backend_rejects_caller_transport_overrides(key: str, value: object) -> None:
    """Request settings cannot bypass the approved provider deployment."""

    backend = InferenceBackend(SimpleNamespace())

    with pytest.raises(ValueError, match="cannot override provider transport"):
        backend.request_settings({key: value})  # type: ignore[typeddict-item]


def test_normalize_inference_usage_accepts_run_usage_and_tool_calls() -> None:
    """Session and direct inference share every workflow budget axis."""

    assert normalize_inference_usage(RunUsage(input_tokens=3, output_tokens=2, requests=2, tool_calls=1)) == {
        "input_tokens": 3,
        "output_tokens": 2,
        "tokens": 5,
        "requests": 2,
        "tool_calls": 1,
    }


def test_ollama_request_settings_admit_only_deployment_keep_alive() -> None:
    """The Ollama owner admits its one named extra-body extension only."""

    backend = OllamaInferenceBackend(SimpleNamespace(config={"keep_alive": "7m"}))

    assert backend.request_settings({"extra_body": {"keep_alive": "7m"}})["extra_body"] == {"keep_alive": "7m"}
    with pytest.raises(ValueError, match="do not admit extra_body keys"):
        backend.request_settings({"extra_body": {"model": "unchecked-model"}})
    with pytest.raises(ValueError, match="cannot override provider transport"):
        backend.request_settings({"extra_headers": {"Authorization": "Bearer unchecked"}})


def test_ollama_backend_keeps_an_explicit_gateway_credential() -> None:
    """An attached credential wins over Ollama's unauthenticated fallback."""

    freshened: list[bool] = []
    credential = SimpleNamespace(
        kind=CredentialKind.STATIC_TOKEN,
        ensure_fresh=lambda **kwargs: freshened.append(True),
        secret_value=lambda: "gateway-key",
    )
    backend = OllamaInferenceBackend(SimpleNamespace(credential=None))

    assert backend._credential_auth(credential=credential) == {"api_key": "gateway-key"}
    assert freshened == [True]


def test_ollama_backend_owns_protocol_details_and_provider_identity() -> None:
    """The compatible subclass owns its JSON envelope and provider identity."""

    assert OllamaInferenceBackend._build_model is not OpenAIInferenceBackend._build_model
    assert OllamaInferenceBackend.effective_defaults() == {
        "name": "Ollama",
        "status": "draft",
        "vendor": "ollama",
    }


def test_openai_model_filtering_keeps_its_existing_prefix_policy() -> None:
    """Lifting the prefix data onto the class does not broaden OpenAI's catalogue."""

    backend = OpenAIInferenceBackend(SimpleNamespace(config={}))

    assert backend._is_chat_model("gpt-4.1") is True
    assert backend._is_chat_model("llama3.2:latest") is False
    assert backend._is_chat_model("text-embedding-3-large") is False


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
        return Message(
            id="msg_1",
            type="message",
            role="assistant",
            model="claude-sonnet-4-6",
            content=[TextBlock(type="text", text="pong")],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=3, output_tokens=1),
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
        return ChatCompletion(
            id="chatcmpl_1",
            choices=[
                Choice(
                    finish_reason="stop",
                    index=0,
                    message=ChatCompletionMessage(role="assistant", content="pong"),
                )
            ],
            created=1,
            model="gpt-4.1",
            object="chat.completion",
            usage=CompletionUsage(prompt_tokens=3, completion_tokens=1, total_tokens=4),
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
def test_anthropic_model_chat_uses_native_messages_and_strips_broker_prefix(agents_tables, inference_http):
    provider = _provider("anthropic-chat", backend_class="anthropic", name="Anthropic", material={"api_key": "api-key"})
    with system_context(reason="test anthropic chat"):
        model = InferenceModel.objects.create(provider=provider, name="anthropic/claude-sonnet-4-6")
    response = model.chat(
        [ModelRequest(parts=[SystemPromptPart("Policy"), SystemPromptPart("Be brief."), UserPromptPart("Ping")])],
        model_settings={"max_tokens": 12, "temperature": 0.2, "top_p": 0.9},
    )
    requests, clients = inference_http
    payload = json.loads(requests[-1].content)
    assert payload["model"] == "claude-sonnet-4-6"
    assert payload["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Ping"}]}]
    assert payload["max_tokens"] == 12
    assert payload["temperature"] == 0.2
    assert payload["top_p"] == 0.9
    assert "Policy" in json.dumps(payload["system"])
    assert "Be brief." in json.dumps(payload["system"])
    assert response.text == "pong"
    assert response.usage.input_tokens == 3
    assert response.usage.output_tokens == 1
    assert response.provider_response_id == "msg_1"
    assert all(client.is_closed() for client in clients)


@pytest.mark.django_db(transaction=True)
def test_anthropic_backend_uses_auth_token_for_oauth_credentials(agents_tables, inference_http):
    from angee.agents.runtimes import ANTHROPIC_OAUTH_SYSTEM_PREAMBLE

    provider = _provider(
        "anthropic-oauth-chat",
        kind=CredentialKind.OAUTH,
        backend_class="anthropic",
        name="Anthropic",
        material={"access_token": "oauth-token"},
    )
    with system_context(reason="test anthropic oauth chat"):
        model = InferenceModel.objects.create(provider=provider, name="claude-sonnet-4-6")
    assert model.chat([ModelRequest(parts=[UserPromptPart("Ping")])]).text == "pong"
    requests, clients = inference_http
    assert requests[-1].headers["authorization"] == "Bearer oauth-token"
    assert "oauth-2025-04-20" in requests[-1].headers["anthropic-beta"]
    assert json.loads(requests[-1].content)["system"][0] == {"type": "text", "text": ANTHROPIC_OAUTH_SYSTEM_PREAMBLE}
    assert all(client.is_closed() for client in clients)


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


def test_ollama_backend_lists_tagged_models_without_a_credential(monkeypatch: Any) -> None:
    """Ollama uses its native show endpoint once per physical listed model."""

    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": "llama3.2:latest", "object": "model", "owned_by": "library"},
                        {"id": "nomic-embed-text:latest", "object": "model", "owned_by": "library"},
                        {"id": "qwen2.5-coder:7b", "object": "model", "owned_by": "library"},
                    ],
                },
            )
        assert request.url.path == "/api/show"
        model_id = json.loads(request.content)["model"]
        if model_id == "nomic-embed-text:latest":
            return httpx.Response(503, json={"error": "native metadata unavailable"})
        if model_id == "llama3.2:latest":
            return httpx.Response(
                200,
                json={
                    "details": {"family": "llama"},
                    "model_info": {"llama.context_length": 131072},
                    "parameters": "temperature 0.8\nnum_ctx 32768",
                },
            )
        return httpx.Response(
            200,
            json={
                "details": {},
                "model_info": {
                    "qwen.context_length": 65536,
                    "qwen.vision.context_length": 8192,
                },
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handle))
    backend = OllamaInferenceBackend(SimpleNamespace(credential=None, base_url="", config={}))
    monkeypatch.setattr(
        backend,
        "_client_kwargs",
        lambda **kwargs: {
            "api_key": "not-required",
            "base_url": "http://localhost:11434/v1",
            "http_client": http_client,
        },
    )

    specs = backend.list_models()

    assert [spec.handle for spec in specs] == [
        "llama3.2:latest",
        "ollama/llama3.2:latest",
        "nomic-embed-text:latest",
        "ollama/nomic-embed-text:latest",
        "qwen2.5-coder:7b",
        "ollama/qwen2.5-coder:7b",
    ]
    assert {spec.config["source"] for spec in specs} == {"ollama"}
    assert all(spec.model_use == "" for spec in specs)
    assert all("model_use" not in spec.upsert_defaults() for spec in specs)
    assert [json.loads(request.content)["model"] for request in requests[1:]] == [
        "llama3.2:latest",
        "nomic-embed-text:latest",
        "qwen2.5-coder:7b",
    ]
    assert [request.url.path for request in requests] == [
        "/v1/models",
        "/api/show",
        "/api/show",
        "/api/show",
    ]
    by_handle = {spec.handle: spec for spec in specs}
    assert by_handle["llama3.2:latest"].context_window == 131072
    assert by_handle["ollama/llama3.2:latest"].context_window == 131072
    assert by_handle["llama3.2:latest"].config["ollama_num_ctx"] == 32768
    assert by_handle["ollama/llama3.2:latest"].config["ollama_num_ctx"] == 32768
    assert by_handle["nomic-embed-text:latest"].context_window == 0
    assert by_handle["qwen2.5-coder:7b"].context_window == 0
    assert all(spec.max_output_tokens == 0 for spec in specs)


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
        model.chat([ModelRequest(parts=[UserPromptPart("Ping")])])
    assert _FakeOpenAIClient.instances == []


@pytest.mark.django_db(transaction=True)
def test_openai_model_chat_uses_native_messages_and_strips_broker_prefix(agents_tables, inference_http):
    provider = _provider("openai-chat", backend_class="openai", name="OpenAI", material={"api_key": "api-key"})
    with system_context(reason="test openai chat"):
        model = InferenceModel.objects.create(provider=provider, name="openai/gpt-4.1")
    response = model.chat(
        [ModelRequest(parts=[SystemPromptPart("Policy"), UserPromptPart("Ping")])],
        model_settings={"max_tokens": 12, "temperature": 0.2, "top_p": 0.9},
    )
    requests, clients = inference_http
    payload = json.loads(requests[-1].content)
    assert payload["model"] == "gpt-4.1"
    assert payload["messages"] == [{"role": "system", "content": "Policy"}, {"role": "user", "content": "Ping"}]
    assert payload["max_tokens"] == 12
    assert "max_completion_tokens" not in payload
    assert payload["temperature"] == 0.2 and payload["top_p"] == 0.9
    assert response.text == "pong" and isinstance(response.parts[0], TextPart)
    assert response.usage.input_tokens == 3 and response.usage.output_tokens == 1
    assert response.provider_response_id == "chatcmpl_1"
    assert all(client.is_closed() for client in clients)


@pytest.mark.django_db(transaction=True)
def test_openai_backend_can_configure_max_completion_tokens(agents_tables, inference_http):
    provider = _provider(
        "openai-max-completion",
        backend_class="openai",
        name="OpenAI",
        material={"api_key": "api-key"},
        config={"max_tokens_param": "max_completion_tokens"},
    )
    with system_context(reason="test openai max tokens"):
        model = InferenceModel.objects.create(provider=provider, name="gpt-4.1")
    assert model.chat([ModelRequest(parts=[UserPromptPart("Ping")])], model_settings={"max_tokens": 8}).text == "pong"
    requests, clients = inference_http
    payload = json.loads(requests[-1].content)
    assert payload["max_completion_tokens"] == 8 and "max_tokens" not in payload
    assert all(client.is_closed() for client in clients)


@pytest.mark.django_db(transaction=True)
def test_ollama_backend_translates_native_thinking_setting(agents_tables, inference_http):
    """Ollama maps deployment and analytical defaults onto its SDK request."""

    provider = _provider(
        "ollama-chat",
        backend_class="ollama",
        name="Ollama",
        config={"keep_alive": "7m", "generation_limit": 2048},
    )

    response = provider.chat(
        model="qwen3.6:35b-a3b",
        messages=[ModelRequest(parts=[UserPromptPart("Map retained evidence")])],
        model_settings={"temperature": 0, "thinking": False},
    )

    requests, clients = inference_http
    payload = json.loads(requests[-1].content)
    assert payload["reasoning_effort"] == "none"
    assert payload["max_tokens"] == 2048
    assert payload["temperature"] == 0
    assert payload["keep_alive"] == "7m"
    assert "think" not in payload
    assert response.text == "pong"
    assert all(client.is_closed() for client in clients)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.invalid/v1",
        "ftp://localhost/v1",
        "http://user:pass@localhost/v1",
        "http://localhost/v1?token=secret",
        "http://localhost/v1#fragment",
    ],
)
def test_ollama_backend_rejects_non_loopback_endpoints(base_url: str) -> None:
    backend = OllamaInferenceBackend(SimpleNamespace(credential=None, base_url=base_url, config={}))

    with pytest.raises(ValueError, match="loopback endpoint"):
        backend._client_kwargs()


def test_ollama_backend_disables_environment_proxies_for_both_sdk_clients(
    monkeypatch: Any,
) -> None:
    from angee.agents_integrate_ollama import backend as ollama_backend

    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        ollama_backend,
        "DefaultHttpxClient",
        lambda **kwargs: calls.append(("sync", kwargs)) or object(),
    )
    monkeypatch.setattr(
        ollama_backend,
        "DefaultAsyncHttpxClient",
        lambda **kwargs: calls.append(("async", kwargs)) or object(),
    )
    backend = OllamaInferenceBackend(SimpleNamespace(credential=None, base_url="http://127.0.0.1:11434/v1", config={}))

    backend._client_kwargs()
    backend._async_client_kwargs()

    assert calls == [
        ("sync", {"trust_env": False, "follow_redirects": False}),
        ("async", {"trust_env": False, "follow_redirects": False}),
    ]


@pytest.mark.parametrize("generation_limit", [0, -1, True, "unbounded"])
def test_ollama_backend_rejects_invalid_generation_limits(generation_limit: object) -> None:
    backend = OllamaInferenceBackend(
        SimpleNamespace(credential=None, base_url="", config={"generation_limit": generation_limit})
    )

    with pytest.raises(ValueError, match="positive integer"):
        backend.request_settings(None)


@pytest.fixture()
def inference_http(monkeypatch, request):
    """Exercise real SDK/native adapters while replacing only HTTP transport."""
    from django.utils.module_loading import import_string

    scenario = getattr(request, "param", "success")
    requests = []
    clients = []

    async def respond(self, request):
        transport_http = httpx2 if isinstance(request, httpx2.Request) else httpx
        content = b"".join([chunk async for chunk in request.stream])
        requests.append(transport_http.Request(request.method, request.url, headers=request.headers, content=content))
        if isinstance(scenario, int):
            return transport_http.Response(
                scenario, json={"error": {"message": "provider rejected request", "type": "test"}}, request=request
            )
        if scenario == "tool":
            return transport_http.Response(
                200,
                json={
                    "id": "call-response",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-4.1",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {"name": "lookup", "arguments": '{"key":"value"}'},
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                },
                request=request,
            )
        response = (
            _FakeAnthropicMessages(None).create()
            if request.url.path.endswith("/messages")
            else _FakeOpenAICompletions(None).create()
        )
        if scenario == "structured":
            response.choices[0].message.content = '{"text":"pong"}'
        return transport_http.Response(200, json=response.model_dump(mode="json"), request=request)

    def client_class(path):
        cls = import_string(path)

        def build(**kwargs):
            if "http_client" not in kwargs:
                kwargs["http_client"] = httpx.AsyncClient()
            kwargs.setdefault("base_url", "https://provider.invalid/v1")
            kwargs["max_retries"] = 0
            client = cls(**kwargs)
            clients.append(client)
            return client

        return build

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", respond)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", respond)
    monkeypatch.setattr("angee.agents.sdk_backends.import_string", client_class)
    return requests, clients


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("inference_http", ["tool"], indirect=True)
def test_direct_native_request_returns_tool_calls_without_executing_a_loop(agents_tables, inference_http):
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    provider = _provider("openai-tools", backend_class="openai", material={"api_key": "api-key"})
    with system_context(reason="test direct inference tools"):
        model = InferenceModel.objects.create(provider=provider, name="gpt-4.1")
    result = model.infer(
        messages=[ModelRequest(parts=[UserPromptPart("Look up a value")])],
        output_schema=[
            ToolDefinition(
                name="lookup",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            )
        ],
    )
    requests, clients = inference_http
    assert len(requests) == 1
    assert json.loads(requests[0].content)["tools"][0]["function"]["name"] == "lookup"
    assert isinstance(result.response.parts[0], ToolCallPart)
    assert result.response.parts[0].args_as_dict() == {"key": "value"}
    assert result.response.usage.total_tokens == 5
    assert result.usage == {"input_tokens": 3, "output_tokens": 2, "tokens": 5, "requests": 1}
    assert result.output is None
    assert all(client.is_closed() for client in clients)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("backend_class", ["openai", "ollama"])
@pytest.mark.parametrize("inference_http", ["structured"], indirect=True)
def test_direct_inference_builds_one_structured_multimodal_envelope(
    agents_tables,
    inference_http,
    backend_class,
):
    provider = _provider(
        f"{backend_class}-structured-image",
        backend_class=backend_class,
        material={"api_key": "api-key"},
    )
    with system_context(reason="test structured multimodal inference"):
        model = InferenceModel.objects.create(provider=provider, name="gpt-4.1")

    output_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    result = model.infer(
        [ModelRequest(parts=[UserPromptPart("Read the image")])],
        output_schema=output_schema,
        images=(BinaryContent(b"image", media_type="image/jpeg"),),
        settings={"max_tokens": 16},
    )

    requests, clients = inference_http
    payload = json.loads(requests[-1].content)
    if backend_class == "ollama":
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["name"] == "inference_output"
        assert payload["response_format"]["json_schema"]["schema"] == output_schema
    else:
        assert payload["tools"][0]["type"] == "function"
        assert payload["tools"][0]["function"]["name"] == "inference_output"
        assert payload["tools"][0]["function"]["parameters"] == output_schema
        assert payload["tool_choice"] == "required"
    assert "data:image/jpeg;base64,aW1hZ2U=" in json.dumps(payload["messages"])
    assert result.output == {"text": "pong"}
    assert result.usage == {"input_tokens": 3, "output_tokens": 1, "tokens": 4, "requests": 1}
    assert all(client.is_closed() for client in clients)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("inference_http,retryable", [(429, True), (400, False)], indirect=["inference_http"])
def test_native_provider_errors_preserve_retry_classification_and_close_clients(
    agents_tables, inference_http, retryable
):
    from pydantic_ai.exceptions import ModelHTTPError

    provider = _provider("openai-errors", backend_class="openai", material={"api_key": "api-key"})
    with pytest.raises(ModelHTTPError) as error:
        provider.chat(model="gpt-4.1", messages=[ModelRequest(parts=[UserPromptPart("Fail")])])
    requests, clients = inference_http
    assert len(requests) == 1
    assert provider.backend.is_transient_error(error.value) is retryable
    assert all(client.is_closed() for client in clients)


@pytest.mark.parametrize("status", [429, 500, 501, 502, 503, 504, 529, 599])
def test_inference_backend_classifies_transient_http_status(status):
    """Native HTTP status owns retry policy, including every server-error code."""

    assert InferenceBackend(SimpleNamespace()).is_transient_error(ModelHTTPError(status, "test-model"))


@pytest.mark.parametrize("status", [400, 401, 403, 408, 409, 425, 600])
def test_inference_backend_does_not_retry_nontransient_http_status(status):
    assert not InferenceBackend(SimpleNamespace()).is_transient_error(ModelHTTPError(status, "test-model"))


@pytest.mark.parametrize(
    "error", [TimeoutError(), ConnectionError(), httpx.ReadTimeout("timeout"), httpx.ConnectError("connection")]
)
def test_inference_backend_classifies_transport_exception_types(error):
    assert InferenceBackend(SimpleNamespace()).is_transient_error(error)


@pytest.mark.parametrize("message", ["timeout", "rate limit", "temporarily unavailable", "try again"])
def test_inference_backend_never_guesses_transient_from_message(message):
    assert not InferenceBackend(SimpleNamespace()).is_transient_error(ValueError(message))


@pytest.mark.parametrize("backend_class", [OpenAIInferenceBackend, AnthropicInferenceBackend])
def test_native_connection_error_wrapping_remains_transient(backend_class):
    import anthropic
    import openai

    connection_error = (
        anthropic.APIConnectionError if backend_class is AnthropicInferenceBackend else openai.APIConnectionError
    )
    error = ModelAPIError("test-model", "Connection error")
    error.__cause__ = connection_error(request=httpx2.Request("POST", "https://provider.invalid"))
    assert backend_class(SimpleNamespace()).is_transient_error(error)


@pytest.mark.parametrize(
    "timeout", [0, -1, float("nan"), float("inf"), "invalid", httpx.Timeout(-1), httpx.Timeout(5, read=0)]
)
def test_invalid_timeout_is_configuration_error_before_binding(timeout, monkeypatch):
    backend = InferenceBackend(SimpleNamespace())
    monkeypatch.setattr(backend, "model", lambda *args, **kwargs: pytest.fail("provider was bound"))
    with pytest.raises(ValueError, match="timeout") as error:
        backend.chat("model", [], model_settings={"timeout": timeout})
    assert not backend.is_transient_error(error.value)


def test_unknown_inference_settings_are_rejected_before_binding(monkeypatch):
    backend = InferenceBackend(SimpleNamespace())
    monkeypatch.setattr(backend, "model", lambda *args, **kwargs: pytest.fail("provider was bound"))
    with pytest.raises(ValueError, match="Unknown inference request settings: mystery_knob"):
        backend.chat("model", [], model_settings={"mystery_knob": True})


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf"), "invalid"])
def test_invalid_provider_timeout_is_configuration_error_before_sdk_client(timeout, monkeypatch):
    backend = OllamaInferenceBackend(SimpleNamespace(credential=None, base_url="", config={"timeout_seconds": timeout}))
    monkeypatch.setattr(backend, "_credential_auth", lambda **kwargs: pytest.fail("credential was refreshed"))
    monkeypatch.setattr(
        "angee.agents_integrate_ollama.backend.DefaultHttpxClient",
        lambda **kwargs: pytest.fail("provider client constructed"),
    )
    with pytest.raises(ValueError, match="timeout") as error:
        backend._client_kwargs()
    assert not backend.is_transient_error(error.value)


@pytest.mark.parametrize("timeout", [None, 5, httpx.Timeout(5, read=None)])
def test_native_valid_timeout_settings_are_preserved(timeout):
    assert InferenceBackend(SimpleNamespace()).request_settings({"timeout": timeout}) == {"timeout": timeout}


@pytest.mark.parametrize("text", ['{"value":3}', '```json\n{"value":3}\n```', '```\n{"value":3}\n```'])
def test_structured_decoder_accepts_json_and_fenced_objects(text):
    assert decode_inference_output(ModelResponse(parts=[TextPart(text)])) == {"value": 3}


def test_structured_decoder_uses_native_output_tool():
    response = ModelResponse(parts=[ToolCallPart(INFERENCE_OUTPUT_TOOL, {"value": 3})])
    assert decode_inference_output(response) == {"value": 3}


@pytest.mark.parametrize(
    "parts",
    [
        [],
        [TextPart("[]")],
        [TextPart("```json")],
        [ToolCallPart(INFERENCE_OUTPUT_TOOL, "[]")],
        [ToolCallPart(INFERENCE_OUTPUT_TOOL, {}), ToolCallPart(INFERENCE_OUTPUT_TOOL, {})],
    ],
)
def test_structured_decoder_rejects_missing_ambiguous_or_nonobject_output(parts):
    with pytest.raises(ValueError):
        decode_inference_output(ModelResponse(parts=parts))


def test_infer_decoding_error_retains_usage_and_explicit_alias(monkeypatch):
    model = InferenceModel(name="structured")
    response = ModelResponse(parts=[TextPart("[]")], usage=RequestUsage(input_tokens=3, output_tokens=2))

    def chat(messages, *, using, **kwargs):
        assert using == "inference_writer"
        return response

    monkeypatch.setattr(model, "chat", chat)
    with pytest.raises(InferenceOutputError) as error:
        model.infer([], output_schema={"type": "object"}, using="inference_writer")
    assert error.value.response is response
    assert error.value.usage == {"input_tokens": 3, "output_tokens": 2, "tokens": 5, "requests": 1}
    assert model._state.db is None


@pytest.mark.parametrize(
    "status,model_use,role",
    [
        (InferenceModelStatus.RETIRED, InferenceModelUse.CHAT, "mapping"),
        (InferenceModelStatus.DEPRECATED, InferenceModelUse.MULTIMODAL, "recognition"),
        (InferenceModelStatus.AVAILABLE, InferenceModelUse.CHAT, "recognition"),
        (InferenceModelStatus.AVAILABLE, InferenceModelUse.EMBEDDING, "inference"),
    ],
)
def test_model_capability_rejects_unusable_lifecycle_and_modality(status, model_use, role):
    model = InferenceModel(status=status, model_use=model_use)
    with pytest.raises(ValueError):
        model.require_capability(role)


def test_model_authorization_rejects_nondefault_database_before_rebac(monkeypatch):
    model = InferenceModel()
    monkeypatch.setattr(model, "with_actor", lambda actor: pytest.fail("unbound REBAC lookup"))
    with pytest.raises(ValidationError, match="default authorization database"):
        model.require_usable(object(), "inference", using="other")


@pytest.mark.django_db(transaction=True)
def test_model_requires_read_and_exact_approved_deployment(agents_tables, settings):
    provider = _provider("approved-ollama", backend_class="ollama")
    with system_context(reason="test.agents.authorization.seed"):
        model = InferenceModel.objects.create(provider=provider, name="llama")
        denied_actor = get_user_model().objects.create_user(username="no-model-read")
    identity = model.deployment_identity()
    assert identity["endpoint"] == provider.backend.endpoint == "http://localhost:11434/v1"
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"mapping": [identity]}
    with pytest.raises(PermissionDenied, match="cannot read"):
        model.require_usable(denied_actor, "mapping")
    model.require_usable(provider.owner, "mapping")
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"mapping": [{**identity, "endpoint": "http://localhost:9999/v1"}]}
    with pytest.raises(ValueError, match="not approved"):
        model.require_usable(provider.owner, "mapping")
