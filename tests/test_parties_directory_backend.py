"""A pull-only directory through the real driver and parties writers.

No vendor backend pulls yet, so a small backend here scripts its source in memory:
contacts keyed by a stable id, records it reports deleted (tombstones in a page),
and merges whose survivor names the ids it replaced. The driver opens, pages and
sweeps the streams onto real tables; nothing replaces the ingest owners or the
three-way classifier.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import system_context
from rebac.actors import to_subject_ref
from rebac.roles import grant as grant_role

from angee.base.models.identity import public_id_of
from angee.integrate.states import LinkStatus, StreamDirection, StreamKind
from angee.integrate.streams import StreamPage, advance_stream, reconcile_stream
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.messaging.testing.models import Directory, Handle, Organization, Party, Person, RelationshipKind
from angee.parties.backends import (
    CONTACTS,
    DirectoryBackend,
    ManualDirectoryBackend,
    ParsedAddressbook,
    ParsedContact,
)
from angee.provenance.evidence import EvidenceReference
from tests.conftest import current_stream, make_integration
from tests.stream_adapters import applied_history

pytestmark = pytest.mark.django_db(transaction=True)

_BOOK = "people"
_ADA = ParsedContact(
    display_name="Ada Lovelace",
    given_name="Ada",
    family_name="Lovelace",
    notes="Wrote the first program.",
    emails=(("ada@example.com", "home", True),),
)
_GRACE = ParsedContact(
    display_name="Grace Hopper",
    given_name="Grace",
    family_name="Hopper",
    emails=(("grace@example.com", "work", True),),
)


@dataclass
class Source:
    """What the scripted source serves: its contacts, the ids each merge replaced, and what changed since a page."""

    contacts: dict[str, ParsedContact] = field(default_factory=dict)
    replaced: dict[str, tuple[str, ...]] = field(default_factory=dict)
    deleted: set[str] = field(default_factory=set)
    changed: set[str] = field(default_factory=set)

    def put(self, key: str, contact: ParsedContact, *, replaces: tuple[str, ...] = ()) -> None:
        self.contacts[key] = contact
        self.replaced[key] = replaces
        self.changed.add(key)


class PullDirectoryBackend(DirectoryBackend):
    """A pull-only backend over :class:`Source`: one address book, a delta page per sync."""

    key = "pull_test"
    label = "Pull test"
    source: Source

    def discover(self) -> list[ParsedAddressbook]:
        return [ParsedAddressbook(key=_BOOK, name="People")]

    def extract_contacts(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """A baseline pages every contact; a delta pages what changed and what the source lists as deleted."""

        keys = sorted(self.source.changed if stream.cursor else self.source.contacts)
        payloads = {key: self._payload(key) for key in keys} | dict.fromkeys(sorted(self.source.deleted))
        self.source.changed.clear()
        self.source.deleted.clear()
        return StreamPage(self.record_changes(stream, payloads), {"delta": True})

    def read_contacts(self, stream: Any, keys: Sequence[str]) -> Mapping[str, Mapping[str, Any] | None]:
        return {key: self._payload(key) for key in keys}

    def contact_keys(self, stream: Any, *, after: str | None) -> Iterator[str]:
        return (key for key in sorted(self.source.contacts) if after is None or key > after)

    def _payload(self, key: str) -> dict[str, Any] | None:
        contact = self.source.contacts.get(key)
        return None if contact is None else self.observe(contact, replaces=self.source.replaced.get(key, ()))


@dataclass
class Pull:
    directory: Any
    backend: PullDirectoryBackend
    source: Source
    stream: Any

    def sync(self, *, page_bound: int = 100) -> Any:
        result = advance_stream(self.stream, self.backend, page_bound=page_bound)
        self.stream = result.stream
        return result

    def sweep(self) -> int:
        """Run one absence sweep to completion, one key per pulse; return the absences it observed."""

        SyncStream._base_manager.filter(pk=self.stream.pk).update(last_reconciled_at=None)
        absent = 0
        for _ in range(20):
            absent += reconcile_stream(self.stream, self.backend, page_bound=1)
            self.stream.refresh_from_db()
            if not self.stream.reconcile_state:
                return absent
        pytest.fail("The absence sweep did not finish")

    def link(self, key: str) -> Any:
        return RecordLink.objects.get(stream=self.stream, external_key=key)


class TwoWayDirectoryBackend(PullDirectoryBackend):
    """The scripted source, declared two-way: its contacts never adopt an existing person."""

    key = "two_way_test"
    direction = StreamDirection.BIDIRECTIONAL


def _connect(backend_class: type[PullDirectoryBackend]) -> Iterator[Pull]:
    with system_context(reason="test scripted directory"):
        directory = make_integration("pull-directory", model=Directory)
        RelationshipKind.objects.create(
            slug="employee", name="Employee", inverse_name="Employer", category="professional",
            other_party_kind="organization",
        )
        source = Source()
        source.put("ada", _ADA)
        source.put("grace", _GRACE)
        backend = backend_class(directory)
        backend.source = source
        (books,) = backend.streams()
        advance_stream(SyncStream.objects.current(directory, books), backend)
        _, contacts = backend.streams()
        assert (contacts.direction, contacts.mapping_version) == (backend_class.direction, 1)
        yield Pull(directory, backend, source, SyncStream.objects.current(directory, contacts))
        backend.close()


@pytest.fixture
def pull(db: None) -> Iterator[Pull]:
    del db
    yield from _connect(PullDirectoryBackend)


@pytest.fixture
def two_way(db: None) -> Iterator[Pull]:
    del db
    yield from _connect(TwoWayDirectoryBackend)


def _asserted(party_id: Any, link: Any) -> Any:
    """The statements ``link`` currently asserts on the party."""

    statements = Party.statements.filter(subject_id=party_id, link=link).current()
    return statements.filter(status=Party.statements.model.StatementStatus.ASSERTED)


def _failures(pull: Pull) -> list[Any]:
    return [failure for link in RecordLink.objects.filter(stream=pull.stream) for failure in link.failures()]


def test_a_second_sync_of_an_unchanged_source_applies_nothing(pull: Pull) -> None:
    """Re-reading every record, by page and by the sweep, applies none: the base is the source's own side."""

    assert pull.sync().count == 2
    ada = pull.link("ada")
    assert ada.status == LinkStatus.CURRENT
    assert ada.applied_base.mapped_payload == ada.source_payload["contact"]
    applications = applied_history().count()

    pull.source.changed.update(pull.source.contacts)
    assert pull.sync().count == 0
    assert pull.sweep() == 0
    assert applied_history().count() == applications
    assert not _failures(pull)


