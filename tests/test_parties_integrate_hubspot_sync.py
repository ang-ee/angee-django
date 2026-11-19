"""HubSpot contacts through the real directory driver, onto real tables, against the scripted account.

The backend reads the scripted HubSpot (``HubSpotServer`` behind ``mock_transport``);
the driver opens, pages and sweeps the streams, and the parties ingest owners land
the people, handles and statements. Tests assert those outcomes and what HubSpot
was asked, never how the directory base builds its records.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rebac import system_context

from angee.integrate.errors import IntegrationError
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.states import LinkStatus, StreamDirection, StreamPhase
from angee.integrate.streams import BridgeSyncError, PageResult, advance_stream, open_stream, reconcile_stream
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.messaging.testing.models import Directory, Handle, Party, Person, RelationshipKind
from angee.parties.backends import CONTACTS, ParsedContact
from angee.parties_integrate_hubspot.backends import OVERLAP, HubSpotDirectoryBackend
from angee.parties_integrate_hubspot.client import EPOCH, HubSpotError
from angee.parties_integrate_hubspot.testing.server import HUB_ID, TOKEN, HubSpotServer, error_response, serve
from tests.conftest import Credential, make_integration
from tests.stream_adapters import applied_history

pytestmark = pytest.mark.django_db(transaction=True)

CONTACTS_READ = "crm.objects.contacts.read"
OTHER_KEY = "pat-na1-ffffffff-ffff-4fff-8fff-ffffffffffff"
SECOND_KEY = "pat-na1-11111111-1111-4111-8111-111111111111"
OTHER_HUB_ID = 9876543
CONNECTED = "Connected to HubSpot: the service key reads this account's contacts."
REJECTED = "HubSpot rejected the access token. Enter a valid HubSpot service key."
CHECK_THE_KEY = "This key lacks a service key's usual shape: check it for extra or missing characters."
MISSING_SCOPE = f"The HubSpot service key is missing a scope this connection needs: {CONTACTS_READ}."
UNREADABLE = "HubSpot returned an unreadable response."
UNAVAILABLE = "HubSpot is temporarily unavailable (HTTP 503)."
RATE_LIMITED = "HubSpot is rate limiting requests (HTTP 429)."
DAILY_LIMIT = "This HubSpot account has used its daily API request limit."
NOT_A_SERVICE_KEY = "A HubSpot directory reads with a HubSpot service key: attach a static token that holds one."
RECONNECT_THE_KEY = "This HubSpot service key must be reconnected: enter the key again."
UNANSWERED = "HubSpot did not answer for some records."
EMPTY_WALK = "HubSpot listed no contacts, but its search still counts some; retrying later."
IMPORTED = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
"""When the scripted contacts of a test that keeps HubSpot's time were created."""

ADA = {
    "firstname": "Ada",
    "lastname": "Example",
    "email": "ada@example.com",
    "phone": "+15555550100",
    "jobtitle": "Engineer",
    "company": "Example Engines",
}
GRACE = {
    "firstname": "Grace",
    "lastname": "Example",
    "email": "grace@example.com",
    "hs_additional_emails": "grace.work@example.com",
    "mobilephone": "+15555550101",
}
MARY = {"firstname": "Mary", "lastname": "Example", "email": "mary@example.com", "favourite_colour": "green"}
ALAN = {"firstname": "Alan", "lastname": "Example", "email": "alan@example.com"}
KATE = {"email": "kate@example.com"}


def ago(*, days: float = 0, minutes: float = 0) -> datetime:
    return timezone.now() - timedelta(days=days, minutes=minutes)


@dataclass
class HubSpot:
    directory: Any
    backend: HubSpotDirectoryBackend
    server: HubSpotServer
    stream: Any

    def add_people(self, count: int) -> list[str]:
        """Add ``count`` invented contacts with an email each, modified a minute apart a day ago."""

        return [
            self.server.add("contacts", {"email": f"person{number}@example.com"}, modified=ago(days=1, minutes=number))
            for number in range(count)
        ]

    def pull(self, *, page_bound: int = 100) -> PageResult:
        result = advance_stream(self.stream, self.backend, page_bound=page_bound)
        self.stream = result.stream
        return result

    def reopen(self) -> Any:
        """Open the contacts stream against the backend's declaration now, as each sync cycle does."""

        self.stream = open_stream(self.directory, CONTACTS, "contacts", self.backend)
        return self.stream

    def drain(self, *, page_bound: int = 100, between: Callable[[], None] | None = None) -> list[PageResult]:
        """Pull pages until the pass is done; ``between`` runs after each page."""

        pages = []
        for _ in range(60):
            pages.append(result := self.pull(page_bound=page_bound))
            if result.exhausted:
                return pages
            if between is not None:
                between()
        pytest.fail("The HubSpot pass did not finish")

    def sweep(self, *, page_bound: int = 1) -> int:
        """Make the absence sweep due, as its daily interval passing does, and run it to completion.

        Each pulse reads at most ``page_bound`` keys. Returns the absences the sweep observed.
        """

        SyncStream.objects.filter(pk=self.stream.pk).update(last_reconciled_at=None)
        absent = 0
        for _ in range(30):
            absent += reconcile_stream(self.stream, self.backend, page_bound=page_bound)
            self.stream.refresh_from_db()
            if not self.stream.reconcile_state:
                return absent
        pytest.fail("The absence sweep did not finish")

    def discover(self) -> dict[str, Any]:
        """Run the directory's connection discovery, as the discovery task does."""

        result = self.directory.run_discovery(
            credential_pk=self.directory.credential_id, generation=self.directory.discovery_generation
        )
        self.directory.refresh_from_db()
        return result

    def attach_key(self, key: str, *, name: str) -> Any:
        """Attach a new static token holding ``key`` to the directory, as an operator does."""

        credential = Credential.objects.create_local_credential(
            self.directory.owner, kind="static_token", name=name, material={"api_key": key}
        )
        self.directory.attach_connection(credential)
        return credential

    def link(self, key: str) -> Any:
        return RecordLink.objects.get(stream=self.stream, external_key=key)

    def person(self, key: str) -> Any:
        return Party.objects.contact_person(self.link(key))

    def contact(self, key: str) -> ParsedContact:
        return Party.objects.project_contact(self.person(key))

    def searches(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.server.requests if request.url.path.endswith("/search")]

    def listings(self) -> list[dict[str, str]]:
        return [
            {name: values[-1] for name, values in parse_qs(urlsplit(str(request.url)).query).items()}
            for request in self.server.requests
            if request.method == "GET" and request.url.path == "/crm/objects/2026-09/contacts"
        ]


