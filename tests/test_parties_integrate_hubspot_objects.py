"""Other HubSpot objects as plain records a consumer's mapping lands, one stream per mapping, through the real driver.

The test mappings below read deals and notes of the scripted account (``HubSpotServer``)
and state what they read on the person a contact landed on, through the record's own
link, in the test person's colour group (``PersonFavourites``). What deals and notes
mean is a consumer's business; these mappings give them none. The harness is
``test_parties_integrate_hubspot_sync``'s: an object stream's harness is the directory's
with that stream in place of the contacts stream.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from django.utils import timezone
from rebac import to_subject_ref

from angee.integrate.states import LinkStatus, StreamPhase, SyncFailureKind
from angee.integrate.streams import begin_stream_cycle, open_stream
from angee.integrate.testing.integration import Integration
from angee.messaging.testing.models import Person
from angee.parties.backends import MAPPING_REFUSED
from angee.parties_integrate_hubspot.backends import (
    OBJECTS,
    ContactNotLanded,
    HubSpotObjectMapping,
    HubSpotObjectRecord,
)
from angee.parties_integrate_hubspot.checks import check_object_mappings
from angee.parties_integrate_hubspot.client import parse_number, parse_string
from tests.test_parties_integrate_hubspot_sync import ADA, GRACE, HubSpot, ago, failures
from tests.test_parties_integrate_hubspot_sync import hubspot as hubspot  # noqa: F401 -- the scripted directory

pytestmark = pytest.mark.django_db(transaction=True)

DEALS_READ = "crm.objects.deals.read"
API = "https://api.hubapi.com"
MOMENT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

RECEIVED: list[tuple[HubSpotObjectRecord, str]] = []
"""Each record a test mapping's ``apply_record`` was handed, with the source its link asserts as."""
GONE: list[str] = []
"""The id of each record a test mapping's gone hook was called for."""


class DealMapping(HubSpotObjectMapping):
    """States a deal's name as its first contact's favourite colour; a deal without a contact states nothing."""

    key = "example_deals"
    object_type = "deals"
    properties = ("dealname", "dealstage")
    contacts = True

    def apply_record(self, record: HubSpotObjectRecord, *, link: Any) -> Any:
        RECEIVED.append((record, str(link.asserting_source())))
        if not record.contacts:
            return None
        person = record.person(record.contacts[0])
        if person is None:
            raise ContactNotLanded(record.contacts[0])
        person.statements_for("colour").set(
            aspect="colour",
            values={"favourite_colour": parse_string(record.properties.get("dealname"))},
            asserted_by=link.asserting_source(),
            link=link,
        )
        return None

    def withdraw_record(self, link: Any) -> None:
        GONE.append(link.external_key)
        super().withdraw_record(link)


class RevisedDealMapping(DealMapping):
    """The same mapping at its next version."""

    version = 2


class NoteMapping(HubSpotObjectMapping):
    """Reads notes' bodies and no contacts, and only records what it was handed."""

    key = "example_notes"
    object_type = "notes"
    properties = ("hs_note_body",)

    def apply_record(self, record: HubSpotObjectRecord, *, link: Any) -> Any:
        RECEIVED.append((record, str(link.asserting_source())))
        return None


class AmountMapping(HubSpotObjectMapping):
    """Reads a deal's amount as a number, which ``parse_number`` refuses with ``ValueError`` when it is none."""

    key = "example_deal_amounts"
    object_type = "deals"
    properties = ("amount",)

    def apply_record(self, record: HubSpotObjectRecord, *, link: Any) -> Any:
        parse_number(record.properties.get("amount"))
        RECEIVED.append((record, str(link.asserting_source())))
        return None


class ContactRecordsMapping(NoteMapping):
    """Names contacts, which a directory reads itself: refused at startup."""

    key = "example_contact_records"
    object_type = "contacts"


@pytest.fixture(autouse=True)
def _received() -> Iterator[None]:
    RECEIVED.clear()
    GONE.clear()
    yield
    RECEIVED.clear()
    GONE.clear()


