"""HubSpot's wire client against the scripted account: paging, reads, refusals and property parsing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest

from angee.integrate.testing.http import request_fields
from angee.parties_integrate_hubspot.client import (
    HubSpotClient,
    HubSpotError,
    HubSpotObject,
    parse_options,
    parse_string,
)
from angee.parties_integrate_hubspot.testing.server import (
    HUB_ID,
    TOKEN,
    VENDOR_TEXT,
    HubSpotServer,
    error_response,
)
from angee.parties_integrate_hubspot.testing.server import hubspot_server as hubspot_server

MODIFIED = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
NAMES = ("firstname", "lastname", "email")
CONTACTS_READ = "crm.objects.contacts.read"


@pytest.fixture
def client() -> HubSpotClient:
    return HubSpotClient({"Authorization": f"Bearer {TOKEN}"})


def contact(server: HubSpotServer, number: int) -> str:
    """Add an invented contact modified ``number`` minutes after :data:`MODIFIED`."""

    return server.add(
        "contacts", {"email": f"person{number}@example.com"}, modified=MODIFIED + timedelta(minutes=number)
    )


def epoch_ms(moment: datetime) -> str:
    """Return ``moment`` as HubSpot's epoch milliseconds."""

    return str(int(moment.timestamp() * 1000))