@pytest.fixture
def hubspot(db: None) -> Iterator[HubSpot]:
    del db
    with system_context(reason="test HubSpot directory"), serve(HubSpotServer()) as server:
        directory = make_integration(
            "hubspot-directory", model=Directory, backend_class="hubspot", material={"api_key": TOKEN}
        )
        RelationshipKind.objects.create(
            slug="employee", name="Employee", inverse_name="Employer", category="professional",
            other_party_kind="organization",
        )
        backend = directory.backend
        assert isinstance(backend, HubSpotDirectoryBackend)
        (books,) = backend.streams()
        advance_stream(SyncStream.objects.current(directory, books), backend)
        (_, contacts) = backend.streams()
        assert (contacts.partition, contacts.direction) == ("contacts", StreamDirection.PULL)
        yield HubSpot(directory, backend, server, SyncStream.objects.current(directory, contacts))
        backend.close()


def asserted(party_id: Any, link: Any) -> Any:
    """The statements ``link`` currently asserts on the party."""

    records: Any = Party.statements
    statements = records.filter(subject_id=party_id, link=link).current()
    return statements.filter(status=records.model.StatementStatus.ASSERTED)


def link_snapshots() -> int:
    """Count the history snapshots saved of every link: each save of a link adds one."""

    history: Any = RecordLink.history
    return int(history.count())


def failures(hubspot: HubSpot) -> list[Any]:
    return [failure for link in RecordLink.objects.filter(stream=hubspot.stream) for failure in link.failures()]


def handle_values(person: Any) -> set[str]:
    return set(Handle.objects.filter(party_id=person.pk).values_list("value", flat=True))


def test_a_baseline_over_several_pages_lands_people_with_their_handles_and_a_second_run_changes_nothing(
    hubspot: HubSpot,
) -> None:
    ids = [
        hubspot.server.add("contacts", properties, modified=ago(days=days))
        for days, properties in zip((5, 4, 3, 2, 1), (ADA, GRACE, MARY, ALAN, KATE), strict=True)
    ]

    pages = hubspot.drain(page_bound=2)

    assert [page.count for page in pages] == [2, 2, 1]
    assert hubspot.stream.phase == StreamPhase.DELTA
    assert [listing["limit"] for listing in hubspot.listings()] == ["2", "2", "2"]
    ada, grace = hubspot.contact(ids[0]), hubspot.contact(ids[1])
    assert (ada.display_name, ada.title, ada.organization) == ("Ada Example", "Engineer", "Example Engines")
    assert {email for email, _label, _preferred in ada.emails} == {"ada@example.com"}
    assert {phone for phone, _label, _preferred in ada.phones} == {"+15555550100"}
    assert {email for email, _label, _preferred in grace.emails} == {"grace@example.com", "grace.work@example.com"}
    assert handle_values(hubspot.person(ids[1])) == {"grace@example.com", "grace.work@example.com", "+15555550101"}
    assert hubspot.contact(ids[4]).display_name == "kate@example.com"
    assert Person.objects.count() == 5
    assert all(hubspot.link(key).status == LinkStatus.CURRENT for key in ids)

    applications = applied_history().count()
    hubspot.server.requests.clear()
    assert [page.count for page in hubspot.drain(page_bound=2)] == [0]
    assert applied_history().count() == applications
    assert hubspot.listings() == []
    assert all("after" not in search for search in hubspot.searches())
    assert not failures(hubspot)


def test_a_delta_lands_hubspots_edits_including_one_made_between_its_pages(hubspot: HubSpot) -> None:
    ada, grace, mary = (
        hubspot.server.add("contacts", properties, modified=ago(days=days))
        for days, properties in zip((3, 2, 1), (ADA, GRACE, MARY), strict=True)
    )
    hubspot.drain()
    hubspot.server.requests.clear()
    hubspot.server.update("contacts", grace, {"lastname": "Hopper"}, modified=timezone.now())
    alan = hubspot.server.add("contacts", ALAN, modified=timezone.now())
    edited: list[str] = []

    def edit_grace_once_read() -> None:
        """Once the delta has landed Grace's first edit, HubSpot records a second one before the next page."""

        if not edited and hubspot.contact(grace).display_name == "Grace Hopper":
            hubspot.server.update("contacts", grace, {"email": "grace@navy.example"}, modified=timezone.now())
            edited.append(grace)

    pages = hubspot.drain(page_bound=1, between=edit_grace_once_read)

    assert edited
    assert sum(page.count for page in pages) == 3
    assert (hubspot.contact(grace).display_name, hubspot.contact(alan).display_name) == (
        "Grace Hopper",
        "Alan Example",
    )
    assert "grace@navy.example" in {email for email, _label, _preferred in hubspot.contact(grace).emails}
    assert hubspot.contact(ada).title == "Engineer"
    assert hubspot.contact(mary).display_name == "Mary Example"
    assert hubspot.listings() == []
    assert all("after" not in search and search["limit"] == 1 for search in hubspot.searches())
    assert [page.count for page in hubspot.drain()] == [0]
    assert not failures(hubspot)