def _register(settings: Any, *mappings: type[HubSpotObjectMapping]) -> None:
    """Register ``mappings`` as consumer addons do, each under its key."""

    settings.ANGEE_HUBSPOT_OBJECT_MAPPING_CLASSES = {
        mapping.key: f"{mapping.__module__}.{mapping.__qualname__}" for mapping in mappings
    }


def _objects(hubspot: HubSpot, mapping: type[HubSpotObjectMapping]) -> HubSpot:
    """The directory's harness on ``mapping``'s stream, opened against the backend's declaration now."""

    return replace(hubspot, stream=open_stream(hubspot.directory, OBJECTS, mapping.key, hubspot.backend))


def _colour(hubspot: HubSpot, contact: str) -> str:
    return str(Person.objects.get(pk=hubspot.link(contact).target_object_id).favourite_colour)


def _stated(hubspot: HubSpot, contact: str) -> Any:
    """The colour statements currently asserted on the contact's person."""

    rows = Person.statements.filter(subject_id=hubspot.link(contact).target_object_id, aspect="colour")
    return rows.filter(status=Person.statements.model.StatementStatus.ASSERTED).current()


def _landed_deal(hubspot: HubSpot, settings: Any, **properties: str) -> tuple[HubSpot, str, str]:
    """Land Ada's contact, then one deal associated with her through :class:`DealMapping`."""

    _register(settings, DealMapping)
    hubspot.server.scopes.add(DEALS_READ)
    ada = hubspot.server.add("contacts", ADA, modified=ago(days=2))
    hubspot.drain()
    deal = hubspot.server.add("deals", {"dealname": "green", **properties}, modified=ago(days=1))
    hubspot.server.associate("deals", deal, "contacts", ada)
    deals = _objects(hubspot, DealMapping)
    assert [page.count for page in deals.drain()] == [1]
    return deals, ada, deal


def test_with_no_object_mapping_a_directory_declares_and_reads_only_its_contacts(hubspot: HubSpot) -> None:
    """The byte-for-byte pin of the contacts requests is the mapping module's ``[no-mapping]`` case."""

    hubspot.server.add("contacts", ADA, modified=ago(days=1))

    assert [(item.key, item.partition) for item in hubspot.backend.streams()] == [
        ("addressbooks", ""),
        ("contacts", "contacts"),
    ]
    assert hubspot.discover()["ok"]
    hubspot.server.requests.clear()
    assert hubspot.directory.run_sync(now=timezone.now()) == 1
    assert hubspot.server.requests
    assert all(request.url.path.startswith("/crm/objects/2026-09/contacts") for request in hubspot.server.requests)


def test_a_record_lands_through_apply_record_with_exactly_its_mapped_properties_and_contact_ids(
    hubspot: HubSpot, settings: Any
) -> None:
    _register(settings, DealMapping)
    hubspot.server.scopes.add(DEALS_READ)
    ada, grace = (
        hubspot.server.add("contacts", properties, modified=ago(days=days))
        for days, properties in zip((3, 2), (ADA, GRACE), strict=True)
    )
    hubspot.drain()
    deal = hubspot.server.add(
        "deals", {"dealname": "green", "dealstage": "won", "amount": "12"}, modified=ago(days=1)
    )
    for contact in (grace, ada):
        hubspot.server.associate("deals", deal, "contacts", contact)
    deals = _objects(hubspot, DealMapping)

    assert [page.count for page in deals.drain()] == [1]

    ((record, _source),) = RECEIVED
    assert (record.id, dict(record.properties), record.contacts) == (
        deal,
        {"dealname": "green", "dealstage": "won"},
        (ada, grace),
    )
    link = deals.link(deal)
    assert (link.status, link.source_payload) == (
        LinkStatus.CURRENT,
        {"properties": {"dealname": "green", "dealstage": "won"}, "contacts": [ada, grace]},
    )
    assert (_colour(hubspot, ada), _colour(hubspot, grace)) == ("green", "")
    (statement,) = _stated(hubspot, ada)
    assert statement.link_id == link.pk
    assert not failures(deals)


