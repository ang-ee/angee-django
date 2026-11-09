"""Passage-level retrieval preserves Knowledge's page-only callers and access."""

import importlib

from rebac import actor_context

from angee.graphql.testing.documents import execute_schema, result_data
from angee.knowledge.retrieval import LexicalRetrievalBackend, RetrievalHit
from tests.conftest import Page, Vault, addon_schema, create_user, vault_for


def test_lexical_hits_keep_page_search_and_actor_scope(composed_tables):
    owner, outsider = create_user("hit-owner"), create_user("hit-outsider")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Needle", body="A canonical body.")
        hits = Vault.objects.search_passages("Needle")
        assert hits == [RetrievalHit(page=page, rank=1)]
        assert Vault.objects.search_pages("Needle") == [page]
        assert list(vault.retrieval.search("Needle")) == hits
    with actor_context(outsider):
        assert Vault.objects.search_passages("Needle") == []


def test_search_passages_projects_evidence_and_page_search_keeps_its_shape(composed_tables, monkeypatch):
    owner = create_user("hit-projection")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Needle", body="# Section\nEvidence")
    hit = RetrievalHit(page=page, rank=1, heading_path=("Section",), start=10, end=18, text="Evidence", score=0.5)
    monkeypatch.setattr(LexicalRetrievalBackend, "search_many", classmethod(lambda cls, *args, **kwargs: [hit]))
    schema = addon_schema(importlib.import_module("angee.knowledge.schema").schemas, "public")
    data = result_data(
        execute_schema(
            schema,
            """query {
      search_passages(query: "Needle") { page { id title } heading_path start end text rank score }
      search_pages(query: "Needle") { id title }
    }""",
            user=owner,
        )
    )
    assert data["search_passages"] == [
        {
            "page": {"id": str(page.sqid), "title": "Needle"},
            "heading_path": ["Section"],
            "start": 10,
            "end": 18,
            "text": "Evidence",
            "rank": 1,
            "score": 0.5,
        }
    ]
    assert data["search_pages"] == [{"id": str(page.sqid), "title": "Needle"}]


def test_vault_backend_is_writable_only_by_its_writer(composed_tables):
    owner, outsider = create_user("selection-owner"), create_user("selection-outsider")
    vault = vault_for(owner)
    schema = addon_schema(importlib.import_module("angee.knowledge.schema").schemas, "public")
    document = """mutation ($id: String!) {
      update_vaults_by_pk(pk_columns: {id: $id}, _set: {retrieval_class: "lexical"}) { id retrieval_class }
    }"""
    assert execute_schema(schema, document, {"id": str(vault.sqid)}, user=outsider).errors
    data = result_data(execute_schema(schema, document, {"id": str(vault.sqid)}, user=owner))
    assert data["update_vaults_by_pk"]["retrieval_class"] == "LEXICAL"