def modified_after(search: dict[str, Any]) -> datetime:
    """Return the time a delta search reads changes strictly after."""

    (changed,) = (
        item
        for item in search["filterGroups"][0]["filters"]
        if (item["propertyName"], item["operator"]) == ("lastmodifieddate", "GT")
    )
    return EPOCH + timedelta(milliseconds=int(changed["value"]))


def test_a_quiet_accounts_polls_after_its_baseline_send_one_search_and_save_nothing(hubspot: HubSpot) -> None:
    """Contacts imported together and not changed since: no later poll reads them again."""

    for second, properties in enumerate((ADA, GRACE, MARY)):
        hubspot.server.add("contacts", properties, modified=IMPORTED + timedelta(seconds=second))
    hubspot.server.clock = IMPORTED + timedelta(days=1)
    assert sum(page.count for page in hubspot.drain(page_bound=2)) == 3
    snapshots, applications = link_snapshots(), applied_history().count()

    for _ in range(3):
        hubspot.server.clock += timedelta(minutes=5)
        hubspot.server.requests.clear()
        assert [page.count for page in hubspot.drain(page_bound=2)] == [0]
        assert [modified_after(search) for search in hubspot.searches()] == [IMPORTED + timedelta(seconds=2)]
        assert len(hubspot.server.requests) == 1

    assert (link_snapshots(), applied_history().count()) == (snapshots, applications)
    assert not failures(hubspot)


def test_a_burst_is_read_again_only_until_a_poll_starts_an_overlap_after_it(hubspot: HubSpot) -> None:
    """A burst of changes, then silence: polls re-read the window behind the newest change until one starts the
    overlap after it; that poll settles the window, and every later one searches strictly after it."""

    ada = hubspot.server.add("contacts", ADA, modified=IMPORTED)
    hubspot.server.clock = IMPORTED + timedelta(days=1)
    hubspot.drain()
    burst = hubspot.server.clock + timedelta(minutes=1)
    hubspot.server.update("contacts", ada, {"lastname": "Lovelace"}, modified=burst)
    newest = burst + timedelta(seconds=1)
    hubspot.server.add("contacts", GRACE, modified=newest)
    polls = []

    for minutes in (2, 4, 7, 9, 11):
        hubspot.server.clock = burst + timedelta(minutes=minutes)
        hubspot.server.requests.clear()
        landed = sum(page.count for page in hubspot.drain())
        polls.append((landed, [modified_after(search) for search in hubspot.searches()]))

    window = newest - OVERLAP
    assert polls == [(2, [IMPORTED]), (0, [window]), (0, [window]), (0, [newest]), (0, [newest])]
    assert hubspot.contact(ada).display_name == "Ada Lovelace"
    assert not failures(hubspot)


@pytest.mark.parametrize(
    "ahead", [pytest.param(timedelta(hours=1), id="local-clock-ahead"), pytest.param(-timedelta(hours=1), id="behind")]
)
def test_hubspots_clock_paces_every_pass_whatever_the_local_clock_says(hubspot: HubSpot, ahead: timedelta) -> None:
    """The local clock runs ``ahead`` of HubSpot's: HubSpot edits one contact the baseline already read and,
    nine minutes later, one it has not; the delta reads the first edit, and the window then settles."""

    hubspot.server.clock = start = (timezone.now() - ahead).replace(microsecond=0)
    ada, grace, mary = (
        hubspot.server.add("contacts", properties, modified=start - timedelta(days=1))
        for properties in (ADA, GRACE, MARY)
    )
    edits = iter(
        [(timedelta(minutes=1), ada, {"lastname": "Lovelace"}), (timedelta(minutes=9), mary, {"jobtitle": "Analyst"})]
    )

    def edit_during_the_baseline() -> None:
        wait, key, properties = next(edits)
        hubspot.server.clock += wait
        hubspot.server.update("contacts", key, properties, modified=hubspot.server.clock)

    assert sum(page.count for page in hubspot.drain(page_bound=1, between=edit_during_the_baseline)) == 3
    hubspot.server.clock = start + timedelta(minutes=20)
    hubspot.drain()

    assert hubspot.contact(ada).display_name == "Ada Lovelace"
    assert hubspot.contact(grace).display_name == "Grace Example"
    hubspot.server.clock += timedelta(minutes=5)
    hubspot.server.requests.clear()
    assert [page.count for page in hubspot.drain()] == [0]
    assert [modified_after(search) for search in hubspot.searches()] == [start + timedelta(minutes=10)]
    assert not failures(hubspot)


