"""HubSpot contact properties a consumer maps onto its own person columns, through the real driver.

A donor composed into the test person (``PersonFavourites``) owns the columns and
their statement groups. The mappings below name custom properties of the scripted
HubSpot account and project them with the addon's value parsers; the harness is
``test_parties_integrate_hubspot_sync``'s.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone

from angee.integrate.states import LinkStatus, StreamPhase
from angee.messaging.testing.models import Person
from angee.parties.backends import MAPPINGS, DirectoryMapping
from angee.parties_integrate_hubspot.backends import HubSpotDirectoryBackend
from angee.parties_integrate_hubspot.client import parse_bool, parse_number, parse_string
from tests.test_parties_directory_backend import _existing
from tests.test_parties_integrate_hubspot_sync import ADA, ALAN, GRACE, HubSpot, ago, failures
from tests.test_parties_integrate_hubspot_sync import hubspot as hubspot  # noqa: F401 -- the scripted directory

pytestmark = pytest.mark.django_db(transaction=True)

OWN_PROPERTIES = [
    "firstname",
    "lastname",
    "email",
    "hs_additional_emails",
    "phone",
    "mobilephone",
    "jobtitle",
    "company",
    "address",
    "city",
    "state",
    "zip",
    "country",
    "hs_merged_object_ids",
]
"""The properties every contacts read asked HubSpot for before mappings existed, in its order."""
CONTACTS = "https://api.hubapi.com/crm/objects/2026-09/contacts"
MOMENT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ColourMapping(DirectoryMapping):
    """A contact's favourite colour from a custom HubSpot property, unless HubSpot marks the contact not to record."""

    key = "hubspot_colour"
    backend = HubSpotDirectoryBackend.key
    fields = ("preferred_colour", "do_not_record")

    def project(self, row: Mapping[str, Any]) -> dict[str, Any]:
        if parse_bool(row.get("do_not_record")):
            return {"favourite_colour": ""}
        return {"favourite_colour": parse_string(row.get("preferred_colour"))}

    def apply_record(self, person: Any, values: Mapping[str, Any], *, link: Any) -> None:
        person.statements_for("colour").set(
            aspect="colour", values=values, asserted_by=link.asserting_source(), link=link
        )


class NumberMapping(DirectoryMapping):
    """A contact's favourite number from a HubSpot number property, which HubSpot states as a decimal string."""

    key = "hubspot_number"
    backend = HubSpotDirectoryBackend.key
    fields = ("lucky_number",)

    def project(self, row: Mapping[str, Any]) -> dict[str, Any]:
        number = parse_number(row.get("lucky_number"))
        return {"favourite_number": None if number is None else int(number)}

    def apply_record(self, person: Any, values: Mapping[str, Any], *, link: Any) -> None:
        person.statements_for("number").set(
            aspect="number", values=values, asserted_by=link.asserting_source(), link=link
        )


