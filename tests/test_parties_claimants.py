"""The person a source makes of a contact its record carried: ``PartyManager.claimant_as_system`` and its claims."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import count
from typing import Any, NamedTuple

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rebac import actor_context, system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.base.querysets import query_batch_size
from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink
from angee.messaging.testing.models import Channel, Handle, Organization, Party, Person
from angee.provenance.evidence import EvidenceReference
from tests.conftest import create_user, current_stream, make_integration
from tests.test_parties_same_as import _pairs, _reconcile

pytestmark = pytest.mark.django_db

ADDRESS = "ada@example.test"
NUMBER = "+1 787 555 0100"

behind = EvidenceReference.of
"""Name a record as a claim's evidence names it."""


class Given(NamedTuple):
    """What the verb answered for one contact, and the record that carried it."""

    person: Any
    email: Any
    phone: Any
    record: Any


@dataclass
class Source:
    """A stand-in integration: its record links are the records it keeps, and it calls the verb as its importer does."""

    stream: Any
    own: Any
    """Its own records, as the queryset the verb takes: the links of this integration."""
    owner: Any
    reason: str
    keys: Iterator[int]

    def record(self, **facts: Any) -> Any:
        """Keep one more record that carries a contact."""

        return RecordLink.objects.observe(self.stream, f"contact-{next(self.keys)}", **facts)

    def given(self, *, evidence: Any = None, **contact: str) -> Given:
        """Hand the verb one contact, on a new record unless ``evidence`` names the one that carried it."""

        evidence = self.record() if evidence is None else evidence
        person, email, phone = Party.objects.claimant_as_system(
            **contact, evidence=evidence, own=self.own, owner_id=self.owner.pk, reason=self.reason,
        )
        return Given(person, email, phone, evidence)


