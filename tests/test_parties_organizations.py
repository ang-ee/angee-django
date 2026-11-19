"""Organizations and employment land from a source through the parties verbs.

``PartyManager.ingest_organization`` lands a neutral ``ParsedOrganization`` on the
Organization its link names, minting one for an unbound link, and
``RelationshipManager.land_employment`` is the one verb stating a person's
employers, tracked organizations or free-text names, for a source link.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from rebac import system_context

from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink
from angee.messaging.testing.models import (
    Address,
    Channel,
    Directory,
    Handle,
    Organization,
    Party,
    Person,
    Relationship,
    RelationshipKind,
)
from angee.parties.backends import ParsedAddress, ParsedOrganization, organization_projection
from angee.parties.managers import Employment
from tests.conftest import current_stream, make_integration

EXAMPLE = ParsedOrganization(
    display_name="Example Ltd",
    legal_name="Example Limited",
    domain="example.com",
    emails=(("hello@example.com", "work", True),),
    phones=(("+15550100", "", False),),
    addresses=(ParsedAddress(label="office", street="1 Main St", city="Springfield", country="USA"),),
)


@pytest.fixture
def source(db: None) -> SimpleNamespace:
    """A directory with an organizations stream, a people stream and the employee kind."""

    del db
    with system_context(reason="tests.organizations.seed"):
        directory = make_integration("organizations-source", model=Directory)
        RelationshipKind._base_manager.create(
            slug="employee",
            name="Employee",
            inverse_name="Employer",
            category="professional",
            other_party_kind="organization",
        )
        return SimpleNamespace(
            directory=directory,
            integration=directory.integration_ptr,
            owner=directory.owner,
            organizations=current_stream(directory, "organizations", kind=StreamKind.RECORD_REPLICA),
            people=current_stream(directory, "people", kind=StreamKind.RECORD_REPLICA),
        )


def _ingest(source: SimpleNamespace, link: Any, parsed: ParsedOrganization) -> Any:
    with system_context(reason="tests.organizations.ingest"):
        return Party.objects.ingest_organization(
            parsed, stream=source.organizations, link=link, created_by_id=source.owner.pk
        )


def _stated(link: Any) -> dict[str, list[tuple[Any, Any, str]]]:
    """Every statement ``link`` holds, per statement table: key, last change and status."""

    return {
        model.__name__: sorted(model._base_manager.filter(link=link).values_list("pk", "updated_at", "status"))
        for model in (Party.statements.model, Organization.statements.model, Address.statements.model)
    }


def _handle_status(organization: Any, link: Any, value: str) -> str:
    return Party.statements.model._base_manager.get(subject_id=organization.pk, link=link, handle__value=value).status


def test_an_unbound_link_mints_one_organization_and_a_replay_changes_nothing(source: SimpleNamespace) -> None:
    """The first pass binds the link and states every fact; the same record again writes nothing."""

    with system_context(reason="tests.organizations.link"):
        link = RecordLink.objects.observe(source.organizations, "company-1")
    organization = _ingest(source, link, EXAMPLE)
    link.refresh_from_db()
    with system_context(reason="tests.organizations.read"):
        assert link.target_object_id == str(organization.pk)
        assert (organization.display_name, organization.legal_name, organization.domain) == (
            "Example Ltd",
            "Example Limited",
            "example.com",
        )
        handles = Handle._base_manager.filter(party_id=organization.pk)
        assert sorted(handles.values_list("platform", "value", "label", "is_preferred")) == [
            ("email", "hello@example.com", "work", True),
            ("phone", "+15550100", "", False),
        ]
        (address,) = Address._base_manager.filter(party_id=organization.pk)
        # The address lands in its stored form: the spelled-out country becomes its code.
        assert (address.label, address.street, address.country) == ("office", "1 Main St", "US")
        before = _stated(link)
        assert all(before.values())
        assert all(status == "asserted" for rows in before.values() for _pk, _at, status in rows)

    again = _ingest(source, link, EXAMPLE)

    with system_context(reason="tests.organizations.replay"):
        assert again.pk == organization.pk
        assert Organization._base_manager.count() == 1
        assert Address._base_manager.filter(party_id=organization.pk).count() == 1
        assert _stated(link) == before


def test_a_changed_record_renames_clears_drops_and_respells_in_place(source: SimpleNamespace) -> None:
    """A rename, a cleared domain, a dropped phone and a respelled address restate the same rows."""

    with system_context(reason="tests.organizations.link"):
        link = RecordLink.objects.observe(source.organizations, "company-2")
    organization = _ingest(source, link, EXAMPLE)
    with system_context(reason="tests.organizations.read"):
        (address,) = Address._base_manager.filter(party_id=organization.pk)

    changed = replace(
        EXAMPLE,
        display_name="Example Group",
        legal_name="Example Group Limited",
        phones=(),
        addresses=(ParsedAddress(label="Office", street="1  MAIN st", city="springfield", country="US"),),
    )
    _ingest(source, link, changed)
    # The domain is part of the legal identity: omitting it states it empty, beside the legal name.
    renamed = _ingest(source, link, replace(changed, domain=""))

    with system_context(reason="tests.organizations.read"):
        assert renamed.pk == organization.pk
        assert (renamed.display_name, renamed.legal_name, renamed.domain) == (
            "Example Group",
            "Example Group Limited",
            "",
        )
        assert _handle_status(renamed, link, "hello@example.com") == "asserted"
        assert _handle_status(renamed, link, "+15550100") == "withdrawn"
        (respelled,) = Address._base_manager.filter(party_id=organization.pk)
        assert respelled.pk == address.pk
        assert (respelled.label, respelled.street, respelled.city) == ("Office", "1  MAIN st", "springfield")


def test_an_organization_lands_only_through_its_directorys_stream(source: SimpleNamespace) -> None:
    """A link of another stream, or a stream of an integration that is not a directory, is refused."""

    with system_context(reason="tests.organizations.foreign"):
        people_link = RecordLink.objects.observe(source.people, "company-3")
        other = make_integration("organizations-channel", model=Channel, backend_class="manual")
        foreign = current_stream(other, "organizations", kind=StreamKind.RECORD_REPLICA)
        foreign_link = RecordLink.objects.observe(foreign, "company-3")
        with pytest.raises(ValidationError, match="directory's stream"):
            Party.objects.ingest_organization(
                EXAMPLE, stream=source.organizations, link=people_link, created_by_id=source.owner.pk
            )
        with pytest.raises(ValidationError, match="directory's stream"):
            Party.objects.ingest_organization(
                EXAMPLE, stream=foreign, link=foreign_link, created_by_id=source.owner.pk
            )
        assert not Organization._base_manager.exists()


def test_the_organization_projection_ignores_collection_order() -> None:
    """Contact points compare as sets and addresses as a sorted list, as a contact's do."""

    shuffled = replace(
        EXAMPLE,
        emails=(("info@example.com", "", False), *EXAMPLE.emails, *EXAMPLE.emails),
        addresses=(ParsedAddress(street="2 Side St"), *EXAMPLE.addresses),
    )
    reordered = replace(
        shuffled,
        emails=tuple(reversed(shuffled.emails)),
        addresses=tuple(reversed(shuffled.addresses)),
    )
    assert organization_projection(shuffled) == organization_projection(reordered)
    assert organization_projection(EXAMPLE) != organization_projection(replace(EXAMPLE, domain=""))
    assert set(organization_projection(EXAMPLE)) == {
        "display_name",
        "legal_name",
        "domain",
        "emails",
        "phones",
        "addresses",
    }


