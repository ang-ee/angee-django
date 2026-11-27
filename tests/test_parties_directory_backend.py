"""A pull-only directory through the real driver and parties writers.

No vendor backend pulls yet, so a small backend here scripts its source in memory:
contacts keyed by a stable id, records it reports deleted (tombstones in a page),
and merges whose survivor names the ids it replaced. The driver opens, pages and
sweeps the streams onto real tables; nothing replaces the ingest owners or the
three-way classifier.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    system_context,
    to_object_ref,
    write_relationships,
)
from rebac.actors import to_subject_ref
from rebac.roles import grant as grant_role

from angee import parties as parties_package
from angee.base.models.identity import public_id_of
from angee.integrate.states import LinkStatus, StreamDirection, StreamKind
from angee.integrate.streams import StreamPage, advance_stream, open_stream, reconcile_stream
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.messaging.testing.models import Directory, Handle, Organization, Party, Person, RelationshipKind
from angee.parties.backends import (
    CONTACTS,
    DirectoryBackend,
    ManualDirectoryBackend,
    ParsedAddressbook,
    ParsedContact,
)
from angee.provenance.context import changeset
from angee.provenance.evidence import EvidenceReference
from tests.conftest import File, current_stream, make_integration
from tests.iam_models import Group
from tests.lock_waits import submit, wait_for_lock
from tests.stream_adapters import applied_history
from tests.test_storage import PNG_BYTES, drive, image_drive  # noqa: F401 -- the image drive avatars land on

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
    """What the scripted source serves: its contacts, the ids each merge replaced, and what changed since a page.

    ``fields`` holds each contact's other source fields; the source answers only
    those it is asked for, and ``requested`` records every request's field names.
    """

    contacts: dict[str, ParsedContact] = field(default_factory=dict)
    replaced: dict[str, tuple[str, ...]] = field(default_factory=dict)
    deleted: set[str] = field(default_factory=set)
    changed: set[str] = field(default_factory=set)
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    requested: list[tuple[str, ...]] = field(default_factory=list)

    def put(
        self, key: str, contact: ParsedContact, *, replaces: tuple[str, ...] = (), fields: dict[str, Any] | None = None
    ) -> None:
        self.contacts[key] = contact
        self.replaced[key] = replaces
        if fields is not None:
            self.fields[key] = fields
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
        """Ask the source for the contact and the fields the mappings name, as a vendor's request would."""

        contact = self.source.contacts.get(key)
        if contact is None:
            return None
        asked = self.mapped_fields()
        self.source.requested.append(asked)
        row = {name: value for name, value in self.source.fields.get(key, {}).items() if name in asked}
        return self.observe(contact, replaces=self.source.replaced.get(key, ()), row=row)


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

    def reopen(self) -> Any:
        """Open the contacts stream against the backend's declaration now, as each sync cycle does."""

        self.stream = open_stream(self.directory, CONTACTS, _BOOK, self.backend)
        return self.stream

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