def test_a_persons_edit_survives_the_next_pull_and_holds_no_conflict(pull: Pull) -> None:
    """A person's own edit is a statement that outranks the source, never a local change of the source's."""

    pull.sync()
    ada = pull.link("ada")
    Party.objects.get(pk=ada.target_object_id).with_actor(pull.directory.owner).statements.set(
        aspect="identity", values={"display_name": "Ada (a friend)"}
    )

    pull.source.changed.add("ada")
    assert pull.sync().count == 0
    pull.source.put("ada", replace(_ADA, notes="Corresponded with Babbage."))
    result = pull.sync()

    assert (result.count, result.failure_ids) == (1, ())
    person = Person.objects.get(pk=ada.target_object_id)
    assert (person.display_name, person.notes) == ("Ada (a friend)", "Corresponded with Babbage.")
    ada.refresh_from_db()
    assert ada.status == LinkStatus.CURRENT
    assert ada.applied_base.mapped_payload["display_name"] == "Ada Lovelace"
    # Re-reading the source applies nothing although the person's own name differs from it.
    pull.source.changed.add("ada")
    assert pull.sync().count == 0
    assert not _failures(pull)


def test_a_source_edit_arrives_and_becomes_the_base(pull: Pull) -> None:
    pull.sync()
    pull.source.put("grace", replace(_GRACE, emails=(("grace@navy.example", "work", True),), notes="Admiral"))

    assert pull.sync().count == 1
    grace = pull.link("grace")
    person = Person.objects.get(pk=grace.target_object_id)
    assert person.notes == "Admiral"
    assert grace.applied_base.mapped_payload == grace.source_payload["contact"]
    assert grace.applied_base.mapped_payload["emails"] == [["grace@navy.example", "work", True]]


def test_a_tombstone_in_a_page_withdraws_the_statements_and_keeps_the_party(pull: Pull) -> None:
    pull.sync()
    grace = pull.link("grace")
    party_id = grace.target_object_id
    assert _asserted(party_id, grace).exists()

    del pull.source.contacts["grace"]
    pull.source.deleted.add("grace")
    assert pull.sync().count == 1
    grace.refresh_from_db()
    assert (grace.status, grace.applied_base.mapped_payload) == (LinkStatus.TOMBSTONE, None)
    assert grace.target_object_id == party_id and Party.objects.filter(pk=party_id).exists()
    assert not _asserted(party_id, grace).exists()

    # The record comes back: the withdrawn base makes it a remote change, onto the same party.
    pull.source.put("grace", _GRACE)
    assert pull.sync().count == 1
    grace.refresh_from_db()
    assert (grace.status, grace.target_object_id) == (LinkStatus.CURRENT, party_id)
    assert _asserted(party_id, grace).exists()