@pytest.fixture
def employment(source: SimpleNamespace) -> SimpleNamespace:
    """A person a people link names, and two organizations the directory landed."""

    with system_context(reason="tests.organizations.employment"):
        person = Person.objects.create(display_name="Ada Example", created_by=source.owner)
        person_link = RecordLink.objects.observe(source.people, "ada", target=Party._base_manager.get(pk=person.pk))
        first_link = RecordLink.objects.observe(source.organizations, "company-a")
        second_link = RecordLink.objects.observe(source.organizations, "company-b")
    first = _ingest(source, first_link, replace(EXAMPLE, display_name="Example A", emails=(), phones=()))
    second = _ingest(source, second_link, replace(EXAMPLE, display_name="Example B", emails=(), phones=()))
    return SimpleNamespace(source=source, person=person, link=person_link, first=first, second=second)


def _land(employment: SimpleNamespace, *employers: Employment) -> None:
    with system_context(reason="tests.organizations.land_employment"):
        Relationship.objects.land_employment(
            employment.person,
            employers,
            created_by_id=employment.source.owner.pk,
            asserted_by=employment.source.integration,
            link=employment.link,
        )


def _edges(employment: SimpleNamespace) -> dict[Any, tuple[Any, str, str, str]]:
    """The person's employment edges: counterparty, name, title and this link's statement status."""

    with system_context(reason="tests.organizations.edges"):
        statuses = dict(
            Relationship.statements.current().filter(link=employment.link).values_list("subject_id", "status")
        )
        return {
            edge.pk: (edge.other_party_id, edge.other_name, edge.title, statuses.get(edge.pk, ""))
            for edge in Relationship._base_manager.filter(party_id=employment.person.pk).order_by("pk")
        }