@pytest.fixture
def adoption(pull: Pull) -> Pull:
    """The scripted directory with its option to bind a contact to an existing person (adoption) turned on."""

    with system_context(reason="tests.directory.adoption"):
        pull.directory.config = {**pull.directory.config, "adopt_existing_people": True}
        pull.directory.save(update_fields=["config"])
    return pull


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
    """The current same-as statements a directory's contacts made, oldest first.

    An address pair is the same-person verb's (the ``same_as`` rule, citing the
    handle that links the other person); what no address links is the
    ``directory_match`` rule's (citing the contact's link first).
    """

    rules = [str(to_subject_ref(Party.objects.rule_source(key))) for key in ("same_as", "directory_match")]
    return list(
        Party.same_as.model.statements.filter(asserted_by__in=rules).current().select_related("subject").order_by("pk")
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


def test_a_record_rebinding_off_a_trashed_person_withdraws_what_its_link_stated_there(pull: Pull) -> None:
    """The link's statements on the person it leaves are withdrawn before it binds the new one."""

    pull.sync()
    link = pull.link("ada")
    trashed = Person.objects.get(pk=link.target_object_id)
    assert _asserted(trashed.pk, link).exists()
    trashed.trash(reason="Removed by its holder")
    pull.source.put("ada", replace(_ADA, notes="Still at the source."))

    assert pull.sync().count == 1

    link.refresh_from_db()
    assert link.target_object_id not in (None, str(trashed.pk))
    assert not _asserted(trashed.pk, link).exists()
    assert not Person.statements.filter(subject_id=trashed.pk, link=link, status="asserted").current().exists()
    assert _asserted(int(link.target_object_id), link).exists()
    # The trashed person held the address through this link alone, now withdrawn: the new person holds it.
    assert _claim_confidences(link.target_object_id, "ada@example.com") == [1.0]


def test_a_record_minting_again_after_its_person_is_trashed_withdraws_the_pair_the_old_one_had(pull: Pull) -> None:
    """The contact never lands on the existing person: the new mint is proposed as them and the trashed one is not."""

    person = _refused_account_holder(pull)
    pull.sync()
    first = Person.objects.get(pk=pull.link("ada").target_object_id)
    first.trash(reason="Removed by its holder")
    pull.source.put("ada", replace(_ADA, notes="Still at the source."))

    assert pull.sync().count == 1

    second = pull.link("ada").target_object_id
    assert second not in (str(first.pk), str(person.pk))
    # A trashed person is linked to nothing, so the pairing job withdraws the pair the old mint had.
    Party.objects.reconcile_same_as()
    assert sorted((sorted(pair), status) for pair, status, _evidence in _proposals()) == sorted(
        [(sorted({str(first.pk), str(person.pk)}), "withdrawn"), (sorted({second, str(person.pk)}), "asserted")]
    )


@pytest.mark.usefixtures("adoption")
def test_a_contact_adopts_the_one_person_a_claim_proposes_its_address_for(pull: Pull) -> None:
    """Nobody holds the address; the one claimant, whom the owner owns, is adopted and then holds it."""

    claimant = Person.objects.create(display_name="Ada (claimed)", created_by=pull.directory.owner)
    handle = Handle.objects.upsert(platform="email", value="ada@example.com", created_by_id=pull.directory.owner_id)
    Party.objects.assert_handle(claimant, handle, asserted_by=Party.objects.rule_source("display_name"), confidence=0.4)
    assert _held_by("ada@example.com") is None

    pull.sync()

    assert pull.link("ada").target_object_id == str(claimant.pk)
    assert _held_by("ada@example.com") == claimant.pk
    assert _identity_evidence(claimant.pk) == _refs(handle)
    assert _proposals() == []


@pytest.mark.usefixtures("adoption")
def test_a_contact_adopts_a_later_candidate_when_the_first_is_refused_and_only_claims_the_firsts_address(
    pull: Pull,
) -> None:
    """Candidates are tried in the contact's preference order; the refused one keeps their address and is proposed."""

    refused, refused_handle = _existing(_user("someone-else"), email="ada@example.com")
    adopted, adopted_handle = _existing(pull.directory.owner, email="ada@work.example")
    emails = (("ada@example.com", "home", True), ("ada@work.example", "work", False))
    pull.source.put("ada", replace(_ADA, emails=emails))

    pull.sync()

    ada = pull.link("ada")
    assert ada.target_object_id == str(adopted.pk)
    assert (_held_by("ada@example.com"), _held_by("ada@work.example")) == (refused.pk, adopted.pk)
    assert _claim_confidences(adopted.pk, "ada@example.com") == [0.4]
    assert _identity_evidence(adopted.pk) == _refs(adopted_handle)
    assert _proposals() == [(frozenset({str(adopted.pk), str(refused.pk)}), "asserted", _refs(refused_handle))]


def test_a_merge_whose_replaced_record_is_bound_to_an_organization_quarantines_the_record(pull: Pull) -> None:
    """A contacts link naming an organization is no person's: the survivor's record is refused, and states nothing."""

    company = Organization.objects.create(display_name="Example Ltd", created_by=pull.directory.owner)
    RecordLink.objects.observe(pull.stream, "acme", target=Party._base_manager.get(pk=company.pk))
    pull.source.put("merged", replace(_ADA, emails=(("merged@example.com", "work", True),)), replaces=("acme",))

    result = pull.sync()

    assert len(result.failure_ids) == 1
    assert pull.link("merged").target_object_id is None
    assert not Party.statements.filter(subject_id=company.pk, asserted_by=str(pull.link("merged").asserted_by))


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


def _phone_holder(creator: Any, number: str = "+44 20 7946 0000") -> tuple[Any, Any]:
    """A person ``creator`` owns, holding the phone ``number`` at full confidence, as another importer mints one."""

    person = Person.objects.create(display_name="Ada (existing)", created_by=creator)
    handle = Handle.objects.upsert(platform="phone", value=number, label="office", created_by_id=creator.pk)
    Party.objects.assert_handle(person, handle, asserted_by=Party.objects.rule_source("source_sender"), confidence=1.0)
    handle.refresh_from_db()
    assert handle.party_id == person.pk
    return person, handle


def test_a_contact_only_claims_a_phone_number_another_person_holds_and_proposes_them(pull: Pull) -> None:
    """A number names one account as an address does: the contact claims it for review, its holder keeps it.

    The pair is the same-person verb's, citing the holder's handle, once the page commits.
    """

    holder, held = _phone_holder(_user("someone-else"))
    pull.source.put("ada", replace(_ADA, phones=((held.value, "mobile", True),)))

    pull.sync()

    minted = pull.link("ada").target_object_id
    assert minted not in (None, str(holder.pk))
    held.refresh_from_db()
    assert (held.party_id, held.label) == (holder.pk, "office")
    assert _claim_confidences(minted, held.normalized_value) == [Party.CLAIM_CONFIDENCE]
    assert (frozenset({minted, str(holder.pk)}), "asserted", _refs(held)) in _proposals()


def test_a_contact_holds_a_phone_number_nobody_is_linked_to(pull: Pull) -> None:
    """Nobody is linked to the number: the contact states it in full and its person holds it, as before."""

    pull.source.put("ada", replace(_ADA, phones=(("+44 20 7946 0001", "mobile", True),)))

    pull.sync()

    minted = pull.link("ada").target_object_id
    (handle,) = Handle.objects.filter(platform="phone", normalized_value__endswith="2079460001")
    assert handle.party_id == int(minted)
    assert _claim_confidences(minted, handle.normalized_value) == [1.0]
    assert _proposals() == []


def _identity_evidence(party_id: Any) -> list[Any]:
    """The evidence of the directory's current identity statement on the party."""

    stated = Party.statements.filter(subject_id=party_id, aspect="identity", source_kind="directory").current()
    return stated.get().evidence


def test_by_default_a_contact_whose_address_the_owners_person_holds_lands_on_a_new_person(
    pull: Pull, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each source creates its own people, and the merge joins them: adoption is off unless the directory turns it on.

    The directory's owner owns the existing person, the case adoption binds when it
    is on. The contact mints its own person, which only claims the address, and the
    two are proposed as one person, citing the contact's link and the handle.
    Adoption's admission is never reached, and no caller binds the existing person
    to a contact of the directory either.
    """

    def unreachable(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Adoption's admission was reached while the directory does not adopt.")

    monkeypatch.setattr(Party.objects, "adoption_blocker", unreachable)
    person, handle = _existing(pull.directory.owner)

    assert pull.sync().count == 2

    ada = pull.link("ada")
    assert ada.target_object_id not in (None, str(person.pk))
    assert not Party.statements.filter(subject_id=person.pk, asserted_by=str(ada.asserted_by)).exists()
    assert _held_by("ada@example.com") == person.pk
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [Party.CLAIM_CONFIDENCE]
    assert _proposals() == [(frozenset({ada.target_object_id, str(person.pk)}), "asserted", _refs(handle))]
    link = RecordLink.objects.observe(pull.stream, "downstream")
    with pytest.raises(ValidationError, match="binds no existing person"):
        Party.objects.ingest_contact(
            _ADA, stream=pull.stream, link=link, created_by_id=pull.directory.owner_id, target=person
        )
    link.refresh_from_db()
    assert link.target_object_id is None


def test_by_default_a_contact_only_claims_each_address_someone_else_is_linked_to_and_proposes_them(pull: Pull) -> None:
    """Whoever holds an address, claims it or signs in with it keeps it; each is proposed as the contact's person."""

    holder, held = _existing(_user("someone-else"), email="ada@work.example")
    claimant = Person.objects.create(display_name="Ada (claimed)", created_by=pull.directory.owner)
    claimed = Handle.objects.upsert(platform="email", value="ada@home.example", created_by_id=pull.directory.owner_id)
    rule = Party.objects.rule_source("display_name")
    Party.objects.assert_handle(claimant, claimed, asserted_by=rule, confidence=Party.CLAIM_CONFIDENCE)
    account = get_user_model().objects.create_user(username="ada-signin", email="ada@example.com")
    signer = Person.objects.create(display_name="Ada (account)", created_by=pull.directory.owner, user=account)
    addresses = ("ada@example.com", "ada@work.example", "ada@home.example")
    pull.source.put("ada", replace(_ADA, emails=tuple((address, "home", False) for address in addresses)))

    pull.sync()

    ada = pull.link("ada")
    minted = ada.target_object_id
    assert minted not in (None, str(holder.pk), str(claimant.pk), str(signer.pk))
    assert [_claim_confidences(minted, address) for address in addresses] == [[Party.CLAIM_CONFIDENCE]] * 3
    # The holder is the same-person verb's pair (the handle links them); the account, whose sign-in address no
    # handle of theirs carries, is the directory's; a name suggester's claim links no one (same_person_links).
    assert {pair: evidence for pair, _status, evidence in _proposals()} == {
        frozenset({minted, str(holder.pk)}): _refs(held),
        frozenset({minted, str(signer.pk)}): _refs(ada),
    }


@pytest.mark.usefixtures("adoption")
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


@pytest.mark.usefixtures("adoption")
def test_a_directory_whose_owner_manages_contacts_adopts_a_person_they_neither_own_nor_edit(pull: Pull) -> None:
    """The owner's ``adopt`` on the person admits it (``adoption_blocker``), and the contact manager role lends it."""

    with system_context(reason="tests.directory.adoption.contact_manager"):
        grant_role(actor=pull.directory.owner, role="parties/role:contact_manager")
        person = _existing(_user("someone-else"))[0]
    assert person.owner_id != pull.directory.owner.pk

    pull.sync()

    assert pull.link("ada").target_object_id == str(person.pk)


@pytest.mark.usefixtures("adoption")
def test_a_platform_admin_owner_adopts_only_once_the_platform_admins_are_granted_a_contact_role(pull: Pull) -> None:
    """The documented condition: a role's members may be a userset, and granting the platform admins' set
    (``angee/role:admin#member``) onto the contact manager role makes every platform admin a member, so every
    directory, whose owner is a platform admin, adopts the people it names. Without that grant a platform-admin
    owner adopts nobody it neither owns nor edits (``_refused_admin_owner``)."""

    with system_context(reason="tests.directory.adoption.admin_set"):
        grant_role(actor=pull.directory.owner, role="angee/role:admin")
        person = _existing(_user("someone-else"))[0]
        grant_role(actor=SubjectRef.of("angee/role", "admin", "member"), role="parties/role:contact_manager")

    pull.sync()

    assert pull.link("ada").target_object_id == str(person.pk)


@pytest.mark.usefixtures("adoption")
def test_an_adoption_through_an_alias_lands_on_the_handle_that_found_the_person(pull: Pull) -> None:
    """The contact gives an alias of the address: its claim lands on the existing row, never a second one."""

    contact = replace(_ADA, emails=(("ada.lovelace@gmail.com", "home", True),))
    pull.source.put("ada", contact)
    person, handle = _existing(pull.directory.owner, email="adalovelace@gmail.com")

    pull.sync()

    ada = pull.link("ada")
    assert ada.target_object_id == str(person.pk)
    aliases = Handle.objects.filter(platform="email", normalized_value="adalovelace@gmail.com")
    assert list(aliases.values_list("pk", flat=True)) == [handle.pk]
    assert _handle_claims(person.pk, "adalovelace@gmail.com") == [(ada.pk, "asserted"), (None, "asserted")]
    pull.source.put("ada", replace(contact, notes="Edited at the source."))
    assert pull.sync().count == 1
    assert list(aliases.values_list("pk", flat=True)) == [handle.pk]
    assert _handle_claims(person.pk, "adalovelace@gmail.com") == [(ada.pk, "asserted"), (None, "asserted")]


def test_a_page_locks_every_spelling_of_its_contacts_addresses_and_the_people_they_name_first(
    pull: Pull, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before any record lands, the page locks every spelling of the address and the person it names.

    A record proposes that person (with adoption on, it may adopt them), so two
    address books of one directory syncing at once serialize on them, and each
    record reads them again under the lock.
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


@pytest.mark.usefixtures("adoption")
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


@pytest.mark.usefixtures("adoption")
def test_an_address_an_account_signs_in_with_adopts_nobody(pull: Pull) -> None:
    """Its holder is no account's own person and the owner owns them, yet the address is an account's: no adoption."""

    get_user_model().objects.create_user(username="ada-signin", email="ada@example.com")
    person, handle = _existing(pull.directory.owner)

    pull.sync()

    ada = pull.link("ada")
    assert ada.target_object_id not in (None, str(person.pk))
    assert not Party.statements.filter(subject_id=person.pk, asserted_by=str(ada.asserted_by)).exists()


@pytest.mark.usefixtures("adoption")
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


@pytest.mark.usefixtures("image_drive")
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Open for the architect's ruling: a source states whole aspects, and survivorship applies a winning "
        "empty value over a column no statement holds; no provenance option keeps it for an adopted person alone."
    ),
)
@pytest.mark.usefixtures("adoption")
def test_an_adopted_person_keeps_the_values_no_statement_holds_when_the_contact_has_none(pull: Pull) -> None:
    """Notes, an avatar and a birthday written without a statement survive a contact that carries none of them."""

    avatar = File.objects.ingest_image(PNG_BYTES)
    person, _handle = _existing(
        pull.directory.owner, notes="Prefers letters.", avatar=avatar, birthday=date(1815, 12, 10)
    )
    pull.source.put("ada", replace(_ADA, notes=""))

    pull.sync()

    assert pull.link("ada").target_object_id == str(person.pk)
    person.refresh_from_db()
    assert (person.notes, person.avatar_id, person.birthday) == ("Prefers letters.", avatar.pk, date(1815, 12, 10))


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


def _refused_other_stream(pull: Pull) -> Any:
    """A live link of this directory's other address book already names the person."""

    person = _existing(pull.directory.owner)[0]
    other = current_stream(pull.directory, CONTACTS, "other-book", kind=StreamKind.RECORD_REPLICA)
    RecordLink.objects.observe(other, "ada-elsewhere", target=Party._base_manager.get(pk=person.pk))
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
        _refused_other_stream,
    ],
)
@pytest.mark.usefixtures("adoption")
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
    proposed = [(
        frozenset({ada.target_object_id, str(person.pk)}),
        "asserted",
        _refs(found_by) if found_by is not None else _refs(ada),
    )]
    assert _proposals() == proposed
    pull.source.put("ada", replace(_ADA, notes="Edited at the source."))
    assert pull.sync().count == 1
    assert _held_by("ada@example.com") == holder
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [0.4]
    assert _proposals() == proposed