def test_a_mapping_states_as_the_integration_itself_through_the_records_link(hubspot: HubSpot, settings: Any) -> None:
    """The streams this addon builds hand a mapping a link whose asserting source is the integration row.

    Never the directory child: a link of a stream built by hand on the child would name it, and other
    addons refuse that source.
    """

    deals, ada, deal = _landed_deal(hubspot, settings)

    integration = to_subject_ref(Integration._base_manager.get(pk=hubspot.directory.pk))
    ((_record, source),) = RECEIVED
    (statement,) = _stated(hubspot, ada)
    assert integration.subject_type == "integrate/integration"
    assert (source, deals.link(deal).asserted_by, statement.asserted_by) == (str(integration),) * 3


def test_a_changed_record_replays_and_a_change_to_a_property_it_does_not_map_applies_nothing(
    hubspot: HubSpot, settings: Any
) -> None:
    deals, ada, deal = _landed_deal(hubspot, settings, amount="12")
    RECEIVED.clear()

    hubspot.server.update("deals", deal, {"amount": "15"}, modified=timezone.now())
    assert [page.count for page in deals.drain()] == [0]
    assert RECEIVED == []

    hubspot.server.update("deals", deal, {"dealname": "violet"}, modified=timezone.now())
    assert [page.count for page in deals.drain()] == [1]
    ((record, _source),) = RECEIVED
    assert (record.properties["dealname"], _colour(hubspot, ada)) == ("violet", "violet")
    assert [page.count for page in deals.drain()] == [0]


def test_a_new_version_of_the_mapping_replays_every_record_once(hubspot: HubSpot, settings: Any) -> None:
    deals, ada, deal = _landed_deal(hubspot, settings)
    other = hubspot.server.add("deals", {"dealname": "blue"}, modified=ago(days=1))
    assert [page.count for page in deals.drain()] == [1]
    RECEIVED.clear()

    _register(settings, RevisedDealMapping)
    revised = _objects(hubspot, RevisedDealMapping)

    assert (revised.stream.generation, revised.stream.phase) == (deals.stream.generation + 1, StreamPhase.BASELINE)
    assert sum(page.count for page in revised.drain()) == 2
    assert sorted(record.id for record, _source in RECEIVED) == sorted((deal, other))
    assert [page.count for page in revised.drain()] == [0]
    assert _colour(hubspot, ada) == "green"


def test_a_deleted_record_reaches_the_gone_hook_on_the_second_sweep_and_what_it_stated_is_withdrawn(
    hubspot: HubSpot, settings: Any
) -> None:
    """The default gone hook withdraws the link's statements; the value stays, unsupported, as survivorship keeps it."""

    deals, ada, deal = _landed_deal(hubspot, settings)
    kept = hubspot.server.add("deals", {"dealname": "blue"}, modified=ago(days=1))
    assert [page.count for page in deals.drain()] == [1]
    assert deals.sweep() == 0

    hubspot.server.delete("deals", deal)
    assert [page.count for page in deals.drain()] == [0]

    assert deals.sweep() == 1
    assert (deals.link(deal).status, GONE, _stated(hubspot, ada).exists()) == (LinkStatus.UNAVAILABLE, [], True)
    assert deals.sweep() == 1
    assert (deals.link(deal).status, GONE, _stated(hubspot, ada).exists()) == (LinkStatus.TOMBSTONE, [deal], False)
    assert deals.link(kept).status == LinkStatus.CURRENT


