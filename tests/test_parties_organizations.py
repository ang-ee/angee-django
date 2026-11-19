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


def _ingest(source: SimpleNamespace, link: Any, parsed: ParsedOrganization, *, target: Any = None) -> Any:
    with system_context(reason="tests.organizations.ingest"):
        return Party.objects.ingest_organization(
            parsed, stream=source.organizations, link=link, created_by_id=source.owner.pk, target=target
        )


def _legal(organization: Any) -> tuple[str, str]:
    with system_context(reason="tests.organizations.legal"):
        row = Organization._base_manager.get(pk=organization.pk)
        return row.legal_name, row.domain


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


def test_a_source_carrying_no_legal_identity_leaves_it_to_the_sources_that_do(source: SimpleNamespace) -> None:
    """Neither legal name nor domain withdraws this link's legal identity; either states it whole again."""

    unknown = replace(EXAMPLE, legal_name="", domain="")
    with system_context(reason="tests.organizations.link"):
        link = RecordLink.objects.observe(source.organizations, "company-legal")
    organization = _ingest(source, link, EXAMPLE)
    with system_context(reason="tests.organizations.register"):
        register = make_integration("legal-register", model=Directory)
        register_stream = current_stream(register, "organizations", kind=StreamKind.RECORD_REPLICA)
        register_link = RecordLink.objects.observe(
            register_stream, "register-1", target=Party._base_manager.get(pk=organization.pk)
        )
        # A lower-ranked source: the same kind, at a lower confidence.
        Organization._base_manager.get(pk=organization.pk).statements.set(
            aspect="legal_identity",
            values={"legal_name": "Example Registered Limited", "domain": "example.org"},
            asserted_by=register.integration_ptr,
            link=register_link,
            confidence=0.5,
        )
    assert _legal(organization) == ("Example Limited", "example.com")

    _ingest(source, link, unknown)
    assert _legal(organization) == ("Example Registered Limited", "example.org")
    with system_context(reason="tests.organizations.read"):
        statement = Organization.statements.model._base_manager.get(subject_id=organization.pk, link=link)
        assert statement.status == "withdrawn"

    # Carrying either field states the whole aspect again, and it outranks the register.
    _ingest(source, link, replace(unknown, domain="example.net"))
    assert _legal(organization) == ("", "example.net")


def test_a_passed_target_is_an_organization_and_never_rebinds_a_link(source: SimpleNamespace) -> None:
    """Binding lends the party to the directory's contact readers, so ingestion never moves a link."""

    with system_context(reason="tests.organizations.links"):
        person = Person.objects.create(display_name="Ada Example", created_by=source.owner)
        person_link = RecordLink.objects.observe(
            source.organizations, "company-person", target=Party._base_manager.get(pk=person.pk)
        )
        bound = RecordLink.objects.observe(source.organizations, "company-bound")
        unbound = RecordLink.objects.observe(source.organizations, "company-unbound")
    landed = _ingest(source, bound, EXAMPLE)
    with system_context(reason="tests.organizations.other"):
        other = Organization.objects.create(display_name="Other Ltd", created_by=source.owner)
    for link, target, message in (
        (person_link, None, "kind Organization"),
        (unbound, person, "kind Organization"),
        (bound, other, "never rebinds"),
    ):
        with pytest.raises(ValidationError, match=message):
            _ingest(source, link, EXAMPLE, target=target)
    with system_context(reason="tests.organizations.read"):
        assert RecordLink.objects.get(pk=bound.pk).target_object_id == str(landed.pk)
        assert RecordLink.objects.get(pk=person_link.pk).target_object_id == str(person.pk)
        assert not Organization.statements.model._base_manager.filter(link__in=[person_link, unbound]).exists()
    # The link's own target is accepted, and an unbound link binds the organization passed.
    assert _ingest(source, bound, EXAMPLE, target=landed).pk == landed.pk
    assert _ingest(source, unbound, EXAMPLE, target=other).pk == other.pk
    with system_context(reason="tests.organizations.read"):
        assert RecordLink.objects.get(pk=unbound.pk).target_object_id == str(other.pk)