@pytest.mark.usefixtures("adoption")
def test_merging_a_proposed_pair_withdraws_it_and_the_same_as_reconciler_leaves_it_standing(pull: Pull) -> None:
    """A contact's minted person and the existing person linked to one address are proposed once, by one rule.

    The existing person holds the address and the directory's contact claims it:
    two sources at one address, the pair the same-person verb proposes once the
    page commits, citing the handle (``propose_same_person_as_system``), and no
    second proposal by the directory's rule. The pairing job keeps it, since a
    link supports it. Merging the two through the review's verb records the
    person's decision and leaves no proposal standing. The merging person writes
    both (the platform admin who owns the directory), the deliberate override of
    the ``adopt`` permission the limit refused.
    """

    owner = pull.directory.owner
    person = _refused_admin_owner(pull)
    pull.sync()
    minted = pull.link("ada").target_object_id
    handle = Handle.objects.get(platform="email", normalized_value="ada@example.com")
    pair_ids = frozenset({minted, str(person.pk)})
    reviewed = Party.objects.with_actor(owner).same_as_pairs()
    assert [(frozenset({str(pair.left.pk), str(pair.right.pk)}), pair.normalized_value) for pair in reviewed] == [
        (pair_ids, "ada@example.com")
    ]
    (proposal,) = _proposal_rows()
    same_as = str(to_subject_ref(Party.objects.rule_source("same_as")))
    assert (proposal.asserted_by, proposal.evidence) == (same_as, _refs(handle))

    Party.objects.reconcile_same_as()
    assert _proposals() == [(pair_ids, "asserted", _refs(handle))]

    assert Party.objects.apply_same_as_pair(
        left_id=public_id_of(Party.objects.get(pk=person.pk)),
        right_id=public_id_of(Party.objects.get(pk=minted)),
        survivor="left",
        action="merge",
        actor=owner,
    ) == "merged"

    statements = Party.same_as.model.statements.filter(subject=proposal.subject).current()
    decided = statements.filter(asserted_by=str(to_subject_ref(owner)))
    assert list(decided.values_list("status", "rank")) == [("asserted", "preferred")]
    assert not Party.objects.with_actor(owner).same_as_pairs()
    Party.objects.reconcile_same_as()  # The merged record is linked to nothing: the pairing job withdraws the proposal.
    assert _proposals() == [(pair_ids, "withdrawn", _refs(handle))]


@pytest.mark.usefixtures("adoption")
def test_an_address_pair_the_directory_rule_proposed_before_stays_in_review_until_merged(pull: Pull) -> None:
    """An upgrade keeps the directory rule's earlier proposals of address pairs as they are.

    Before, the directory's own rule proposed an address pair, citing the
    contact's link and the handle. Such a statement stays readable and in review,
    the pairing job states no second proposal beside it, and merging the two
    withdraws it, as before.
    """

    owner = pull.directory.owner
    person = _refused_admin_owner(pull)
    pull.sync()
    ada = pull.link("ada")
    minted = Person.objects.get(pk=ada.target_object_id)
    handle = Handle.objects.get(platform="email", normalized_value="ada@example.com")
    (proposal,) = _proposal_rows()
    same_as, directory_match = (Party.objects.rule_source(key) for key in ("same_as", "directory_match"))
    with system_context(reason="tests.directory_match.earlier_release"):
        Party.same_as.model.statements.withdraw(same_as, [proposal.subject_id])
        Party.objects.propose_directory_match(minted, person, evidence=[ada, handle])
    earlier = [(str(to_subject_ref(directory_match)), "asserted", _refs(ada, handle))]

    def stated() -> list[tuple[str, str, list[Any]]]:
        rows = Party.same_as.model.statements.filter(subject_id=proposal.subject_id).current()
        return [(row.asserted_by, row.status, row.evidence) for row in rows.exclude(status="withdrawn")]

    assert stated() == earlier
    reviewed = Party.objects.with_actor(owner).same_as_pairs()
    assert [frozenset({pair.left.pk, pair.right.pk}) for pair in reviewed] == [frozenset({minted.pk, person.pk})]
    Party.objects.reconcile_same_as()
    assert stated() == earlier

    assert Party.objects.apply_same_as_pair(
        left_id=public_id_of(Party.objects.get(pk=person.pk)),
        right_id=public_id_of(Party.objects.get(pk=minted.pk)),
        survivor="left",
        action="merge",
        actor=owner,
    ) == "merged"
    assert stated() == [(str(to_subject_ref(owner)), "asserted", [])]  # The person's decision; the proposal withdrawn.