def test_a_record_whose_contact_has_not_landed_waits_and_lands_once_it_has(hubspot: HubSpot, settings: Any) -> None:
    """Nothing orders the contacts stream before others: the first sync drains the deals first.

    The deal waits on the driver's missing-dependency refusal, kept under its id and read again on
    the stream's backoff; the rescan lands it once its contact has.
    """

    _register(settings, DealMapping)
    hubspot.server.scopes.add(DEALS_READ)
    ada = hubspot.server.add("contacts", ADA, modified=ago(days=2))
    deal = hubspot.server.add("deals", {"dealname": "green"}, modified=ago(days=1))
    hubspot.server.associate("deals", deal, "contacts", ada)
    assert hubspot.discover()["ok"]

    hubspot.directory.run_sync(now=timezone.now())

    deals = _objects(hubspot, DealMapping)
    link = deals.link(deal)
    (waiting,) = link.failures()
    assert (waiting.kind, waiting.code, waiting.details["contact"], link.status) == (
        SyncFailureKind.MISSING_DEPENDENCY,
        "contact_not_landed",
        ada,
        LinkStatus.FAILED,
    )
    assert waiting.retry_at is not None and waiting.retry_at > timezone.now()
    assert _colour(hubspot, ada) == ""

    # Before its backoff passes, the stream's rescan leaves it waiting.
    begin_stream_cycle(deals.stream, hubspot.backend)
    assert deals.link(deal).status == LinkStatus.FAILED

    link.retry_failure(waiting.slot)
    begin_stream_cycle(deals.stream, hubspot.backend)

    assert (deals.link(deal).status, _colour(hubspot, ada)) == (LinkStatus.CURRENT, "green")
    assert not deals.link(deal).failures(open_only=True)


def test_a_waiting_record_hubspot_deletes_reaches_the_gone_hook_on_its_retry(hubspot: HubSpot, settings: Any) -> None:
    """A read of the record answers it gone on HubSpot's word: the gone hook runs at once, no sweep needed."""

    _register(settings, DealMapping)
    hubspot.server.scopes.add(DEALS_READ)
    ada = hubspot.server.add("contacts", ADA, modified=ago(days=2))
    deal = hubspot.server.add("deals", {"dealname": "green"}, modified=ago(days=1))
    hubspot.server.associate("deals", deal, "contacts", ada)
    deals = _objects(hubspot, DealMapping)
    assert [page.count for page in deals.drain()] == [0]
    (waiting,) = deals.link(deal).failures()

    hubspot.server.delete("deals", deal)
    deals.link(deal).retry_failure(waiting.slot)
    begin_stream_cycle(deals.stream, hubspot.backend)

    assert (GONE, deals.link(deal).status) == ([deal], LinkStatus.TOMBSTONE)
    assert not deals.link(deal).failures(open_only=True)


