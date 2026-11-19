"""A party is a knowledge binding target: knowledge declares it, since parties cannot depend on knowledge."""

from __future__ import annotations

import pytest
from rebac import PermissionDenied, actor_context, system_context

from angee.knowledge.schema import schemas as knowledge_schemas
from angee.messaging.testing.models import Party, Person
from angee.parties.schema import schemas as parties_schemas
from tests.conftest import Page, RecordBinding, addon_schema, create_user, vault_for
from tests.test_knowledge import _grant


def test_a_person_carries_page_bindings_read_and_written_through_both_ends(db: None) -> None:
    """The binding reads with read on the page and the party, and is written with write on both."""

    owner, reader, page_reader = create_user("party-pages-owner"), create_user("party-pages-reader"), create_user(
        "party-pages-page-reader"
    )
    vault = vault_for(owner, name="People")
    with actor_context(owner):
        person = Person.objects.create(display_name="Ada Example")
        page = Page.objects.create_in(vault, title="Ada Example")
        binding = RecordBinding.objects.upsert(page=page, target=person, role="related")
    with system_context(reason="tests.knowledge.party_bindings.party"):
        party = Party.objects.get(pk=person.pk)
    assert RecordBinding.declares_target(Person) and RecordBinding.declares_target(Party)
    assert binding.record_model_label == "parties.Party"

    for user in (reader, page_reader):
        _grant(vault, "viewer", user)
    _grant(party, "reader", reader)
    with actor_context(reader):
        assert list(RecordBinding.objects.pages_for_record(person)) == [binding]
    with actor_context(page_reader):
        assert list(RecordBinding.objects.pages_for_record(person)) == []

    # Writing the page and reading the person binds nothing: the party's write is the other end.
    _grant(vault, "editor", reader)
    with actor_context(reader), pytest.raises(PermissionDenied):
        RecordBinding.objects.upsert(page=page, target=person, role="reference")


def test_a_merge_moves_each_persons_bindings_to_the_survivor_and_loses_none(db: None) -> None:
    """Two people's pages both end on the survivor; one page bound to both under one role stays one binding."""

    owner = create_user("party-pages-merge-owner")
    vault = vault_for(owner, name="People")
    with actor_context(owner):
        survivor = Person.objects.create(display_name="Ada Example")
        merged = Person.objects.create(display_name="Ada E.")
        first, second, shared = (Page.objects.create_in(vault, title=title) for title in ("Ada", "Ada E.", "Shared"))
        RecordBinding.objects.upsert(page=first, target=survivor, role="related")
        RecordBinding.objects.upsert(page=second, target=merged, role="related")
        RecordBinding.objects.upsert(page=shared, target=survivor, role="related")
        RecordBinding.objects.upsert(page=shared, target=merged, role="related")

        Party.objects.get(pk=survivor.pk).merge(records=[merged.public_id])

        bound = sorted(page.title for page in Page.objects.filter(record_bindings__object_id=survivor.pk))
        assert bound == ["Ada", "Ada E.", "Shared"]
        assert not RecordBinding.objects.filter(object_id=merged.pk).exists()


def test_a_persons_record_lists_the_binding_edge_so_it_shows_its_pages() -> None:
    """Knowledge's Pages group shows on the record views of a type whose resource lists the binding edge."""

    people = {item.model_label: item for item in addon_schema(parties_schemas, "public").angee_resources}
    assert "knowledge.RecordBinding" in people["parties.Person"].record_edges
    assert "knowledge.RecordBinding" in people["parties.Organization"].record_edges
    pages = {item.model_label: item for item in addon_schema(knowledge_schemas, "public").angee_resources}
    assert "knowledge.Page" in pages
