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
from rebac import system_context

from angee.integrate.states import LinkStatus, StreamDirection
from angee.integrate.streams import StreamPage, advance_stream, reconcile_stream
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.messaging.testing.models import Directory, Handle, Organization, Party, Person, RelationshipKind
from angee.parties.backends import (
    ADOPTED_THROUGH,
    DirectoryBackend,
    ManualDirectoryBackend,
    ParsedAddressbook,
    ParsedContact,
)
from angee.provenance.evidence import EvidenceReference
from tests.conftest import make_integration
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


def test_the_survivor_of_a_merge_takes_the_first_replaced_records_person_in_the_sources_order(pull: Pull) -> None:
    """Replaced records linked to different people: the source's first names the survivor's person, not key order."""

    pull.sync()
    ada, grace = (pull.link(key).target_object_id for key in ("ada", "grace"))
    for key in ("ada", "grace"):
        del pull.source.contacts[key]
    pull.source.put("merged", replace(_GRACE, notes="Merged at the source."), replaces=("grace", "ada"))

    assert pull.sync().count == 1

    assert pull.link("merged").target_object_id == grace
    assert Person.objects.filter(pk__in=[ada, grace]).count() == 2


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


def test_a_pull_only_contact_adopts_the_person_its_email_names_when_the_owner_writes_them(pull: Pull) -> None:
    """The record attaches to the existing person, keeps one handle row, and the link records the handle as evidence.

    The person was minted by another source's rule at full confidence; the
    directory's own handle claim lands on the same row.
    """

    person, handle = _existing(pull.directory.owner)
    people = Person.objects.count()

    assert pull.sync().count == 2

    ada = pull.link("ada")
    assert ada.target_object_id == str(person.pk)
    assert Person.objects.count() == people + 1  # Grace alone is new.
    assert Handle.objects.filter(platform="email", normalized_value="ada@example.com").count() == 1
    assert _handle_claims(person.pk, "ada@example.com") == [(ada.pk, "asserted"), (None, "asserted")]
    assert ada.metadata[ADOPTED_THROUGH] == EvidenceReference.of(handle).model_dump(mode="json")
    # The next pass finds the person through the link itself: nothing applies and the evidence stays.
    pull.source.changed.add("ada")
    assert pull.sync().count == 0
    assert pull.link("ada").metadata[ADOPTED_THROUGH] == EvidenceReference.of(handle).model_dump(mode="json")


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


@pytest.mark.parametrize("refusal", [_refused_owner, _refused_account_holder, _refused_account_basis, _refused_linked])
def test_a_contact_mints_its_own_person_when_an_adoption_limit_refuses_the_one_its_email_names(
    pull: Pull, refusal: Any
) -> None:
    """Each limit alone refuses the adoption: the contact mints, and the existing person gains no statement of it."""

    person = refusal(pull)

    assert pull.sync().count == 2

    ada = pull.link("ada")
    assert ada.target_object_id not in (None, str(person.pk))
    assert ADOPTED_THROUGH not in ada.metadata
    assert not Party.statements.filter(subject_id=person.pk, asserted_by=str(ada.asserted_by)).exists()


def test_a_two_way_directory_never_adopts(two_way: Pull) -> None:
    person, _handle = _existing(two_way.directory.owner)

    assert two_way.sync().count == 2

    assert two_way.link("ada").target_object_id not in (None, str(person.pk))


def test_two_contacts_that_would_adopt_one_person_adopt_it_once(pull: Pull) -> None:
    """The second contact of the same directory finds the person already linked, so it mints its own."""

    person, _handle = _existing(pull.directory.owner)
    pull.source.put("ada-copy", replace(_ADA, display_name="Ada L."))

    assert pull.sync().count == 3

    ada, copy = pull.link("ada"), pull.link("ada-copy")
    assert ada.target_object_id == str(person.pk)
    assert copy.target_object_id not in (None, str(person.pk))


def test_a_directory_forwards_its_connection_test_and_credential_admission(
    pull: Pull, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Directory`` asks its backend; the base's connection test proves only the credential."""

    assert pull.directory.test_connection() == "Credential is usable."
    asked: list[Any] = []

    def refuse(backend: DirectoryBackend, credential: Any) -> None:
        del backend
        asked.append(credential)
        raise ValidationError({"credential": "This directory cannot read with that credential."})

    monkeypatch.setattr(ManualDirectoryBackend, "admit_credential", refuse)
    with pytest.raises(ValidationError):
        pull.directory.attach_connection(pull.directory.credential)
    assert asked == [pull.directory.credential]
