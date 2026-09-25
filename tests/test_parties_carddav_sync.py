"""CardDAV replica contracts through the real adapter, driver and parties writers.

An in-memory DAV transport implements discovery, resource versions and opaque
collection tokens. Tests exercise the real HTTP client, observable conflicts and
persistence, without replacing the ingest owner or the three-way classifier.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from typing import Any
from urllib.parse import urljoin
from xml.sax.saxutils import escape

import httpcore
import httpx
import pytest
import vobject
from defusedxml.common import DefusedXmlException
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone
from rebac import system_context

from angee.base.serialization import canonical_json_sha256
from angee.integrate.http import HttpClient, PinnedTransport
from angee.integrate.states import (
    DiscrepancyKind,
    DiscrepancyStatus,
    LinkStatus,
    StreamDirection,
    StreamKind,
    StreamPhase,
)
from angee.integrate.streams import (
    CursorInvalid,
    RemoteRejected,
    advance_stream,
    begin_stream_cycle,
    push_stream,
    reconcile_stream,
    sync_bridge,
)
from angee.parties.backends import (
    CONTACT_FIELDS,
    ParsedAddress,
    ParsedContact,
    ParsedPhoto,
    contact_from_projection,
    contact_projection,
)
from angee.parties_integrate_carddav.backend import CardDavDirectoryBackend, CardDavError, _parse_vcard, _xml
from angee.storage.models import UploadState
from angee.testing.models import RecordLink, RecordRevision, SyncDiscrepancy, SyncStream
from tests.conftest import Backend, Drive, File, MimeType, make_integration
from tests.messaging_models import Directory, Folder
from tests.test_messaging import Party, Person, RelationshipKind

_BASE = "https://dav.example/"
_BOOK = f"{_BASE}books/contacts/"
_HREF = f"{_BOOK}ada.vcf"
_NAMESPACES = {"d": "DAV:", "card": "urn:ietf:params:xml:ns:carddav"}
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
        self.photos: dict[str, bytes] = {}
        self.private_access: list[bool] = []
        self.include_collection_response = False
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

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        method, url = request.method, str(request.url)
        text = request.content.decode()
        headers = dict(request.headers)
        self.requests.append((method, url, headers, text))
        assert not connection.in_atomic_block, "DAV transport must remain outside database transactions"
        if method == "GET":
            if url in self.photos:
                return httpx.Response(200, content=self.photos[url])
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
                if self.include_collection_response:
                    responses = self._property(_BOOK, "<d:resourcetype><d:collection/></d:resourcetype>") + responses
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
        result = advance_stream(self.stream, self.backend)
        self.stream = result.stream
        return result

    def baseline(self) -> tuple[Any, Any]:
        result = self.pull()
        assert result.count == 1
        return Person.objects.get(source_uid="ada"), RecordLink.objects.get(stream=self.stream, external_key="ada")

    def reconcile(self, *, page_bound: int = 100) -> int:
        absent = 0
        for _ in range(20):
            absent += reconcile_stream(self.stream, self.backend, page_bound=page_bound)
            self.stream.refresh_from_db()
            if not self.stream.reconcile_state:
                return absent
        pytest.fail("The CardDAV reconciliation checkpoint did not finish")


@pytest.fixture
def replica(transactional_db: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Replica]:
    """Create the complete existing model graph, preserving real ingest/cascades."""

    del transactional_db
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

        def transport(*, allow_private: bool) -> httpx.MockTransport:
            server.private_access.append(allow_private)
            return httpx.MockTransport(server.handle_request)

        monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(transport))
        backend = CardDavDirectoryBackend(directory)
        definitions = tuple(backend.streams())
        assert len(definitions) == 1
        definition = definitions[0]
        assert (definition.key, definition.partition, definition.kind, definition.direction) == (
            "contacts",
            _BOOK,
            StreamKind.RECORD_REPLICA,
            StreamDirection.BIDIRECTIONAL,
        )
        stream = SyncStream.objects.current(directory, **asdict(definition))
        yield Replica(directory, backend, server, stream)
        backend.close()


def test_new_remote_contact_uses_ingest_identity_and_both_bases(replica: Replica) -> None:
    person, link = replica.baseline()
    assert person.folder_id == Folder.objects.get(directory=replica.directory, source_href=_BOOK).pk
    assert link.target_id == str(person.pk)
    assert link.remote_version == replica.server.cards[_HREF][1]
    assert link.remote_base_hash and link.local_base_hash
    assert link.local_base_hash == canonical_json_sha256(contact_projection(Party.objects.project_contact(person)))
    assert link.origin == "remote"
    assert replica.stream.cursor == {"sync_token": replica.server.token}


@pytest.mark.parametrize("reset_before_baseline", [False, True])
def test_baseline_adopts_existing_contacts_across_pages_without_write_back(
    replica: Replica, reset_before_baseline: bool
) -> None:
    folder = Folder.objects.get(directory=replica.directory, source_href=_BOOK)
    existing = {
        uid: Person.objects.create(
            display_name=f"Local {uid}",
            notes="Pre-existing local notes",
            source_uid=uid,
            folder=folder,
            created_by_id=replica.directory.owner_id,
        )
        for uid in ("ada", "grace")
    }
    replica.server.store(f"{_BOOK}grace.vcf", _card(uid="grace", name="Grace Hopper", notes="Remote Grace"))
    if reset_before_baseline:
        replica.stream = SyncStream.objects.bump_generation(replica.stream)
    assert not RecordRevision.objects.exists()
    replica.server.requests.clear()

    for index, (uid, person) in enumerate(existing.items(), start=1):
        result = advance_stream(replica.stream, replica.backend, page_bound=1)
        replica.stream = result.stream

        assert result.count == 1
        assert not result.discrepancy_ids
        assert result.exhausted is (index == 2)
        assert replica.stream.phase == (StreamPhase.DELTA if result.exhausted else StreamPhase.BASELINE)
        person.refresh_from_db()
        href = f"{_BOOK}{uid}.vcf"
        raw, etag = replica.server.cards[href]
        remote = _parse_vcard(vobject.readOne(raw), etag=etag, href=href, raw=raw)
        projection = contact_projection(Party.objects.project_contact(person))
        assert projection == contact_projection(remote)
        link = RecordLink.objects.get(stream=replica.stream, external_key=uid)
        assert link.target_id == str(person.pk)
        assert link.status == LinkStatus.CURRENT
        assert link.origin == "remote"
        assert link.local_base_hash == canonical_json_sha256(projection)
        assert link.remote_base_hash == canonical_json_sha256(contact_projection(remote))
        assert link.remote_version == etag
        assert RecordRevision.objects.filter(link=link, applied_at__isnull=False).count() == 1
        assert RecordRevision.objects.count() == index

    assert Person.objects.count() == RecordLink.objects.count() == 2
    assert not SyncDiscrepancy.objects.exists()
    assert push_stream(replica.stream, replica.backend).count == 0
    assert not [request for request in replica.server.requests if request[0] in {"PUT", "DELETE"}]


@pytest.mark.parametrize("bounded", [False, True])
def test_local_only_contact_waits_until_first_baseline_completes(
    replica: Replica, bounded: bool
) -> None:
    folder = Folder.objects.get(directory=replica.directory, source_href=_BOOK)
    person = Person.objects.create(
        display_name="Grace Hopper",
        given_name="Grace",
        family_name="Hopper",
        source_uid="grace",
        folder=folder,
        created_by_id=replica.directory.owner_id,
    )
    replica.server.requests.clear()

    assert push_stream(replica.stream, replica.backend).count == 0
    assert not RecordLink.objects.exists()
    assert not replica.server.requests

    if bounded:
        replica.stream = advance_stream(replica.stream, replica.backend).stream
        assert set(replica.server.cards) == {_HREF}
        assert not [request for request in replica.server.requests if request[0] in {"PUT", "DELETE"}]
        assert push_stream(replica.stream, replica.backend).count == 1
    else:
        assert sync_bridge(replica.directory) == 2

    replica.stream.refresh_from_db()
    assert replica.stream.phase == StreamPhase.DELTA
    assert Person.objects.filter(pk=person.pk).exists()
    link = RecordLink.objects.get(stream=replica.stream, target_id=str(person.pk))
    puts = [request for request in replica.server.requests if request[0] == "PUT"]
    assert len(puts) == 1
    assert puts[0][2]["if-none-match"] == "*"
    assert link.external_key == "grace"
    assert link.remote_version == replica.server.cards[puts[0][1]][1]
    assert link.remote_base_hash and link.local_base_hash
    assert link.origin == "local"
    assert vobject.readOne(replica.server.cards[puts[0][1]][0]).fn.value == "Grace Hopper"
    assert not SyncDiscrepancy.objects.exists()
    replica.server.requests.clear()

    assert sync_bridge(replica.directory) == 0
    assert not [request for request in replica.server.requests if request[0] in {"PUT", "DELETE"}]


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
    result = push_stream(replica.stream, replica.backend)
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


@pytest.mark.parametrize("selected", ["linked", "named", "generated", "empty", "bound-alias"])
def test_selected_push_projects_only_requested_contacts(
    replica: Replica, monkeypatch: pytest.MonkeyPatch, selected: str
) -> None:
    person, link = replica.baseline()
    person.notes = "Local edit"
    person.save(update_fields=["notes"])
    named = Person.objects.create(
        display_name="Grace Hopper",
        source_uid="grace",
        folder_id=person.folder_id,
        created_by_id=replica.directory.owner_id,
    )
    generated = Person.objects.create(
        display_name="Margaret Hamilton",
        folder_id=person.folder_id,
        created_by_id=replica.directory.owner_id,
    )
    choices = {
        "linked": (link.external_key, person),
        "named": (named.source_uid, named),
        "generated": (f"angee-{generated.pk}", generated),
        "bound-alias": (named.source_uid, named),
    }
    if selected == "bound-alias":
        aliased = RecordLink.objects.observe(replica.stream, "remote-grace")
        RecordLink.objects.promote(
            aliased, source_payload={}, source_hash="base", mapped_payload={}, local_hash="base", target=named
        )
    keys = frozenset() if selected == "empty" else frozenset({choices[selected][0]})
    expected = set() if selected in {"empty", "bound-alias"} else {choices[selected][1].pk}
    projected = set()
    manager_class = type(Party.objects)
    project_contacts = manager_class.project_contacts

    def capture_projection(self: Any, people: Any) -> Any:
        people = tuple(people)
        projected.update(person.pk for person in people)
        return project_contacts(self, people)

    monkeypatch.setattr(manager_class, "project_contacts", capture_projection)
    replica.server.requests.clear()

    result = push_stream(replica.stream, replica.backend, external_keys=keys)

    assert projected == expected
    assert result.count == len(expected)
    assert len([request for request in replica.server.requests if request[0] == "PUT"]) == len(expected)


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
    result = push_stream(replica.stream, replica.backend)
    person.refresh_from_db()
    link.refresh_from_db()
    assert result.count == 0
    assert result.discrepancy_ids
    assert SyncDiscrepancy.objects.get(link=link).kind == DiscrepancyKind.CONFLICT
    assert person.notes == "Keep local edit"
    assert (link.remote_base_hash, link.local_base_hash, link.remote_version) == old_bases
    assert "Concurrent remote edit" in replica.server.cards[_HREF][0]


@pytest.mark.parametrize("keep", ["remote", "local"])
def test_etag_conflict_resolution_re_reads_before_keeping_a_side(
    replica: Replica, keep: str
) -> None:
    person, link = replica.baseline()
    original_version = link.remote_version
    person.notes = "Chosen local edit"
    person.save(update_fields=["notes"])
    remote_version = replica.server.store(_HREF, _card(notes="Concurrent remote edit"))
    assert push_stream(replica.stream, replica.backend).count == 0
    failed_put = [request for request in replica.server.requests if request[0] == "PUT"][-1]
    assert failed_put[2]["if-match"] == original_version
    discrepancy = SyncDiscrepancy.objects.get(link=link)
    replica.server.requests.clear()

    resolved = SyncDiscrepancy.objects.resolve_conflict(discrepancy, keep=keep)

    person.refresh_from_db()
    link.refresh_from_db()
    assert resolved.status == DiscrepancyStatus.RESOLVED
    assert not SyncDiscrepancy.objects.unresolved().filter(link=link).exists()
    puts = [request for request in replica.server.requests if request[0] == "PUT"]
    if keep == "local":
        assert person.notes == "Chosen local edit"
        assert "Chosen local edit" in replica.server.cards[_HREF][0]
        assert puts[0][2]["if-match"] == remote_version
    else:
        assert person.notes == "Concurrent remote edit"
        assert not puts
    assert link.remote_version == replica.server.cards[_HREF][1]
    assert push_stream(replica.stream, replica.backend).count == 0


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
    assert push_stream(replica.stream, replica.backend).count == 0
    assert not [request for request in replica.server.requests if request[0] == "PUT"]


@pytest.mark.parametrize("policy", ["conflict", "propagate"])
def test_local_delete_requires_explicit_propagation(replica: Replica, policy: str) -> None:
    person, link = replica.baseline()
    replica.stream.config = {**replica.stream.config, "local_delete": policy}
    replica.stream.save(update_fields=["config"])
    person.delete()
    result = push_stream(replica.stream, replica.backend)
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
    pushed = push_stream(replica.stream, replica.backend)
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
    result = push_stream(replica.stream, replica.backend)
    link = RecordLink.objects.get(stream=replica.stream, target_id=str(person.pk))
    puts = [request for request in replica.server.requests if request[0] == "PUT"]
    assert result.count == 1
    assert link.external_key == f"angee-{person.pk}"
    assert puts[-1][2]["if-none-match"] == "*"
    assert link.origin == "local"
    # A new outbound identity has its locator in the applied revision before a
    # pull can populate link metadata; both locator consumers must retain it.
    assert not link.metadata.get("href")
    assert set(replica.backend.enumerate_keys(replica.stream)) == {"ada", link.external_key}
    cursor = dict(replica.stream.cursor)
    requested = replica.backend.read_keys(replica.stream, (link.external_key,))
    assert [record.external_key for record in requested] == [link.external_key]
    assert requested[0].metadata["href"] == puts[-1][1]
    assert requested[0].remote_version == link.remote_version
    assert replica.stream.cursor == cursor
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
    assert push_stream(replica.stream, replica.backend).count == 1
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
    assert projection["photo"] == {"hash": hashlib.sha256(b"ABC").hexdigest(), "mime": "image/png"}
    assert projection == contact_projection(reordered)
    assert projection == contact_projection(contact_from_projection(projection))
    assert canonical_json_sha256(projection) == canonical_json_sha256(contact_projection(reordered))


def test_bounded_baseline_commits_each_identity_and_replays_concurrent_edit(replica: Replica) -> None:
    """Pending hrefs keep the initial token until every baseline page commits."""

    grace_href = f"{_BOOK}grace.vcf"
    replica.server.store(grace_href, _card(uid="grace", name="Grace Hopper"))
    baseline_token = replica.server.token
    first = advance_stream(replica.stream, replica.backend, page_bound=1)
    assert first.count == 1
    assert not first.exhausted
    assert first.stream.cursor["sync_token"] == ""
    assert first.stream.cursor["next_token"] == baseline_token
    assert len(first.stream.cursor["pending"]) == 1
    ada = Person.objects.get(source_uid="ada")
    ada_link = RecordLink.objects.get(stream=first.stream, external_key="ada")

    replica.server.store(_HREF, _card(notes="Edited during the baseline"))
    second = advance_stream(first.stream, replica.backend, page_bound=1)
    assert second.count == 1
    assert second.exhausted
    assert second.stream.cursor == {"sync_token": baseline_token}
    assert set(RecordLink.objects.filter(stream=second.stream).values_list("external_key", flat=True)) == {
        "ada",
        "grace",
    }
    assert Person.objects.count() == 2

    delta = advance_stream(second.stream, replica.backend, page_bound=1)
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
    assert push_stream(replica.stream, replica.backend).count == 1

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
    assert push_stream(replica.stream, replica.backend).count == 1
    link.refresh_from_db()
    assert (link.status, link.origin) == (LinkStatus.TOMBSTONE, "local")
    assert _HREF not in replica.server.cards
    assert len([request for request in replica.server.requests if request[0] == "DELETE"]) == 1

    replica.server.requests.clear()
    result = replica.pull()
    assert result.count == 0
    assert push_stream(replica.stream, replica.backend).count == 0
    link.refresh_from_db()
    assert (link.status, link.origin) == (LinkStatus.TOMBSTONE, "local")
    assert link.tombstoned_at is not None
    assert not SyncDiscrepancy.objects.exists()
    assert not Party.objects.exists()
    assert not [request for request in replica.server.requests if request[0] in {"PUT", "DELETE"}]


def test_remote_xml_rejects_entity_expansion_before_parsing_resources() -> None:
    payload = (
        b'<!DOCTYPE multistatus [<!ENTITY seed "expanded">'
        b'<!ENTITY repeated "&seed;&seed;&seed;&seed;">]>'
        b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>&repeated;</d:href></d:response></d:multistatus>'
    )
    with pytest.raises(DefusedXmlException):
        _xml(payload)


@pytest.mark.parametrize("uri", ["http://127.0.0.1/avatar.png", "https://photos.example/avatar.png"])
def test_photo_uri_outside_collection_origin_is_refused(replica: Replica, uri: str) -> None:
    replica.server.store(_HREF, _card().replace("END:VCARD", f"PHOTO;VALUE=URI:{uri}\r\nEND:VCARD"))
    with pytest.raises(CardDavError):
        replica.pull()
    assert not [request for request in replica.server.requests if request[0] == "GET"]
    assert not Person.objects.exists()
    replica.stream.refresh_from_db()
    assert replica.stream.cursor == {}


def test_same_origin_private_photo_is_refused_by_pinned_client(
    replica: Replica,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator-selected private DAV access must not authorize private photo reads."""

    def unexpected_socket(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        pytest.fail("A private photo address must be rejected before opening a socket")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", unexpected_socket)
    monkeypatch.setattr(
        "angee.integrate.http.resolved_addresses", lambda host, port: (ipaddress.ip_address("127.0.0.1"),)
    )
    monkeypatch.setattr(HttpClient, "transport_factory", PinnedTransport)
    contact = ParsedContact(photo=ParsedPhoto(uri="http://private.example/avatar.png", mime="image/png"))
    with pytest.raises(CardDavError) as rejected:
        replica.backend._resolve_photo(contact, collection="http://private.example/book/")
    assert isinstance(rejected.value.__cause__, ValidationError)


def test_photo_download_uses_shared_cap_and_disallows_private_addresses(replica: Replica) -> None:
    uri = f"{_BASE}avatar.png"
    replica.server.photos[uri] = b"ABC"
    contact = ParsedContact(photo=ParsedPhoto(uri=uri, mime="image/png"))
    resolved = replica.backend._resolve_photo(contact, collection=_BOOK)
    assert resolved.photo is not None
    assert resolved.photo.data == b"ABC"
    assert [request[1] for request in replica.server.requests if request[0] == "GET"] == [uri]
    assert replica.server.private_access[-1] is False

    replica.server.photos[uri] = b"x" * (5 * 1024 * 1024 + 1)
    with pytest.raises(CardDavError):
        replica.backend._resolve_photo(contact, collection=_BOOK)


def test_extract_prestores_avatar_and_apply_and_local_scan_do_no_storage_io(
    replica: Replica,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The storage-owner double observes intake before apply; only hashes enter revisions."""

    backend = Backend.objects.create(slug="avatar-double", label="Avatar double", backend_class="local")
    drive = Drive.objects.create(backend=backend, slug="assets", name="Assets")
    mime = MimeType.objects.create(mime_type="image/png", category="image", label="PNG")
    digest = hashlib.sha256(b"ABC").hexdigest()
    phases: list[str] = []

    def ingest_bytes(
        manager: Any,
        content: bytes,
        *,
        filename: str,
        owner_id: Any = None,
        **kwargs: Any,
    ) -> Any:
        del kwargs
        assert not connection.in_atomic_block
        assert content == b"ABC"
        phases.append("extract")
        stored, _ = manager.get_or_create(
            drive=drive,
            content_hash=hashlib.sha256(content).hexdigest(),
            defaults={
                "filename": filename,
                "size_bytes": len(content),
                "mime_type": mime,
                "storage_path": f"avatars/{digest}",
                "upload_state": UploadState.READY,
                "created_by_id": owner_id,
            },
        )
        return stored

    def unexpected_open(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        pytest.fail("Apply and local comparison must not open avatar storage")

    original_apply = replica.backend.apply_record

    def apply_after_intake(stream: Any, record: Any) -> Any:
        assert connection.in_atomic_block
        assert phases == ["extract"]
        assert File.objects.get(content_hash=digest).upload_state == UploadState.READY
        assert record.source_payload["contact"]["photo"] == {"hash": digest, "mime": "image/png"}
        phases.append("apply")
        return original_apply(stream, record)

    monkeypatch.setattr(type(File.objects), "ingest_bytes", ingest_bytes)
    monkeypatch.setattr(File, "open_stream", unexpected_open)
    monkeypatch.setattr(replica.backend, "apply_record", apply_after_intake)
    replica.server.store(_HREF, _card().replace("END:VCARD", "PHOTO;ENCODING=b;TYPE=PNG:QUJD\r\nEND:VCARD"))
    person, link = replica.baseline()
    stored = File.objects.get(content_hash=digest)
    assert phases == ["extract", "apply"]
    assert person.avatar_id == stored.pk
    assert tuple(replica.backend.local_changes(replica.stream)) == ()
    assert phases == ["extract", "apply"]
    revisions = list(RecordRevision.objects.filter(link=link))
    assert revisions
    for revision in revisions:
        assert revision.source_payload["contact"]["photo"] == {"hash": digest, "mime": "image/png"}
        assert revision.mapped_payload["photo"] == {"hash": digest, "mime": "image/png"}
        assert "QUJD" not in json.dumps(revision.source_payload)
        assert "QUJD" not in json.dumps(revision.mapped_payload)


@pytest.mark.parametrize("page_bound", [1, 100])
def test_duplicate_uid_quarantines_one_resource_and_commits_the_rest(replica: Replica, page_bound: int) -> None:
    duplicate_href = f"{_BOOK}duplicate.vcf"
    replica.server.store(duplicate_href, _card(uid="ada", name="Impostor"))
    replica.server.store(f"{_BOOK}grace.vcf", _card(uid="grace", name="Grace Hopper"))
    landed = 0
    for _ in range(3):
        result = advance_stream(replica.stream, replica.backend, page_bound=page_bound)
        replica.stream = result.stream
        landed += result.count
        if result.exhausted:
            break
    assert result.exhausted
    assert landed == 2
    assert set(Person.objects.values_list("source_uid", flat=True)) == {"ada", "grace"}
    discrepancy = SyncDiscrepancy.objects.get(stream=replica.stream)
    link = RecordLink.objects.get(pk=discrepancy.link_id)
    assert discrepancy.kind == DiscrepancyKind.SEMANTIC
    assert link.status == LinkStatus.DISCREPANT
    assert link.external_key == link.metadata["href"] == duplicate_href
    assert replica.stream.cursor == {"sync_token": replica.server.token}


def test_uidless_cards_with_same_name_use_distinct_resource_hrefs(replica: Replica) -> None:
    other_href = f"{_BOOK}other.vcf"
    raw = _card().replace("UID:ada\r\n", "")
    replica.server.store(_HREF, raw)
    replica.server.store(other_href, raw)
    result = replica.pull()
    assert result.count == 2
    assert not result.discrepancy_ids
    assert set(Person.objects.values_list("source_uid", flat=True)) == {_HREF, other_href}
    assert set(RecordLink.objects.values_list("external_key", flat=True)) == {_HREF, other_href}


@pytest.mark.parametrize("sweep", [False, True])
def test_uid_matching_uidless_resource_href_quarantines_without_rebinding_owner(replica: Replica, sweep: bool) -> None:
    collision_href = f"{_BOOK}duplicate.vcf"
    replica.server.store(_HREF, _card(uid=collision_href))
    assert replica.pull().count == 1
    original = Person.objects.get(source_uid=collision_href)
    owner = RecordLink.objects.get(stream=replica.stream, external_key=collision_href)
    bases = owner.remote_base_hash, owner.local_base_hash, owner.remote_version
    old_cursor = dict(replica.stream.cursor)
    replica.server.store(collision_href, _card(name="Impostor").replace("UID:ada\r\n", ""))
    replica.server.store(f"{_BOOK}grace.vcf", _card(uid="grace", name="Grace Hopper"))

    if sweep:
        assert replica.reconcile(page_bound=1) == 0
    else:
        result = replica.pull()
        assert result.exhausted
        assert result.count == 1
    discrepancy = SyncDiscrepancy.objects.get(stream=replica.stream)
    refused = RecordLink.objects.get(pk=discrepancy.link_id)
    assert discrepancy.kind == DiscrepancyKind.SEMANTIC
    assert refused.status == LinkStatus.DISCREPANT
    assert refused.metadata["href"] == collision_href
    assert refused.external_key != owner.external_key
    assert refused.target_id is None
    owner.refresh_from_db()
    original.refresh_from_db()
    assert owner.metadata["href"] == _HREF
    assert owner.target_id == str(original.pk)
    assert (owner.remote_base_hash, owner.local_base_hash, owner.remote_version) == bases
    assert original.display_name == "Ada Lovelace"
    assert set(Person.objects.values_list("display_name", flat=True)) == {"Ada Lovelace", "Grace Hopper"}
    assert replica.stream.cursor == (old_cursor if sweep else {"sync_token": replica.server.token})


def test_due_read_keys_redrives_only_discrepant_hrefs_without_advancing_cursor(replica: Replica) -> None:
    replica.server.store(f"{_BOOK}grace.vcf", _card(uid="grace", name="Grace Hopper"))
    assert replica.pull().count == 2
    replica.server.store(_HREF, "Not a vCard")
    assert replica.pull().discrepancy_ids
    discrepancy = SyncDiscrepancy.objects.get(stream=replica.stream)
    link = RecordLink.objects.get(pk=discrepancy.link_id)
    assert link.external_key == "ada"
    replica.server.store(_HREF, _card(notes="Fixed without a baseline"))
    SyncDiscrepancy.objects.filter(pk=discrepancy.pk).update(retry_at=timezone.now() - timedelta(seconds=1))
    old_cursor = dict(replica.stream.cursor)
    old_generation, old_advanced_at = replica.stream.generation, replica.stream.last_advanced_at
    replica.server.requests.clear()

    replica.stream = begin_stream_cycle(replica.stream, replica.backend)

    discrepancy.refresh_from_db()
    link.refresh_from_db()
    assert discrepancy.status == DiscrepancyStatus.RESOLVED
    assert link.status == LinkStatus.CURRENT
    assert Person.objects.get(source_uid="ada").notes == "Fixed without a baseline"
    assert replica.stream.cursor == old_cursor
    assert replica.stream.generation == old_generation
    assert replica.stream.last_advanced_at == old_advanced_at
    assert not replica.stream.resync_required
    assert len(replica.server.requests) == 1
    method, collection, _, body = replica.server.requests[0]
    assert (method, collection) == ("REPORT", _BOOK)
    report = ElementTree.fromstring(body)
    assert report.tag == "{urn:ietf:params:xml:ns:carddav}addressbook-multiget"
    assert [item.text for item in report.findall("d:href", _NAMESPACES)] == [_HREF]


@pytest.mark.parametrize("linked", [False, True])
def test_read_keys_returns_tombstone_for_missing_unbound_href(replica: Replica, linked: bool) -> None:
    missing_href = f"{_BOOK}missing.vcf"
    if linked:
        RecordLink.objects.observe(replica.stream, missing_href)

    records = replica.backend.read_keys(replica.stream, [missing_href])

    assert len(records) == 1
    assert records[0].external_key == missing_href
    assert records[0].tombstone
    assert records[0].metadata["href"] == missing_href
    assert RecordLink.objects.exists() is linked


def test_enumeration_sweep_reads_present_href_and_marks_vanished_unavailable(replica: Replica) -> None:
    replica.server.store(f"{_BOOK}grace.vcf", _card(uid="grace", name="Grace Hopper"))
    assert replica.pull().count == 2
    person = Person.objects.get(source_uid="ada")
    replica.server.remove(_HREF)
    old_cursor = dict(replica.stream.cursor)
    replica.server.requests.clear()
    assert replica.stream.reconcile_interval == timedelta(days=1)

    assert replica.reconcile() == 1

    link = RecordLink.objects.get(stream=replica.stream, external_key="ada")
    assert (link.status, link.absence_count, link.tombstoned_at) == (LinkStatus.UNAVAILABLE, 1, None)
    assert RecordLink.objects.get(stream=replica.stream, external_key="grace").status == LinkStatus.CURRENT
    assert Person.objects.filter(pk=person.pk).exists()
    replica.stream.refresh_from_db()
    assert replica.stream.cursor == old_cursor
    assert len(replica.server.requests) == 3
    for index in (0, 2):
        method, collection, headers, body = replica.server.requests[index]
        assert (method, collection, headers["depth"]) == ("PROPFIND", _BOOK, "1")
        assert ElementTree.fromstring(body).find(".//d:getetag", _NAMESPACES) is not None
    method, collection, _, body = replica.server.requests[1]
    assert (method, collection) == ("REPORT", _BOOK)
    report = ElementTree.fromstring(body)
    assert report.tag == "{urn:ietf:params:xml:ns:carddav}addressbook-multiget"
    assert [item.text for item in report.findall("d:href", _NAMESPACES)] == [f"{_BOOK}grace.vcf"]


def test_enumeration_sweep_imports_unlinked_contact_once_across_later_delta(replica: Replica) -> None:
    replica.baseline()
    old_cursor = dict(replica.stream.cursor)
    href = f"{_BOOK}grace.vcf"
    replica.server.store(href, _card(uid="grace", name="Grace Hopper"))
    assert not RecordLink.objects.filter(stream=replica.stream, metadata__href=href).exists()

    assert replica.reconcile(page_bound=1) == 0

    link = RecordLink.objects.get(stream=replica.stream, metadata__href=href)
    person = Person.objects.get(pk=link.target_id)
    assert person.display_name == "Grace Hopper"
    assert link.metadata["uid"] == "grace"
    assert link.status == LinkStatus.CURRENT
    assert link.remote_base_hash and link.local_base_hash
    assert replica.stream.cursor == old_cursor
    assert RecordRevision.objects.filter(link=link).count() == 1
    assert replica.pull().count == 0
    link.refresh_from_db()
    assert link.target_id == str(person.pk)
    assert Person.objects.count() == RecordLink.objects.count() == RecordRevision.objects.count() == 2
    assert not SyncDiscrepancy.objects.exists()


@pytest.mark.parametrize("filename", ["0-ada.vcf", "z-ada.vcf"])
def test_sweep_imported_identity_survives_remote_href_relocation(replica: Replica, filename: str) -> None:
    assert replica.reconcile() == 0
    link = RecordLink.objects.get(stream=replica.stream, metadata__href=_HREF)
    person = Person.objects.get(pk=link.target_id)
    assert link.external_key == _HREF
    assert replica.pull().count == 0
    moved_href = f"{_BOOK}{filename}"
    replica.server.remove(_HREF)
    replica.server.store(moved_href, _card())

    result = replica.pull()

    assert result.exhausted
    assert not result.discrepancy_ids
    link.refresh_from_db()
    assert link.external_key == _HREF
    assert link.target_id == str(person.pk)
    assert link.metadata["href"] == moved_href
    assert link.metadata["uid"] == "ada"
    assert link.status == LinkStatus.CURRENT
    assert Person.objects.count() == RecordLink.objects.count() == 1
    assert replica.stream.cursor == {"sync_token": replica.server.token}
    remote_version = replica.server.cards[moved_href][1]
    person.notes = "Edited after relocation"
    person.save(update_fields=["notes"])
    replica.server.requests.clear()

    assert push_stream(replica.stream, replica.backend).count == 1

    puts = [request for request in replica.server.requests if request[0] == "PUT"]
    assert len(puts) == 1
    assert puts[0][1] == moved_href
    assert puts[0][2]["if-match"] == remote_version
    assert vobject.readOne(replica.server.cards[moved_href][0]).uid.value == "ada"
    link.refresh_from_db()
    assert link.remote_version == replica.server.cards[moved_href][1]
    assert Person.objects.count() == RecordLink.objects.count() == 1


def test_enumeration_sweep_resumes_checkpoint_after_apply_crash(
    replica: Replica, monkeypatch: pytest.MonkeyPatch
) -> None:
    second_href, unseen_href = f"{_BOOK}beta.vcf", f"{_BOOK}gamma.vcf"
    replica.server.store(_HREF, _card(uid="zulu"))
    replica.server.store(second_href, _card(uid="alpha", name="Grace Hopper"))
    assert replica.pull().count == 2
    old_cursor = dict(replica.stream.cursor)
    replica.server.store(_HREF, _card(uid="zulu", notes="First committed page"))
    replica.server.store(second_href, _card(uid="alpha", name="Grace Hopper", notes="Second page"))
    replica.server.store(unseen_href, _card(uid="beta", name="Katherine Johnson"))
    assert reconcile_stream(replica.stream, replica.backend, page_bound=1) == 0
    replica.stream.refresh_from_db()
    committed_cursor = dict(replica.stream.cursor)
    committed_reconcile_state = dict(replica.stream.reconcile_state)
    assert committed_reconcile_state["after"] == "zulu"
    assert Person.objects.get(source_uid="zulu").notes == "First committed page"
    original_apply = replica.backend.apply_record

    def crash_after_apply(stream: Any, record: Any) -> Any:
        outcome = original_apply(stream, record)
        assert record.external_key == "alpha"
        assert Person.objects.get(source_uid="alpha").notes == "Second page"
        assert outcome
        raise RuntimeError("Interrupted CardDAV sweep apply")

    monkeypatch.setattr(replica.backend, "apply_record", crash_after_apply)
    with pytest.raises(RuntimeError, match="Interrupted CardDAV sweep apply"):
        reconcile_stream(replica.stream, replica.backend, page_bound=1)
    replica.stream.refresh_from_db()
    assert replica.stream.cursor == committed_cursor
    assert replica.stream.reconcile_state == committed_reconcile_state
    assert Person.objects.get(source_uid="alpha").notes == "Original"
    assert Person.objects.count() == 2
    assert RecordRevision.objects.count() == 3

    replica.backend = CardDavDirectoryBackend(replica.directory)
    replica.stream = SyncStream.objects.get(pk=replica.stream.pk)
    replica.server.requests.clear()
    assert replica.reconcile(page_bound=1) == 0
    assert replica.stream.cursor == old_cursor
    assert replica.stream.last_reconciled_at is not None
    assert Person.objects.get(source_uid="alpha").notes == "Second page"
    assert Person.objects.count() == RecordLink.objects.count() == 3
    assert RecordRevision.objects.count() == 5
    fetched = [
        [item.text for item in ElementTree.fromstring(body).findall("d:href", _NAMESPACES)]
        for method, _, _, body in replica.server.requests
        if method == "REPORT"
    ]
    assert fetched == [[second_href], [unseen_href]]
    assert not SyncDiscrepancy.objects.exists()


def test_generation_bump_deepcopies_nested_config(replica: Replica, monkeypatch: pytest.MonkeyPatch) -> None:
    replica.stream.config = {"policy": {"fields": ["notes"]}}
    replica.stream.save(update_fields=["config"])
    previous: list[Any] = []
    original_from_db = SyncStream.from_db

    def capture_previous(cls: Any, db: str, field_names: Any, values: Any) -> Any:
        del cls
        row = original_from_db(db, field_names, values)
        if row.pk == replica.stream.pk:
            previous.append(row)
        return row

    monkeypatch.setattr(SyncStream, "from_db", classmethod(capture_previous))
    successor = SyncStream.objects.bump_generation(replica.stream)
    assert previous
    assert successor.config == replica.stream.config
    successor.config["policy"]["fields"].append("photo")
    assert all(row.config == {"policy": {"fields": ["notes"]}} for row in previous)
    replica.stream.refresh_from_db()
    assert replica.stream.config == {"policy": {"fields": ["notes"]}}


@pytest.mark.parametrize("url", ["file:///tmp/addressbook", "http://169.254.169.254/addressbook/"])
def test_request_preserves_url_gate_validation_error(
    replica: Replica, monkeypatch: pytest.MonkeyPatch, url: str,
) -> None:
    monkeypatch.setattr(HttpClient, "transport_factory", PinnedTransport)
    with pytest.raises(ValidationError):
        replica.backend._request("PROPFIND", url, "<propfind/>")


def test_cross_origin_redirect_refuses_to_forward_basic_auth(
    replica: Replica,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.example/collect"})

    monkeypatch.setattr(replica.server, "handle_request", redirect)
    monkeypatch.setattr(replica.backend, "_auth", lambda: {"Authorization": "Basic dXNlcjpwYXNz"})
    with pytest.raises(CardDavError, match="request origin"):
        replica.backend._request("PROPFIND", _BOOK, "<propfind/>")
    assert len(sent) == 1
    assert str(sent[0].url) == _BOOK
    assert sent[0].headers["authorization"] == "Basic dXNlcjpwYXNz"


@pytest.mark.parametrize("status", [207, 412, 404])
def test_same_origin_redirect_retains_dav_request_and_failure_translation(
    replica: Replica, monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    sent: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(302, headers={"Location": "/relocated/"}) if len(sent) == 1 else httpx.Response(status)

    monkeypatch.setattr(replica.server, "handle_request", redirect)
    headers = {"If-Match": '"version"'}
    if status == 207:
        response = replica.backend._request("REPORT", _BOOK, "<sync/>", depth="1", headers=headers, cursor_request=True)
        assert response.status_code == 207
    else:
        with pytest.raises(RemoteRejected if status == 412 else CursorInvalid):
            replica.backend._request("REPORT", _BOOK, "<sync/>", depth="1", headers=headers, cursor_request=True)
    assert [str(request.url) for request in sent] == [_BOOK, f"{_BASE}relocated/"]
    assert all(request.method == "REPORT" and request.content == b"<sync/>" for request in sent)
    assert all(request.headers["Depth"] == "1" and request.headers["If-Match"] == '"version"' for request in sent)


def test_sync_report_skips_successful_collection_self_response(replica: Replica) -> None:
    person, _ = replica.baseline()
    replica.server.include_collection_response = True
    replica.server.store(_HREF, _card(notes="Remote edit with collection response"))
    replica.server.requests.clear()
    assert replica.pull().count == 1
    person.refresh_from_db()
    assert person.notes == "Remote edit with collection response"
    multigets = [body for method, _, _, body in replica.server.requests if method == "REPORT" and "multiget" in body]
    assert len(multigets) == 1
    assert [item.text for item in ElementTree.fromstring(multigets[0]).findall("d:href", _NAMESPACES)] == [_HREF]


def test_extract_multigets_the_complete_bounded_page_once(replica: Replica) -> None:
    hrefs = [_HREF, *(f"{_BOOK}contact-{index}.vcf" for index in range(4))]
    for index, href in enumerate(hrefs[1:]):
        replica.server.store(href, _card(uid=f"contact-{index}"))
    replica.server.requests.clear()
    assert replica.pull().count == len(hrefs)
    multigets = [body for method, _, _, body in replica.server.requests if method == "REPORT" and "multiget" in body]
    assert len(multigets) == 1
    assert {item.text for item in ElementTree.fromstring(multigets[0]).findall("d:href", _NAMESPACES)} == set(hrefs)