def test_a_persons_own_name_stands_when_hubspot_renames_them_afterwards(hubspot: HubSpot) -> None:
    ada = hubspot.server.add("contacts", ADA, modified=ago(days=1))
    hubspot.drain()
    person = hubspot.person(ada)
    party: Any = Party.objects.get(pk=person.pk).with_actor(hubspot.directory.owner)
    party.statements.set(aspect="identity", values={"display_name": "Ada (a friend)"})

    hubspot.server.update("contacts", ada, {"firstname": "Augusta"}, modified=timezone.now())
    assert [page.count for page in hubspot.drain()] == [1]

    person.refresh_from_db()
    link = hubspot.link(ada)
    assert (person.display_name, link.status) == ("Ada (a friend)", LinkStatus.CURRENT)
    assert link.applied_base.mapped_payload["display_name"] == "Augusta Example"
    assert [page.count for page in hubspot.drain()] == [0]
    assert not failures(hubspot)


@pytest.mark.parametrize("removal", ["archived", "deleted"])
def test_a_contact_hubspot_removes_is_withdrawn_by_the_second_sweep_and_the_person_stays(
    hubspot: HubSpot, removal: str
) -> None:
    ada, grace = (
        hubspot.server.add("contacts", properties, modified=ago(days=days))
        for days, properties in zip((2, 1), (ADA, GRACE), strict=True)
    )
    hubspot.drain()
    assert hubspot.sweep() == 0
    person, link = hubspot.person(grace), hubspot.link(grace)
    assert asserted(person.pk, link).exists()

    if removal == "archived":
        hubspot.server.archive("contacts", grace, at=timezone.now())
    else:
        hubspot.server.delete("contacts", grace)
    assert [page.count for page in hubspot.drain()] == [0]

    for status, stated in ((LinkStatus.UNAVAILABLE, True), (LinkStatus.TOMBSTONE, False)):
        assert hubspot.sweep() == 1
        link.refresh_from_db()
        assert (link.status, asserted(person.pk, link).exists()) == (status, stated)
    assert Person.objects.filter(pk=person.pk).exists()
    assert hubspot.link(ada).status == LinkStatus.CURRENT


def test_a_contact_an_answer_merely_omits_is_withdrawn_by_nothing_and_read_again(hubspot: HubSpot) -> None:
    """A batch read that leaves an id unanswered without naming it gone, on an identity read or a sweep's read."""

    ada, grace = (
        hubspot.server.add("contacts", properties, modified=ago(days=days))
        for days, properties in zip((2, 1), (ADA, GRACE), strict=True)
    )
    hubspot.drain()
    person = hubspot.person(ada)
    omitted = httpx2.Response(207, json={"status": "COMPLETE", "results": []})

    hubspot.server.queue(omitted)
    with pytest.raises(HubSpotError) as unanswered:
        hubspot.backend.read_keys(hubspot.stream, [ada])
    assert (unanswered.value.public_message, unanswered.value.transient) == (UNANSWERED, True)

    # The sweep's id walk is answered and its read of those ids is not: the pulse fails and nothing is absent.
    hubspot.server.queue(None, omitted)
    with pytest.raises(HubSpotError):
        hubspot.sweep()
    assert {hubspot.link(key).status for key in (ada, grace)} == {LinkStatus.CURRENT}
    assert asserted(person.pk, hubspot.link(ada)).exists()
    assert hubspot.sweep() == 0

    # HubSpot's own word, a not-found error naming the id, makes it gone.
    hubspot.server.delete("contacts", ada)
    (gone,) = hubspot.backend.read_keys(hubspot.stream, [ada])
    assert (gone.external_key, gone.tombstone) == (ada, True)


def test_an_empty_id_walk_is_believed_only_when_hubspots_search_counts_no_contact_either(hubspot: HubSpot) -> None:
    ada, grace = (
        hubspot.server.add("contacts", properties, modified=ago(days=days))
        for days, properties in zip((2, 1), (ADA, GRACE), strict=True)
    )
    hubspot.drain()
    hubspot.server.queue(httpx2.Response(200, json={"results": []}))
    hubspot.server.requests.clear()

    with pytest.raises(HubSpotError) as unconfirmed:
        hubspot.sweep()

    assert (unconfirmed.value.public_message, unconfirmed.value.transient) == (EMPTY_WALK, True)
    (counted,) = hubspot.searches()
    assert (counted["limit"], modified_after(counted)) == (1, EPOCH)
    assert [(hubspot.link(key).status, hubspot.link(key).absence_count) for key in (ada, grace)] == [
        (LinkStatus.CURRENT, 0)
    ] * 2

    for key in (ada, grace):
        hubspot.server.delete("contacts", key)
    assert hubspot.sweep() == 2
    assert {hubspot.link(key).status for key in (ada, grace)} == {LinkStatus.UNAVAILABLE}


def test_a_sweep_pulse_sends_one_list_request_per_page_of_ids(hubspot: HubSpot) -> None:
    hubspot.add_people(200)
    hubspot.drain(page_bound=200)
    hubspot.server.requests.clear()

    assert hubspot.sweep(page_bound=100) == 0

    # Two pulses of 100 ids read one full list page each; the third finds the walk's end.
    assert [listing["limit"] for listing in hubspot.listings()] == ["100", "100", "100"]
    assert not failures(hubspot)


