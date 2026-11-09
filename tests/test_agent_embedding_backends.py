"""Native model bindings share SDK lifetime and refuse unsupported providers."""

from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from pydantic_ai.embeddings import EmbeddingModel, TestEmbeddingModel
from pydantic_ai.models.test import TestModel

from angee.agents.backends import ManualInferenceBackend
from angee.agents.sdk_backends import SDKInferenceBackend
from angee.agents_integrate_anthropic.backend import AnthropicInferenceBackend


@pytest.mark.parametrize("backend_class", [ManualInferenceBackend, AnthropicInferenceBackend])
def test_embedding_binding_refuses_unsupported_backend_before_credentials(backend_class):
    backend = backend_class(SimpleNamespace())
    with pytest.raises(NotImplementedError, match=f"{backend.label} does not support embedding inference"):
        backend.embedding_model("unsupported")


@pytest.mark.parametrize("embedding", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_sdk_binding_shares_native_model_lifetime(monkeypatch, embedding, fails):
    events = []

    class Client:
        async def __aenter__(self):
            events.append("opened")
            return self

        async def __aexit__(self, *args):
            events.append("closed")

    class Backend(SDKInferenceBackend):
        default_broker_name = "test"

        def _async_client_kwargs(self, *, credential=None):
            events.append(("credential", credential))
            return {}

        def _build_model(self, handle, client):
            assert handle == "native"
            return TestModel()

        def embedding_model(
            self,
            handle: str,
            *,
            credential: Any | None = None,
        ) -> AbstractAsyncContextManager[EmbeddingModel]:
            return self._bind_model(handle, self._build_embedding_model, credential=credential)

        def _build_embedding_model(self, handle, client):
            assert handle == "native"
            return TestEmbeddingModel(dimensions=3)

    backend = Backend(SimpleNamespace(config={}))
    monkeypatch.setattr(backend, "_async_client_class", lambda: Client)
    bind = backend.embedding_model if embedding else backend.model
    binding = bind("test/native", credential="owned credential")
    assert events == [("credential", "owned credential")]

    async def request():
        async with binding as model:
            assert isinstance(model, TestEmbeddingModel if embedding else TestModel)
            if fails:
                raise ValueError("provider failed")

    if fails:
        with pytest.raises(ValueError, match="provider failed"):
            async_to_sync(request)()
    else:
        async_to_sync(request)()
    assert events == [("credential", "owned credential"), "opened", "closed"]