def test_list_pages_walk_every_record_in_id_order_to_exhaustion(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    ids = [contact(hubspot_server, number) for number in range(5)]
    read, after = [], ""
    while True:
        page = client.list_page("contacts", after=after, limit=2, properties=NAMES)
        read += [record.id for record in page.results]
        if not page.next_after:
            break
        after = page.next_after

    assert read == ids
    assert page.results[0].properties["email"] == "person4@example.com"
    assert page.results[0].properties["firstname"] is None
    assert page.results[0].modified_at("contacts") == MODIFIED + timedelta(minutes=4)
    first = hubspot_server.requests[0]
    assert (first.url.params["properties"], first.url.params["archived"]) == (
        "firstname,lastname,email,lastmodifieddate",
        "false",
    )
    assert first.headers["authorization"] == f"Bearer {TOKEN}"
    assert len(hubspot_server.requests) == 3


def test_an_archived_listing_returns_only_soft_deleted_records(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    kept, archived = contact(hubspot_server, 1), contact(hubspot_server, 2)
    hubspot_server.archive("contacts", archived, at=MODIFIED + timedelta(days=1))

    live = client.list_page("contacts", after="", limit=10, properties=())
    gone = client.list_page("contacts", after="", limit=10, properties=(), archived=True)

    assert [record.id for record in live.results] == [kept]
    assert [record.id for record in gone.results] == [archived]
    assert hubspot_server.requests[1].url.params["archived"] == "true"


def test_record_ids_seek_strictly_past_an_id_in_numeric_order(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    for key in ("9", "10", "100", "11"):
        hubspot_server.add("contacts", modified=MODIFIED, record_id=key)

    walk = client.record_ids("contacts", after="10", limit=1)
    assert hubspot_server.requests == []
    assert next(walk) == "11"
    assert hubspot_server.requests[0].url.params["after"] == "10"
    assert list(walk) == ["100"]
    assert list(client.record_ids("contacts", limit=1)) == ["9", "10", "11", "100"]
    assert list(client.record_ids("contacts", after="12")) == ["100"]
    assert list(client.record_ids("contacts", after="100")) == []


def test_a_record_listing_that_names_its_own_position_again_is_refused(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    stuck = {"results": [{"id": "205", "properties": {}}], "paging": {"next": {"after": "205"}}}
    hubspot_server.queue(httpx2.Response(200, json=stuck), httpx2.Response(200, json=stuck))

    with pytest.raises(HubSpotError) as refusal:
        list(client.record_ids("contacts"))

    assert (refusal.value.public_message, refusal.value.transient) == (
        "HubSpot did not advance its record listing.",
        False,
    )
    assert [request.url.params.get("after") for request in hubspot_server.requests] == [None, "205"]


def test_a_batch_read_reports_the_ids_hubspot_has_no_record_under(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    first, archived, last = (contact(hubspot_server, number) for number in range(3))
    hubspot_server.archive("contacts", archived, at=MODIFIED)

    read = client.batch_read("contacts", [first, archived, "999", last, first], ("email",))

    assert [record.id for record in read.found] == [first, last]
    assert read.missing == (archived, "999")
    assert request_fields(hubspot_server.requests[0]) == {
        "inputs": [{"id": first}, {"id": archived}, {"id": "999"}, {"id": last}],
        "properties": ["email"],
        "propertiesWithHistory": [],
    }


def test_a_batch_read_splits_ids_into_hubspots_batch_size(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    ids = [contact(hubspot_server, number) for number in range(101)]

    read = client.batch_read("contacts", ids, ())

    assert [record.id for record in read.found] == ids and read.missing == ()
    assert [len(request_fields(request)["inputs"]) for request in hubspot_server.requests] == [100, 1]


def test_a_batch_read_of_a_merged_id_reports_it_missing_beside_the_survivor(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    kept, folded = contact(hubspot_server, 1), contact(hubspot_server, 2)
    survivor = hubspot_server.merge("contacts", kept, folded, modified=MODIFIED + timedelta(hours=1))

    read = client.batch_read("contacts", [kept], ("hs_merged_object_ids",))

    assert [record.id for record in read.found] == [survivor] and read.missing == (kept,)
    assert parse_options(read.found[0].properties["hs_merged_object_ids"]) == (kept, folded)

    hubspot_server.resolves_merged = False
    read = client.batch_read("contacts", [folded, survivor], ())
    assert [record.id for record in read.found] == [survivor] and read.missing == (folded,)


def test_a_record_hubspot_failed_to_read_is_a_transient_refusal_and_never_missing(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    kept, failing = contact(hubspot_server, 1), contact(hubspot_server, 2)
    hubspot_server.read_errors[failing] = "VALIDATION_ERROR"

    with pytest.raises(HubSpotError) as refusal:
        client.batch_read("contacts", [kept, failing, "999"], ())

    assert (refusal.value.public_message, refusal.value.transient) == (
        "HubSpot could not read some records (VALIDATION_ERROR).",
        True,
    )
    assert VENDOR_TEXT not in refusal.value.public_message


def test_token_info_states_the_hub_and_its_scopes(hubspot_server: HubSpotServer, client: HubSpotClient) -> None:
    info = client.token_info(TOKEN)

    assert (info.hub_id, info.scopes) == (HUB_ID, (CONTACTS_READ,))
    assert request_fields(hubspot_server.requests[0]) == {"tokenKey": TOKEN}
    with pytest.raises(HubSpotError) as rejected:
        client.token_info("pat-na1-ffffffff-ffff-4fff-8fff-ffffffffffff")
    assert (rejected.value.status, rejected.value.transient) == (401, False)


def test_account_details_state_the_portal(hubspot_server: HubSpotServer, client: HubSpotClient) -> None:
    account = client.account()

    assert account.portal_id == HUB_ID
    assert hubspot_server.requests[0].url.path == "/account-info/2026-09/details"


def test_a_search_asks_for_changes_since_a_time_ascending_on_the_contacts_modified_property(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    contact(hubspot_server, 1)
    late = contact(hubspot_server, 30)
    since = MODIFIED + timedelta(minutes=10)

    page = client.search_modified("contacts", since=since, after="", limit=10, properties=("email",))

    assert [record.id for record in page.results] == [late]
    assert page.results[0].modified_at("contacts") == MODIFIED + timedelta(minutes=30)
    assert request_fields(hubspot_server.requests[0]) == {
        "filterGroups": [
            {"filters": [{"propertyName": "lastmodifieddate", "operator": "GTE", "value": epoch_ms(since)}]}
        ],
        "sorts": [{"propertyName": "lastmodifieddate", "direction": "ASCENDING"}],
        "properties": ["email", "lastmodifieddate"],
        "limit": 10,
    }


UNAVAILABLE = "HubSpot is temporarily unavailable."


@pytest.mark.parametrize(
    ("status", "fields", "transient", "wait", "message"),
    [
        pytest.param(
            401,
            {"category": "INVALID_AUTHENTICATION"},
            False,
            None,
            "HubSpot rejected the access token. Enter a valid HubSpot service key.",
            id="401-token-rejected",
        ),
        pytest.param(403, {}, False, None, "HubSpot refused this request for the service key.", id="403-refused"),
        pytest.param(423, {}, True, timedelta(seconds=2), None, id="423-waits-two-seconds"),
        pytest.param(423, {"headers": {"Retry-After": "1"}}, True, timedelta(seconds=2), None, id="423-short-hint"),
        pytest.param(423, {"headers": {"Retry-After": "7"}}, True, timedelta(seconds=7), None, id="423-longer-hint"),
        pytest.param(
            429, {"policy_name": "TEN_SECONDLY_ROLLING"}, True, timedelta(seconds=10), None, id="429-ten-secondly"
        ),
        pytest.param(429, {"policy_name": "DAILY"}, True, timedelta(hours=1), None, id="429-daily"),
        pytest.param(
            429,
            {"policy_name": "TEN_SECONDLY_ROLLING", "headers": {"Retry-After": "30"}},
            True,
            timedelta(seconds=30),
            None,
            id="429-ten-secondly-retry-after",
        ),
        pytest.param(
            429,
            {"policy_name": "DAILY", "headers": {"Retry-After": "30"}},
            True,
            timedelta(seconds=30),
            None,
            id="429-daily-retry-after",
        ),
        *(
            pytest.param(status, {}, True, None, UNAVAILABLE, id=f"{status}-transient")
            for status in (500, 501, 502, 503, 504, 521, 523, 524, 599)
        ),
        pytest.param(
            503, {"headers": {"Retry-After": "120"}}, True, timedelta(minutes=2), UNAVAILABLE, id="503-retry-after"
        ),
        pytest.param(
            400,
            {"category": "VALIDATION_ERROR"},
            False,
            None,
            "HubSpot refused the request (HTTP 400).",
            id="400-permanent",
        ),
        pytest.param(404, {}, False, None, "HubSpot refused the request (HTTP 404).", id="404-permanent"),
    ],
)
def test_each_refusal_maps_to_its_transience_wait_and_message(
    hubspot_server: HubSpotServer,
    client: HubSpotClient,
    status: int,
    fields: dict[str, Any],
    transient: bool,
    wait: timedelta | None,
    message: str | None,
) -> None:
    hubspot_server.queue(error_response(status, **fields))

    with pytest.raises(HubSpotError) as refusal:
        client.list_page("contacts", after="", limit=10, properties=())

    assert (refusal.value.status, refusal.value.transient, refusal.value.retry_after) == (status, transient, wait)
    assert message is None or refusal.value.public_message == message
    assert VENDOR_TEXT not in refusal.value.public_message


def test_a_missing_scope_names_the_scope_the_request_needed(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    hubspot_server.scopes = set()

    with pytest.raises(HubSpotError) as refusal:
        client.list_page("contacts", after="", limit=10, properties=())

    assert refusal.value.public_message == (
        f"The HubSpot service key is missing a scope this connection needs: {CONTACTS_READ}."
    )
    assert (refusal.value.status, refusal.value.transient) == (403, False)


def test_a_rejected_bearer_is_a_permanent_refusal(hubspot_server: HubSpotServer) -> None:
    stranger = HubSpotClient({"Authorization": "Bearer pat-na1-ffffffff-ffff-4fff-8fff-ffffffffffff"})

    with pytest.raises(HubSpotError) as refusal:
        stranger.list_page("contacts", after="", limit=10, properties=())

    assert (refusal.value.status, refusal.value.transient) == (401, False)


def test_an_unreachable_host_and_an_oversized_answer_are_transient(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    oversized = httpx2.Response(200, content=b"{}", headers={"content-length": str(9 * 1024 * 1024)})
    hubspot_server.queue(httpx2.ConnectError("connection refused"), oversized)

    for message in ("HubSpot could not be reached.", "HubSpot returned an oversized response."):
        with pytest.raises(HubSpotError) as refusal:
            client.account()
        assert (refusal.value.public_message, refusal.value.status, refusal.value.transient) == (message, None, True)


def test_an_answer_that_fails_validation_is_a_permanent_vendor_error(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    hubspot_server.queue(httpx2.Response(200, json={"hubId": "not a number"}))

    with pytest.raises(HubSpotError) as refusal:
        client.token_info(TOKEN)

    assert (refusal.value.status, refusal.value.transient) == (None, False)


@pytest.mark.parametrize(
    ("parse", "raw", "expected"),
    [
        pytest.param(parse_string, None, "", id="string-unset"),
        pytest.param(parse_string, "", "", id="string-empty"),
        pytest.param(parse_string, "  Ada Example ", "Ada Example", id="string-trimmed"),
        pytest.param(parse_string, "green", "green", id="single-enumeration-option"),
        pytest.param(parse_options, None, (), id="options-unset"),
        pytest.param(parse_options, "", (), id="options-empty"),
        pytest.param(parse_options, "red", ("red",), id="options-one"),
        pytest.param(parse_options, "red;green; blue;", ("red", "green", "blue"), id="options-joined"),
    ],
)
def test_each_parser_reads_hubspots_nullable_string(
    parse: Callable[[str | None], Any], raw: str | None, expected: Any
) -> None:
    assert parse(raw) == expected


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        pytest.param(None, None, id="unset"),
        pytest.param("", None, id="empty"),
        pytest.param(
            "2026-11-18T12:34:56.789Z", datetime(2026, 11, 18, 12, 34, 56, 789000, tzinfo=UTC), id="iso-utc"
        ),
        pytest.param(
            "2026-11-18T08:34:56-04:00", datetime(2026, 11, 18, 12, 34, 56, tzinfo=UTC), id="offset-to-utc"
        ),
        pytest.param("2026-11-18T12:34:56", datetime(2026, 11, 18, 12, 34, 56, tzinfo=UTC), id="naive-is-utc"),
        pytest.param(
            epoch_ms(datetime(2026, 11, 18, 12, 34, 56, 789000, tzinfo=UTC)),
            datetime(2026, 11, 18, 12, 34, 56, 789000, tzinfo=UTC),
            id="epoch-ms",
        ),
        pytest.param("-86400000", datetime(1969, 12, 31, tzinfo=UTC), id="before-the-epoch"),
        pytest.param("soon", None, id="words"),
        pytest.param("9" * 30, None, id="absurd-epoch-ms"),
        pytest.param("253402300800000", None, id="epoch-ms-past-the-calendar"),
        pytest.param("0001-01-01T00:00:00+01:00", None, id="iso-before-the-calendar"),
    ],
)
def test_a_records_modified_time_reads_in_utc_and_an_unreadable_one_is_unset(
    stated: str | None, expected: datetime | None
) -> None:
    record = HubSpotObject(id="101", properties={"lastmodifieddate": stated})

    assert record.modified_at("contacts") == expected
    assert HubSpotObject(id="101").modified_at("contacts") is None