def _merge(pull: Pull, survivor: Any, merged: Any) -> str:
    """Merge the proposed pair through the review's verb, as the directory's owner, who writes both."""

    return Party.objects.apply_same_as_pair(
        left_id=public_id_of(Party.objects.get(pk=survivor)),
        right_id=public_id_of(Party.objects.get(pk=merged)),
        survivor="left",
        action="merge",
        actor=pull.directory.owner,
    )


def test_a_merge_never_sets_two_live_contacts_of_one_pull_only_directory_on_one_person(pull: Pull) -> None:
    """The source keeps them separate, so each sync would overwrite one with the other: refused until one is gone.

    A tombstoned link counts for nothing: once the source has removed one of the
    two records, the merge goes through.
    """

    pull.source.put("ada-copy", replace(_ADA, display_name="Ada L."))
    pull.sync()
    ada, copy = (pull.link(key).target_object_id for key in ("ada", "ada-copy"))

    with pytest.raises(ValidationError, match="kept separate at its source"):
        _merge(pull, ada, copy)
    assert Party._base_manager.filter(pk__in=[ada, copy], merged_into__isnull=True).count() == 2

    _tombstone(pull, "ada-copy")
    assert _merge(pull, ada, copy) == "merged"


@pytest.mark.parametrize("holder", [_refused_account_holder, _refused_write_back])
def test_a_directorys_contact_merges_into_an_accounts_person_or_a_two_way_cards_person(pull: Pull, holder: Any) -> None:
    """Writing both people is enough: the directory's contact follows its person into the survivor."""

    person = holder(pull)
    pull.sync()

    assert _merge(pull, person.pk, pull.link("ada").target_object_id) == "merged"
    assert pull.link("ada").target_object_id == str(person.pk)


@pytest.mark.usefixtures("adoption")
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
    Party.objects.reconcile_same_as()  # The trashed mint is linked to nothing: the pairing job withdraws the pair.
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


@pytest.mark.usefixtures("adoption")
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
    assert _proposals() == [(frozenset({copy.target_object_id, str(person.pk)}), "asserted", _refs(handle))]


def test_two_contacts_with_one_address_nobody_held_leave_it_with_the_first(pull: Pull) -> None:
    """The first contact mints its person, who then holds the address; the second mints its own, who only claims it."""

    pull.source.put("ada-copy", replace(_ADA, display_name="Ada L."))

    assert pull.sync().count == 3

    ada, copy = pull.link("ada"), pull.link("ada-copy")
    assert ada.target_object_id != copy.target_object_id
    assert _held_by("ada@example.com") == int(ada.target_object_id)
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [1.0]
    assert _claim_confidences(copy.target_object_id, "ada@example.com") == [0.4]
    # One source's two records are no same-person pair (two sources link a pair), and the merge guard keeps two
    # live contacts of one pull-only directory apart anyway.
    assert _proposals() == []
    pull.source.put("ada-copy", replace(_ADA, display_name="Ada L.", notes="Edited at the source."))
    assert pull.sync().count == 1
    assert _held_by("ada@example.com") == int(ada.target_object_id)
    assert _claim_confidences(copy.target_object_id, "ada@example.com") == [0.4]


def test_a_contact_refused_the_one_person_claimed_for_its_address_keeps_only_claiming_it(pull: Pull) -> None:
    """Nobody holds the address; another person's claim leaves the contact's own claim a claim, record after record.

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
    assert _proposals() == []  # A name suggester's claim links its party to nothing (same_person_links).
    pull.source.put("ada", replace(_ADA, notes="Edited at the source."))
    assert pull.sync().count == 1
    assert _held_by("ada@example.com") is None
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [0.4]


def _tombstone(pull: Pull, key: str) -> None:
    """The source deletes the contact and the next page reports it."""

    del pull.source.contacts[key]
    pull.source.deleted.add(key)
    assert pull.sync().count == 1
    assert pull.link(key).status == LinkStatus.TOMBSTONE


@pytest.mark.usefixtures("adoption")
def test_a_replaced_link_of_another_stream_hands_no_person_over(pull: Pull) -> None:
    """A caller may name any link as replaced; only one of the contact's own stream hands its person over."""

    person = _existing(_user("someone-else"))[0]
    other = current_stream(pull.directory, CONTACTS, "other-book", kind=StreamKind.RECORD_REPLICA)
    foreign = RecordLink.objects.observe(other, "ada-elsewhere", target=Party._base_manager.get(pk=person.pk))
    link = RecordLink.objects.observe(pull.stream, "downstream")

    with pytest.raises(ValidationError, match="may not adopt"):
        Party.objects.ingest_contact(
            _ADA,
            stream=pull.stream,
            link=link,
            created_by_id=pull.directory.owner_id,
            target=person,
            replaces=[foreign],
        )
    link.refresh_from_db()
    assert link.target_object_id is None


@pytest.mark.usefixtures("adoption")
def test_a_tombstoned_replaced_link_hands_over_only_a_person_admitted_again(pull: Pull) -> None:
    """The merged-away record's person became an account's own while its link was tombstoned: the survivor mints."""

    pull.sync()
    first = pull.link("ada").target_object_id
    _tombstone(pull, "ada")
    Person._base_manager.filter(pk=first).update(user=_user("ada-account"))
    pull.source.put("merged", _ADA, replaces=("ada",))

    result = pull.sync()

    assert (result.count, result.failure_ids) == (1, ())
    assert pull.link("merged").target_object_id not in (None, first)


@pytest.mark.usefixtures("adoption")
def test_a_tombstoned_contact_still_names_its_person_for_the_directorys_other_contacts(pull: Pull) -> None:
    """While its record is gone at the source, no other contact of the directory adopts the person its link names."""

    person, _handle = _existing(pull.directory.owner)
    pull.sync()
    assert pull.link("ada").target_object_id == str(person.pk)
    _tombstone(pull, "ada")
    pull.source.put("ada-again", _ADA)

    assert pull.sync().count == 1

    assert pull.link("ada-again").target_object_id not in (None, str(person.pk))
    assert _held_by("ada@example.com") == person.pk


@pytest.mark.usefixtures("adoption")
def test_a_contact_returning_from_a_tombstone_is_admitted_again(pull: Pull) -> None:
    """Another contact of the directory names the person now: the returning record mints its own and proposes them."""

    person, handle = _existing(pull.directory.owner)
    pull.sync()
    _tombstone(pull, "ada")
    copy = RecordLink.objects.observe(pull.stream, "ada-copy", target=Party._base_manager.get(pk=person.pk))
    pull.source.put("ada", _ADA)

    result = pull.sync()

    assert (result.count, result.failure_ids) == (1, ())
    ada = pull.link("ada")
    assert ada.target_object_id not in (None, str(person.pk))
    assert copy.pk in RecordLink.objects.filter(target_object_id=str(person.pk)).values_list("pk", flat=True)
    assert _held_by("ada@example.com") == person.pk
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [0.4]
    assert frozenset({ada.target_object_id, str(person.pk)}) in {pair for pair, _status, _evidence in _proposals()}