def _epoch_ms(moment: datetime) -> str:
    return str((moment - EPOCH) // timedelta(milliseconds=1))


def _json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


@pytest.mark.parametrize(
    ("mapping", "names", "associated"),
    [
        pytest.param(DealMapping, ["dealname", "dealstage"], True, id="deals-with-their-contacts"),
        pytest.param(NoteMapping, ["hs_note_body"], False, id="notes"),
    ],
)
def test_every_read_asks_for_exactly_the_mapped_properties_and_the_associations_it_names(
    hubspot: HubSpot,
    settings: Any,
    mapping: type[HubSpotObjectMapping],
    names: list[str],
    associated: bool,
) -> None:
    """A list, a search, a drain of one modified time and a batch read, on the type's own modified property.

    The modified property comes back with every list and search, which pace the pass; a batch read asks
    for the merged ids, which make an id gone on HubSpot's word. A mapping that names no contacts sends
    no association request.
    """

    _register(settings, mapping)
    hubspot.server.scopes.add(DEALS_READ)
    kind = mapping.object_type
    first, second, third = (hubspot.server.add(kind, {names[0]: "text"}, modified=MOMENT) for _ in range(3))
    objects = _objects(hubspot, mapping)

    objects.drain(page_bound=2)
    # The delta's overlap finds all three at one modified time: two fill a page, so the block drains by id.
    assert [page.exhausted for page in objects.drain(page_bound=2)] == [False, False, True]
    hubspot.backend.read_keys(objects.stream, [first])

    records = f"{API}/crm/objects/2026-09/{kind}"
    listed = "%2C".join([*names, "hs_lastmodifieddate"])
    at, since = _epoch_ms(MOMENT), _epoch_ms(MOMENT - timedelta(minutes=5))
    modified_after = {"propertyName": "hs_lastmodifieddate", "operator": "GT", "value": since}
    modified_at = {"propertyName": "hs_lastmodifieddate", "operator": "EQ", "value": at}
    above_second = {"propertyName": "hs_object_id", "operator": "GT", "value": second}
    after_block = {"propertyName": "hs_lastmodifieddate", "operator": "GT", "value": at}

    def search(filters: list[dict[str, str]], sort: str, limit: int) -> tuple[str, str, bytes]:
        body = {
            "filterGroups": [{"filters": filters}],
            "sorts": [{"propertyName": sort, "direction": "ASCENDING"}],
            "properties": [*names, "hs_lastmodifieddate"],
            "limit": limit,
        }
        return ("POST", f"{records}/search", _json(body))

    def contacts_of(*ids: str) -> list[tuple[str, str, bytes]]:
        path = f"{API}/crm/associations/2026-09/{kind}/contacts/batch/read"
        return [("POST", path, _json({"inputs": [{"id": key} for key in ids]}))] if associated else []

    batch = {"inputs": [{"id": first}], "properties": [*names, "hs_merged_object_ids"], "propertiesWithHistory": []}
    assert [(request.method, str(request.url), request.content) for request in hubspot.server.requests] == [
        ("GET", f"{records}?limit=2&archived=false&properties={listed}", b""),
        *contacts_of(first, second),
        ("GET", f"{records}?limit=2&archived=false&properties={listed}&after={third}", b""),
        *contacts_of(third),
        search([modified_after], "hs_lastmodifieddate", 2),
        *contacts_of(third, second),
        search([modified_at], "hs_object_id", 2),
        *contacts_of(first, second),
        search([modified_at, above_second], "hs_object_id", 2),
        search([after_block], "hs_lastmodifieddate", 1),
        *contacts_of(third),
        ("POST", f"{records}/batch/read", _json(batch)),
        *contacts_of(first),
    ]


def test_a_record_its_mapping_cannot_read_or_hubspot_states_unreadably_is_refused_alone_and_the_page_lands(
    hubspot: HubSpot, settings: Any
) -> None:
    _register(settings, AmountMapping)
    hubspot.server.scopes.add(DEALS_READ)
    words, odd, twelve = (
        hubspot.server.add("deals", {"amount": amount}, modified=ago(days=1)) for amount in ("seven", "3", "12")
    )
    stated = {key: record["properties"] for key, record in hubspot.server.records["deals"].items()}
    listed = [
        {"id": words, "properties": stated[words]},
        {"id": odd, "properties": {**stated[odd], "amount": 3}},
        {"id": twelve, "properties": stated[twelve]},
    ]
    hubspot.server.queue(httpx2.Response(200, json={"results": listed}))
    amounts = _objects(hubspot, AmountMapping)

    assert [page.count for page in amounts.drain()] == [1]

    assert [record.id for record, _source in RECEIVED] == [twelve]
    refused = {link.external_key: link.failures() for link in (amounts.link(words), amounts.link(odd))}
    ((by_words,), (by_odd,)) = refused[words], refused[odd]
    assert (by_words.code, by_words.details["mapping"]) == (MAPPING_REFUSED, AmountMapping.key)
    assert "seven" not in json.dumps(by_words.model_dump(mode="json"), default=str)
    assert by_odd.code == "unreadable_record"


def test_object_mappings_pass_the_startup_check_and_a_key_is_registered_once(settings: Any) -> None:
    """A mapping claims one key, and names another object type than contacts, which a directory reads itself."""

    _register(settings, DealMapping, NoteMapping, AmountMapping)
    assert check_object_mappings() == []

    deals = f"{DealMapping.__module__}.{DealMapping.__qualname__}"
    settings.ANGEE_HUBSPOT_OBJECT_MAPPING_CLASSES = {DealMapping.key: deals, "example_deals_again": deals}
    assert [error.id for error in check_object_mappings()] == ["angee.E004"]

    _register(settings, ContactRecordsMapping)
    assert [error.id for error in check_object_mappings()] == ["parties_integrate_hubspot.E001"]
