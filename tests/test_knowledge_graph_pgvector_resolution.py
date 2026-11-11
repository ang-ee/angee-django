"""PostgreSQL's native trigram resolver stays within a vault and entity kind."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import connection
from pydantic_ai.exceptions import UnexpectedModelBehavior
from rebac import system_context

from angee.agents.testing.models import InferenceModel
from angee.knowledge_graph_pgvector.options import GraphOptions
from angee.knowledge_graph_pgvector.testing.models import Entity
from tests.conftest import vault_for
from tests.pgvector_fixtures import passage_records as passage_records
from tests.pgvector_graph_fixtures import graph_records as graph_records

pytestmark = [
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Requires PostgreSQL pg_trgm"),
    pytest.mark.django_db(transaction=True),
]


def test_resolution_prefers_normalized_exact_then_same_kind_trigrams_in_one_vault(graph_records):
    records = graph_records
    other = vault_for(records.owner, name="Other resolver boundary")
    options = GraphOptions()
    # pg_trgm pads each word by two leading spaces and one trailing space,
    # then divides shared unique trigrams by their union: 15 / 18 here.
    with connection.cursor() as cursor:
        cursor.execute("SELECT similarity('acme corporation', 'acme corporatio')")
        assert cursor.fetchone()[0] == pytest.approx(15 / 18)
    assert options.trigram_threshold < 15 / 18
    with system_context(reason="test.pgvector.entity_resolution"):
        target = Entity.objects.resolve_in(records.vault.pk, "Acme Corporation", "organization", candidate_id=None)
        foreign = Entity.objects.resolve_in(other.pk, "Acme Corporatio", "organization", candidate_id=None)
        wrong_kind = Entity.objects.resolve_in(records.vault.pk, "Acme Corporatio", "concept", candidate_id=None)
        assert (
            Entity.objects.resolution_candidates(
                records.vault.pk,
                [SimpleNamespace(name=" ACME  CORPORATION ", kind="organization")],
                model=records.extraction_model,
                options=options,
            )[0]
            == target.pk
        )
        assert (
            Entity.objects.resolution_candidates(
                records.vault.pk,
                [SimpleNamespace(name="Acme Corporatio", kind="organization")],
                model=records.extraction_model,
                options=options,
            )[0]
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
    # X and Y introduce no duplicate leading ' a' trigram from Apollo.
    # Both candidates share seven of eleven unique trigrams with Apollo C.
    with connection.cursor() as cursor:
        cursor.execute("SELECT similarity('apollo x', 'apollo c'), similarity('apollo y', 'apollo c')")
        assert cursor.fetchone() == pytest.approx((7 / 11, 7 / 11))
    infer = Mock()
    monkeypatch.setattr(InferenceModel, "infer", infer)
    with system_context(reason="test.pgvector.ambiguous_entities"):
        first = Entity.objects.resolve_in(records.vault.pk, "Apollo X", "concept", candidate_id=None)
        Entity.objects.resolve_in(records.vault.pk, "Apollo Y", "concept", candidate_id=None)
        assert (
            Entity.objects.resolution_candidates(
                records.vault.pk,
                [SimpleNamespace(name="Apollo C", kind="concept")],
                model=records.extraction_model,
                options=GraphOptions(trigram_threshold=0.5),
            )[0]
            is None
        )
        infer.assert_not_called()
        infer.return_value = SimpleNamespace(output={"candidate": first.pk})
        assert (
            Entity.objects.resolution_candidates(
                records.vault.pk,
                [SimpleNamespace(name="Apollo C", kind="concept")],
                model=records.extraction_model,
                options=GraphOptions(trigram_threshold=0.5, llm_adjudication=True),
            )[0]
            == first.pk
        )
        infer.assert_called_once()
        assert infer.call_args.kwargs["output_schema"]["title"] == "EntityResolution"
        infer.return_value = SimpleNamespace(output={"candidate": 999999})
        with pytest.raises(UnexpectedModelBehavior, match="unknown candidate"):
            Entity.objects.resolution_candidates(
                records.vault.pk,
                [SimpleNamespace(name="Apollo C", kind="concept")],
                model=records.extraction_model,
                options=GraphOptions(trigram_threshold=0.5, llm_adjudication=True),
            )[0]