@pytest.mark.usefixtures("adoption")
def test_an_address_an_account_signs_in_with_adopts_nobody_without_the_handle_that_found_them(pull: Pull) -> None:
    """A caller binding the person directly passes no handle; the contact's own addresses still refuse it."""

    get_user_model().objects.create_user(username="ada-signin", email="ada@example.com")
    person, _handle = _existing(pull.directory.owner)
    link = RecordLink.objects.observe(pull.stream, "downstream")

    with pytest.raises(ValidationError, match="sign-in address"):
        Party.objects.ingest_contact(
            _ADA, stream=pull.stream, link=link, created_by_id=pull.directory.owner_id, target=person
        )


@pytest.mark.usefixtures("adoption")
def test_an_address_an_account_signs_in_with_in_another_spelling_adopts_nobody(pull: Pull) -> None:
    """The handle that found the person spells the account's address the provider's other way; it is the same one."""

    get_user_model().objects.create_user(username="ada-signin", email="ada.lovelace@gmail.com")
    person, handle = _existing(pull.directory.owner, email="adalovelace@gmail.com")
    pull.source.put("ada", replace(_ADA, emails=(("ada.lovelace@gmail.com", "home", True),)))

    pull.sync()

    ada = pull.link("ada")
    assert ada.target_object_id not in (None, str(person.pk))
    handle.refresh_from_db()
    assert handle.party_id == person.pk
    assert _claim_confidences(ada.target_object_id, "adalovelace@gmail.com") == [0.4]


@pytest.mark.usefixtures("adoption")
def test_an_address_an_account_signs_in_with_in_a_spelling_no_handle_holds_adopts_nobody(pull: Pull) -> None:
    """The account spells it the provider's other way, which neither the contact nor any handle does: still the same."""

    get_user_model().objects.create_user(username="ada-signin", email="ada.lovelace@gmail.com")
    person, _handle = _existing(pull.directory.owner, email="adalovelace@gmail.com")
    pull.source.put("ada", replace(_ADA, emails=(("adalovelace@gmail.com", "home", True),)))

    pull.sync()

    ada = pull.link("ada")
    assert ada.target_object_id not in (None, str(person.pk))
    assert _claim_confidences(ada.target_object_id, "adalovelace@gmail.com") == [Party.CLAIM_CONFIDENCE]


def test_the_schema_reads_claims_at_the_models_review_confidence() -> None:
    """The handle's stating-directory filter copies a number the model owns; they must stay one number."""

    zed = (Path(parties_package.__file__).parent / "permissions.zed").read_text()
    declaration = r"relation stating_directory: parties/directory // rebac:field=(\{.*partystatement.*\})"
    (declared,) = re.findall(declaration, zed)
    assert json.loads(declared)["filters"]["partystatement__confidence__gte"] == Party.REVIEW_CONFIDENCE
    assert Party.CLAIM_CONFIDENCE < Party.REVIEW_CONFIDENCE


def test_an_address_an_account_without_a_person_signs_in_with_stays_a_claim(pull: Pull) -> None:
    """The lookup names nobody by it, yet it is the account's: the contact only claims it, record after record."""

    account = get_user_model().objects.create_user(username="ada-signin", email="ada@example.com")
    assert not Person._base_manager.filter(user=account).exists()

    pull.sync()

    ada = pull.link("ada")
    assert _held_by("ada@example.com") is None
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [0.4]
    pull.source.put("ada", replace(_ADA, notes="Edited at the source."))
    assert pull.sync().count == 1
    assert _held_by("ada@example.com") is None
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [0.4]


def _between_lookup_and_lock(monkeypatch: pytest.MonkeyPatch, change: Any) -> None:
    """Run ``change`` once, in the page's transaction, just before the page takes its identity locks."""

    lock = Party.objects.lock_identity_rows
    pending = [change]

    def racing(**kwargs: Any) -> Any:
        while pending:
            pending.pop()()
        return lock(**kwargs)

    monkeypatch.setattr(Party.objects, "lock_identity_rows", racing)


@pytest.mark.usefixtures("adoption")
def test_a_person_trashed_before_the_page_locks_is_not_adopted(pull: Pull, monkeypatch: pytest.MonkeyPatch) -> None:
    person, _handle = _existing(pull.directory.owner)
    _between_lookup_and_lock(
        monkeypatch, lambda: Person._base_manager.get(pk=person.pk).trash(reason="Removed by its holder")
    )

    result = pull.sync()

    assert result.failure_ids == ()
    assert pull.link("ada").target_object_id not in (None, str(person.pk))


@pytest.mark.usefixtures("adoption")
def test_a_person_merged_away_before_the_page_locks_is_not_adopted(pull: Pull, monkeypatch: pytest.MonkeyPatch) -> None:
    """The survivor holds the address once the page reads under its locks, and is the one adopted."""

    person, _handle = _existing(pull.directory.owner)
    survivor = Person.objects.create(display_name="Ada (kept)", created_by=pull.directory.owner)
    _between_lookup_and_lock(
        monkeypatch,
        lambda: Party._base_manager.filter(pk=person.pk).update(
            merged_into_id=survivor.pk, is_trashed=True, trashed_at=timezone.now()
        ),
    )

    result = pull.sync()

    assert result.failure_ids == ()
    assert pull.link("ada").target_object_id == str(survivor.pk)


def test_a_page_looks_up_its_addresses_once(pull: Pull, monkeypatch: pytest.MonkeyPatch) -> None:
    """The contested-address rule (and, with adoption on, its admission) reads the page's one lookup."""

    _existing(pull.directory.owner)
    lookups: list[list[str]] = []
    lookup = Party.objects.people_for_emails

    def counted(emails: Any) -> Any:
        lookups.append(list(emails))
        return lookup(lookups[-1])

    monkeypatch.setattr(Party.objects, "people_for_emails", counted)

    assert pull.sync().count == 2

    assert lookups == [["ada@example.com", "grace@example.com"]]


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required.")
def test_a_page_holds_every_handle_its_linked_people_carry_before_any_record_applies(pull: Pull) -> None:
    """A record's own locks then find its person's other handles held: no handle is locked after a person.

    Mail ingestion locks a handle, then its party; a page that locked a person and
    only later another handle of theirs could deadlock with it.
    """

    pull.sync()
    person = Party.objects.get(pk=pull.link("ada").target_object_id)
    owner_id = pull.directory.owner_id
    other = Handle.objects.upsert(platform="email", value="ada@elsewhere.example", created_by_id=owner_id)
    Party.objects.assert_handle(person, other, asserted_by=pull.directory.owner)
    pull.source.changed.add("ada")
    page = pull.backend.extract_contacts(pull.stream, 100)

    def lock_other_handle() -> None:
        with system_context(reason="tests.directory_page.contender"), transaction.atomic():
            list(Handle._base_manager.filter(pk=other.pk).select_for_update())

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="tests.directory_page.holder"), transaction.atomic():
            pull.backend.prepare_page(pull.stream, page)
            contender, pid = submit(pool, lock_other_handle)
            wait_for_lock(pid, contender)
        contender.result(timeout=10)