class SentMapping(DirectoryMapping):
    """States nothing: its projection is the row the backend handed it, so a test reads what HubSpot sent."""

    key = "hubspot_sent"
    backend = HubSpotDirectoryBackend.key
    fields = ("no_such_property", "preferred_colour")

    def project(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return dict(row)

    def apply_record(self, person: Any, values: Mapping[str, Any], *, link: Any) -> None:
        del person, values, link


def _register(settings: Any, *mappings: type[DirectoryMapping]) -> None:
    """Register ``mappings`` as consumer addons do, each under its key."""

    settings.ANGEE_DIRECTORY_MAPPING_CLASSES = {
        mapping.key: f"{mapping.__module__}.{mapping.__qualname__}" for mapping in mappings
    }


def _person(hubspot: HubSpot, key: str) -> Any:
    return Person.objects.get(pk=hubspot.link(key).target_object_id)


def _stated(hubspot: HubSpot, key: str, aspect: str) -> Any:
    """The directory's current statement of ``aspect`` on the contact's person."""

    rows = Person.statements.filter(subject_id=hubspot.link(key).target_object_id, aspect=aspect)
    return rows.filter(source_kind="directory").current().get()


def _epoch_ms(moment: datetime) -> str:
    return str((moment - EPOCH) // timedelta(milliseconds=1))


def _json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


@pytest.mark.parametrize(
    ("mappings", "mapped"),
    [
        pytest.param((), (), id="no-mapping"),
        pytest.param(
            (ColourMapping, NumberMapping), ("do_not_record", "lucky_number", "preferred_colour"), id="mapped"
        ),
    ],
)
def test_every_contacts_read_asks_for_the_backends_properties_and_the_mapped_ones_and_nothing_more(
    hubspot: HubSpot, settings: Any, mappings: tuple[type[DirectoryMapping], ...], mapped: tuple[str, ...]
) -> None:
    """A list, a search, a drain of one modified time and a batch read; with no mapping, byte for byte as before."""

    _register(settings, *mappings)
    hubspot.reopen()
    ada, grace, alan = (hubspot.server.add("contacts", contact, modified=MOMENT) for contact in (ADA, GRACE, ALAN))

    hubspot.drain(page_bound=2)
    # The delta's overlap finds all three at one modified time: two fill a page, so the block drains by id.
    assert [page.exhausted for page in hubspot.drain(page_bound=2)] == [False, False, True]
    hubspot.backend.read_keys(hubspot.stream, [ada])

    listed = "%2C".join([*OWN_PROPERTIES, *mapped, "lastmodifieddate"])
    at, since = _epoch_ms(MOMENT), _epoch_ms(MOMENT - timedelta(minutes=5))
    modified_after = {"propertyName": "lastmodifieddate", "operator": "GT", "value": since}
    modified_at = {"propertyName": "lastmodifieddate", "operator": "EQ", "value": at}
    above_grace = {"propertyName": "hs_object_id", "operator": "GT", "value": grace}
    after_block = {"propertyName": "lastmodifieddate", "operator": "GT", "value": at}

    def search(filters: list[dict[str, str]], sort: str, limit: int) -> bytes:
        return _json(
            {
                "filterGroups": [{"filters": filters}],
                "sorts": [{"propertyName": sort, "direction": "ASCENDING"}],
                "properties": [*OWN_PROPERTIES, *mapped, "lastmodifieddate"],
                "limit": limit,
            }
        )

    batch = {"inputs": [{"id": ada}], "properties": [*OWN_PROPERTIES, *mapped], "propertiesWithHistory": []}
    assert [(request.method, str(request.url), request.content) for request in hubspot.server.requests] == [
        ("GET", f"{CONTACTS}?limit=2&archived=false&properties={listed}", b""),
        ("GET", f"{CONTACTS}?limit=2&archived=false&properties={listed}&after={alan}", b""),
        ("POST", f"{CONTACTS}/search", search([modified_after], "lastmodifieddate", 2)),
        ("POST", f"{CONTACTS}/search", search([modified_at], "hs_object_id", 2)),
        ("POST", f"{CONTACTS}/search", search([modified_at, above_grace], "hs_object_id", 2)),
        ("POST", f"{CONTACTS}/search", search([after_block], "lastmodifieddate", 1)),
        ("POST", f"{CONTACTS}/batch/read", _json(batch)),
    ]


def test_a_mapped_property_lands_on_the_person_through_the_link_and_hubspots_change_of_it_reapplies(
    hubspot: HubSpot, settings: Any
) -> None:
    """Only the projection reaches the record, an identity read compares it unchanged, and the delta brings a change."""

    _register(settings, ColourMapping)
    hubspot.reopen()
    ada = hubspot.server.add("contacts", {**ADA, "preferred_colour": "green"}, modified=ago(days=2))
    grace = hubspot.server.add(
        "contacts", {**GRACE, "preferred_colour": "vermilion", "do_not_record": "true"}, modified=ago(days=1)
    )

    assert sum(page.count for page in hubspot.drain()) == 2

    link = hubspot.link(ada)
    assert (_person(hubspot, ada).favourite_colour, _person(hubspot, grace).favourite_colour) == ("green", "")
    statement = _stated(hubspot, ada, "colour")
    assert (statement.status, statement.applied, statement.link_id) == ("asserted", True, link.pk)
    assert link.source_payload[MAPPINGS] == {"hubspot_colour": {"favourite_colour": "green"}}
    kept = json.dumps([hubspot.link(key).source_payload for key in (ada, grace)])
    assert "preferred_colour" not in kept and "vermilion" not in kept
    (read,) = hubspot.backend.read_keys(hubspot.stream, [ada])
    assert read.mapped_payload == link.applied_base.mapped_payload

    hubspot.server.update("contacts", ada, {"preferred_colour": "violet"}, modified=timezone.now())
    assert [page.count for page in hubspot.drain()] == [1]

    assert _person(hubspot, ada).favourite_colour == "violet"
    assert hubspot.link(ada).source_payload[MAPPINGS] == {"hubspot_colour": {"favourite_colour": "violet"}}
    assert [page.count for page in hubspot.drain()] == [0]
    assert not failures(hubspot)


def test_adding_a_mapping_replays_the_baseline_asking_hubspot_for_the_new_property(
    hubspot: HubSpot, settings: Any
) -> None:
    ada = hubspot.server.add("contacts", {**ADA, "lucky_number": "7"}, modified=ago(days=1))
    hubspot.drain()
    assert all("lucky_number" not in listing["properties"].split(",") for listing in hubspot.listings())
    assert _person(hubspot, ada).favourite_number is None
    plain = hubspot.stream
    hubspot.server.requests.clear()

    _register(settings, NumberMapping)
    composed = hubspot.reopen()

    assert (composed.generation, composed.phase) == (plain.generation + 1, StreamPhase.BASELINE)
    assert [page.count for page in hubspot.drain()] == [1]
    (listing,) = hubspot.listings()
    assert listing["properties"].split(",")[-2:] == ["lucky_number", "lastmodifieddate"]
    assert _person(hubspot, ada).favourite_number == 7
    assert [page.count for page in hubspot.drain()] == [0]


def test_an_adopted_contact_is_mapped_like_a_minted_one(hubspot: HubSpot, settings: Any) -> None:
    person, _handle = _existing(hubspot.directory.owner, email=ADA["email"])
    _register(settings, ColourMapping)
    hubspot.reopen()
    ada = hubspot.server.add("contacts", {**ADA, "preferred_colour": "green"}, modified=ago(days=1))

    hubspot.drain()

    link = hubspot.link(ada)
    assert link.target_object_id == str(person.pk)
    person.refresh_from_db()
    assert person.favourite_colour == "green"
    assert _stated(hubspot, ada, "colour").link_id == link.pk


def test_a_property_the_account_lacks_reaches_the_mapping_as_not_sent_and_the_sync_goes_on(
    hubspot: HubSpot, settings: Any
) -> None:
    """HubSpot leaves out a property name the account does not define, and answers an unset one it does as null."""

    _register(settings, SentMapping)
    hubspot.reopen()
    ada = hubspot.server.add("contacts", {**ADA, "preferred_colour": "green"}, modified=ago(days=2))
    grace = hubspot.server.add("contacts", GRACE, modified=ago(days=1))

    assert sum(page.count for page in hubspot.drain()) == 2

    assert all("no_such_property" in listing["properties"].split(",") for listing in hubspot.listings())
    assert hubspot.link(ada).source_payload[MAPPINGS] == {"hubspot_sent": {"preferred_colour": "green"}}
    assert hubspot.link(grace).source_payload[MAPPINGS] == {"hubspot_sent": {"preferred_colour": None}}
    assert {hubspot.link(key).status for key in (ada, grace)} == {LinkStatus.CURRENT}
    assert not failures(hubspot)