def test_a_record_the_sweep_confirms_absent_is_withdrawn_and_applies_again_when_it_returns(pull: Pull) -> None:
    pull.sync()
    assert pull.sweep() == 0
    grace = pull.link("grace")
    party_id = grace.target_object_id
    del pull.source.contacts["grace"]

    for status, asserted in ((LinkStatus.UNAVAILABLE, True), (LinkStatus.TOMBSTONE, False)):
        assert pull.sweep() == 1
        grace.refresh_from_db()
        assert (grace.status, _asserted(party_id, grace).exists()) == (status, asserted)
    assert grace.applied_base.mapped_payload is None
    assert Party.objects.filter(pk=party_id).exists()

    pull.source.put("grace", _GRACE)
    assert pull.sync().count == 1
    grace.refresh_from_db()
    assert (grace.status, grace.target_object_id) == (LinkStatus.CURRENT, party_id)
    assert _asserted(party_id, grace).exists()


_PHONE = "+15550100"


def _handle_claims(party_id: Any, value: str) -> list[tuple[Any, str]]:
    """Each current claim this directory's statements make of ``value`` on the party: its link and status."""

    claims = Party.statements.filter(subject_id=party_id, handle__value=value).current()
    return sorted(claims.values_list("link_id", "status"), key=lambda claim: (claim[0] is None, claim))


def _held_by(address: str) -> Any:
    """The party the address's one handle resolves to, ``None`` for nobody."""

    (handle,) = Handle.objects.filter(platform="email", normalized_value=address)
    return handle.party_id


def _claim_confidences(party_id: Any, address: str) -> list[float]:
    """The confidence of each current claim a directory asserts of the address on the party."""

    claims = Party.statements.filter(
        subject_id=party_id, handle__normalized_value=address, source_kind="directory", status="asserted"
    ).current()
    return list(claims.values_list("confidence", flat=True))


def test_a_merge_at_the_source_lands_on_the_replaced_records_person_and_withdraws_what_only_it_stated(
    pull: Pull,
) -> None:
    """The survivor restates what it keeps, through its own link; a fact only the merged-away record had is withdrawn.

    The replaced record is no record of the survivor's page: it leaves the source's
    key walk, so the absence sweep tombstones its link, and an identity read of it
    answers it gone. A page of one record stays one record (``page_bound=1``).
    """

    pull.source.put("ada", replace(_ADA, phones=((_PHONE, "cell", False),)))
    pull.sync()
    ada = pull.link("ada")
    party_id = ada.target_object_id
    people = Person.objects.count()
    assert _handle_claims(party_id, _PHONE) == [(ada.pk, "asserted")]

    del pull.source.contacts["ada"]
    pull.source.put("ada-2", replace(_ADA, notes="Merged at the source."), replaces=("ada", "never-linked"))
    records = pull.backend.record_changes(pull.stream, {"ada-2": pull.backend._payload("ada-2")})
    assert [(record.external_key, record.tombstone) for record in records] == [("ada-2", False)]
    assert records[0].source_payload["replaces"] == ["ada", "never-linked"]
    # An identity read of the merged-away key answers it gone.
    assert [(record.external_key, record.tombstone) for record in pull.backend.read_keys(pull.stream, ["ada"])] == [
        ("ada", True)
    ]

    result = pull.sync(page_bound=1)

    assert (result.count, result.failure_ids) == (1, ())
    survivor = pull.link("ada-2")
    ada.refresh_from_db()
    assert survivor.target_object_id == party_id
    assert Person.objects.count() == people
    assert Person.objects.get(pk=party_id).notes == "Merged at the source."
    # The email the survivor restates is its own claim now; the phone only the merged-away record carried
    # is withdrawn, not handed to the survivor's link.
    assert _handle_claims(party_id, "ada@example.com") == [(survivor.pk, "asserted")]
    assert _handle_claims(party_id, _PHONE) == [(ada.pk, "withdrawn")]
    assert ada.status == LinkStatus.CURRENT
    assert not _asserted(party_id, ada).exists()
    assert not RecordLink.objects.filter(stream=pull.stream, external_key="never-linked").exists()

    # The sweep finds the merged-away key gone and tombstones its link; nothing moves back.
    pull.sweep()
    pull.sweep()
    ada.refresh_from_db()
    assert ada.status == LinkStatus.TOMBSTONE
    assert [status for _link, status in _handle_claims(party_id, _PHONE)] == ["withdrawn"]
    assert _handle_claims(party_id, "ada@example.com") == [(survivor.pk, "asserted")]

    # Re-reading the survivor after its replaced link's tombstone applies nothing and emits nothing else.
    applications = applied_history().count()
    pull.source.changed.add("ada-2")
    assert [record.external_key for record in pull.backend.record_changes(
        pull.stream, {"ada-2": pull.backend._payload("ada-2")}
    )] == ["ada-2"]
    assert pull.sync().count == 0
    assert applied_history().count() == applications
    assert not _failures(pull)