def _page_holds_while(pull: Pull, contend: Any) -> Any:
    """Prepare a page in a transaction, prove ``contend`` waits on a lock it holds, then commit; return its result."""

    page = pull.backend.extract_contacts(pull.stream, 100)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="tests.directory_page.holder"), transaction.atomic():
            pull.backend.prepare_page(pull.stream, page)
            contender, pid = submit(pool, contend)
            wait_for_lock(pid, contender)
        return contender.result(timeout=10)


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required.")
def test_mail_landing_on_a_pages_address_waits_for_the_page(pull: Pull) -> None:
    """Mail ingestion locks a handle, then its party: it queues on the page's handle and lands after it."""

    person, handle = _existing(pull.directory.owner)

    def land_mail() -> Any:
        with system_context(reason="tests.directory_page.mail"):
            return Party.objects.assert_handle(person, handle, asserted_by=Party.objects.rule_source("source_sender"))

    assert _page_holds_while(pull, land_mail) is not None


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required.")
def test_a_merge_of_a_pages_person_waits_for_the_page(pull: Pull) -> None:
    """The merge locks every handle of its people first, so it queues on the page's handle, never holding a person."""

    owner = pull.directory.owner
    kept, _handle = _existing(owner)
    merged, _other = _existing(owner, email="ada@work.example")

    def merge() -> Any:
        with actor_context(owner):
            return Party.objects.get(pk=kept.pk).with_actor(owner).merge(records=[merged.public_id])

    assert _page_holds_while(pull, merge).pk == kept.pk
    assert Party._base_manager.get(pk=merged.pk).merged_into_id == kept.pk


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required.")
def test_a_confirmation_of_a_pages_address_waits_for_the_page(pull: Pull) -> None:
    owner = pull.directory.owner
    claimant = Person.objects.create(display_name="Ada (claimed)", created_by=owner)
    handle = Handle.objects.upsert(platform="email", value="ada@example.com", created_by_id=owner.pk)
    claim = Party.objects.assert_handle(
        claimant, handle, asserted_by=Party.objects.rule_source("display_name"), confidence=0.4
    )

    def confirm() -> Any:
        with actor_context(owner):
            return claim.with_actor(owner).confirm()

    assert _page_holds_while(pull, confirm).is_confirmed


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required.")
@pytest.mark.usefixtures("adoption")
def test_an_account_adopting_a_pages_candidate_waits_for_the_page(pull: Pull) -> None:
    """``for_user(adopt=)`` locks the person row with its party row, which the page holds, so no verdict goes stale."""

    person, _handle = _existing(pull.directory.owner)
    account = get_user_model().objects.create_user(username="ada-account")

    def adopt() -> Any:
        with system_context(reason="tests.directory_page.account"):
            return Person.objects.for_user(account, adopt=person)

    assert _page_holds_while(pull, adopt).user_id == account.pk


def _page_locks(pull: Pull, monkeypatch: pytest.MonkeyPatch) -> list[tuple[set[Any], set[Any]]]:
    """Extract one page and prepare it, recording each identity lock call's parties and handles."""

    page = pull.backend.extract_contacts(pull.stream, 100)
    locked: list[tuple[set[Any], set[Any]]] = []
    lock = Party.objects.lock_identity_rows

    def spy(**kwargs: Any) -> Any:
        locked.append((set(kwargs["party_ids"]), set(kwargs["handle_ids"])))
        return lock(**kwargs)

    monkeypatch.setattr(Party.objects, "lock_identity_rows", spy)
    with transaction.atomic():
        pull.backend.prepare_page(pull.stream, page)
    return locked


def _handle_ids(party_id: Any) -> set[Any]:
    return set(Party.objects.handle_statements().filter(subject_id=party_id).values_list("handle_id", flat=True))