def _source(name: str, owner: Any) -> Source:
    with system_context(reason="tests.parties.claimants"):
        bridge = make_integration(name, model=Channel, backend_class="manual")
        stream = current_stream(bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
    return Source(
        stream=stream,
        own=RecordLink._base_manager.filter(stream__integration_id=bridge.pk),
        owner=owner,
        reason=f"tests.{name}.contact",
        keys=count(1),
    )


@pytest.fixture
def owner(db: None) -> Any:
    del db
    return create_user("claimant-owner")


@pytest.fixture
def source(owner: Any) -> Source:
    return _source("source", owner)


def _person(owner: Any, name: str) -> Any:
    with system_context(reason="tests.parties.claimants"):
        return Person.objects.create(display_name=name, created_by=owner)


def _row(owner: Any, value: str, *, platform: str = "email") -> Any:
    with system_context(reason="tests.parties.claimants"):
        return Handle.objects.upsert(platform=platform, value=value, created_by_id=owner.pk)


def _holds(party: Any, value: str, owner: Any, *, platform: str = "email") -> Any:
    """State in full that ``value`` is ``party``'s, as a source that vouches for it does; return the row they hold."""

    handle = _row(owner, value, platform=platform)
    with system_context(reason="tests.parties.claimants"):
        rule = Party.objects.rule_source("normalized_twin")
        Party.objects.assert_handle(party, handle, asserted_by=rule, evidence=[handle])
        handle.refresh_from_db()
    assert handle.party_id == party.pk
    return handle


def _claimed_for(party: Any, handle: Any, evidence: Any) -> None:
    """Record, as some other caller of the claim owner does, that ``handle`` was claimed for ``party``."""

    with system_context(reason="tests.parties.claimants"):
        Party.objects.propose_claimed_handle_as_system(party, handle, evidence=evidence, reason="tests.other.claim")


def _standing(**narrowed: Any) -> dict[tuple[Any, Any], frozenset[EvidenceReference]]:
    with system_context(reason="tests.parties.claimants"):
        return Party.objects.handle_statements().filter(**narrowed).standing_claims()


def _claims(party: Any) -> dict[str, set[EvidenceReference]]:
    """The addresses and numbers ``party`` claims without holding them, each with the records behind the claim."""

    standing = _standing(subject_id=party.pk)
    rows = Handle._base_manager.in_bulk([handle_id for _party_id, handle_id in standing])
    return {
        rows[handle_id].value: set(records)
        for (_party_id, handle_id), records in standing.items()
        if rows[handle_id].party_id != party.pk
    }


def _stance_model() -> Any:
    return Party.objects.handle_statements().model


def _stances(party: Any) -> list[Any]:
    """Every handle statement made of ``party``, whatever became of it."""

    return list(_stance_model()._base_manager.filter(subject_id=party.pk, aspect="handle").order_by("pk"))


def _withdraw_claims(party: Any) -> None:
    """The rule that records sources' claims takes back what it stated of ``party``."""

    with system_context(reason="tests.parties.claimants"):
        subject: Any = Party._base_manager.get(pk=party.pk)
        subject.statements.withdraw(Party.objects.rule_source("source_sender"), subjects=[subject])


def _holder_of(handle: Any) -> Any:
    return Handle._base_manager.get(pk=handle.pk).party_id


def _people() -> int:
    return int(Person._base_manager.count())


def _merge(owner: Any, survivor: Any, merged: Any) -> None:
    """A person who writes both says the two are one."""

    with actor_context(owner):
        Party.objects.get(pk=survivor.pk).merge(records=[Party._base_manager.get(pk=merged.pk).public_id])


def test_a_first_contact_makes_a_person_who_claims_the_address_and_holds_nothing(owner: Any, source: Source) -> None:
    """Named system work with no actor around it: the address is recorded for review and decides nothing."""

    ada = source.given(email=ADDRESS, display_name="  Ada   Example ", given_name="Ada ", family_name=" Example")

    person = Person._base_manager.get(pk=ada.person.pk)
    assert (person.display_name, person.given_name, person.family_name) == ("Ada Example", "Ada", "Example")
    assert person.created_by_id == owner.pk
    assert (ada.email.platform, ada.email.value, ada.email.display_name, ada.phone) == (
        "email", ADDRESS, "Ada Example", None,
    )
    assert _holder_of(ada.email) is None
    assert not Handle._base_manager.filter(party=person).exists()
    assert _claims(person) == {ADDRESS: {behind(ada.record)}}
    (claim,) = _stances(person)
    assert (claim.applied, claim.confidence < Party.REVIEW_CONFIDENCE) == (False, True)


def test_a_sources_next_record_finds_the_person_it_made_and_stands_behind_their_claim(source: Source) -> None:
    """Found again, they have nothing new stated of them; their claim gains one reference per record, once."""

    first = source.given(email=ADDRESS, display_name="Ada Example")

    second = source.given(email=ADDRESS, display_name="A. Example", given_name="A.")

    assert (second.person.pk, second.email.pk, _people()) == (first.person.pk, first.email.pk, 1)
    kept = Person._base_manager.get(pk=first.person.pk)
    assert (kept.display_name, kept.given_name) == ("Ada Example", "")
    assert _claims(kept) == {ADDRESS: {behind(first.record), behind(second.record)}}
    # A record handed over twice stands behind the claim once.
    source.given(email=ADDRESS, evidence=second.record)
    (claim,) = _stances(kept)
    assert [reference["id"] for reference in claim.evidence] == [behind(first.record).id, behind(second.record).id]


def test_another_sources_record_for_the_address_makes_its_own_person_and_takes_nobodys(
    owner: Any, source: Source,
) -> None:
    other = _source("another", owner)
    theirs = other.given(email=ADDRESS, display_name="Ada Example")

    ours = source.given(email="Ada@Example.test", display_name="A. Example")

    assert ours.person.pk != theirs.person.pk
    assert (ours.email.pk, _holder_of(ours.email)) == (theirs.email.pk, None)
    assert _claims(theirs.person) == {ADDRESS: {behind(theirs.record)}}
    assert _claims(ours.person) == {ADDRESS: {behind(ours.record)}}
    # Each finds its own again, and only its own.
    assert other.given(email=ADDRESS).person.pk == theirs.person.pk
    assert source.given(email=ADDRESS).person.pk == ours.person.pk
    assert _people() == 2


@pytest.mark.parametrize(
    "linked",
    [
        pytest.param("holder", id="a-person-who-holds-it"),
        pytest.param("organization", id="an-organization-that-holds-it"),
        pytest.param("account", id="an-account-that-signs-in-with-it"),
        pytest.param("claimant", id="a-person-someone-else-claimed-it-for"),
    ],
)
def test_nobody_elses_person_is_taken_whoever_is_linked_to_the_address(owner: Any, source: Source, linked: str) -> None:
    """Whether the two are one is for the same-person review: the source's person only claims the address."""

    with system_context(reason="tests.parties.claimants"):
        if linked == "account":
            account = get_user_model().objects.create_user(username="ada-account", email=ADDRESS)
            known = Party.objects.for_user(account)
        elif linked == "organization":
            known = Organization.objects.create(display_name="Example School", created_by=owner)
        else:
            known = Person.objects.create(display_name="A. Example", created_by=owner)
    if linked in ("holder", "organization"):
        _holds(known, ADDRESS, owner)
    elif linked == "claimant":
        row = _row(owner, ADDRESS)
        _claimed_for(known, row, row)
    held, stated = list(Handle._base_manager.filter(party=known)), _standing(subject_id=known.pk)

    ada = source.given(email="Ada@Example.test", display_name="Ada Example")

    assert ada.person.pk != known.pk
    assert (list(Handle._base_manager.filter(party=known)), _standing(subject_id=known.pk)) == (held, stated)
    assert Handle._base_manager.filter(platform="email").count() == 1
    assert _claims(ada.person) == {ada.email.value: {behind(ada.record)}}
    assert source.given(email=ADDRESS).person.pk == ada.person.pk


def test_a_person_the_source_adopted_from_someone_else_is_not_its_own(owner: Any, source: Source) -> None:
    """A record that points at a person does not make them the source's: only a claim's evidence says who made them."""

    theirs = _source("another", owner).given(email=ADDRESS, display_name="Ada Example")
    adopted = source.record(target=theirs.person)
    assert adopted.target_object_id == str(theirs.person.pk)

    made = source.given(email=ADDRESS, display_name="Ada Example", evidence=adopted)

    assert made.person.pk != theirs.person.pk
    assert _claims(theirs.person) == {ADDRESS: {behind(theirs.record)}}
    assert _claims(made.person) == {ADDRESS: {behind(adopted)}}
    assert source.given(email=ADDRESS).person.pk == made.person.pk


@pytest.mark.parametrize(
    "ruling",
    [
        pytest.param("withdrawn", id="its-source-withdrew-it"),
        pytest.param("dismissed", id="a-person-ruled-against-it"),
    ],
)
def test_a_claim_that_no_longer_stands_finds_nobody(owner: Any, source: Source, ruling: str) -> None:
    first = source.given(email=ADDRESS, display_name="Ada Example")
    party = Party._base_manager.get(pk=first.person.pk)
    if ruling == "withdrawn":
        _withdraw_claims(party)
    else:
        (claim,) = _stances(party)
        claim.with_actor(owner).dismiss()
    assert _claims(party) == {}

    second = source.given(email=ADDRESS, display_name="Ada Example")

    assert second.person.pk != first.person.pk
    assert second.email.pk == first.email.pk
    assert _claims(party) == {}
    assert _claims(second.person) == {ADDRESS: {behind(second.record)}}


def test_a_trashed_person_is_nobodys_answer(source: Source) -> None:
    first = source.given(email=ADDRESS, display_name="Ada Example")
    with system_context(reason="tests.parties.claimants"):
        Party._base_manager.get(pk=first.person.pk).trash(reason="Removed by their owner")

    second = source.given(email=ADDRESS, display_name="Ada Example")

    assert second.person.pk != first.person.pk
    assert source.given(email=ADDRESS).person.pk == second.person.pk


def test_of_several_people_the_source_stands_behind_for_an_address_the_earliest_is_found(
    owner: Any, source: Source,
) -> None:
    """The earliest by key, whichever of them the source made or claimed for last."""

    elder = _person(owner, "Ada Example")
    made = source.given(email=ADDRESS, display_name="A. Example")
    _claimed_for(elder, made.email, source.record())
    people = _people()

    found = source.given(email=ADDRESS)

    assert elder.pk < made.person.pk
    assert (found.person.pk, _people()) == (elder.pk, people)


def test_a_claimant_merged_into_the_person_who_holds_the_address_is_found_as_the_survivor(
    owner: Any, source: Source,
) -> None:
    holder = _person(owner, "Ada Example")
    held = _holds(holder, ADDRESS, owner)
    made = source.given(email=ADDRESS, display_name="A. Example")
    _merge(owner, holder, made.person)
    people = _people()

    again = source.given(email=ADDRESS)

    assert (again.person.pk, again.email.pk) == (holder.pk, held.pk)
    assert (_people(), _holder_of(held)) == (people, holder.pk)


def test_two_sources_claimants_of_one_address_merged_are_each_found_as_the_survivor(
    owner: Any, source: Source,
) -> None:
    """The merge folds the two claims into one: the records that carried the address for either stay behind it."""

    other = _source("another", owner)
    theirs = other.given(email=ADDRESS, display_name="Ada Example")
    ours = source.given(email=ADDRESS, display_name="A. Example")
    _merge(owner, theirs.person, ours.person)
    people = _people()

    again = other.given(email=ADDRESS), source.given(email=ADDRESS)

    assert {found.person.pk for found in again} == {theirs.person.pk}
    assert _people() == people
    assert _claims(theirs.person) == {
        ADDRESS: {behind(row.record) for row in (theirs, ours, *again)},
    }


def test_a_claim_a_merge_superseded_makes_nothing_stand_by_itself(owner: Any, source: Source) -> None:
    """Once the claim it was folded under is withdrawn, the superseded one finds nobody."""

    other = _source("another", owner)
    theirs = other.given(email=ADDRESS, display_name="Ada Example")
    ours = source.given(email=ADDRESS, display_name="A. Example")
    _merge(owner, theirs.person, ours.person)
    survivor = Party._base_manager.get(pk=theirs.person.pk)
    assert sorted(stance.superseded_id is not None for stance in _stances(survivor)) == [False, True]
    _withdraw_claims(survivor)

    again = other.given(email=ADDRESS), source.given(email=ADDRESS)

    assert len({survivor.pk, *(found.person.pk for found in again)}) == 3


def test_a_claim_still_naming_a_merged_away_person_answers_through_their_survivor(owner: Any, source: Source) -> None:
    """A redirect is identity: whatever still names the merged row is read as its survivor's."""

    made = source.given(email=ADDRESS, display_name="A. Example")
    survivor = _person(owner, "Ada Example")
    Party._base_manager.filter(pk=made.person.pk).update(merged_into=survivor)
    people = _people()

    again = source.given(email=ADDRESS)

    assert (again.person.pk, again.email.pk, _people()) == (survivor.pk, made.email.pk, people)
    assert _claims(survivor) == {ADDRESS: {behind(again.record)}}


def test_one_person_with_two_addresses_is_found_again_by_each_addresss_own_claim(owner: Any, source: Source) -> None:
    home = source.given(email=ADDRESS, display_name="Ada Example")
    work = source.given(email="ada.work@example.test", display_name="Ada Example")
    assert home.person.pk != work.person.pk
    _merge(owner, home.person, work.person)
    people = _people()

    at_home, at_work = source.given(email=ADDRESS), source.given(email="Ada.Work@example.test")

    assert {at_home.person.pk, at_work.person.pk} == {home.person.pk}
    assert (at_home.email.pk, at_work.email.pk) == (home.email.pk, work.email.pk)
    assert _people() == people


def test_another_spelling_of_the_address_is_the_same_address(source: Source) -> None:
    first = source.given(email="Ada.Example@Example.TEST", display_name="Ada Example")

    second = source.given(email="  ada.example@example.test ")

    assert (second.person.pk, second.email.pk) == (first.person.pk, first.email.pk)
    assert Handle._base_manager.filter(platform="email").count() == 1


def test_a_claim_on_any_row_of_the_address_finds_the_person_and_that_row_is_the_one_they_carry(
    owner: Any, source: Source,
) -> None:
    """Two spellings can be two rows: the person is found by the row their claim names, not only the oldest."""

    older, newer = _row(owner, "Ada@Example.test"), _row(owner, ADDRESS)
    ada = _person(owner, "Ada Example")
    _claimed_for(ada, newer, source.record())

    found = source.given(email="ADA@example.test")

    assert (found.person.pk, found.email.pk) == (ada.pk, newer.pk)
    assert not _standing(handle=older)


def test_a_new_persons_number_is_made_and_claimed_beside_the_address(source: Source) -> None:
    ada = source.given(email=ADDRESS, phone=f" {NUMBER} ", display_name="Ada Example")

    assert (ada.phone.platform, ada.phone.value, ada.phone.normalized_value) == ("phone", NUMBER, "+17875550100")
    assert _claims(ada.person) == {ADDRESS: {behind(ada.record)}, NUMBER: {behind(ada.record)}}
    assert not Handle._base_manager.filter(party__isnull=False).exists()


def test_a_number_someone_else_holds_is_only_claimed_by_a_new_person(owner: Any, source: Source) -> None:
    other = _person(owner, "Bo Example")
    held = _holds(other, NUMBER, owner, platform="phone")

    ada = source.given(email=ADDRESS, phone="+17875550100", display_name="Ada Example")

    assert (ada.phone.pk, _holder_of(held)) == (held.pk, other.pk)
    assert _claims(ada.person)[NUMBER] == {behind(ada.record)}


def test_a_person_found_again_carries_the_number_their_own_claim_names(source: Source) -> None:
    first = source.given(email=ADDRESS, phone=NUMBER, display_name="Ada Example")

    second = source.given(email=ADDRESS, phone="+17875550100")

    assert (second.person.pk, second.phone.pk) == (first.person.pk, first.phone.pk)
    assert _claims(first.person)[NUMBER] == {behind(first.record), behind(second.record)}


def test_a_person_found_again_carries_a_number_they_hold(owner: Any, source: Source) -> None:
    first = source.given(email=ADDRESS, display_name="Ada Example")
    held = _holds(first.person, NUMBER, owner, platform="phone")

    second = source.given(email=ADDRESS, phone=NUMBER)

    assert (second.person.pk, second.phone.pk) == (first.person.pk, held.pk)
    assert NUMBER not in _claims(first.person)


@pytest.mark.parametrize(
    "number",
    [
        pytest.param("unknown", id="one-nobody-has"),
        pytest.param("held", id="one-someone-else-holds"),
        pytest.param("claimed", id="one-another-source-claimed-for-them"),
    ],
)
def test_a_number_new_to_a_person_found_again_is_left_out_and_nothing_is_stated(
    owner: Any, source: Source, number: str,
) -> None:
    """A number given beside a known address may be someone else's: a second mention confirms nothing."""

    first = source.given(email=ADDRESS, display_name="Ada Example")
    if number == "held":
        _holds(_person(owner, "Bo Example"), NUMBER, owner, platform="phone")
    elif number == "claimed":
        _claimed_for(first.person, _row(owner, NUMBER, platform="phone"), _source("another", owner).record())
    numbers = list(Handle._base_manager.filter(platform="phone").values_list("pk", "party_id"))
    stated = _standing(handle__platform="phone")

    second = source.given(email=ADDRESS, phone=NUMBER)

    assert (second.person.pk, second.phone) == (first.person.pk, None)
    assert list(Handle._base_manager.filter(platform="phone").values_list("pk", "party_id")) == numbers
    assert _standing(handle__platform="phone") == stated


@pytest.mark.parametrize(
    "number",
    [
        pytest.param("n/a", id="nothing-to-read-in-it"),
        pytest.param("7" * 600, id="refused-by-the-handle-owner"),
    ],
)
def test_a_number_the_handle_owner_cannot_read_is_left_out(source: Source, number: str) -> None:
    ada = source.given(email=ADDRESS, phone=number, display_name="Ada Example")

    assert (ada.email.value, ada.phone) == (ADDRESS, None)
    assert not Handle._base_manager.filter(platform="phone").exists()
    assert _claims(ada.person) == {ADDRESS: {behind(ada.record)}}


def test_a_contact_with_no_address_and_no_number_states_nothing(source: Source) -> None:
    named = source.given(given_name="Bo", family_name="Example")
    unnamed = source.given()

    assert (named.person.display_name, named.email, named.phone) == ("Bo Example", None, None)
    assert unnamed.person.pk != named.person.pk
    assert (unnamed.person.display_name, unnamed.email, unnamed.phone) == (Party.PLACEHOLDER_NAME, None, None)
    assert not Handle._base_manager.exists()
    assert not _stance_model()._base_manager.exists()


@pytest.mark.parametrize(
    "address",
    [
        pytest.param("n/a", id="n-a"),
        pytest.param("ada", id="a-bare-word"),
        pytest.param("@example.test", id="no-name-before-the-at"),
        pytest.param("ada@", id="no-domain-after-the-at"),
        pytest.param("a b@example.test", id="whitespace-in-the-name"),
        pytest.param("ada\x07@example.test", id="a-control-character-in-the-name"),
        pytest.param("a" * 600 + "@example.test", id="longer-than-an-address-may-be"),
    ],
)
def test_what_cannot_be_an_email_address_is_no_address(source: Source, address: str) -> None:
    """Two contacts that gave the same non-address are not one person for it, and it makes no handle."""

    first = source.given(email=address, display_name="Ada Example")
    second = source.given(email=address, display_name="Bo Example")

    assert first.person.pk != second.person.pk
    assert (first.email, second.email) == (None, None)
    assert not Handle._base_manager.exists()
    assert not _stance_model()._base_manager.exists()


@pytest.mark.parametrize(
    "address",
    [
        pytest.param("jörg@example.test", id="jorg-with-an-umlaut"),
        pytest.param("peña@example.test", id="pena-with-a-tilde"),
    ],
)
def test_a_name_part_in_any_script_is_an_address_that_finds_its_person_again(source: Source, address: str) -> None:
    """An address is not only plain ASCII: its handle is made as typed, and the same spelling finds the person."""

    first = source.given(email=address, display_name="Ada Example")

    second = source.given(email=address)

    assert (first.email.platform, first.email.value) == ("email", address)
    assert (second.person.pk, second.email.pk, _people()) == (first.person.pk, first.email.pk, 1)
    assert _claims(first.person) == {address: {behind(first.record), behind(second.record)}}


def test_an_internationalised_domain_is_an_address_stored_as_typed(source: Source) -> None:
    """The handle keeps the domain as typed and compares it lower-cased; the handle owner never punycodes it.

    So another letter case of it is the same address, and its punycode spelling is another row.
    """

    first = source.given(email="Ada@Exämple.test", display_name="Ada Example")

    second = source.given(email="ada@exämple.test")
    punycoded = source.given(email="ada@xn--exmple-cua.test")

    assert (first.email.value, first.email.normalized_value) == ("Ada@Exämple.test", "ada@exämple.test")
    assert (second.person.pk, second.email.pk) == (first.person.pk, first.email.pk)
    assert punycoded.person.pk != first.person.pk
    assert punycoded.email.normalized_value == "ada@xn--exmple-cua.test"


def test_a_contact_whose_address_is_no_email_address_is_decided_by_its_number(source: Source) -> None:
    first = source.given(email="n/a", phone=NUMBER, display_name="Bo Example")

    second = source.given(email="n/a", phone=NUMBER)

    assert (second.person.pk, second.phone.pk, second.email) == (first.person.pk, first.phone.pk, None)
    assert not Handle._base_manager.filter(platform="email").exists()


def test_a_contact_with_only_a_number_is_found_again_by_the_sources_claim_on_it(source: Source) -> None:
    """The same evidence rule as for the address: the number's claim has one of the source's records behind it."""

    first = source.given(phone=NUMBER, display_name="Bo Example")

    second = source.given(phone="+17875550100", display_name="B. Example")

    assert (second.person.pk, second.phone.pk, second.email, _people()) == (first.person.pk, first.phone.pk, None, 1)
    assert Person._base_manager.get(pk=first.person.pk).display_name == "Bo Example"
    assert _claims(first.person) == {NUMBER: {behind(first.record), behind(second.record)}}


def test_another_sources_contact_with_only_the_number_makes_its_own_person(owner: Any, source: Source) -> None:
    other = _source("another", owner)
    theirs = other.given(phone=NUMBER, display_name="Bo Example")

    ours = source.given(phone=NUMBER, display_name="B. Example")

    assert ours.person.pk != theirs.person.pk
    assert (ours.phone.pk, _holder_of(ours.phone)) == (theirs.phone.pk, None)
    assert (_claims(theirs.person), _claims(ours.person)) == (
        {NUMBER: {behind(theirs.record)}}, {NUMBER: {behind(ours.record)}},
    )
    assert (other.given(phone=NUMBER).person.pk, source.given(phone=NUMBER).person.pk) == (
        theirs.person.pk, ours.person.pk,
    )


def test_a_number_someone_else_holds_is_not_taken_by_a_contact_with_only_the_number(
    owner: Any, source: Source,
) -> None:
    holder = _person(owner, "Bo Example")
    held = _holds(holder, NUMBER, owner, platform="phone")

    made = source.given(phone=NUMBER, display_name="B. Example")

    assert made.person.pk != holder.pk
    assert (made.phone.pk, _holder_of(held)) == (held.pk, holder.pk)
    assert _claims(made.person) == {NUMBER: {behind(made.record)}}
    assert source.given(phone=NUMBER).person.pk == made.person.pk


def test_the_email_decides_when_a_contact_gives_both(source: Source) -> None:
    """A number the source's person claims does not find them for a record that carries another address."""

    first = source.given(email=ADDRESS, phone=NUMBER, display_name="Ada Example")

    second = source.given(email="bo@example.test", phone=NUMBER, display_name="Bo Example")

    assert second.person.pk != first.person.pk
    assert (second.phone.pk, _holder_of(second.phone)) == (first.phone.pk, None)
    assert source.given(email=ADDRESS, phone=NUMBER).person.pk == first.person.pk


def test_two_records_in_one_transaction_decide_one_after_the_other(source: Source) -> None:
    with transaction.atomic():
        first = source.given(email=ADDRESS, phone=NUMBER, display_name="Ada Example")
        second = source.given(email=ADDRESS, phone=NUMBER)

    assert (second.person.pk, second.email.pk, second.phone.pk) == (first.person.pk, first.email.pk, first.phone.pk)
    assert _people() == 1


_ARRIVALS = st.lists(
    st.tuples(
        st.sampled_from(("first", "second")),
        st.sampled_from(
            (("email", ADDRESS), ("email", "Ada@Example.TEST"), ("email", "bo@example.test"), ("phone", NUMBER),
             ("phone", "+17875550100"))
        ),
    ),
    min_size=1,
    max_size=5,
)


@settings(deadline=None, max_examples=12, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(arrivals=_ARRIVALS)
def test_the_order_records_arrive_in_never_changes_whom_each_source_finds(
    db: None, arrivals: list[tuple[str, tuple[str, str]]],
) -> None:
    """One person per source and address or number, whoever met it first, and nobody holds it; examples roll back."""

    del db
    with transaction.atomic():
        owner = create_user("claimant-arrivals-owner")
        sources = {name: _source(name, owner) for name in ("first", "second")}
        found: defaultdict[tuple[str, str, str], set[Any]] = defaultdict(set)
        for name, (kind, value) in arrivals:
            ada = sources[name].given(**{kind: value}, display_name="Ada Example")
            found[name, kind, value.replace(" ", "").casefold()].add(ada.person.pk)
        assert [len(people) for people in found.values()] == [1] * len(found)
        assert len(set().union(*found.values())) == len(found)
        assert not Handle._base_manager.filter(party__isnull=False).exists()
        transaction.set_rollback(True)


def test_the_number_is_held_with_the_address_before_any_claim_is_read(source: Source) -> None:
    """One read by key takes the rows of both (a row lock where the database has one), then the claims are read."""

    first = source.given(email=ADDRESS, phone=NUMBER, display_name="Ada Example")
    record = source.record()

    with CaptureQueriesContext(connection) as queries:
        source.given(email=ADDRESS, phone=NUMBER, evidence=record)

    statements = [query["sql"] for query in queries]
    handles = connection.ops.quote_name(Handle._meta.db_table)
    by_key = re.compile(rf'FROM {handles} WHERE {handles}\."id" IN \(([\d, ]+)\)')
    held, keys = next(
        (index, match.group(1)) for index, sql in enumerate(statements) if (match := by_key.search(sql)) is not None
    )
    stances = connection.ops.quote_name(_stance_model()._meta.db_table)
    claims = next(index for index, sql in enumerate(statements) if f"FROM {stances}" in sql)
    assert {int(key) for key in keys.split(",")} == {first.email.pk, first.phone.pk}
    assert held < claims
    if connection.features.has_select_for_update:
        assert " FOR " in statements[held]


def test_one_read_by_key_says_which_claims_are_the_sources_own_whoever_else_claims_the_address(
    owner: Any, source: Source,
) -> None:
    """What a call reads of the source's records is their keys, once: not per claimant, and never the records."""

    first = source.given(email=ADDRESS, phone=NUMBER, display_name="Ada Example")
    links = connection.ops.quote_name(RecordLink._meta.db_table)

    def reads() -> list[str]:
        record = source.record()
        with CaptureQueriesContext(connection) as queries:
            assert source.given(email=ADDRESS, phone=NUMBER, evidence=record).person.pk == first.person.pk
        return [query["sql"] for query in queries if f"FROM {links}" in query["sql"]]

    alone = reads()
    other = _source("another", owner)
    for index in range(6):
        _claimed_for(_person(owner, f"Claimant {index}"), first.email, other.record())
    for _ in range(25):
        source.record()
    crowded = reads()

    assert len(alone) == len(crowded) == 1
    assert re.match(rf'SELECT {re.escape(links)}\."id"( AS "pk")? FROM {re.escape(links)} ', crowded[0])


def test_more_records_behind_one_claim_than_one_read_holds_are_asked_for_in_batches(source: Source) -> None:
    """A claim gains a reference per record: past the database's parameter budget their keys are read in parts."""

    ada = source.given(email=ADDRESS, display_name="Ada Example")
    size = query_batch_size(source.own)
    elsewhere = [RecordLink(pk=1_000_000 + index) for index in range(size)]
    with system_context(reason="tests.parties.claimants"):
        Party.objects.assert_handle(
            ada.person, ada.email, asserted_by=Party.objects.rule_source("source_sender"),
            confidence=Party.CLAIM_CONFIDENCE, evidence=[*elsewhere, ada.record],
        )
    links = connection.ops.quote_name(RecordLink._meta.db_table)
    record = source.record()

    with CaptureQueriesContext(connection) as queries:
        found = source.given(email=ADDRESS, evidence=record)

    reads = [query["sql"] for query in queries if f"FROM {links}" in query["sql"]]
    assert found.person.pk == ada.person.pk
    assert [sql.count(",") < size for sql in reads] == [True, True]


def test_the_verb_proposes_no_pair_and_the_pairing_pass_proposes_the_claimant(
    owner: Any, source: Source, django_capture_on_commit_callbacks: Any,
) -> None:
    """A claim links its person to the address, so the scheduled pass pairs them with whoever else is linked."""

    holder = _person(owner, "A. Example")
    held = _holds(holder, ADDRESS, owner)

    with django_capture_on_commit_callbacks(execute=True):  # a proposal deferred to the commit would run here
        ada = source.given(email=ADDRESS, display_name="Ada Example")

    assert _pairs() == []
    assert _reconcile() == 1
    assert _pairs() == [({holder.pk, ada.person.pk}, (held,))]


def test_a_claimant_needs_a_named_reason(source: Source) -> None:
    with pytest.raises(ValueError, match="named reason"):
        Party.objects.claimant_as_system(email=ADDRESS, evidence=source.record(), own=source.own, reason="  ")

    assert (_people(), Handle._base_manager.count()) == (0, 0)


def test_evidence_of_another_kind_than_the_sources_own_records_is_refused(owner: Any, source: Source) -> None:
    """No record of the source's could ever stand behind that claim, so its person would never be found again."""

    with pytest.raises(TypeError, match="own kind"):
        source.given(email=ADDRESS, display_name="Ada Example", evidence=owner)

    assert (_people(), Handle._base_manager.count()) == (0, 0)


def test_a_reference_names_the_sources_record_whatever_the_labels_case_or_a_pointer(owner: Any, source: Source) -> None:
    """The verb asks which record a reference names (``EvidenceReference.named_record``), not how it was written."""

    ada, row = _person(owner, "Ada Example"), _row(owner, ADDRESS)
    written = behind(source.record())
    cited = EvidenceReference(model=written.model.lower(), id=written.id, pointer="/display_name")
    with system_context(reason="tests.parties.claimants"):
        subject: Any = Party._base_manager.get(pk=ada.pk)
        subject.statements.set(
            aspect="handle", handle=row, values={}, asserted_by=Party.objects.rule_source("source_sender"),
            confidence=Party.CLAIM_CONFIDENCE, evidence=[cited],
        )
    assert cited != written

    found = source.given(email=ADDRESS)

    assert (found.person.pk, found.email.pk) == (ada.pk, row.pk)


def test_standing_claims_narrow_by_handle_or_by_party_and_keep_each_claims_records(owner: Any, source: Source) -> None:
    """The read a caller's own rules compose: who still claims these handles, and which records said so."""

    ada = source.given(email=ADDRESS, phone=NUMBER, display_name="Ada Example")
    other = _source("another", owner).given(email=ADDRESS, display_name="A. Example")

    with system_context(reason="tests.parties.claimants"):
        standing = Party.objects.handle_statements().filter(handle=ada.email).standing()
        assert set(standing.values_list("subject_id", "handle_id")) == set(_standing(handle=ada.email))
    assert _standing(handle=ada.email) == {
        (ada.person.pk, ada.email.pk): frozenset({behind(ada.record)}),
        (other.person.pk, ada.email.pk): frozenset({behind(other.record)}),
    }
    assert _standing(subject_id=ada.person.pk) == {
        (ada.person.pk, ada.email.pk): frozenset({behind(ada.record)}),
        (ada.person.pk, ada.phone.pk): frozenset({behind(ada.record)}),
    }
    assert _standing(handle=ada.phone, subject_id=other.person.pk) == {}
