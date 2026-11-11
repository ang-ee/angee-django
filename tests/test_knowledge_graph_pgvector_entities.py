"""Native graph permissions apply to queryset and point reads for every actor."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from rebac import actor_context, system_context

from angee.agents.models import InferenceModelUse
from angee.agents.testing.models import InferenceModel
from angee.graphql.testing.documents import execute_schema, result_data
from angee.knowledge_graph_pgvector.testing.models import Entity, EntityEdge, EntityMention
from tests.conftest import Vault, create_platform_admin, create_user, vault_for
from tests.pgvector_fixtures import passage_records as passage_records
from tests.pgvector_graph_fixtures import graph_page
from tests.pgvector_graph_fixtures import graph_records as graph_records
from tests.test_knowledge import _grant
from tests.test_knowledge_plugin import _merged_schemas


def test_entities_need_vault_access_and_live_readable_mentions(graph_records):
    records = graph_records
    page, passage, entities = graph_page(records, "Graph evidence", edges=[("Orion", "Lyra")])
    reader, outsider = create_user("entity-reader"), create_user("entity-outsider")
    page_reader, admin = create_user("entity-page-reader"), create_platform_admin("entity-admin")
    _grant(page, "viewer", page_reader)
    with actor_context(records.owner):
        records.vault.grant_record_access("viewer", reader)
    with system_context(reason="test.pgvector.graph_permission_records"):
        edge = EntityEdge.objects.get(passage=passage)
        mention = EntityMention.objects.get(passage=passage, entity=entities["Orion"])
        orphan = Entity.objects.create(vault=records.vault, key="orphan", kind="concept")
    for actor, evidence_read, entity_read in (
        (records.owner, True, True),
        (reader, True, True),
        (admin, True, True),
        (page_reader, True, False),
        (outsider, False, False),
    ):
        with actor_context(actor):
            assert EntityEdge.objects.filter(pk=edge.pk).exists() is evidence_read
            assert EntityMention.objects.filter(pk=mention.pk).exists() is evidence_read
            assert edge.with_actor(actor).has_access("read") is evidence_read
            assert mention.with_actor(actor).has_access("read") is evidence_read
            assert Entity.objects.filter(pk=entities["Orion"].pk).exists() is entity_read
            assert entities["Orion"].with_actor(actor).has_access("read") is entity_read
            assert not Entity.objects.filter(pk=orphan.pk).exists()
            assert not orphan.with_actor(actor).has_access("read")
            assert not edge.with_actor(actor).has_access("write")
    with actor_context(reader), pytest.raises(PermissionDenied):
        mention.with_actor(reader).delete()


def test_unreadable_and_trashed_only_evidence_never_exposes_edges_or_entities(graph_records):
    records = graph_records
    reader = create_user("entity-private-reader")
    hidden_vault = vault_for(records.owner, name="Hidden graph")
    page, passage, entities = graph_page(
        records, "Hidden graph evidence", edges=[("Secret", "Partner")], vault=hidden_vault
    )
    with actor_context(records.owner):
        records.vault.grant_record_access("viewer", reader)
    with system_context(reason="test.pgvector.hidden_edge"):
        edge = EntityEdge.objects.get(passage=passage)
    with actor_context(reader):
        assert not Entity.objects.filter(pk__in=[entity.pk for entity in entities.values()]).exists()
        assert not EntityEdge.objects.filter(pk=edge.pk).exists()
        assert not edge.with_actor(reader).has_access("read")
    with actor_context(records.owner):
        page.trash()
    # Page deleters can inspect trash, but derived graph names and claims stay hidden.
    for actor in (records.owner, reader, create_platform_admin("graph-trash-admin")):
        with actor_context(actor):
            assert not Entity.objects.filter(vault=hidden_vault).exists()
            assert not EntityMention.objects.filter(passage=passage).exists()
            assert not EntityEdge.objects.filter(pk=edge.pk).exists()
            assert not edge.with_actor(actor).has_access("read")
            assert not entities["Secret"].with_actor(actor).has_access("read")
    with actor_context(records.owner):
        page.restore()
        assert EntityEdge.objects.filter(pk=edge.pk).exists()
        assert Entity.objects.filter(pk=entities["Secret"].pk).exists()


def test_diff_cascades_graph_evidence_and_reconciler_collects_only_orphans(graph_records):
    records = graph_records
    page, passage, entities = graph_page(records, "Transient evidence", edges=[("Only here", "Shared")])
    _, retained, _ = graph_page(records, "Retained evidence", names=["Shared"])
    with actor_context(records.owner):
        page.trash()
        page.delete()
    with system_context(reason="test.pgvector.graph_delete"):
        assert not EntityEdge.objects.filter(passage_id=passage.pk).exists()
        assert not EntityMention.objects.filter(passage_id=passage.pk).exists()
        assert Entity.objects.collect_orphans() == 1
        assert not Entity.objects.filter(pk=entities["Only here"].pk).exists()
        assert EntityMention.objects.filter(passage=retained, entity=entities["Shared"]).exists()


def test_extraction_selection_requires_writer_model_access_approval_and_chat_use(graph_records, monkeypatch, settings):
    records = graph_records
    monkeypatch.setattr(
        "angee.knowledge_graph_pgvector.retrieval.PgvectorRetrievalBackend.require_database", lambda: None,
    )
    with system_context(reason="test.selection_change"):
        Vault.objects.filter(pk=records.vault.pk).update(extraction_model=None)
    with actor_context(records.owner), pytest.raises(PermissionDenied, match="cannot read"):
        records.vault.clean()
    selector = records.extraction_model.provider.owner
    with actor_context(selector):
        records.vault.clean()
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"knowledge_graph_extraction": []}
    with actor_context(selector), pytest.raises(PermissionDenied, match="not approved"):
        records.vault.clean()
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = None
    with system_context(reason="test.pgvector.graph_wrong_use"):
        InferenceModel.objects.filter(pk=records.extraction_model.pk).update(model_use=InferenceModelUse.DECISION)
    with actor_context(selector), pytest.raises(ValueError, match="uses"):
        records.vault.clean()


def test_graph_opt_in_defaults_and_vocabulary_validation(db):
    vault = Vault(name="Default")
    assert vault.extraction_model_id is None
    assert vault.graph_entity_kinds == ["concept", "event", "organization", "person", "place"]
    for kinds in ([], ["concept", "concept"], [None], "concept"):
        vault.graph_entity_kinds = kinds
        with pytest.raises(ValidationError):
            vault.clean()


def test_extraction_model_and_kinds_are_writable_through_the_vault_resource(graph_records, monkeypatch):
    records = graph_records
    monkeypatch.setattr(
        "angee.knowledge_graph_pgvector.retrieval.PgvectorRetrievalBackend.require_database", lambda: None,
    )
    selector = records.extraction_model.provider.owner
    _grant(records.vault, "editor", selector)
    schema = _merged_schemas().build("public")
    document = """mutation ($id: String!, $kinds: JSON!) {
      update_vaults_by_pk(pk_columns: {id: $id}, _set: {
        extraction_model: "MODEL", graph_entity_kinds: $kinds
      }) { id extraction_model graph_entity_kinds }
    }""".replace("MODEL", str(records.extraction_model.sqid))
    variables = {"id": str(records.vault.sqid), "kinds": ["concept"]}
    assert execute_schema(schema, document, variables, user=create_user("graph-selection-outsider")).errors
    data = result_data(execute_schema(schema, document, variables, user=selector))["update_vaults_by_pk"]
    assert data["extraction_model"] == str(records.extraction_model.sqid)
    assert data["graph_entity_kinds"] == ["concept"]



def test_unchanged_model_selection_does_not_require_catalogue_read_again(graph_records, monkeypatch):
    records = graph_records
    monkeypatch.setattr(
        "angee.knowledge_graph_pgvector.retrieval.PgvectorRetrievalBackend.require_database", lambda: None,
    )
    with actor_context(records.owner):
        assert not records.extraction_model.with_actor(records.owner).has_access("read")
        records.vault.graph_entity_kinds = list(reversed(records.vault.graph_entity_kinds))
        records.vault.clean()
        records.vault.save(update_fields=["graph_entity_kinds"])
    with system_context(reason="test.graph.sorted_kinds"):
        records.vault.refresh_from_db()
    assert records.vault.graph_entity_kinds == sorted(records.vault.graph_entity_kinds)
