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
from django.core.exceptions import ValidationError
from rebac import system_context

from angee.integrate.states import LinkStatus, StreamDirection
from angee.integrate.streams import PushPreview, StreamPage, advance_stream, preview_push, reconcile_stream
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.messaging.testing.models import Directory, Party, Person, RelationshipKind
from angee.parties.backends import DirectoryBackend, ManualDirectoryBackend, ParsedAddressbook, ParsedContact
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

    def sync(self) -> Any:
        result = advance_stream(self.stream, self.backend)
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


@pytest.fixture
def pull(db: None) -> Iterator[Pull]:
    del db
    with system_context(reason="test pull-only directory"):
        directory = make_integration("pull-directory", model=Directory)
        RelationshipKind.objects.create(
            slug="employee", name="Employee", inverse_name="Employer", category="professional",
            other_party_kind="organization",
        )
        source = Source()
        source.put("ada", _ADA)
        source.put("grace", _GRACE)
        backend = PullDirectoryBackend(directory)
        backend.source = source
        (books,) = backend.streams()
        advance_stream(SyncStream.objects.current(directory, books), backend)
        _, contacts = backend.streams()
        assert (contacts.direction, contacts.mapping_version) == (StreamDirection.PULL, 1)
        yield Pull(directory, backend, source, SyncStream.objects.current(directory, contacts))
        backend.close()


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
    assert preview_push(pull.stream, pull.backend) == PushPreview()


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


def test_a_merge_at_the_source_lands_on_the_replaced_records_person_and_moves_its_statements(pull: Pull) -> None:
    pull.sync()
    ada = pull.link("ada")
    party_id = ada.target_object_id
    people = Person.objects.count()

    del pull.source.contacts["ada"]
    pull.source.put("ada-2", replace(_ADA, notes="Merged at the source."), replaces=("ada", "never-linked"))
    # The page orders the survivor first, then the replaced record a link names, as a tombstone.
    records = pull.backend.record_changes(pull.stream, {"ada-2": pull.backend._payload("ada-2")})
    assert [(record.external_key, record.tombstone) for record in records] == [("ada-2", False), ("ada", True)]
    # An identity read returns only the key it was asked for.
    assert [record.external_key for record in pull.backend.read_keys(pull.stream, ["ada-2"])] == ["ada-2"]

    result = pull.sync()

    assert (result.count, result.failure_ids) == (2, ())
    survivor = pull.link("ada-2")
    ada.refresh_from_db()
    assert survivor.target_object_id == party_id
    assert Person.objects.count() == people
    assert Person.objects.get(pk=party_id).notes == "Merged at the source."
    assert ada.status == LinkStatus.TOMBSTONE
    assert not _asserted(party_id, ada).exists()
    assert _asserted(party_id, survivor).exists()
    assert not RecordLink.objects.filter(stream=pull.stream, external_key="never-linked").exists()


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
