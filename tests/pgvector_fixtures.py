"""Source-addon records shared by passage index and PostgreSQL retrieval contracts."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.db import connection
from pydantic_ai.embeddings import TestEmbeddingModel
from rebac import actor_context, system_context

from angee.agents.backends import InferenceBackend
from angee.agents.models import InferenceModelUse
from angee.agents.testing.models import InferenceModel, InferenceProvider
from angee.knowledge_graph_pgvector.testing.models import Passage
from tests.conftest import MarkdownPage, Page, Vault, create_user, make_integration, vault_for


@pytest.fixture
def passage_records(composed_tables):
    cache.clear()
    owner = create_user("passage-owner")
    vault = vault_for(owner, name="Passages")
    provider = make_integration("passage-model", model=InferenceProvider, backend_class="manual")
    with system_context(reason="test.pgvector.model"):
        model = InferenceModel.objects.create(
            provider=provider,
            name="embedding-test",
            model_use=InferenceModelUse.EMBEDDING,
            capabilities={"embedding_dimensions": 2},
        )
        Vault.objects.filter(pk=vault.pk).update(retrieval_class="pgvector", embedding_model=model)
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Guide", body="# First\nAlpha evidence.\n\n# Second\nBeta evidence.")
        markdown = MarkdownPage.objects.get(pk=page.pk)
    vault.refresh_from_db()
    passages = Passage.objects.rebuild_for(markdown)
    return SimpleNamespace(owner=owner, vault=vault, page=page, markdown=markdown, model=model, passages=passages)


class NativeEmbeddingBackend(InferenceBackend):
    """Exercise the real agents embedding contract through a native test adapter."""

    key = "native_embedding_test"
    calls = []

    def embedding_model(self, handle, *, credential=None):
        assert not connection.in_atomic_block
        type(self).calls.append(handle)

        @asynccontextmanager
        async def binding():
            yield TestEmbeddingModel(dimensions=2)

        return binding()