def _proposal_rows() -> list[Any]:
    """The ``directory_match`` rule's current same-as statements, oldest first."""

    rule = str(to_subject_ref(Party.objects.rule_source("directory_match")))
    return list(
        Party.same_as.model.statements.filter(asserted_by=rule).current().select_related("subject").order_by("pk")
    )


def _proposals() -> list[tuple[frozenset[str], str, list[Any]]]:
    """Each pair the directory proposed: its two people (as link target ids), its status and its evidence."""

    return [
        (frozenset({str(row.subject.left_id), str(row.subject.right_id)}), row.status, row.evidence)
        for row in _proposal_rows()
    ]


def _refs(*records: Any) -> list[Any]:
    return [EvidenceReference.of(record).model_dump(mode="json") for record in records]


def _merge_grace_and_ada(pull: Pull) -> tuple[str, str]:
    """Sync both, then merge their records at the source into one replacing Grace's first, then Ada's."""

    pull.sync()
    ada, grace = (pull.link(key).target_object_id for key in ("ada", "grace"))
    for key in ("ada", "grace"):
        del pull.source.contacts[key]
    pull.source.put("merged", replace(_GRACE, notes="Merged at the source."), replaces=("grace", "ada"))
    assert pull.sync().count == 1
    return ada, grace


def test_the_survivor_of_a_merge_takes_the_first_replaced_records_person_and_proposes_the_other(pull: Pull) -> None:
    """Replaced records linked to different people: the source's first names the survivor's person, not key order.

    The other person is proposed as the survivor's same person, citing the
    survivor's link and the replaced record that named them.
    """

    ada, grace = _merge_grace_and_ada(pull)

    assert pull.link("merged").target_object_id == grace
    assert Person.objects.filter(pk__in=[ada, grace]).count() == 2
    assert _proposals() == [(frozenset({ada, grace}), "asserted", _refs(pull.link("merged"), pull.link("ada")))]
    reviewed = Party.objects.with_actor(pull.directory.owner).same_as_pairs()
    assert [(frozenset({str(pair.left.pk), str(pair.right.pk)}), pair.normalized_value) for pair in reviewed] == [
        (frozenset({ada, grace}), "")
    ]


def test_a_proposed_pair_is_stated_once_and_a_dismissal_of_it_stands(pull: Pull) -> None:
    """A later record restating the merge proposes nothing new, nor does one after a person keeps the two apart.

    The second merge passes Ada's person over again on other evidence (its own
    link), which would restate the pair were the dismissal not honoured.
    """

    ada, grace = _merge_grace_and_ada(pull)
    (proposal,) = _proposal_rows()

    pull.source.put("merged", replace(_GRACE, notes="Restated at the source."), replaces=("grace", "ada"))
    assert pull.sync().count == 1
    assert [(row.pk, row.updated_at) for row in _proposal_rows()] == [(proposal.pk, proposal.updated_at)]

    owner = pull.directory.owner
    left, right = (Party.objects.get(pk=pk) for pk in (ada, grace))
    assert Party.objects.apply_same_as_pair(
        left_id=public_id_of(left), right_id=public_id_of(right), survivor="left", action="keep_separate", actor=owner
    ) == "dismissed"
    del pull.source.contacts["merged"]
    pull.source.put("merged-again", replace(_GRACE, notes="Merged once more."), replaces=("merged", "ada"))
    assert pull.sync().count == 1

    assert pull.link("merged-again").target_object_id == grace
    assert Party.same_as.kept_separate(left, right)
    assert [(row.pk, row.updated_at, row.status) for row in _proposal_rows()] == [
        (proposal.pk, proposal.updated_at, "asserted")
    ]
    assert Party.objects.with_actor(owner).same_as_pairs() == []


def test_a_trashed_person_is_nobodys_target_and_the_record_mints_another(pull: Pull) -> None:
    """A link bound to a trashed person lands its next record on a new person; a live bound link keeps its own."""

    pull.sync()
    link = pull.link("ada")
    trashed = Person.objects.get(pk=link.target_object_id)
    with pytest.raises(ValidationError, match="never rebinds"):
        Party.objects.ingest_contact(
            _ADA, stream=pull.stream, link=link, created_by_id=pull.directory.owner_id, target=None
        )
    trashed.trash(reason="Removed by its holder")
    pull.source.put("ada", replace(_ADA, notes="Still at the source."))

    result = pull.sync()

    assert (result.count, result.failure_ids) == (1, ())
    link.refresh_from_db()
    assert link.target_object_id not in (None, str(trashed.pk))
    assert Person.objects.get(pk=link.target_object_id).notes == "Still at the source."