def test_a_merged_contact_lands_on_the_person_of_a_record_it_replaced(hubspot: HubSpot) -> None:
    ada, ada_again, grace = (
        hubspot.server.add("contacts", properties, modified=ago(days=days))
        for days, properties in zip((3, 2, 1), (ADA, {**ADA, "email": "ada.lovelace@example.com"}, GRACE), strict=True)
    )
    hubspot.drain()
    people = Person.objects.count()
    person, again = hubspot.person(ada), hubspot.person(ada_again)

    survivor = hubspot.server.merge("contacts", ada, ada_again, modified=timezone.now())
    # A page of one record holds the survivor: the replaced records are no records of its page.
    assert sum(page.count for page in hubspot.drain(page_bound=1)) == 1

    assert hubspot.person(survivor).pk == person.pk
    assert Person.objects.count() == people
    assert Person.objects.filter(pk=again.pk).exists()
    assert asserted(person.pk, hubspot.link(survivor)).exists()
    assert not asserted(person.pk, hubspot.link(ada)).exists()
    assert {hubspot.link(ada).status, hubspot.link(ada_again).status} == {LinkStatus.CURRENT}
    (merged,) = hubspot.backend.read_keys(hubspot.stream, [ada])
    assert (merged.external_key, merged.tombstone) == (ada, True)

    # The replaced records left HubSpot's id walk, so the absence sweeps tombstone their links.
    assert [hubspot.sweep(), hubspot.sweep()] == [2, 2]
    assert {hubspot.link(ada).status, hubspot.link(ada_again).status} == {LinkStatus.TOMBSTONE}
    assert hubspot.person(survivor).pk == person.pk
    assert hubspot.link(grace).status == LinkStatus.CURRENT
    assert not failures(hubspot)


def test_a_page_stays_within_its_bound_hubspots_page_limit_and_the_deadline(hubspot: HubSpot) -> None:
    hubspot.add_people(101)

    page = hubspot.backend.extract_contacts(hubspot.stream, 101)
    assert (len(page.records), page.exhausted) == (101, True)
    assert [listing["limit"] for listing in hubspot.listings()] == ["100", "1"]

    hubspot.server.requests.clear()
    late = hubspot.backend.extract_contacts(hubspot.stream, 101, deadline=monotonic() - 1)
    assert (len(late.records), late.exhausted) == (100, False)
    assert len(hubspot.listings()) == 1


def test_a_transient_refusal_inside_one_extract_keeps_what_it_fetched_and_the_next_page_meets_it(
    hubspot: HubSpot,
) -> None:
    hubspot.add_people(101)
    limited = error_response(429, policy_name="TEN_SECONDLY_ROLLING")
    hubspot.server.queue(None, limited, limited)

    kept = hubspot.pull(page_bound=101)
    assert (kept.count, kept.exhausted) == (100, False)
    with pytest.raises(HubSpotError) as met:
        hubspot.pull(page_bound=101)
    assert (met.value.public_message, met.value.transient) == (RATE_LIMITED, True)

    assert [page.count for page in hubspot.drain(page_bound=101)] == [1]
    assert Person.objects.count() == 101
    assert [listing["limit"] for listing in hubspot.listings()] == ["100", "1", "100", "100"]
    assert not failures(hubspot)


def test_a_server_error_mid_pass_fails_its_page_and_the_pass_goes_on_from_where_it_was(hubspot: HubSpot) -> None:
    ids = hubspot.add_people(3)
    assert hubspot.pull(page_bound=1).count == 1
    cursor = hubspot.stream.cursor
    hubspot.server.queue(error_response(503))

    with pytest.raises(HubSpotError) as failed:
        hubspot.pull(page_bound=1)

    assert (failed.value.public_message, failed.value.transient) == (UNAVAILABLE, True)
    hubspot.stream.refresh_from_db()
    assert hubspot.stream.cursor == cursor
    assert sum(page.count for page in hubspot.drain(page_bound=1)) == 2
    assert Person.objects.count() == 3
    assert all(hubspot.link(key).status == LinkStatus.CURRENT for key in ids)


def test_one_unreadable_contact_is_quarantined_by_its_id_and_one_without_an_id_is_reported_and_the_page_lands(
    hubspot: HubSpot, caplog: pytest.LogCaptureFixture
) -> None:
    ada, grace, mary = (
        hubspot.server.add("contacts", properties, modified=ago(days=days))
        for days, properties in zip((3, 2, 1), (ADA, GRACE, MARY), strict=True)
    )
    stated = {key: record["properties"] for key, record in hubspot.server.records["contacts"].items()}
    listed = [
        {"id": ada, "properties": stated[ada]},
        {"id": grace, "properties": {**stated[grace], "mobilephone": 15555550101}},
        {"properties": {"email": "unkeyed@example.com"}},
        {"id": mary, "properties": stated[mary]},
    ]
    hubspot.server.queue(httpx2.Response(200, json={"results": listed}))

    with caplog.at_level("WARNING", logger="angee.parties_integrate_hubspot.backends"):
        assert [page.count for page in hubspot.drain()] == [2]

    assert (hubspot.contact(ada).display_name, hubspot.contact(mary).display_name) == ("Ada Example", "Mary Example")
    (failure,) = failures(hubspot)
    assert (failure, failure.code) == (*hubspot.link(grace).failures(), "unreadable_record")
    assert "1 contact without a usable record id" in caplog.text
    assert "unkeyed@example.com" not in caplog.text

    # Once HubSpot states the contact readably again, the next delta lands it.
    hubspot.server.update("contacts", grace, {"lastname": "Hopper"}, modified=timezone.now())
    assert [page.count for page in hubspot.drain()] == [1]
    assert hubspot.contact(grace).display_name == "Grace Hopper"