def test_a_page_locks_the_people_its_tombstones_name_in_its_one_lock_call(
    pull: Pull, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tombstone's withdrawal locks its person: the page holds them, and their handles, before any record applies."""

    pull.sync()
    grace = int(pull.link("grace").target_object_id)
    del pull.source.contacts["grace"]
    pull.source.deleted.add("grace")

    (parties, handles), *_rest = _page_locks(pull, monkeypatch)

    assert grace in parties
    assert _handle_ids(grace) <= handles


def test_a_page_locks_the_person_of_an_account_its_address_names_in_its_one_lock_call(
    pull: Pull, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The account's person holds none of the page's addresses, yet is proposed: the page locks them, handles too."""

    person = _refused_account_basis(pull)
    owner_id = pull.directory.owner_id
    other = Handle.objects.upsert(platform="email", value="ada@elsewhere.example", created_by_id=owner_id)
    Party.objects.assert_handle(person, other, asserted_by=Party.objects.rule_source("source_sender"))

    (parties, handles), *_rest = _page_locks(pull, monkeypatch)

    assert person.pk in parties
    assert other.pk in handles


def test_a_merge_locks_every_handle_of_its_people_before_any_of_them(
    pull: Pull, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page locks handles, then people: a merge holding a person while it waits for a handle could deadlock it."""

    owner = pull.directory.owner
    kept, kept_handle = _existing(owner, email="ada@example.com")
    merged, merged_handle = _existing(owner, email="ada@work.example")
    locked: list[tuple[set[Any], set[Any]]] = []
    lock = Party.objects.lock_identity_rows

    def spy(**kwargs: Any) -> Any:
        locked.append((set(kwargs["party_ids"]), set(kwargs["handle_ids"])))
        return lock(**kwargs)

    monkeypatch.setattr(Party.objects, "lock_identity_rows", spy)
    Party.objects.get(pk=kept.pk).with_actor(owner).merge(records=[Party.objects.get(pk=merged.pk).public_id])

    parties, handles = locked[0]
    assert {kept.pk, merged.pk} <= parties
    assert {kept_handle.pk, merged_handle.pk} <= handles


def test_a_tombstone_earlier_in_a_page_frees_its_address_for_a_later_contact_of_the_page(
    pull: Pull, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tombstone withdraws the only statement holding the address; the new contact then holds it in full."""

    pull.sync()
    del pull.source.contacts["grace"]
    pull.source.deleted.add("grace")
    pull.source.put("grace-again", _GRACE)
    extract = pull.backend.extract_contacts

    def tombstones_first(stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        page = extract(stream, page_bound, deadline=deadline)
        return replace(page, records=sorted(page.records, key=lambda record: not record.tombstone))

    monkeypatch.setattr(pull.backend, "extract_contacts", tombstones_first)

    assert pull.sync().count == 2

    again = pull.link("grace-again").target_object_id
    assert _held_by("grace@example.com") == int(again)
    assert _claim_confidences(again, "grace@example.com") == [1.0]


def _claim(pull: Pull, key: str, address: str) -> Any:
    """The directory's current claim of ``address`` on the contact's person."""

    rows = Party.statements.filter(
        subject_id=pull.link(key).target_object_id, handle__normalized_value=address, source_kind="directory"
    )
    return rows.current().get()


def test_a_claim_stays_a_claim_while_its_holder_is_trashed_and_restored(pull: Pull) -> None:
    """The contact's own claim is the only one left while the holder is trashed; it never becomes the address's holding.

    Restored, the holder holds the address again: a directory statement would
    otherwise outrank the holder's rule statement for good.
    """

    person, _handle = _existing(_user("someone-else"))
    pull.sync()
    minted = pull.link("ada").target_object_id
    assert _claim_confidences(minted, "ada@example.com") == [0.4]

    Person._base_manager.get(pk=person.pk).trash(reason="Removed by its holder")
    pull.source.put("ada", replace(_ADA, notes="Edited at the source."))
    assert pull.sync().count == 1
    assert _claim_confidences(minted, "ada@example.com") == [0.4]

    Person._base_manager.get(pk=person.pk).restore()
    pull.source.put("ada", replace(_ADA, notes="Edited again."))
    assert pull.sync().count == 1
    assert _held_by("ada@example.com") == person.pk
    assert _claim_confidences(minted, "ada@example.com") == [0.4]


def _restore(person: Any, handle: Any) -> None:
    """Restore the trashed holder, then resolve the handle as the next statement on it would.

    Restoring a person re-resolves no handle: a handle resolved while its holder
    was trashed keeps that answer until a statement on it changes.
    """

    Person._base_manager.get(pk=person.pk).restore()
    Handle(pk=handle.pk).resolve()


def test_a_holder_trashed_before_the_contact_first_states_its_address_keeps_it_when_restored(pull: Pull) -> None:
    """A trashed holder whose statement of the address is current still holds it: the contact only claims it."""

    person, handle = _existing(_user("someone-else"))
    Person._base_manager.get(pk=person.pk).trash(reason="Removed by its holder")

    pull.sync()

    minted = pull.link("ada").target_object_id
    assert minted not in (None, str(person.pk))
    _restore(person, handle)
    assert _held_by("ada@example.com") == person.pk
    assert _claim_confidences(minted, "ada@example.com") == [0.4]


def test_a_holder_trashed_while_the_source_dropped_the_address_keeps_it_when_the_source_adds_it_again(
    pull: Pull,
) -> None:
    """The contact's claim was withdrawn, so it is no claim of its own any more; the lapsed holder keeps it one."""

    person, handle = _existing(_user("someone-else"))
    pull.sync()
    minted = pull.link("ada").target_object_id
    pull.source.put("ada", replace(_ADA, emails=()))
    assert pull.sync().count == 1
    assert _claim_confidences(minted, "ada@example.com") == []
    Person._base_manager.get(pk=person.pk).trash(reason="Removed by its holder")

    pull.source.put("ada", _ADA)
    assert pull.sync().count == 1

    _restore(person, handle)
    assert _held_by("ada@example.com") == person.pk
    assert _claim_confidences(minted, "ada@example.com") == [0.4]


@pytest.mark.usefixtures("adoption")
def test_a_contact_adopts_no_claimant_of_an_address_a_trashed_holder_keeps(pull: Pull) -> None:
    """Another source only claims the address while its holder is trashed: the claim names nobody to adopt."""

    person, handle = _existing(_user("someone-else"))
    Person._base_manager.get(pk=person.pk).trash(reason="Removed by its holder")
    claimant = Person.objects.create(display_name="Ada (claimed)", created_by=pull.directory.owner)
    Party.objects.assert_handle(claimant, handle, asserted_by=Party.objects.rule_source("display_name"), confidence=0.4)

    pull.sync()

    ada = pull.link("ada")
    assert ada.target_object_id not in (None, str(claimant.pk), str(person.pk))
    _restore(person, handle)
    assert _held_by("ada@example.com") == person.pk
    assert _claim_confidences(ada.target_object_id, "ada@example.com") == [0.4]
    assert not Party.statements.filter(subject_id=claimant.pk, asserted_by=str(ada.asserted_by)).exists()


def test_confirming_a_claim_needs_write_on_the_person_who_holds_the_address(pull: Pull) -> None:
    """The review queue's accept moves the address off its holder, so it takes authority over the holder too."""

    holder_owner = _user("someone-else")
    person, _handle = _existing(holder_owner)
    pull.sync()
    minted = pull.link("ada").target_object_id
    claim = _claim(pull, "ada", "ada@example.com")

    with pytest.raises(PermissionDenied, match=_HOLDING_REFUSAL):
        claim.with_actor(pull.directory.owner).confirm()
    assert _held_by("ada@example.com") == person.pk

    with system_context(reason="tests.directory_claim.editor"):
        claimant = to_object_ref(Person._base_manager.get(pk=minted))
        write_relationships([RelationshipTuple(claimant, "editor", to_subject_ref(holder_owner))])
    claim.with_actor(holder_owner).confirm()
    assert _held_by("ada@example.com") == int(minted)


_HOLDING_REFUSAL = "This address cannot be given to this party."
"""The one refusal of a stance that would move an address off a holder its author cannot write, whoever holds it."""


def test_confirming_a_claim_spelled_otherwise_needs_write_on_the_holder_of_the_address(pull: Pull) -> None:
    """The contact's alias is a row of its own that nobody holds; the address is still its holder's."""

    pull.source.put("ada", replace(_ADA, emails=(("ada.lovelace@gmail.com", "home", True),)))
    person, handle = _existing(_user("someone-else"), email="adalovelace@gmail.com")
    pull.sync()
    claim = _claim(pull, "ada", "adalovelace@gmail.com")
    assert claim.handle_id != handle.pk

    with pytest.raises(PermissionDenied, match=_HOLDING_REFUSAL):
        claim.with_actor(pull.directory.owner).confirm()

    assert Handle.objects.get(pk=claim.handle_id).party_id is None
    handle.refresh_from_db()
    assert handle.party_id == person.pk


def test_a_claim_in_another_letter_case_lands_on_the_holders_row_and_confirming_it_needs_write_on_the_holder(
    pull: Pull,
) -> None:
    """Another letter case is the holder's own handle: the contact claims that row, and the address stays theirs."""

    person, handle = _existing(_user("someone-else"), email="Ada@Example.COM")
    pull.sync()
    claim = _claim(pull, "ada", "ada@example.com")
    assert claim.handle_id == handle.pk

    with pytest.raises(PermissionDenied, match=_HOLDING_REFUSAL):
        claim.with_actor(pull.directory.owner).confirm()

    handle.refresh_from_db()
    assert (handle.party_id, handle.value) == (person.pk, "Ada@Example.COM")


def test_confirming_a_claim_needs_write_on_a_trashed_holder_of_the_address(pull: Pull) -> None:
    person, handle = _existing(_user("someone-else"))
    pull.sync()
    claim = _claim(pull, "ada", "ada@example.com")
    Person._base_manager.get(pk=person.pk).trash(reason="Removed by its holder")

    with pytest.raises(PermissionDenied, match=_HOLDING_REFUSAL):
        claim.with_actor(pull.directory.owner).confirm()

    _restore(person, handle)
    assert _held_by("ada@example.com") == person.pk


def test_confirming_a_claim_of_an_accounts_sign_in_address_needs_write_on_the_accounts_person(pull: Pull) -> None:
    """Nobody holds the handle, yet the address is the account's: its person, whom the account owns, must agree."""

    account = get_user_model().objects.create_user(username="ada-signin", email="ada@example.com")
    with system_context(reason="tests.directory_claim.account"):
        Person.objects.for_user(account)
    pull.sync()
    claim = _claim(pull, "ada", "ada@example.com")
    assert _held_by("ada@example.com") is None

    with pytest.raises(PermissionDenied, match=_HOLDING_REFUSAL):
        claim.with_actor(pull.directory.owner).confirm()

    assert _held_by("ada@example.com") is None


def test_taking_control_of_an_address_someone_else_holds_leaves_a_claim_unless_it_is_the_accounts_sign_in_address(
    pull: Pull,
) -> None:
    """Taking control would move the address like a confirmation; held by someone the taker cannot write, it is a claim.

    Control and the holding stay as they were, and nothing is refused: the
    claim waits for review. The holding rule itself still refuses the taker's
    winning stance. An address nobody else holds, and the account's own
    sign-in address, are taken as before.
    """

    person, handle = _existing(_user("someone-else"))
    taker = get_user_model().objects.create_user(username="taker", email="taker@example.com")
    with system_context(reason="tests.directory_claim.taker"):
        taker_person = Person.objects.for_user(taker)

    assert Handle.objects.claim_control(taker, platform="email", value="ada@example.com").pk == handle.pk

    handle.refresh_from_db()
    assert (handle.party_id, handle.controller_id) == (person.pk, None)
    assert _claim_confidences(taker_person.pk, "ada@example.com") == []  # a person's claim, not a directory's
    claim = Party.statements.filter(subject_id=taker_person.pk, handle=handle).current().get()
    assert (claim.status, claim.source_kind, claim.confidence) == ("asserted", "person", Party.CLAIM_CONFIDENCE)
    assert claim.pk in Party.objects.handle_statements().to_review().values_list("pk", flat=True)
    with pytest.raises(PermissionDenied, match=_HOLDING_REFUSAL):
        Party.objects.assert_handle(taker_person, handle, asserted_by=taker, confidence=1.0)
    # An address nobody else holds is taken as before.
    free = Handle.objects.claim_control(taker, platform="email", value="taker@elsewhere.example")
    free.refresh_from_db()
    assert (free.party_id, free.controller_id) == (taker_person.pk, taker.pk)
    # An account's sign-in address is its own: the account takes it whoever holds it.
    account = get_user_model().objects.create_user(username="ada-signin", email="ada@example.com")
    Handle.objects.claim_control(account, platform="email", value="ada@example.com")
    handle.refresh_from_db()
    assert (handle.party_id, handle.controller_id) == (Person.objects.get(user=account).pk, account.pk)


def test_reverting_a_dismissal_needs_write_on_whoever_holds_the_address_now(pull: Pull) -> None:
    """The restored confirmation would make the contact's person hold an address someone else holds since.

    The holder holds an alias of the address, so nothing wrote the dismissed handle
    row after the dismissal and history can replay it.
    """

    pull.source.put("ada", replace(_ADA, emails=(("ada.lovelace@gmail.com", "home", True),)))
    pull.sync()
    owner = pull.directory.owner
    with actor_context(owner), changeset(fresh=True):
        confirmed = _claim(pull, "ada", "adalovelace@gmail.com").with_actor(owner).confirm()
    with actor_context(owner), changeset(fresh=True):
        confirmed.with_actor(owner).dismiss()
    dismissal = Party.statements.model.history.filter(id=confirmed.pk).latest("history_id").changeset
    person, handle = _existing(_user("someone-else"), email="adalovelace@gmail.com")

    with actor_context(owner), pytest.raises(PermissionDenied, match=_HOLDING_REFUSAL):
        dismissal.with_actor(owner).revert()

    assert Party.statements.model._base_manager.get(pk=confirmed.pk).is_dismissed
    assert Handle.objects.get(pk=confirmed.handle_id).party_id is None
    handle.refresh_from_db()
    assert handle.party_id == person.pk


def test_reverting_a_dismissal_after_someone_took_the_address_in_another_letter_case_is_a_later_write(
    pull: Pull,
) -> None:
    """Another letter case is the dismissed row itself: its holder's statement wrote it since, so history refuses."""

    pull.sync()
    owner = pull.directory.owner
    with actor_context(owner), changeset(fresh=True):
        confirmed = _claim(pull, "ada", "ada@example.com").with_actor(owner).confirm()
    with actor_context(owner), changeset(fresh=True):
        confirmed.with_actor(owner).dismiss()
    dismissal = Party.statements.model.history.filter(id=confirmed.pk).latest("history_id").changeset
    person, handle = _existing(_user("someone-else"), email="Ada@Example.COM")
    assert handle.pk == confirmed.handle_id

    with actor_context(owner), pytest.raises(ValidationError) as refusal:
        dismissal.with_actor(owner).revert()

    assert refusal.value.code == "later_change"
    assert Party.statements.model._base_manager.get(pk=confirmed.pk).is_dismissed
    handle.refresh_from_db()
    assert (handle.party_id, handle.value) == (person.pk, "ada@example.com")


def test_a_directorys_contact_reader_reads_no_handle_its_contact_only_claims(pull: Pull) -> None:
    """A claim below review confidence lends the directory's contact readers nothing on the holder's handle."""

    person, handle = _existing(_user("someone-else"))
    pull.sync()
    reader = _user("contacts-reader")
    with system_context(reason="tests.directory_claim.reader"):
        shared = RelationshipTuple(to_object_ref(pull.directory), "contact_reader", to_subject_ref(reader))
        write_relationships([shared])
    grace = Handle.objects.get(platform="email", value="grace@example.com")

    readable = set(Handle.objects.with_actor(reader).filter(pk__in=[handle.pk, grace.pk]).values_list("pk", flat=True))

    assert Party.objects.with_actor(reader).filter(pk=pull.link("ada").target_object_id).exists()
    assert readable == {grace.pk}


def _granted_editor(pull: Pull, *, group: bool) -> Any:
    """A person another user owns, whose party names the directory's owner an editor, directly or through a group."""

    person = _existing(_user("someone-else"))[0]
    subject = to_subject_ref(pull.directory.owner)
    with system_context(reason="tests.directory_adopt.editor"):
        if group:
            editors = Group.objects.create(name="Contact editors")
            editors.add_member(str(subject))
            subject = SubjectRef.of("auth/group", str(editors.pk), "member")
        party = to_object_ref(Party._base_manager.get(pk=person.pk))
        write_relationships([RelationshipTuple(party, "editor", subject)])
    return person


@pytest.mark.parametrize("group", [False, True])
@pytest.mark.usefixtures("adoption")
def test_an_editor_of_the_persons_party_adopts_them(pull: Pull, group: bool) -> None:
    """Editors are granted on the party; ``adopt`` follows them there, a user's grant and a group's alike."""

    person = _granted_editor(pull, group=group)

    pull.sync()

    assert pull.link("ada").target_object_id == str(person.pk)


@pytest.mark.parametrize("to_owner", [True, False])
@pytest.mark.usefixtures("adoption")
def test_adoption_follows_an_ownership_transfer(pull: Pull, to_owner: bool) -> None:
    """The person's owner adopts them: a transfer to the directory's owner admits them, one away refuses."""

    previous, current = (_user("someone-else"), pull.directory.owner) if to_owner else (pull.directory.owner, None)
    current = current or _user("someone-new")
    person = _existing(previous)[0]
    Party.objects.get(pk=person.pk).with_actor(previous).transfer_ownership(current)

    pull.sync()

    assert (pull.link("ada").target_object_id == str(person.pk)) is to_owner


def test_a_record_leaving_a_trashed_person_withdraws_what_it_stated_there_even_beside_a_sibling(pull: Pull) -> None:
    """Another observing link of the source names the trashed person: the link's statements are withdrawn, not moved."""

    pull.sync()
    link = pull.link("ada")
    trashed = Party._base_manager.get(pk=link.target_object_id)
    RecordLink.objects.observe(pull.stream, "ada-elsewhere", target=trashed)
    Person._base_manager.get(pk=trashed.pk).trash(reason="Removed by its holder")
    pull.source.put("ada", replace(_ADA, notes="Still at the source."))

    assert pull.sync().count == 1

    source = str(link.asserting_source())
    for model in (Party, Person):
        stated = model.statements.filter(subject_id=trashed.pk, asserted_by=source, status="asserted").current()
        assert not stated.exists(), model


@pytest.mark.usefixtures("adoption")
def test_a_returning_contact_cites_no_handle_a_withdrawn_adoption_found_them_by(pull: Pull) -> None:
    """Its record comes back with another address: the identity it states again cites only current evidence."""

    person, handle = _existing(pull.directory.owner)
    pull.sync()
    assert _identity_evidence(person.pk) == _refs(handle)
    _tombstone(pull, "ada")
    pull.source.put("ada", replace(_ADA, emails=(("ada@new.example", "home", True),)))

    assert pull.sync().count == 1

    assert pull.link("ada").target_object_id == str(person.pk)
    assert _identity_evidence(person.pk) == []


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