def test_employment_to_organizations_lands_one_edge_each_and_withdraws_the_dropped(
    employment: SimpleNamespace,
) -> None:
    """One organization, then two, then one removed: the removed edge keeps its row and loses the claim."""

    e = employment
    _land(e, Employment(e.first, "Engineer"))
    ((first_edge, first_fact),) = _edges(e).items()
    assert first_fact == (e.first.pk, "", "Engineer", "asserted")
    with system_context(reason="tests.organizations.read"):
        first_changed = Relationship._base_manager.get(pk=first_edge).updated_at

    _land(e, Employment(e.first, "Engineer"), Employment(e.second, "Advisor"))
    both = _edges(e)
    assert sorted(both.values()) == sorted(
        [(e.first.pk, "", "Engineer", "asserted"), (e.second.pk, "", "Advisor", "asserted")]
    )
    with system_context(reason="tests.organizations.read"):
        assert Relationship._base_manager.get(pk=first_edge).updated_at == first_changed

    _land(e, Employment(e.second, "Advisor"))
    after = _edges(e)
    assert set(after) == set(both)
    assert after[first_edge] == (e.first.pk, "", "Engineer", "withdrawn")
    assert sorted(fact[3] for fact in after.values()) == ["asserted", "withdrawn"]


def test_an_organization_employer_shares_the_edge_another_source_made(employment: SimpleNamespace) -> None:
    """A tracked pair is one edge: the source states the existing row instead of a second one."""

    e = employment
    with system_context(reason="tests.organizations.manual_edge"):
        manual = Relationship._base_manager.create(
            party_id=e.person.pk,
            other_party=e.first,
            kind=RelationshipKind.employee(),
            created_by=e.source.owner,
        )
    _land(e, Employment(e.first, "Engineer"))
    assert _edges(e) == {manual.pk: (e.first.pk, "", "Engineer", "asserted")}


def test_a_free_text_employer_keeps_one_edge_per_name_the_link_states(employment: SimpleNamespace) -> None:
    """The free-text path: a replay changes nothing, a rename renames in place, a drop withdraws."""

    e = employment
    _land(e, Employment("Example Works", "Engineer", "Platform"))
    ((edge, fact),) = _edges(e).items()
    assert fact == (None, "Example Works", "Engineer", "asserted")
    with system_context(reason="tests.organizations.read"):
        changed = Relationship._base_manager.get(pk=edge).updated_at

    _land(e, Employment("Example Works", "Engineer", "Platform"))
    with system_context(reason="tests.organizations.read"):
        assert Relationship._base_manager.get(pk=edge).updated_at == changed

    _land(e, Employment("Example Studio", "Lead"))
    assert _edges(e) == {edge: (None, "Example Studio", "Lead", "asserted")}

    _land(e, Employment("Example Studio", "Lead"), Employment("Example Lab", "Adviser"))
    named = _edges(e)
    assert named[edge] == (None, "Example Studio", "Lead", "asserted")
    assert sorted(named.values())[0][1:] == ("Example Lab", "Adviser", "asserted")

    # Naming a tracked organization instead withdraws the link's free-text edges, which keep their rows.
    _land(e, Employment(e.first, "Lead"))
    switched = _edges(e)
    assert len(switched) == 3
    assert [fact[3] for pk, fact in switched.items() if fact[0] is None] == ["withdrawn", "withdrawn"]
    assert [fact for fact in switched.values() if fact[0] is not None] == [(e.first.pk, "", "Lead", "asserted")]

    _land(e)
    assert {fact[3] for fact in _edges(e).values()} == {"withdrawn"}


def test_employment_needs_the_employee_kind_only_to_state_an_employer(employment: SimpleNamespace) -> None:
    """Without the kind no edge exists to withdraw; naming an employer is refused."""

    e = employment
    with system_context(reason="tests.organizations.no_kind"):
        arguments: dict[str, Any] = {
            "created_by_id": e.source.owner.pk,
            "asserted_by": e.source.integration,
            "link": e.link,
            "kinds": {"employee": None},
        }
        Relationship.objects.land_employment(e.person, [], **arguments)
        with pytest.raises(ValidationError, match="employee.*RelationshipKind"):
            Relationship.objects.land_employment(e.person, [Employment("Example Works")], **arguments)
        with pytest.raises(ValidationError, match="non-empty name"):
            Relationship.objects.land_employment(e.person, [Employment("")], **{**arguments, "kinds": None})
    assert _edges(e) == {}