def test_an_unreadable_stored_cursor_starts_a_new_baseline(hubspot: HubSpot) -> None:
    ada = hubspot.server.add("contacts", ADA, modified=ago(days=1))
    hubspot.drain()
    person = hubspot.person(ada)
    SyncStream.objects.advance(hubspot.stream, {"started": "not a time"})

    reset = hubspot.pull()

    assert reset.reset
    assert hubspot.stream.phase == StreamPhase.BASELINE
    hubspot.drain()
    assert hubspot.stream.phase == StreamPhase.DELTA
    assert (hubspot.person(ada).pk, Person.objects.count()) == (person.pk, 1)
    assert hubspot.link(ada).status == LinkStatus.CURRENT


@pytest.mark.parametrize("phase", ["baseline", "drain", "search"])
def test_an_answer_with_no_records_that_names_a_following_page_is_refused(hubspot: HubSpot, phase: str) -> None:
    moment = ago(days=1)
    for properties in (ADA, GRACE, MARY):
        hubspot.server.add("contacts", properties, modified=moment)
    if phase != "baseline":
        hubspot.drain()
    if phase == "drain":
        # The delta's overlap reads the three records of one modified time again; two fill its first page.
        assert not hubspot.pull(page_bound=2).exhausted
    cursor = hubspot.stream.cursor
    hubspot.server.queue(httpx2.Response(200, json={"results": [], "paging": {"next": {"after": "205"}}}))

    with pytest.raises(HubSpotError) as refused:
        hubspot.pull(page_bound=2)

    assert (refused.value.public_message, refused.value.transient) == (UNREADABLE, False)
    hubspot.stream.refresh_from_db()
    assert hubspot.stream.cursor == cursor


def test_an_identity_read_answers_a_missing_contact_as_gone_and_fails_on_any_other_error(hubspot: HubSpot) -> None:
    ada = hubspot.server.add("contacts", ADA, modified=ago(days=1))
    hubspot.drain()

    (record, missing) = hubspot.backend.read_keys(hubspot.stream, [ada, "999"])
    assert (record.external_key, record.tombstone, record.mapped_payload["display_name"]) == (ada, False, "Ada Example")
    assert (missing.external_key, missing.tombstone) == ("999", True)

    hubspot.server.read_errors[ada] = "INTERNAL_ERROR"
    with pytest.raises(HubSpotError) as failed:
        hubspot.backend.read_keys(hubspot.stream, [ada])
    assert failed.value.transient


@pytest.mark.parametrize(
    ("refuse", "message", "transient", "wait"),
    [
        pytest.param(lambda server: setattr(server, "token", OTHER_KEY), REJECTED, False, None, id="401"),
        pytest.param(lambda server: setattr(server, "scopes", set()), MISSING_SCOPE, False, None, id="403-scope"),
        pytest.param(
            lambda server: server.queue(error_response(429, policy_name="TEN_SECONDLY_ROLLING")),
            RATE_LIMITED,
            True,
            timedelta(seconds=10),
            id="429-ten-secondly",
        ),
        pytest.param(
            lambda server: server.queue(error_response(429, policy_name="DAILY")),
            DAILY_LIMIT,
            True,
            timedelta(hours=1),
            id="429-daily",
        ),
        pytest.param(
            lambda server: server.queue(error_response(429, policy_name="A_POLICY_HUBSPOT_ADDS_LATER")),
            RATE_LIMITED,
            True,
            timedelta(seconds=10),
            id="429-unknown-policy",
        ),
    ],
)
def test_a_refusal_reaches_the_operator_and_the_contacts_wait_where_they_were(
    hubspot: HubSpot,
    refuse: Callable[[HubSpotServer], None],
    message: str,
    transient: bool,
    wait: timedelta | None,
) -> None:
    ada = hubspot.server.add("contacts", ADA, modified=ago(days=1))
    assert hubspot.discover()["ok"]
    assert hubspot.directory.run_sync(now=timezone.now()) == 1
    hubspot.stream.refresh_from_db()
    cursor, applications = hubspot.stream.cursor, applied_history().count()
    hubspot.server.update("contacts", ada, {"jobtitle": "Analyst"}, modified=timezone.now())

    refuse(hubspot.server)
    now = timezone.now()
    with pytest.raises(BridgeSyncError) as refused:
        hubspot.directory.run_sync(now=now)

    assert (refused.value.transient, refused.value.retry_after) == (transient, wait)
    hubspot.directory.refresh_from_db()
    assert hubspot.directory.sync_error == f"contacts (contacts): {message}"
    hubspot.stream.refresh_from_db()
    assert (hubspot.stream.cursor, applied_history().count()) == (cursor, applications)
    assert hubspot.contact(ada).title == "Engineer"
    if wait is not None and wait > timedelta(seconds=hubspot.directory.poll_interval):
        assert hubspot.directory.next_sync_at >= now + wait


def test_the_connection_test_reads_one_contact_and_tells_a_rejected_key_from_a_missing_scope(
    hubspot: HubSpot,
) -> None:
    hubspot.server.requests.clear()
    assert hubspot.directory.test_connection() == CONNECTED
    (request,) = hubspot.server.requests
    assert (request.method, request.url.path, request.url.params["limit"], request.content) == (
        "GET",
        "/crm/objects/2026-09/contacts",
        "1",
        b"",
    )

    hubspot.server.scopes = set()
    with pytest.raises(HubSpotError) as missing:
        hubspot.directory.test_connection()
    assert (missing.value.public_message, missing.value.transient) == (MISSING_SCOPE, False)

    hubspot.server.scopes, hubspot.server.token = {CONTACTS_READ}, OTHER_KEY
    with pytest.raises(HubSpotError) as rejected:
        hubspot.directory.test_connection()
    assert (rejected.value.public_message, rejected.value.transient) == (REJECTED, False)

    hubspot.server.token = TOKEN
    hubspot.server.queue(error_response(429, policy_name="TEN_SECONDLY_ROLLING"))
    with pytest.raises(HubSpotError) as throttled:
        hubspot.directory.test_connection()
    assert throttled.value.transient