def test_a_persons_edit_holds_its_whole_aspect_against_a_later_source_edit(pull: Pull) -> None:
    """Resolution is per aspect: a person's name holds every name field, so a source's family name waits behind it.

    The source's statement stays asserted, inspectable and ready to apply once the
    person's own is withdrawn; a source edit of another aspect lands beside it.
    """

    pull.sync()
    link = pull.link("ada")
    person = Person.objects.get(pk=link.target_object_id)
    fields = Person.statements.model.statement_aspects["name"].fields
    person.with_actor(pull.directory.owner).statements.set(
        aspect="name", values={**{name: getattr(person, name) for name in fields}, "given_name": "Augusta"}
    )
    pull.source.put("ada", replace(_ADA, family_name="King", notes="Countess of Lovelace."))

    assert pull.sync().count == 1

    person.refresh_from_db()
    assert (person.given_name, person.family_name, person.notes) == ("Augusta", "Lovelace", "Countess of Lovelace.")
    stated = Person.statements.filter(subject=person, aspect="name", link=link).current().get()
    assert (stated.status, stated.stated_value("family_name")) == ("asserted", "King")


def test_contact_ingest_never_rebinds_a_link(pull: Pull) -> None:
    """A passed target must be a Person and the link's own: binding lends the party to the directory's readers."""

    pull.sync()
    ada, grace = (Party.objects.contact_person(pull.link(key)) for key in ("ada", "grace"))
    company = Organization.objects.create(display_name="Example Ltd")
    for target, message in ((grace, "never rebinds"), (company, "kind Person")):
        with pytest.raises(ValidationError, match=message):
            Party.objects.ingest_contact(
                _ADA, stream=pull.stream, link=pull.link("ada"), created_by_id=pull.directory.owner_id, target=target
            )
    assert Party.objects.contact_person(pull.link("ada")).pk == ada.pk
    assert Party.objects.ingest_contact(
        _ADA, stream=pull.stream, link=pull.link("ada"), created_by_id=pull.directory.owner_id, target=ada
    ).pk == ada.pk


def _existing(creator: Any, *, email: str = "ada@example.com", **fields: Any) -> tuple[Any, Any]:
    """A person ``creator`` owns, holding ``email`` by a rule at full confidence, as another importer mints one."""

    person = Person.objects.create(display_name="Ada (existing)", created_by=creator, **fields)
    handle = Handle.objects.upsert(platform="email", value=email, created_by_id=creator.pk)
    rule = Party.objects.rule_source("source_sender")
    Party.objects.assert_handle(person, handle, asserted_by=rule, confidence=1.0)
    handle.refresh_from_db()
    assert handle.party_id == person.pk
    return person, handle


def _user(username: str) -> Any:
    return get_user_model().objects.create_user(username=username)


def _identity_evidence(party_id: Any) -> list[Any]:
    """The evidence of the directory's current identity statement on the party."""

    stated = Party.statements.filter(subject_id=party_id, aspect="identity", source_kind="directory").current()
    return stated.get().evidence


def test_a_pull_only_contact_adopts_the_person_its_email_names_when_the_owner_owns_them(pull: Pull) -> None:
    """The record attaches to the existing person, keeps one handle row, and cites the handle as its evidence.

    The person was minted by another source's rule at full confidence; the
    directory's own handle claim lands on the same row. The handle is the
    evidence of the identity statement the directory makes, provenance's own
    channel, so a later source edit restates it.
    """

    person, handle = _existing(pull.directory.owner)
    people = Person.objects.count()

    assert pull.sync().count == 2

    ada = pull.link("ada")
    assert ada.target_object_id == str(person.pk)
    assert Person.objects.count() == people + 1  # Grace alone is new.
    assert Handle.objects.filter(platform="email", normalized_value="ada@example.com").count() == 1
    assert _handle_claims(person.pk, "ada@example.com") == [(ada.pk, "asserted"), (None, "asserted")]
    assert _identity_evidence(person.pk) == _refs(handle)
    assert "adopted_through" not in ada.metadata
    # A later source edit lands on the same person through the link and keeps citing the handle.
    pull.source.put("ada", replace(_ADA, notes="Edited at the source."))
    assert pull.sync().count == 1
    assert _identity_evidence(person.pk) == _refs(handle)
    assert Person.objects.get(pk=person.pk).notes == "Edited at the source."


def test_an_adoption_through_another_spelling_lands_on_the_handle_that_found_the_person(pull: Pull) -> None:
    """The contact spells the address differently: its claim lands on the existing row, never a second one."""

    person, handle = _existing(pull.directory.owner, email="Ada@Example.COM")

    pull.sync()

    ada = pull.link("ada")
    assert ada.target_object_id == str(person.pk)
    spellings = Handle.objects.filter(platform="email", normalized_value="ada@example.com")
    assert list(spellings.values_list("pk", flat=True)) == [handle.pk]
    assert _handle_claims(person.pk, "Ada@Example.COM") == [(ada.pk, "asserted"), (None, "asserted")]
    pull.source.put("ada", replace(_ADA, notes="Edited at the source."))
    assert pull.sync().count == 1
    assert list(spellings.values_list("pk", flat=True)) == [handle.pk]
    assert _handle_claims(person.pk, "Ada@Example.COM") == [(ada.pk, "asserted"), (None, "asserted")]


