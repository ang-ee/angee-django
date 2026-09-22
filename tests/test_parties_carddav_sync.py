"""CardDAV replica contracts through the real adapter, driver and parties writers.

The HTTP double implements DAV discovery, resource versions and opaque collection
tokens. Tests exercise observable conflicts and persistence, without replacing the
ingest owner or the three-way classifier.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any
from urllib.parse import urljoin
from xml.sax.saxutils import escape

import httpx
import pytest
import vobject
from django.db import connection
from rebac import system_context

from angee.base.serialization import canonical_json_sha256
from angee.integrate.records import (
    DiscrepancyKind,
    DiscrepancyStatus,
    LinkStatus,
    StreamDirection,
    StreamKind,
    StreamPhase,
)
from angee.integrate.streams import advance_stream, push_stream
from angee.parties.backends import (
    CONTACT_FIELDS,
    ParsedAddress,
    ParsedContact,
    ParsedPhoto,
    contact_from_projection,
    contact_projection,
)
from angee.parties_integrate_carddav.backend import CardDavDirectoryBackend, _parse_vcard
from tests.conftest import _clear_model_tables, _create_missing_tables, make_integration
from tests.integrate_models import RECORD_SYNC_TEST_MODELS, RecordLink, RecordRevision, SyncDiscrepancy, SyncStream
from tests.test_messaging import MESSAGING_TEST_MODELS, Directory, Folder, Party, Person, RelationshipKind

_BASE = "https://dav.example/"
_BOOK = f"{_BASE}books/contacts/"
_HREF = f"{_BOOK}ada.vcf"
_NAMESPACES = {"d": "DAV:", "card": "urn:ietf:params:xml:ns:carddav"}
_MODELS = (*MESSAGING_TEST_MODELS, *RECORD_SYNC_TEST_MODELS)
_DECLARED_FIELDS = {
    "display_name",
    "name_prefix",
    "given_name",
    "additional_name",
    "family_name",
    "name_suffix",
    "nickname",
    "notes",
    "organization",
    "title",
    "role",
    "birthday",
    "anniversary",
    "emails",
    "phones",
    "addresses",
    "photo",
}


def _card(*, uid: str = "ada", name: str = "Ada Lovelace", notes: str = "Original") -> str:
    return f"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:{uid}\r\nFN:{name}\r\nN:Lovelace;Ada;;;\r\nNOTE:{notes}\r\nEND:VCARD\r\n"


class FakeDav:
    """An in-memory DAV server with actual If-Match/If-None-Match semantics."""

    def __init__(self) -> None:
        self.version = 0
        self.cards: dict[str, tuple[str, str]] = {}
        self.events: list[tuple[int, str, bool]] = []
        self.invalid_tokens: set[str] = set()
        self.requests: list[tuple[str, str, dict[str, str], str]] = []
        self.store(_HREF, _card())

    @property
    def token(self) -> str:
        return f"https://dav.example/opaque-token/{self.version}"

    def store(self, href: str, card: str) -> str:
        self.version += 1
        etag = f'"resource-{self.version}"'
        self.cards[href] = (card, etag)
        self.events.append((self.version, href, False))
        return etag

    def remove(self, href: str) -> None:
        self.version += 1
        self.cards.pop(href)
        self.events.append((self.version, href, True))

    def close(self) -> None:
        """Match the shared client's lifecycle without owning an external socket."""

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        body = kwargs.get("body", b"")
        text = body.decode() if isinstance(body, bytes) else str(body)
        headers = {str(key).lower(): str(value) for key, value in kwargs.get("headers", {}).items()}
        self.requests.append((method, url, headers, text))
        assert not connection.in_atomic_block, "DAV transport must remain outside database transactions"
        if method == "GET":
            if url not in self.cards:
                return httpx.Response(404)
            card, etag = self.cards[url]
            return httpx.Response(200, content=card.encode(), headers={"ETag": etag})
        if method in {"PUT", "DELETE"}:
            current = self.cards.get(url)
            rejected = (headers.get("if-none-match") == "*" and current is not None) or (
                "if-match" in headers and (current is None or headers["if-match"] != current[1])
            )
            if rejected:
                return httpx.Response(412)
            if method == "DELETE":
                self.remove(url)
                return httpx.Response(204)
            assert headers.get("if-match") or headers.get("if-none-match") == "*"
            etag = self.store(url, text)
            return httpx.Response(204 if current else 201, headers={"ETag": etag})
        root = ElementTree.fromstring(text)
        if method == "REPORT":
            if root.tag == "{DAV:}sync-collection":
                token = root.findtext("d:sync-token", default="", namespaces=_NAMESPACES)
                if token in self.invalid_tokens:
                    return httpx.Response(403, content=b'<d:error xmlns:d="DAV:"><d:valid-sync-token/></d:error>')
                since = int(token.rsplit("/", 1)[1]) if token else 0
                changed = {href: removed for version, href, removed in self.events if version > since}
                responses = "".join(self._response(href, removed=removed) for href, removed in sorted(changed.items()))
                return self._multistatus(responses, token=True)
            assert root.tag == "{urn:ietf:params:xml:ns:carddav}addressbook-multiget"
            hrefs = [urljoin(url, item.text or "") for item in root.findall("d:href", _NAMESPACES)]
            return self._multistatus("".join(self._response(href, data=True) for href in hrefs))
        assert method == "PROPFIND"
        if root.find(".//d:current-user-principal", _NAMESPACES) is not None:
            return self._multistatus(
                self._property(
                    _BASE,
                    "<d:current-user-principal><d:href>/principal/</d:href></d:current-user-principal>",
                )
            )
        if root.find(".//card:addressbook-home-set", _NAMESPACES) is not None:
            return self._multistatus(
                self._property(
                    f"{_BASE}principal/",
                    "<card:addressbook-home-set><d:href>/books/</d:href></card:addressbook-home-set>",
                )
            )
        if url == f"{_BASE}books/":
            return self._multistatus(
                self._property(
                    _BOOK,
                    "<d:resourcetype><d:collection/><card:addressbook/></d:resourcetype>"
                    "<d:displayname>Contacts</d:displayname>",
                )
            )
        assert url == _BOOK
        collection = self._property(
            _BOOK,
            f"<d:resourcetype><d:collection/></d:resourcetype><d:sync-token>{self.token}</d:sync-token>",
        )
        members = "".join(self._response(href) for href in sorted(self.cards)) if headers.get("depth") == "1" else ""
        return self._multistatus(collection + members)

    def _response(self, href: str, *, removed: bool = False, data: bool = False) -> str:
        if removed or href not in self.cards:
            return (
                f"<d:response><d:href>{escape(href)}</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>"
            )
        card, etag = self.cards[href]
        props = f"<d:getetag>{escape(etag)}</d:getetag><d:getcontenttype>text/vcard</d:getcontenttype><d:resourcetype/>"
        if data:
            props += f"<card:address-data>{escape(card)}</card:address-data>"
        return self._property(href, props)

    @staticmethod
    def _property(href: str, props: str) -> str:
        return (
            f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>{props}</d:prop>"
            "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        )

    def _multistatus(self, responses: str, *, token: bool = False) -> httpx.Response:
        tail = f"<d:sync-token>{self.token}</d:sync-token>" if token else ""
        xml = (
            '<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
            f"{responses}{tail}</d:multistatus>"
        )
        return httpx.Response(207, content=xml.encode())


@dataclass
class Replica:
    directory: Any
    backend: CardDavDirectoryBackend
    server: FakeDav
    stream: Any

    def pull(self) -> Any:
        result = advance_stream(self.stream, self.backend, using="default")
        self.stream = result.stream
        return result

    def baseline(self) -> tuple[Any, Any]:
        result = self.pull()
        assert result.count == 1
        return Person.objects.get(source_uid="ada"), RecordLink.objects.get(stream=self.stream, external_key="ada")


@pytest.fixture
def replica(transactional_db: Any) -> Iterator[Replica]:
    """Create the complete existing model graph, preserving real ingest/cascades."""

    del transactional_db
    created = _create_missing_tables(_MODELS)
    try:
        with system_context(reason="test CardDAV replica"):
            directory = make_integration(
                "carddav-replica",
                model=Directory,
                backend_class="carddav",
                config={"server_url": _BASE},
            )
            RelationshipKind.objects.create(
                slug="employee",
                name="Employee",
                inverse_name="Employer",
                category="professional",
                other_party_kind="organization",
            )
            server = FakeDav()
            backend = CardDavDirectoryBackend(directory)
            backend.__dict__["http"] = server
            definitions = tuple(backend.streams(using="default"))
            assert len(definitions) == 1
            definition = definitions[0]
            assert (definition.key, definition.partition, definition.kind, definition.direction) == (
                "contacts",
                _BOOK,
                StreamKind.RECORD_REPLICA,
                StreamDirection.BIDIRECTIONAL,
            )
            stream = SyncStream.objects.current(directory, **asdict(definition), using="default")
            yield Replica(directory, backend, server, stream)
            backend.close()
    finally:
        _clear_model_tables(_MODELS)
        if created:
            with connection.schema_editor() as editor:
                for model in reversed(created):
                    editor.delete_model(model)


def test_new_remote_contact_uses_ingest_identity_and_both_bases(replica: Replica) -> None:
    person, link = replica.baseline()
    assert person.folder_id == Folder.objects.get(directory=replica.directory, source_href=_BOOK).pk
    assert link.target_id == str(person.pk)
    assert link.remote_version == replica.server.cards[_HREF][1]
    assert link.remote_base_hash and link.local_base_hash
    assert link.local_base_hash == canonical_json_sha256(contact_projection(Party.objects.project_contact(person)))
    assert link.origin == "remote"
    assert replica.stream.cursor == {"sync_token": replica.server.token}


def test_remote_edit_updates_same_party_through_adapter(replica: Replica) -> None:
    person, link = replica.baseline()
    replica.server.store(_HREF, _card(notes="Remote edit"))
    result = replica.pull()
    person.refresh_from_db()
    link.refresh_from_db()
    assert result.count == 1
    assert person.notes == "Remote edit"
    assert link.target_id == str(person.pk)
    assert link.remote_version == replica.server.cards[_HREF][1]
    assert not SyncDiscrepancy.objects.exists()


def test_local_edit_conditional_put_updates_version_and_both_bases(replica: Replica) -> None:
    person, link = replica.baseline()
    old_version, old_remote, old_local = link.remote_version, link.remote_base_hash, link.local_base_hash
    person.notes = "Local edit"
    person.save(update_fields=["notes"])
    result = push_stream(replica.stream, replica.backend, using="default")
    link.refresh_from_db()
    puts = [request for request in replica.server.requests if request[0] == "PUT"]
    assert result.count == len(puts) == 1
    assert puts[0][2]["if-match"] == old_version
    assert link.remote_version == replica.server.cards[_HREF][1] != old_version
    assert link.remote_base_hash != old_remote
    assert link.local_base_hash != old_local
    assert link.local_base_hash == canonical_json_sha256(contact_projection(Party.objects.project_contact(person)))
    raw, etag = replica.server.cards[_HREF]
    observed = _parse_vcard(vobject.readOne(raw), etag=etag, href=_HREF, raw=raw)
    assert link.remote_base_hash == canonical_json_sha256(contact_projection(observed))
    assert link.origin == "local"


@pytest.mark.parametrize("remote_deleted,local_deleted", [(False, False), (True, False), (False, True)])
def test_concurrent_vcard_changes_remain_conflicts(
    replica: Replica,
    remote_deleted: bool,
    local_deleted: bool,
) -> None:
    """Both edit, remote delete/local edit and local delete/remote edit have no winner."""

    person, link = replica.baseline()
    old_bases = link.remote_base_hash, link.local_base_hash, link.remote_version
    person_pk = person.pk
    if local_deleted:
        person.delete()
    else:
        person.notes = "Local edit"
        person.save(update_fields=["notes"])
    if remote_deleted:
        replica.server.remove(_HREF)
    else:
        replica.server.store(_HREF, _card(notes="Remote edit"))
    result = replica.pull()
    link.refresh_from_db()
    assert result.discrepancy_ids
    assert SyncDiscrepancy.objects.get(link=link).kind == DiscrepancyKind.CONFLICT
    assert link.status == LinkStatus.DISCREPANT
    assert (link.remote_base_hash, link.local_base_hash, link.remote_version) == old_bases
    assert not [request for request in replica.server.requests if request[0] in {"PUT", "DELETE"}]
    if local_deleted:
        assert not Party.objects.filter(pk=person_pk).exists()
    else:
        person.refresh_from_db()
        assert person.notes == "Local edit"


def test_put_etag_mismatch_records_conflict_without_local_overwrite(replica: Replica) -> None:
    person, link = replica.baseline()
    old_bases = link.remote_base_hash, link.local_base_hash, link.remote_version
    person.notes = "Keep local edit"
    person.save(update_fields=["notes"])
    replica.server.store(_HREF, _card(notes="Concurrent remote edit"))
    result = push_stream(replica.stream, replica.backend, using="default")
    person.refresh_from_db()
    link.refresh_from_db()
    assert result.count == 0
    assert result.discrepancy_ids
    assert SyncDiscrepancy.objects.get(link=link).kind == DiscrepancyKind.CONFLICT
    assert person.notes == "Keep local edit"
    assert (link.remote_base_hash, link.local_base_hash, link.remote_version) == old_bases
    assert "Concurrent remote edit" in replica.server.cards[_HREF][0]


@pytest.mark.parametrize("policy", ["retain", "propagate"])
def test_removed_member_tombstones_link_and_obeys_local_policy(replica: Replica, policy: str) -> None:
    person, link = replica.baseline()
    replica.stream.config = {**replica.stream.config, "remote_delete": policy}
    replica.stream.save(update_fields=["config"])
    replica.server.remove(_HREF)
    result = replica.pull()
    link.refresh_from_db()
    assert not result.discrepancy_ids
    assert link.status == LinkStatus.TOMBSTONE
    assert link.tombstoned_at is not None
    assert Party.objects.filter(pk=person.pk).exists() is (policy == "retain")
    assert push_stream(replica.stream, replica.backend, using="default").count == 0
    assert not [request for request in replica.server.requests if request[0] == "PUT"]


@pytest.mark.parametrize("policy", ["conflict", "propagate"])
def test_local_delete_requires_explicit_propagation(replica: Replica, policy: str) -> None:
    person, link = replica.baseline()
    replica.stream.config = {**replica.stream.config, "local_delete": policy}
    replica.stream.save(update_fields=["config"])
    person.delete()
    result = push_stream(replica.stream, replica.backend, using="default")
    link.refresh_from_db()
    if policy == "conflict":
        assert result.discrepancy_ids
        assert SyncDiscrepancy.objects.get(link=link).kind == DiscrepancyKind.CONFLICT
        assert _HREF in replica.server.cards
    else:
        assert not result.discrepancy_ids
        assert _HREF not in replica.server.cards
        assert link.status == LinkStatus.TOMBSTONE


def test_unchanged_token_does_not_ingest_put_or_change_cursor(
    replica: Replica,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person, link = replica.baseline()
    old_cursor = dict(replica.stream.cursor)
    old_updated = person.updated_at
    revision_count = RecordRevision.objects.filter(link=link).count()

    def unexpected_ingest(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        pytest.fail("An unchanged token must not ingest a contact")

    monkeypatch.setattr(type(Party.objects), "ingest_contact", unexpected_ingest)
    replica.server.requests.clear()
    result = replica.pull()
    pushed = push_stream(replica.stream, replica.backend, using="default")
    person.refresh_from_db()
    assert result.count == pushed.count == 0
    assert replica.stream.cursor == old_cursor
    assert person.updated_at == old_updated
    assert RecordRevision.objects.filter(link=link).count() == revision_count
    assert not [request for request in replica.server.requests if request[0] in {"PUT", "DELETE"}]
    assert not any("addressbook-multiget" in request[3] for request in replica.server.requests)


def test_invalid_sync_token_bumps_generation_and_repeats_baseline(replica: Replica) -> None:
    person, link = replica.baseline()
    old_generation = replica.stream.generation
    replica.server.invalid_tokens.add(replica.stream.cursor["sync_token"])
    replica.server.store(_HREF, _card(notes="Fresh baseline"))
    result = replica.pull()
    assert result.reset
    assert replica.stream.generation == old_generation + 1
    assert replica.stream.phase == StreamPhase.BASELINE
    assert replica.stream.cursor == {}
    replica.server.requests.clear()
    result = replica.pull()
    link.refresh_from_db()
    assert not result.reset
    assert link.pk == RecordLink.objects.get(stream=replica.stream, external_key="ada").pk
    assert link.target_id == str(person.pk)
    assert link.last_verified_generation == replica.stream.generation
    assert any(
        method == "PROPFIND" and headers.get("depth") == "1" for method, _, headers, _ in replica.server.requests
    )
    assert replica.stream.cursor == {"sync_token": replica.server.token}


def test_new_local_person_is_created_conditionally_then_recognized(replica: Replica) -> None:
    replica.baseline()
    folder = Folder.objects.get(directory=replica.directory, source_href=_BOOK)
    person = Person.objects.create(
        display_name="Grace Hopper",
        given_name="Grace",
        family_name="Hopper",
        folder=folder,
        created_by_id=replica.directory.owner_id,
    )
    result = push_stream(replica.stream, replica.backend, using="default")
    link = RecordLink.objects.get(stream=replica.stream, target_id=str(person.pk))
    puts = [request for request in replica.server.requests if request[0] == "PUT"]
    assert result.count == 1
    assert link.external_key == f"angee-{person.pk}"
    assert puts[-1][2]["if-none-match"] == "*"
    assert link.origin == "local"
    assert replica.pull().count == 0
    assert Person.objects.filter(pk=person.pk).count() == 1
    assert not SyncDiscrepancy.objects.exists()
    remote_href = puts[-1][1]
    replica.server.store(
        remote_href,
        replica.server.cards[remote_href][0].replace("Grace Hopper", "Grace M. Hopper"),
    )
    assert replica.pull().count == 1
    person.refresh_from_db()
    assert person.display_name == "Grace M. Hopper"
    assert RecordLink.objects.get(pk=link.pk).target_id == str(person.pk)
    assert Person.objects.count() == 2


def test_successful_own_write_is_applied_on_next_pull(replica: Replica) -> None:
    person, link = replica.baseline()
    person.notes = "Written here"
    person.save(update_fields=["notes"])
    assert push_stream(replica.stream, replica.backend, using="default").count == 1
    link.refresh_from_db()
    bases = link.remote_base_hash, link.local_base_hash, link.remote_version
    local_updated_at = person.updated_at
    result = replica.pull()
    link.refresh_from_db()
    assert result.count == 0
    assert link.origin == "local"
    assert (link.remote_base_hash, link.local_base_hash, link.remote_version) == bases
    person.refresh_from_db()
    assert person.updated_at == local_updated_at
    assert not SyncDiscrepancy.objects.exists()
    assert len([request for request in replica.server.requests if request[0] == "PUT"]) == 1


def test_contact_projection_is_deterministic_and_declares_exact_bridge_fields() -> None:
    first = ParsedContact(
        uid="one",
        href="https://dav.example/first.vcf",
        etag='"v1"',
        display_name="Ada",
        department="Unmapped department",
        emails=(("z@example.test", "work", False), ("a@example.test", "home", True)),
        addresses=(ParsedAddress(street="Second"), ParsedAddress(street="First")),
        birthday=date(1815, 12, 10),
        anniversary=date(1835, 1, 1),
        photo=ParsedPhoto(data=b"ABC", mime="image/png"),
        raw_vcard="unmapped wire fields",
    )
    reordered = replace(
        first,
        uid="other",
        href="https://dav.example/other.vcf",
        etag='"v2"',
        department="Another department",
        raw_vcard="different bytes",
        emails=tuple(reversed(first.emails)),
        addresses=tuple(reversed(first.addresses)),
    )
    projection = contact_projection(first)
    assert set(CONTACT_FIELDS) == set(projection) == _DECLARED_FIELDS
    assert projection["birthday"] == "1815-12-10"
    assert projection["anniversary"] == "1835-01-01"
    assert projection["photo"] == {"data": "QUJD", "mime": "image/png"}
    assert projection == contact_projection(reordered)
    assert projection == contact_projection(contact_from_projection(projection))
    assert canonical_json_sha256(projection) == canonical_json_sha256(contact_projection(reordered))


def test_bounded_baseline_commits_each_identity_and_replays_concurrent_edit(replica: Replica) -> None:
    """Pending hrefs keep the initial token until every baseline page commits."""

    grace_href = f"{_BOOK}grace.vcf"
    replica.server.store(grace_href, _card(uid="grace", name="Grace Hopper"))
    baseline_token = replica.server.token
    first = advance_stream(replica.stream, replica.backend, page_bound=1, using="default")
    assert first.count == 1
    assert not first.exhausted
    assert first.stream.cursor["sync_token"] == ""
    assert first.stream.cursor["next_token"] == baseline_token
    assert len(first.stream.cursor["pending"]) == 1
    ada = Person.objects.get(source_uid="ada")
    ada_link = RecordLink.objects.get(stream=first.stream, external_key="ada")

    replica.server.store(_HREF, _card(notes="Edited during the baseline"))
    second = advance_stream(first.stream, replica.backend, page_bound=1, using="default")
    assert second.count == 1
    assert second.exhausted
    assert second.stream.cursor == {"sync_token": baseline_token}
    assert set(RecordLink.objects.filter(stream=second.stream).values_list("external_key", flat=True)) == {
        "ada",
        "grace",
    }
    assert Person.objects.count() == 2

    delta = advance_stream(second.stream, replica.backend, page_bound=1, using="default")
    ada.refresh_from_db()
    assert delta.count == 1
    assert delta.exhausted
    assert ada.notes == "Edited during the baseline"
    assert RecordLink.objects.get(stream=delta.stream, external_key="ada").pk == ada_link.pk
    assert delta.stream.cursor == {"sync_token": replica.server.token}
    assert Person.objects.count() == 2


def test_malformed_vcard_is_quarantined_while_later_contact_commits(replica: Replica) -> None:
    """A record-local parse failure does not discard healthy records in its page."""

    replica.server.store(_HREF, "This is not a vCard")
    replica.server.store(f"{_BOOK}grace.vcf", _card(uid="grace", name="Grace Hopper"))
    result = replica.pull()
    assert result.exhausted
    assert result.count == 1
    assert result.discrepancy_ids
    discrepancy = SyncDiscrepancy.objects.get(stream=replica.stream)
    assert discrepancy.kind == DiscrepancyKind.SEMANTIC
    assert discrepancy.source_hash
    assert RecordLink.objects.get(pk=discrepancy.link_id).status == LinkStatus.DISCREPANT
    assert list(Person.objects.values_list("source_uid", flat=True)) == ["grace"]
    assert RecordLink.objects.get(stream=replica.stream, external_key="grace").status == LinkStatus.CURRENT
    assert replica.stream.cursor == {"sync_token": replica.server.token}


def test_corrected_first_seen_vcard_reuses_its_quarantined_identity(replica: Replica) -> None:
    """A newly readable UID cannot strand the original href-keyed quarantine."""

    replica.server.store(_HREF, "This is not a vCard")
    assert replica.pull().discrepancy_ids
    link = RecordLink.objects.get(stream=replica.stream)
    discrepancy = SyncDiscrepancy.objects.get(link=link)
    replica.server.store(_HREF, _card())
    assert replica.pull().count == 1
    link.refresh_from_db()
    discrepancy.refresh_from_db()
    assert RecordLink.objects.filter(stream=replica.stream).count() == 1
    assert Person.objects.get().source_uid == link.external_key == _HREF
    assert link.status == LinkStatus.CURRENT
    assert discrepancy.status == DiscrepancyStatus.RESOLVED


def test_conditional_put_preserves_unmapped_vcard_extensions_and_departments(replica: Replica) -> None:
    """Editing a mapped note preserves the original X property and trailing ORG units."""

    raw = _card().replace(
        "END:VCARD",
        "ORG:Analytical Engines;Research;Prototype\r\nX-ACME-CODE:legacy-value\r\nEND:VCARD",
    )
    replica.server.store(_HREF, raw)
    person, link = replica.baseline()
    prior_version = link.remote_version
    person.notes = "Changed locally"
    person.save(update_fields=["notes"])
    assert push_stream(replica.stream, replica.backend, using="default").count == 1

    written = vobject.readOne(replica.server.cards[_HREF][0])
    assert written.note.value == "Changed locally"
    assert written.org.value == ["Analytical Engines", "Research", "Prototype"]
    assert written.contents["x-acme-code"][0].value == "legacy-value"
    put = next(request for request in replica.server.requests if request[0] == "PUT")
    assert put[2]["if-match"] == prior_version
    assert not SyncDiscrepancy.objects.exists()


def test_propagated_local_delete_round_trip_keeps_local_tombstone(replica: Replica) -> None:
    """The server's echo of our DELETE cannot resurrect a link or repeat a write."""

    person, link = replica.baseline()
    replica.stream.config = {**replica.stream.config, "local_delete": "propagate"}
    replica.stream.save(update_fields=["config"])
    person.delete()
    assert push_stream(replica.stream, replica.backend, using="default").count == 1
    link.refresh_from_db()
    assert (link.status, link.origin) == (LinkStatus.TOMBSTONE, "local")
    assert _HREF not in replica.server.cards
    assert len([request for request in replica.server.requests if request[0] == "DELETE"]) == 1

    replica.server.requests.clear()
    result = replica.pull()
    assert result.count == 0
    assert push_stream(replica.stream, replica.backend, using="default").count == 0
    link.refresh_from_db()
    assert (link.status, link.origin) == (LinkStatus.TOMBSTONE, "local")
    assert link.tombstoned_at is not None
    assert not SyncDiscrepancy.objects.exists()
    assert not Party.objects.exists()
    assert not [request for request in replica.server.requests if request[0] in {"PUT", "DELETE"}]
