"""Passage-local graph records for extraction, permission and retrieval contracts."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rebac import actor_context, system_context

from angee.agents.models import InferenceModelUse
from angee.agents.testing.models import InferenceModel
from angee.knowledge_graph_pgvector.extraction import entity_key
from angee.knowledge_graph_pgvector.testing.models import Entity, EntityEdge, EntityMention, Passage
from tests.conftest import MarkdownPage, Page, Vault
from tests.pgvector_fixtures import passage_records as passage_records


@pytest.fixture
def graph_records(passage_records):
    records = passage_records
    with system_context(reason="test.pgvector.graph_selection"):
        records.extraction_model = InferenceModel.objects.create(
            provider=records.model.provider,
            name="graph-test",
            model_use=InferenceModelUse.CHAT,
        )
        Vault.objects.filter(pk=records.vault.pk).update(
            extraction_model=records.extraction_model, embedding_model=None
        )
        Page.objects.filter(pk=records.page.pk).update(updated_at=timezone.now() - timedelta(minutes=10))
    records.vault.refresh_from_db()
    return records


def graph_page(records, title, *, body=None, names=(), edges=(), vault=None):
    vault = vault or records.vault
    body = body or f"{title}: " + "; ".join(f"{left} relates to {right}" for left, right in edges)
    with actor_context(records.owner):
        page = Page.objects.create_in(vault, title=title, body=body)
        markdown = MarkdownPage.objects.get(pk=page.pk)
    passage = Passage.objects.rebuild_for(markdown)[0]
    with system_context(reason="test.pgvector.graph_evidence"):
        Passage.objects.filter(pk=passage.pk).update(
            graph_model=records.extraction_model,
            graph_kinds=vault.graph_entity_kinds,
        )
        entities = {}
        for name in sorted(set(names) | {name for edge in edges for name in edge}):
            entity, _ = Entity.objects.get_or_create(
                vault=vault,
                key=entity_key(name),
                kind="concept",
                defaults={"name": name},
            )
            entities[name] = entity
            EntityMention.objects.create(passage=passage, entity=entity, description=body)
        for left, right in edges:
            EntityEdge.objects.create(
                passage=passage,
                subject=entities[left],
                object=entities[right],
                predicate="relates_to",
                description=body,
                weight=1,
            )
    return page, passage, entities