def test_a_rejected_key_without_a_service_keys_usual_shape_says_to_check_it_and_the_shape_refuses_nothing(
    hubspot: HubSpot,
) -> None:
    """The key's shape only words HubSpot's rejection: the request is always sent, whatever the key looks like."""

    held = Credential.objects.get(pk=hubspot.directory.credential_id)
    held.replace_material({"api_key": f"{TOKEN}7"})
    hubspot.server.requests.clear()
    with pytest.raises(HubSpotError) as rejected:
        hubspot.directory.test_connection()
    assert rejected.value.public_message == f"{REJECTED} {CHECK_THE_KEY}"
    assert len(hubspot.server.requests) == 1
    assert hubspot.discover() == {"ok": False, "reason": "discovery-failed"}
    assert hubspot.directory.last_error == f"{REJECTED} {CHECK_THE_KEY}"

    hubspot.server.token = "a key of another shape"
    held.replace_material({"api_key": hubspot.server.token})
    assert hubspot.directory.test_connection() == CONNECTED


def test_a_credential_that_needs_reconnecting_is_neither_reported_connected_nor_admitted(hubspot: HubSpot) -> None:
    """The connection test keeps the base's check, on the credential it reloads, before it asks HubSpot anything."""

    held = Credential.objects.get(pk=hubspot.directory.credential_id)
    Credential.objects.filter(pk=held.pk).update(status="revoked")
    hubspot.server.requests.clear()

    with pytest.raises(IntegrationError) as refused:
        hubspot.directory.test_connection()

    assert (refused.value.public_message, hubspot.server.requests) == ("Reconnect this account.", [])
    held.refresh_from_db()
    assert hubspot.directory.credential_blocker(held) == RECONNECT_THE_KEY


def test_admission_judges_the_key_as_the_header_sends_it(hubspot: HubSpot) -> None:
    """Only an empty key, with nothing to send, is refused before HubSpot sees it; HubSpot judges any other."""

    owner = hubspot.directory.owner
    blank = Credential.objects.create_local_credential(
        owner, kind="static_token", name="A blank key", material={"api_key": "   "}
    )
    empty = Credential.objects.create_local_credential(owner, kind="static_token", name="No key", material={})

    assert (hubspot.directory.credential_blocker(empty), hubspot.directory.credential_blocker(blank)) == (
        NOT_A_SERVICE_KEY,
        None,
    )
    hubspot.directory.attach_connection(blank)
    hubspot.server.requests.clear()
    with pytest.raises(HubSpotError) as rejected:
        hubspot.directory.test_connection()
    assert rejected.value.public_message == f"{REJECTED} {CHECK_THE_KEY}"
    (sent,) = hubspot.server.requests
    assert sent.headers["authorization"] == "Bearer    "


def test_the_connection_test_reads_a_saved_directorys_key_afresh_and_an_unsaved_ones_as_attached(
    hubspot: HubSpot,
) -> None:
    assert hubspot.directory.test_connection() == CONNECTED
    Credential.objects.get(pk=hubspot.directory.credential_id).replace_material({"api_key": OTHER_KEY})

    with pytest.raises(HubSpotError) as rejected:
        hubspot.directory.test_connection()
    assert rejected.value.public_message == REJECTED

    owner = hubspot.directory.owner
    unsaved = Credential.objects.prepare_local_credential(
        owner, kind="static_token", name="A new service key", material={"api_key": TOKEN}
    )
    connecting = Directory(owner=owner, vendor=hubspot.directory.vendor, backend_class="hubspot", credential=unsaved)
    assert connecting.test_connection() == CONNECTED


def test_discovery_learns_the_account_a_contacts_source_link_opens(hubspot: HubSpot) -> None:
    ada = hubspot.server.add("contacts", ADA, modified=ago(days=1))
    hubspot.drain()
    assert hubspot.link(ada).source_url is None
    with pytest.raises(IntegrationError, match="discovery"):
        hubspot.directory.run_sync(now=timezone.now())
    hubspot.server.requests.clear()

    assert hubspot.discover() == {"ok": True, "reason": "applied"}

    assert [request.url.path for request in hubspot.server.requests] == ["/account-info/2026-09/details"]
    state = hubspot.directory.subscription_state
    assert (state["hub_id"], state["ui_domain"]) == (HUB_ID, "app.hubspot.com")
    link = hubspot.link(ada)
    assert link.source_url == f"https://app.hubspot.com/contacts/{HUB_ID}/record/0-1/{ada}"
    assert link.source_label == "Ada Example"
    book = RecordLink.objects.get(stream__key="addressbooks", stream__integration=hubspot.directory)
    assert (book.source_url, book.source_label) == (None, "contacts")
    assert hubspot.directory.discovery_ready


def test_discovery_without_a_web_host_still_learns_the_account(hubspot: HubSpot) -> None:
    ada = hubspot.server.add("contacts", ADA, modified=ago(days=1))
    hubspot.server.queue(httpx2.Response(200, json={"portalId": HUB_ID}))

    assert hubspot.discover() == {"ok": True, "reason": "applied"}

    assert hubspot.directory.subscription_state["hub_id"] == HUB_ID
    assert hubspot.directory.run_sync(now=timezone.now()) == 1
    assert hubspot.link(ada).source_url is None