def test_an_organization_named_only_legally_names_its_handles_so(source: SimpleNamespace) -> None:
    """A record with a legal name and no display name names the party and its handles by the legal name."""

    with system_context(reason="tests.organizations.link"):
        link = RecordLink.objects.observe(source.organizations, "company-legal-only")
    organization = _ingest(source, link, replace(EXAMPLE, display_name=""))
    with system_context(reason="tests.organizations.read"):
        assert organization.display_name == "Example Limited"
        names = set(Handle._base_manager.filter(party_id=organization.pk).values_list("display_name", flat=True))
        assert names == {"Example Limited"}


def test_the_organization_projection_compares_the_stored_form() -> None:
    """An organization takes the stored form a contact does, so a spelled-out country equals its code."""

    coded = replace(EXAMPLE, addresses=(replace(EXAMPLE.addresses[0], country="US"),))
    assert organization_projection(EXAMPLE) != organization_projection(coded)
    prepare = Party.objects.prepare_contact
    assert organization_projection(prepare(EXAMPLE)) == organization_projection(prepare(coded))
    assert prepare(EXAMPLE).addresses[0].country == "US"


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


def _land(employment: SimpleNamespace, *employers: Employment, link: Any = None) -> None:
    with system_context(reason="tests.organizations.land_employment"):
        Relationship.objects.land_employment(
            employment.person,
            employers,
            created_by_id=employment.source.owner.pk,
            asserted_by=employment.source.integration,
            link=link or employment.link,
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
        RelationshipKind._base_manager.filter(slug="employee").delete()
        arguments: dict[str, Any] = {
            "created_by_id": e.source.owner.pk,
            "asserted_by": e.source.integration,
            "link": e.link,
        }
        Relationship.objects.land_employment(e.person, [], **arguments)
        with pytest.raises(ValidationError, match="employee.*RelationshipKind"):
            Relationship.objects.land_employment(e.person, [Employment("Example Works")], **arguments)
        with pytest.raises(ValidationError, match="non-empty name"):
            Relationship.objects.land_employment(e.person, [Employment("")], **arguments)
    assert _edges(e) == {}


def test_an_employer_named_twice_lands_once_as_its_first_naming(employment: SimpleNamespace) -> None:
    """A repeated organization or name is one employer: one edge each, stated as first named."""

    e = employment
    _land(
        e,
        Employment(e.first, "Engineer"),
        Employment(e.first, "Advisor"),
        Employment("Example Works", "Lead"),
        Employment("Example Works", "Other"),
    )
    assert sorted(_edges(e).values(), key=str) == sorted(
        [(e.first.pk, "", "Engineer", "asserted"), (None, "Example Works", "Lead", "asserted")], key=str
    )


def test_two_links_naming_one_organization_share_its_edge_and_claim(employment: SimpleNamespace) -> None:
    """One edge per pair, one claim per source and edge: the link that stated it last holds it.

    A link that no longer holds the claim drops nothing when it stops naming the
    employer. The known limit is the converse: the holding link's drop withdraws
    the claim while the other link still names the organization.
    """

    e = employment
    with system_context(reason="tests.organizations.second_link"):
        again = RecordLink.objects.observe(e.source.people, "ada-again", target=Party._base_manager.get(pk=e.person.pk))
    _land(e, Employment(e.first, "Engineer"))
    _land(e, Employment(e.first, "Engineer"), link=again)

    def claims() -> list[tuple[Any, Any, str]]:
        with system_context(reason="tests.organizations.claims"):
            return list(
                Relationship.statements.current()
                .filter(subject__party_id=e.person.pk)
                .values_list("subject__other_party_id", "link_id", "status")
            )

    with system_context(reason="tests.organizations.edges"):
        assert Relationship._base_manager.filter(party_id=e.person.pk).count() == 1
    assert claims() == [(e.first.pk, again.pk, "asserted")]
    _land(e)
    assert claims() == [(e.first.pk, again.pk, "asserted")]