def test_a_page_locks_every_spelling_of_its_contacts_addresses_and_the_people_they_name_first(
    pull: Pull, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before any record decides, the page locks the candidate's handle by normalized value, and the candidate.

    Two address books of one directory syncing at once then serialize on the
    candidate, and each record's admission is checked again under the lock.
    """

    person, handle = _existing(pull.directory.owner, email="Ada@Example.COM")
    page = pull.backend.extract_contacts(pull.stream, 100)
    locked: list[tuple[set[Any], set[Any]]] = []
    lock = Party.objects.lock_identity_rows

    def spy(**kwargs: Any) -> Any:
        locked.append((set(kwargs["party_ids"]), set(kwargs["handle_ids"])))
        return lock(**kwargs)

    monkeypatch.setattr(Party.objects, "lock_identity_rows", spy)
    with transaction.atomic():
        pull.backend.prepare_page(pull.stream, page)

    assert locked and person.pk in locked[0][0] and handle.pk in locked[0][1]


def test_contact_ingest_binds_an_existing_person_new_to_its_link_only_as_an_adoption_would(pull: Pull) -> None:
    """The binding owner admits the target, so neither a vendor's ``apply_record`` nor a caller can skip the limits."""

    refused, _handle = _existing(_user("someone-else"), email="refused@example.com")
    adoptable, _handle = _existing(pull.directory.owner, email="adoptable@example.com")
    link = RecordLink.objects.observe(pull.stream, "downstream")
    owner_id = pull.directory.owner_id

    with pytest.raises(ValidationError, match="may not adopt"):
        Party.objects.ingest_contact(_ADA, stream=pull.stream, link=link, created_by_id=owner_id, target=refused)
    link.refresh_from_db()
    assert link.target_object_id is None
    landed = Party.objects.ingest_contact(_ADA, stream=pull.stream, link=link, created_by_id=owner_id, target=adoptable)
    assert landed.pk == adoptable.pk


def test_an_address_an_account_signs_in_with_adopts_nobody(pull: Pull) -> None:
    """Its holder is no account's own person and the owner owns them, yet the address is an account's: no adoption."""

    get_user_model().objects.create_user(username="ada-signin", email="ada@example.com")
    person, handle = _existing(pull.directory.owner)

    pull.sync()

    ada = pull.link("ada")
    assert ada.target_object_id not in (None, str(person.pk))
    assert not Party.statements.filter(subject_id=person.pk, asserted_by=str(ada.asserted_by)).exists()


def test_an_adopted_person_keeps_their_own_edits_and_private_fields(pull: Pull) -> None:
    owner = pull.directory.owner
    person, _handle = _existing(owner, first_met_note="Met at a lecture.")
    as_person = Party.objects.get(pk=person.pk).with_actor(owner)
    as_person.statements.set(aspect="identity", values={"display_name": "Ada (a friend)"})
    as_person.statements.set(aspect="contact", values={"notes": "Owes me a letter.", "avatar": None})

    pull.sync()

    person.refresh_from_db()
    assert pull.link("ada").target_object_id == str(person.pk)
    assert (person.display_name, person.notes, person.first_met_note) == (
        "Ada (a friend)", "Owes me a letter.", "Met at a lecture."
    )
    # What the person never stated arrives from the source.
    assert (person.given_name, person.family_name) == ("Ada", "Lovelace")


def _refused_owner(pull: Pull) -> Any:
    """The owner cannot write the person: another user owns them."""

    return _existing(_user("someone-else"))[0]


def _refused_account_holder(pull: Pull) -> Any:
    """The person holding the address is an account's own person, though the owner writes them."""

    return _existing(pull.directory.owner, user=_user("ada-account"))[0]


def _refused_account_basis(pull: Pull) -> Any:
    """The address is only an account's: its person holds no handle of it, though the owner writes them."""

    account = get_user_model().objects.create_user(username="ada-signin", email="ada@example.com")
    return Person.objects.create(display_name="Ada (account)", created_by=pull.directory.owner, user=account)


def _refused_linked(pull: Pull) -> Any:
    """Another live link of this directory already names the person."""

    person = _existing(pull.directory.owner)[0]
    RecordLink.objects.observe(pull.stream, "earlier", target=Party._base_manager.get(pk=person.pk))
    return person


def _refused_admin_owner(pull: Pull) -> Any:
    """The owner is a platform admin, whose admin arm writes every person, but neither owns nor edits this one."""

    grant_role(actor=pull.directory.owner, role="angee/role:admin")
    person = _existing(_user("someone-else"))[0]
    assert Person.objects.get(pk=person.pk).with_actor(pull.directory.owner).has_access("write")
    return person


def _refused_write_back(pull: Pull) -> Any:
    """A live link of a stream that writes back names the person: adopting would publish this source's facts there."""

    person = _existing(pull.directory.owner)[0]
    other = make_integration("two-way-directory", model=Directory)
    stream = current_stream(
        other, CONTACTS, "book", kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.BIDIRECTIONAL
    )
    assert stream.writes_back
    RecordLink.objects.observe(stream, "card", target=Party._base_manager.get(pk=person.pk))
    return person


@pytest.mark.parametrize(
    "refusal",
    [
        _refused_owner,
        _refused_account_holder,
        _refused_account_basis,
        _refused_linked,
        _refused_admin_owner,
        _refused_write_back,
    ],
)
def test_a_contact_mints_its_own_person_when_an_adoption_limit_refuses_the_one_its_email_names(
    pull: Pull, refusal: Any
) -> None:
    """Each limit alone refuses the adoption: the contact mints, and the existing person gains no statement of it.

    The address stays with whoever held it: the minted person only claims it, for
    review, below the confidence at which a handle statement applies. The two
    are proposed as one person, once, citing the contact's link and the handle
    the email found the person by (an account's address alone has none). A second
    record of the contact lands on its own person and proposes nothing new.
    """

    person = refusal(pull)
    found_by = Handle.objects.filter(platform="email", normalized_value="ada@example.com").first()
    holder = found_by.party_id if found_by is not None else None

    assert pull.sync().count == 2

    ada = pull.link("ada")
    assert ada.target_object_id not in (None, str(person.pk))
    assert not Party.statements.filter(subject_id=person.pk, asserted_by=str(ada.asserted_by)).exists()
    assert _held_by("ada@example.com") == holder
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [0.4]
    proposed = [(frozenset({ada.target_object_id, str(person.pk)}), "asserted", _refs(ada, *filter(None, [found_by])))]
    assert _proposals() == proposed
    pull.source.put("ada", replace(_ADA, notes="Edited at the source."))
    assert pull.sync().count == 1
    assert _held_by("ada@example.com") == holder
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [0.4]
    assert _proposals() == proposed


def test_merging_a_proposed_pair_withdraws_it_and_the_same_as_reconciler_leaves_it_standing(pull: Pull) -> None:
    """The minted and the existing person claim one handle row, which supports no pair of the ``same_as`` rule.

    That rule's reconciler withdraws its own pair of them and leaves the
    directory's, which the review lists by the address; merging the two through
    the review's verb withdraws the directory's proposal.
    """

    owner = pull.directory.owner
    person = _refused_linked(pull)
    pull.sync()
    minted = pull.link("ada").target_object_id
    reviewed = Party.objects.with_actor(owner).same_as_pairs()
    assert [(frozenset({str(pair.left.pk), str(pair.right.pk)}), pair.normalized_value) for pair in reviewed] == [
        (frozenset({minted, str(person.pk)}), "ada@example.com")
    ]
    same_as = Party.objects.rule_source("same_as")
    with system_context(reason="tests.directory_match.reconciler"):
        left, right = (Party._base_manager.get(pk=pk) for pk in (minted, person.pk))
        pair = Party.same_as.for_pair(left.with_actor(to_subject_ref(same_as)), right)
        pair.statements.set(aspect="same_as", values={}, asserted_by=same_as)
        Party.objects.reconcile_same_as()
    statements = Party.same_as.model.statements.filter(subject=pair).current()
    assert dict(statements.values_list("asserted_by", "status")) == {
        str(to_subject_ref(same_as)): "withdrawn",
        str(to_subject_ref(Party.objects.rule_source("directory_match"))): "asserted",
    }

    assert Party.objects.apply_same_as_pair(
        left_id=public_id_of(Party.objects.get(pk=person.pk)),
        right_id=public_id_of(Party.objects.get(pk=minted)),
        survivor="left",
        action="merge",
        actor=owner,
    ) == "merged"

    assert [status for _pair, status, _evidence in _proposals()] == ["withdrawn"]


def test_adopting_a_person_once_refused_withdraws_the_proposal_its_refusal_made(pull: Pull) -> None:
    """The person is no account's any more and the minted one is trashed: the contact's next record adopts them."""

    person = _refused_account_holder(pull)
    pull.sync()
    minted = Person.objects.get(pk=pull.link("ada").target_object_id)
    assert [status for _pair, status, _evidence in _proposals()] == ["asserted"]

    Person._base_manager.filter(pk=person.pk).update(user=None)
    minted.trash(reason="Removed by its holder")
    pull.source.put("ada", replace(_ADA, notes="Still at the source."))
    assert pull.sync().count == 1

    assert pull.link("ada").target_object_id == str(person.pk)
    assert [(pair, status) for pair, status, _evidence in _proposals()] == [
        (frozenset({str(minted.pk), str(person.pk)}), "withdrawn")
    ]


def test_a_two_way_directory_never_adopts(two_way: Pull) -> None:
    """A two-way directory mints and states the address in full, as CardDAV always has (its card publishes it)."""

    person, _handle = _existing(two_way.directory.owner)

    assert two_way.sync().count == 2

    ada = two_way.link("ada")
    assert ada.target_object_id not in (None, str(person.pk))
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [1.0]


def test_two_contacts_that_would_adopt_one_person_adopt_it_once(pull: Pull) -> None:
    """The second contact of one directory finds the person already linked, so it mints its own, proposed as them.

    The collision leaves the address with the adopted person: the second contact
    only claims it.
    """

    person, handle = _existing(pull.directory.owner)
    pull.source.put("ada-copy", replace(_ADA, display_name="Ada L."))

    assert pull.sync().count == 3

    ada, copy = pull.link("ada"), pull.link("ada-copy")
    assert ada.target_object_id == str(person.pk)
    assert copy.target_object_id not in (None, str(person.pk))
    assert _held_by("ada@example.com") == person.pk
    assert _claim_confidences(copy.target_object_id, "ada@example.com") == [0.4]
    assert _proposals() == [(frozenset({copy.target_object_id, str(person.pk)}), "asserted", _refs(copy, handle))]


def test_two_contacts_with_one_address_nobody_held_leave_it_with_the_first(pull: Pull) -> None:
    """The first contact mints its person, who then holds the address; the second mints its own, who only claims it."""

    pull.source.put("ada-copy", replace(_ADA, display_name="Ada L."))

    assert pull.sync().count == 3

    ada, copy = pull.link("ada"), pull.link("ada-copy")
    assert ada.target_object_id != copy.target_object_id
    handle = Handle.objects.get(platform="email", normalized_value="ada@example.com")
    assert _held_by("ada@example.com") == int(ada.target_object_id)
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [1.0]
    assert _claim_confidences(copy.target_object_id, "ada@example.com") == [0.4]
    assert _proposals() == [
        (frozenset({ada.target_object_id, copy.target_object_id}), "asserted", _refs(copy, handle))
    ]
    pull.source.put("ada-copy", replace(_ADA, display_name="Ada L.", notes="Edited at the source."))
    assert pull.sync().count == 1
    assert _held_by("ada@example.com") == int(ada.target_object_id)
    assert _claim_confidences(copy.target_object_id, "ada@example.com") == [0.4]


def test_a_contact_refused_the_one_person_claimed_for_its_address_keeps_only_claiming_it(pull: Pull) -> None:
    """Nobody holds the address; a refused claimant leaves the contact's own claim a claim, record after record.

    The contact's claim makes two claimants, which the lookup answers with
    nobody; that never turns the claim into the contact's own address.
    """

    claimant = Person.objects.create(display_name="Ada (claimed)", created_by=_user("someone-else"))
    handle = Handle.objects.upsert(platform="email", value="ada@example.com", created_by_id=claimant.created_by_id)
    Party.objects.assert_handle(claimant, handle, asserted_by=Party.objects.rule_source("display_name"), confidence=0.4)

    pull.sync()

    ada = pull.link("ada")
    assert ada.target_object_id not in (None, str(claimant.pk))
    assert _held_by("ada@example.com") is None
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [0.4]
    assert _proposals() == [(frozenset({ada.target_object_id, str(claimant.pk)}), "asserted", _refs(ada, handle))]
    pull.source.put("ada", replace(_ADA, notes="Edited at the source."))
    assert pull.sync().count == 1
    assert _held_by("ada@example.com") is None
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [0.4]


def test_a_directory_forwards_its_connection_test_and_credential_rule(
    pull: Pull, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Directory`` asks its backend: the base's connection test proves only the credential, and its rule admits any.

    A backend's refusal is the one rule: the directory answers it to the connect
    picker (``Integration.credential_blocker``) and attaching raises it.
    """

    credential = pull.directory.credential
    assert pull.directory.test_connection() == "Credential is usable."
    assert pull.directory.credential_blocker(credential) is None
    asked: list[Any] = []

    def refuse(backend: DirectoryBackend, credential: Any) -> str:
        del backend
        asked.append(credential)
        return "This directory cannot read with that credential."

    monkeypatch.setattr(ManualDirectoryBackend, "credential_blocker", refuse)
    assert pull.directory.credential_blocker(credential) == "This directory cannot read with that credential."
    with pytest.raises(ValidationError) as refused:
        pull.directory.attach_connection(credential)
    assert refused.value.message_dict == {"credential": ["This directory cannot read with that credential."]}
    assert asked == [credential, credential]