def test_a_key_refused_the_account_call_for_a_scope_learns_its_hub_from_introspection(hubspot: HubSpot) -> None:
    """Introspection names the hub; the scopes it lists are never read, since HubSpot need not list each one."""

    ada = hubspot.server.add("contacts", ADA, modified=ago(days=1))
    hubspot.server.queue(
        error_response(403, category="MISSING_SCOPES"), httpx2.Response(200, json={"hubId": HUB_ID, "scopes": []})
    )

    assert hubspot.discover() == {"ok": True, "reason": "applied"}

    assert [request.url.path for request in hubspot.server.requests] == [
        "/account-info/2026-09/details",
        "/oauth/v2/private-apps/get/access-token-info",
    ]
    assert hubspot.directory.subscription_state["hub_id"] == HUB_ID
    assert hubspot.directory.run_sync(now=timezone.now()) == 1
    assert hubspot.link(ada).source_url is None


def test_a_discovery_that_fails_transiently_waits_and_then_succeeds(hubspot: HubSpot) -> None:
    ada = hubspot.server.add("contacts", ADA, modified=ago(days=1))
    hubspot.server.queue(error_response(503))

    assert hubspot.discover() == {"ok": False, "reason": "deferred"}
    assert hubspot.directory.discovery_pending and not hubspot.directory.discovery_ready
    with pytest.raises(IntegrationError, match="discovery"):
        hubspot.directory.run_sync(now=timezone.now())

    # The retry's wait passes, as the discovery task waits for it.
    Directory.objects.filter(pk=hubspot.directory.pk).update(discovery_retry_at=timezone.now())
    assert hubspot.discover() == {"ok": True, "reason": "applied"}

    assert hubspot.directory.discovery_ready
    assert hubspot.directory.run_sync(now=timezone.now()) == 1
    assert hubspot.contact(ada).display_name == "Ada Example"


def test_a_key_of_another_account_is_refused_and_the_directory_keeps_reading_its_own(hubspot: HubSpot) -> None:
    ada = hubspot.server.add("contacts", ADA, modified=ago(days=1))
    assert hubspot.discover()["ok"]
    assert hubspot.directory.run_sync(now=timezone.now()) == 1

    hubspot.server.token = SECOND_KEY
    hubspot.attach_key(SECOND_KEY, name="A second key of the account")
    assert hubspot.discover() == {"ok": True, "reason": "applied"}
    assert hubspot.directory.run_sync(now=timezone.now()) == 0
    hubspot.stream.refresh_from_db()
    stream, cursor = hubspot.stream.pk, hubspot.stream.cursor

    other = HubSpotServer()
    other.token, other.hub_id = OTHER_KEY, OTHER_HUB_ID
    other.add("contacts", GRACE, modified=timezone.now())
    with serve(other):
        hubspot.attach_key(OTHER_KEY, name="A key of another account")
        assert hubspot.discover() == {"ok": False, "reason": "discovery-failed"}
        with pytest.raises(IntegrationError, match="discovery"):
            hubspot.directory.run_sync(now=timezone.now())

    assert hubspot.directory.last_error == (
        f"This service key belongs to HubSpot account {OTHER_HUB_ID}, but this directory reads account {HUB_ID}. "
        f"Attach a key of account {HUB_ID}."
    )
    assert hubspot.directory.subscription_state["hub_id"] == HUB_ID
    assert other.requests[-1].url.path == "/account-info/2026-09/details"
    current = SyncStream.objects.get(pk=stream)
    assert (current.cursor, current.generation) == (cursor, hubspot.stream.generation)
    assert [link.external_key for link in RecordLink.objects.filter(stream=current)] == [ada]
    assert (Person.objects.count(), hubspot.link(ada).status) == (1, LinkStatus.CURRENT)


def test_a_rejected_key_stops_at_discovery_with_the_operator_message(hubspot: HubSpot) -> None:
    hubspot.server.token = OTHER_KEY

    assert hubspot.discover() == {"ok": False, "reason": "discovery-failed"}

    assert hubspot.directory.last_error == REJECTED
    assert not hubspot.directory.discovery_ready
    with pytest.raises(IntegrationError, match="discovery"):
        hubspot.directory.run_sync(now=timezone.now())


def test_a_hubspot_directory_admits_only_a_static_token_with_a_key_and_connects_through_no_oauth_client(
    hubspot: HubSpot,
) -> None:
    """The backend's one rule is the directory's blocker, which every attachment raises."""

    owner, held = hubspot.directory.owner, hubspot.directory.credential_id
    basic = Credential.objects.create_local_credential(
        owner, kind="basic_auth", name="Not a service key", material={"username": "ada", "password": "secret"}
    )
    empty = Credential.objects.create_local_credential(owner, kind="static_token", name="No key yet", material={})

    for refused in (basic, empty):
        assert hubspot.directory.credential_blocker(refused) == NOT_A_SERVICE_KEY
        with pytest.raises(ValidationError, match="static token"):
            hubspot.directory.attach_connection(refused)
        hubspot.directory.refresh_from_db()
        assert hubspot.directory.credential_id == held
    with pytest.raises(OAuthFlowError):
        hubspot.directory.connection_oauth_slug()

    key = hubspot.attach_key(TOKEN, name="Another service key")
    assert hubspot.directory.credential_blocker(key) is None
    assert hubspot.directory.credential_id == key.pk
    assert hubspot.discover()["ok"]
