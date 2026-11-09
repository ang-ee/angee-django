"""Embedding admission, native requests and model-owned capability persistence."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from django.core.exceptions import PermissionDenied
from pydantic_ai.embeddings import EmbeddingResult, TestEmbeddingModel
from pydantic_ai.exceptions import UnexpectedModelBehavior
from rebac import system_context

from angee.agents.backends import ManualInferenceBackend
from angee.agents.models import InferenceModelStatus, InferenceModelUnavailable, InferenceModelUse
from angee.agents.testing.models import InferenceModel, InferenceProvider
from tests.conftest import make_integration


@pytest.fixture
def embedding(monkeypatch, settings):
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = None
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", False)
    native = TestEmbeddingModel(dimensions=3, settings={})
    request = AsyncMock(wraps=native.embed)
    monkeypatch.setattr(native, "embed", request)
    events = []

    def bind(backend, handle, *, credential=None):
        events.append(("bound", handle))

        @asynccontextmanager
        async def binding():
            try:
                yield native
            finally:
                events.append("closed")

        return binding()

    monkeypatch.setattr(ManualInferenceBackend, "embedding_model", bind)
    provider = InferenceProvider(name="Embeddings", backend_class="manual", base_url="https://models.example.test/v1")
    model = InferenceModel(
        provider=provider,
        name="broker/catalogue-name",
        model_use=InferenceModelUse.EMBEDDING,
        config={"provider_model": "native-embedding"},
        capabilities={"other": True},
    )
    return SimpleNamespace(model=model, native=native, request=request, events=events)


@pytest.mark.parametrize("input_type", ["document", "query"])
def test_embed_uses_native_batch_and_records_dimensions(embedding, input_type):
    model = embedding.model
    texts = ("first passage", "second passage")
    assert model.embedding_dimensions is None
    assert model.embed(texts, input_type=input_type, role="retrieval") == [[1.0] * 3, [1.0] * 3]
    embedding.request.assert_awaited_once_with(texts, input_type=input_type)
    assert embedding.events == [("bound", "native-embedding"), "closed"]
    assert model.capabilities == {"other": True, "embedding_dimensions": 3}
    assert model.embedding_dimensions == 3


def test_embed_rechecks_the_exact_role_after_selection(embedding, settings):
    model = embedding.model
    identity = model.deployment_identity()
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"retrieval": [identity]}
    model.require_approved("retrieval")
    model.embed(["passage"], input_type="document", role="retrieval")
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"other-role": [identity]}
    with pytest.raises(PermissionDenied, match="retrieval.*not approved"):
        model.embed(["query"], input_type="query", role="retrieval")
    assert embedding.request.await_count == 1


def test_embed_rechecks_deployment_identity(embedding, settings):
    model = embedding.model
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"retrieval": [model.deployment_identity()]}
    model.provider.base_url = "https://changed.example.test/v1"
    with pytest.raises(PermissionDenied, match="not approved"):
        model.embed(["passage"], input_type="document", role="retrieval")
    assert embedding.events == []


@pytest.mark.parametrize("model_use", [use for use in InferenceModelUse if use != InferenceModelUse.EMBEDDING])
def test_embed_refuses_non_embedding_models_before_binding(embedding, model_use):
    embedding.model.model_use = model_use
    with pytest.raises(InferenceModelUnavailable, match="embedding"):
        embedding.model.embed(["passage"], input_type="document", role="retrieval")
    assert embedding.events == []


@pytest.mark.parametrize("status", [InferenceModelStatus.DEPRECATED, InferenceModelStatus.RETIRED])
def test_embed_refuses_unavailable_models(embedding, status):
    embedding.model.status = status
    with pytest.raises(InferenceModelUnavailable, match="available"):
        embedding.model.embed(["passage"], input_type="document", role="retrieval")
    assert embedding.events == []


@pytest.mark.parametrize(
    "texts,input_type,error",
    [("text", "document", TypeError), ([1], "query", TypeError), (["text"], "other", ValueError)],
)
def test_embed_refuses_invalid_inputs_before_binding(embedding, texts, input_type, error):
    with pytest.raises(error):
        embedding.model.embed(texts, input_type=input_type, role="retrieval")
    assert embedding.events == []


def test_embed_empty_batch_still_requires_approval(embedding, settings):
    assert embedding.model.embed([], input_type="document", role="retrieval") == []
    assert embedding.model.embedding_dimensions is None
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {}
    with pytest.raises(PermissionDenied):
        embedding.model.embed([], input_type="document", role="retrieval")
    assert embedding.events == []


def test_embed_failure_closes_binding_without_recording_dimensions(embedding):
    embedding.request.side_effect = UnexpectedModelBehavior("provider failed")
    with pytest.raises(UnexpectedModelBehavior, match="provider failed"):
        embedding.model.embed(["passage"], input_type="document", role="retrieval")
    assert embedding.events[-1] == "closed"
    assert embedding.model.embedding_dimensions is None


@pytest.mark.parametrize(
    "vectors,texts", [([], ["one"]), ([[1.0], [2.0]], ["one"]), ([[]], ["one"]), ([[1.0], [1.0, 2.0]], ["one", "two"])]
)
def test_embed_refuses_malformed_vector_batches(embedding, vectors, texts):
    embedding.request.return_value = EmbeddingResult(
        embeddings=vectors,
        inputs=texts,
        input_type="document",
        model_name="test",
        provider_name="test",
    )
    with pytest.raises(UnexpectedModelBehavior, match="Embedding response"):
        embedding.model.embed(texts, input_type="document", role="retrieval")
    assert embedding.model.embedding_dimensions is None


def test_embed_refuses_dimension_drift(embedding):
    embedding.model.capabilities["embedding_dimensions"] = 2
    with pytest.raises(UnexpectedModelBehavior, match="recorded capabilities"):
        embedding.model.embed(["passage"], input_type="document", role="retrieval")
    assert embedding.model.embedding_dimensions == 2


@pytest.mark.parametrize("value,expected", [(None, None), (0, None), (-3, None), (True, None), ("3", None), (3, 3)])
def test_embedding_dimensions_exposes_only_positive_integers(embedding, value, expected):
    embedding.model.capabilities["embedding_dimensions"] = value
    assert embedding.model.embedding_dimensions == expected


def test_max_input_tokens_uses_discovery_before_native_metadata(embedding):
    embedding.model.context_window = 4096
    assert embedding.model.max_input_tokens() == 4096
    assert embedding.events == []
    embedding.model.context_window = 0
    assert embedding.model.max_input_tokens() == 1024
    embedding.request.assert_not_awaited()
    assert embedding.events[-1] == "closed"


def test_max_input_tokens_preserves_unknown_budget(embedding, monkeypatch):
    monkeypatch.setattr(embedding.native, "max_input_tokens", AsyncMock(return_value=None))
    assert embedding.model.max_input_tokens() is None
    embedding.model.model_use = InferenceModelUse.CHAT
    assert embedding.model.max_input_tokens() is None
    embedding.native.max_input_tokens.assert_awaited_once()


@pytest.mark.django_db(transaction=True)
def test_embed_persists_first_use_dimensions_without_losing_live_capabilities(embedding, composed_tables):
    model = embedding.model
    model.provider = make_integration("embedding", model=InferenceProvider, backend_class="manual")
    with system_context(reason="test.embedding.seed"):
        model.save()
        InferenceModel.objects.filter(pk=model.pk).update(capabilities={"newer": "metadata"})
    assert model.embed(["passage"], input_type="document", role="retrieval") == [[1.0] * 3]
    with system_context(reason="test.embedding.read"):
        model.refresh_from_db()
    assert model.capabilities == {"newer": "metadata", "embedding_dimensions": 3}


@pytest.mark.django_db(transaction=True)
def test_embed_stale_first_use_refuses_a_different_recorded_dimension(embedding, composed_tables):
    model = embedding.model
    model.provider = make_integration("embedding-drift", model=InferenceProvider, backend_class="manual")
    with system_context(reason="test.embedding.seed"):
        model.save()
        InferenceModel.objects.filter(pk=model.pk).update(capabilities={"embedding_dimensions": 2})
    with pytest.raises(UnexpectedModelBehavior, match="recorded capabilities"):
        model.embed(["passage"], input_type="document", role="retrieval")
    with system_context(reason="test.embedding.read"):
        model.refresh_from_db()
    assert model.embedding_dimensions == 2
