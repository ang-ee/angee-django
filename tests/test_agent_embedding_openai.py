"""OpenAI embedding catalogue and native protocol over an in-memory SDK transport."""

import json
from contextlib import asynccontextmanager, nullcontext
from types import SimpleNamespace

import httpx2
import pytest
from asgiref.sync import async_to_sync
from openai import AsyncOpenAI
from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
from pydantic_ai.providers.openai import OpenAIProvider

from angee.agents_integrate_openai.backend import OpenAIInferenceBackend
from angee.integrate.credentials import CredentialKind


@pytest.mark.parametrize(
    "name",
    [
        "text-embedding-ada-002",
        "text-embedding-3-small",
        "text-embedding-3-large",
        "text-embedding-future",
    ],
)
def test_openai_discovers_embeddings_without_an_inference_probe(monkeypatch, name):
    backend = OpenAIInferenceBackend(SimpleNamespace(base_url="", config={}))
    client = SimpleNamespace(models=SimpleNamespace(list=lambda: [SimpleNamespace(id=name)]))
    monkeypatch.setattr(backend, "client", lambda: nullcontext(client))
    @asynccontextmanager
    async def binding(handle):
        # Native model metadata needs no request; no hand-maintained size table.
        async with AsyncOpenAI(api_key="test") as sdk:
            yield OpenAIEmbeddingModel(handle, provider=OpenAIProvider(openai_client=sdk))

    monkeypatch.setattr(backend, "embedding_model", binding)
    specs = backend.list_models()
    assert [spec.handle for spec in specs] == [name, f"openai/{name}"]
    for spec in specs:
        assert spec.model_use == "embedding"
        assert spec.capabilities.get("embedding_dimensions") is None
        assert spec.context_window == 8192


def test_compatible_discovery_does_not_assume_hosted_dimensions():
    backend = OpenAIInferenceBackend(SimpleNamespace(base_url="https://gateway.example.test/v1", config={}))
    assert backend._model_discovery(None, "text-embedding-3-large") == {
        "model_use": "embedding",
        "context_window": 0,
    }


@pytest.mark.parametrize("compatible", [False, True])
def test_openai_embedding_uses_owned_credentials_native_protocol_and_limits(monkeypatch, compatible):
    requests, clients, credentials = [], [], []

    def respond(request):
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
                "data": [{"object": "embedding", "index": 0, "embedding": [0.25, 0.75]}],
            },
        )

    def client(**kwargs):
        result = AsyncOpenAI(**kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)))
        clients.append(result)
        return result

    credential = SimpleNamespace(
        kind=CredentialKind.STATIC_TOKEN,
        ensure_fresh=lambda: credentials.append("refreshed"),
        secret_value=lambda: "owned-key",
    )
    endpoint = "https://gateway.example.test/v1" if compatible else "https://api.openai.com/v1"
    backend = OpenAIInferenceBackend(SimpleNamespace(base_url=endpoint, config={}, credential=credential))
    monkeypatch.setattr(backend, "_async_client_class", lambda: client)
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key")
    binding = backend.embedding_model("openai/text-embedding-3-small")
    assert credentials == ["refreshed"]
    assert not clients

    async def request():
        async with binding as model:
            assert isinstance(model, OpenAIEmbeddingModel)
            assert model.system == ("openai-compatible" if compatible else "openai")
            assert await model.max_input_tokens() == (None if compatible else 8192)
            return await model.embed(["passage"], input_type="document")

    assert async_to_sync(request)().embeddings == [[0.25, 0.75]]
    assert clients[0].is_closed()
    assert len(requests) == 1
    assert str(requests[0].url) == f"{endpoint}/embeddings"
    assert requests[0].headers["authorization"] == "Bearer owned-key"
    body = json.loads(requests[0].content)
    assert body["model"] == "text-embedding-3-small"
    assert body["input"] == ["passage"]
    assert "dimensions" not in body
