"""PostgreSQL's native trigram resolver stays within a vault and entity kind."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import connection
from rebac import system_context

from angee.agents.testing.models import InferenceModel
from angee.knowledge_graph_pgvector.graph_config import GraphOptions
from angee.knowledge_graph_pgvector.testing.models import Entity
from tests.conftest import vault_for
from tests.pgvector_fixtures import passage_records as passage_records
from tests.pgvector_graph_fixtures import graph_records as graph_records

pytestmark = pytest.mark.skipif(connection.vendor != "postgresql", reason="Requires PostgreSQL pg_trgm")


def test_resolution_prefers_normalized_exact_then_same_kind_trigrams_in_one_vault(graph_records):
    records = graph_records
    other = vault_for(records.owner, name="Other resolver boundary")
    options = GraphOptions()
    with system_context(reason="test.pgvector.entity_resolution"):
        target = Entity.objects.resolve_in(records.vault.pk, "Acme Corporation", "organization", candidate_id=None)
        foreign = Entity.objects.resolve_in(other.pk, "Acme Corporatio", "organization", candidate_id=None)
        wrong_kind = Entity.objects.resolve_in(records.vault.pk, "Acme Corporatio", "concept", candidate_id=None)
        assert (
            Entity.objects.resolution_candidate(
                records.vault.pk,
                " ACME  CORPORATION ",
                "organization",
                model=records.extraction_model,
                options=options,
            )
            == target.pk
        )
        assert (
            Entity.objects.resolution_candidate(
                records.vault.pk,
                "Acme Corporatio",
                "organization",
                model=records.extraction_model,
                options=options,
            )
            == target.pk
        )
        assert Entity.objects.resolve_in(
            records.vault.pk,
            "Different entity",
            "organization",
            candidate_id=foreign.pk,
        ).pk not in {foreign.pk, target.pk, wrong_kind.pk}


def test_ambiguous_trigrams_do_not_call_an_llm_unless_enabled(graph_records, monkeypatch):
    records = graph_records
    infer = Mock()
    monkeypatch.setattr(InferenceModel, "infer", infer)
    with system_context(reason="test.pgvector.ambiguous_entities"):
        first = Entity.objects.resolve_in(records.vault.pk, "Apollo A", "concept", candidate_id=None)
        Entity.objects.resolve_in(records.vault.pk, "Apollo B", "concept", candidate_id=None)
        assert (
            Entity.objects.resolution_candidate(
                records.vault.pk,
                "Apollo C",
                "concept",
                model=records.extraction_model,
                options=GraphOptions(trigram_threshold=0.5),
            )
            is None
        )
        infer.assert_not_called()
        infer.return_value = SimpleNamespace(output={"candidate": first.pk})
        assert (
            Entity.objects.resolution_candidate(
                records.vault.pk,
                "Apollo C",
                "concept",
                model=records.extraction_model,
                options=GraphOptions(trigram_threshold=0.5, llm_adjudication=True),
            )
            == first.pk
        )
        infer.assert_called_once()
        assert infer.call_args.kwargs["output_schema"]["title"] == "EntityResolution"
        infer.return_value = SimpleNamespace(output={"candidate": 999999})
        with pytest.raises(ValueError, match="unknown candidate"):
            Entity.objects.resolution_candidate(
                records.vault.pk,
                "Apollo C",
                "concept",
                model=records.extraction_model,
                options=GraphOptions(trigram_threshold=0.5, llm_adjudication=True),
            )
