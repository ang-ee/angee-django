"""Ollama embedding metadata and reuse of the owned OpenAI-compatible transport."""

import json
from contextlib import nullcontext
from types import SimpleNamespace

import httpx2
import pytest
from asgiref.sync import async_to_sync
from openai import DefaultAsyncHttpxClient
from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel

from angee.agents_integrate_ollama.backend import OllamaInferenceBackend


@pytest.mark.parametrize(
    "info,dimensions",
    [
        ({"bert.embedding_length": 768, "vision.embedding_length": 128}, 768),
        ({"other.embedding_length": 1024}, 1024),
        ({"one.embedding_length": 768, "two.embedding_length": 1024}, None),
        ({"bert.embedding_length": 0}, None),
        ({"bert.embedding_length": True}, None),
        ({}, None),
    ],
)
def test_ollama_discovers_embedding_dimensions_and_context(monkeypatch, info, dimensions):
    shown = {
        "capabilities": ["embedding"],
        "details": {"family": "bert"},
        "model_info": {"bert.context_length": 8192, **info},
    }
    client = SimpleNamespace(
        models=SimpleNamespace(list=lambda: [SimpleNamespace(id="embed:latest")]),
        with_options=lambda **kwargs: SimpleNamespace(post=lambda *args, **kwargs: shown),
    )
    backend = OllamaInferenceBackend(SimpleNamespace(base_url="", config={}))
    monkeypatch.setattr(backend, "client", lambda: nullcontext(client))
    specs = backend.list_models()
    assert [spec.handle for spec in specs] == ["embed:latest", "ollama/embed:latest"]
    for spec in specs:
        assert spec.model_use == "embedding"
        assert spec.capabilities.get("embedding_dimensions") == dimensions
        assert spec.context_window == 8192


def test_ollama_chat_hidden_size_is_not_an_embedding_capability():
    shown = {"capabilities": ["completion"], "model_info": {"llama.embedding_length": 4096}}
    client = SimpleNamespace(with_options=lambda **kwargs: SimpleNamespace(post=lambda *args, **kwargs: shown))
    backend = OllamaInferenceBackend(SimpleNamespace(base_url="", config={}))
    discovery = backend._model_discovery(client, "chat:latest")
    assert discovery["model_use"] == "chat"
    assert discovery["capabilities"] == {}


def test_ollama_embedding_uses_openai_path_and_deployment_transport(monkeypatch):
    requests, transports, clients = [], [], []

    def respond(request):
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "model": "embed:latest",
                "data": [
                    {"object": "embedding", "index": 0, "embedding": [0.5, 0.5]},
                ],
            },
        )

    def http_client(**kwargs):
        transports.append(kwargs)
        client = DefaultAsyncHttpxClient(**kwargs, transport=httpx2.MockTransport(respond))
        clients.append(client)
        return client

    monkeypatch.setattr("openai.DefaultAsyncHttpxClient", http_client)
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ambient.example.test/v1")
    monkeypatch.setenv("OLLAMA_API_KEY", "ambient-key")
    backend = OllamaInferenceBackend(SimpleNamespace(base_url="", config={}, credential=None))
    binding = backend.embedding_model("ollama/embed:latest")

    async def request():
        async with binding as model:
            assert isinstance(model, OpenAIEmbeddingModel)
            assert model.system == "ollama"
            assert await model.max_input_tokens() is None
            return await model.embed(["query"], input_type="query")

    assert async_to_sync(request)().embeddings == [[0.5, 0.5]]
    assert transports == [{"trust_env": False, "follow_redirects": False}]
    assert clients[0].is_closed
    assert len(requests) == 1
    assert str(requests[0].url) == "http://localhost:11434/v1/embeddings"
    assert requests[0].headers["authorization"] == "Bearer not-required"
    body = json.loads(requests[0].content)
    assert body["model"] == "embed:latest"
    assert body["input"] == ["query"]
