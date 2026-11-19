"""HubSpot's wire client against the scripted account: paging, reads, refusals and property decoding."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2
import pytest

from angee.integrate.testing.http import request_fields
from angee.parties_integrate_hubspot.client import (
    BATCH_READ_LIMIT,
    HubSpotClient,
    HubSpotError,
    decode_bool,
    decode_date,
    decode_datetime,
    decode_number,
    decode_options,
    decode_string,
    read_scope,
)
from angee.parties_integrate_hubspot.testing.server import (
    CONTACT_TO_COMPANY,
    HUB_ID,
    PRIMARY_COMPANY,
    TOKEN,
    HubSpotServer,
    error_response,
)
from angee.parties_integrate_hubspot.testing.server import hubspot_server as hubspot_server

MODIFIED = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
NAMES = ("firstname", "lastname", "email")


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
    first = hubspot_server.requests[0]
    assert (first.url.params["properties"], first.url.params["archived"]) == ("firstname,lastname,email", "false")
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
    assert [(record.id, record.archived, record.archived_at) for record in gone.results] == [
        (archived, True, MODIFIED + timedelta(days=1))
    ]


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


def test_a_batch_read_reports_the_ids_hubspot_did_not_return(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    first, archived, last = (contact(hubspot_server, number) for number in range(3))
    hubspot_server.archive("contacts", archived, at=MODIFIED)

    read = client.batch_read("contacts", [first, archived, "999", last, first], ("email",))

    assert [record.id for record in read.results] == [first, last]
    assert read.missing == (archived, "999")
    assert request_fields(hubspot_server.requests[0]) == {
        "inputs": [{"id": first}, {"id": archived}, {"id": "999"}, {"id": last}],
        "properties": ["email"],
        "propertiesWithHistory": [],
    }


def test_a_batch_read_splits_ids_into_hubspots_batch_size(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    ids = [contact(hubspot_server, number) for number in range(BATCH_READ_LIMIT + 1)]

    read = client.batch_read("contacts", ids, ())

    assert [record.id for record in read.results] == ids and read.missing == ()
    assert [len(request_fields(request)["inputs"]) for request in hubspot_server.requests] == [BATCH_READ_LIMIT, 1]


def test_a_batch_read_of_a_merged_id_reports_it_missing_beside_the_survivor(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    kept, folded = contact(hubspot_server, 1), contact(hubspot_server, 2)
    survivor = hubspot_server.merge("contacts", kept, folded, modified=MODIFIED + timedelta(hours=1))

    read = client.batch_read("contacts", [kept], ("hs_merged_object_ids",))

    assert [record.id for record in read.results] == [survivor] and read.missing == (kept,)
    assert decode_options(read.results[0].properties["hs_merged_object_ids"]) == (kept, folded)

    hubspot_server.resolves_merged = False
    read = client.batch_read("contacts", [folded, survivor], ())
    assert [record.id for record in read.results] == [survivor] and read.missing == (folded,)


def test_associations_follow_each_records_own_paging(hubspot_server: HubSpotServer, client: HubSpotClient) -> None:
    hubspot_server.association_page_size = 2
    ada, grace, alone = (contact(hubspot_server, number) for number in range(3))
    companies = [hubspot_server.add("companies", {"name": f"Example {n}"}, modified=MODIFIED) for n in range(3)]
    hubspot_server.associate(ada, companies[0], primary=True)
    hubspot_server.associate(ada, companies[1])
    hubspot_server.associate(ada, companies[2])
    hubspot_server.associate(grace, companies[1])

    found = client.associations("contacts", "companies", [ada, grace, alone])

    assert [target.to_object_id for target in found[ada]] == companies
    assert [target.to_object_id for target in found[grace]] == [companies[1]]
    assert found[alone] == ()
    assert {(kind.type_id, kind.label) for kind in found[ada][0].association_types} == {
        (CONTACT_TO_COMPANY, None),
        (PRIMARY_COMPANY, "Primary"),
    }
    assert len(hubspot_server.requests) == 2
    assert request_fields(hubspot_server.requests[1]) == {"inputs": [{"id": ada, "after": "2"}]}


def test_properties_return_standard_and_custom_definitions(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    hubspot_server.define(
        "contacts", "favourite_colour", kind="enumeration", field_type="select", options=("red", "green")
    )

    definitions = {definition.name: definition for definition in client.properties("contacts")}

    colour = definitions["favourite_colour"]
    assert (colour.type, colour.field_type, colour.hubspot_defined) == ("enumeration", "select", False)
    assert [option.value for option in colour.options] == ["red", "green"]
    assert definitions["email"].hubspot_defined is True


def test_token_info_states_the_hub_and_its_scopes(hubspot_server: HubSpotServer, client: HubSpotClient) -> None:
    info = client.token_info(TOKEN)

    assert info.hub_id == HUB_ID
    assert set(info.scopes) == {read_scope("contacts"), read_scope("companies")}
    assert request_fields(hubspot_server.requests[0]) == {"tokenKey": TOKEN}
    with pytest.raises(HubSpotError) as rejected:
        client.token_info("pat-na1-ffffffff-ffff-4fff-8fff-ffffffffffff")
    assert (rejected.value.status, rejected.value.transient) == (401, False)


def test_account_details_answer_when_introspection_is_refused(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    hubspot_server.queue(error_response(403))
    with pytest.raises(HubSpotError):
        client.token_info(TOKEN)

    account = client.account()

    assert account.portal_id == HUB_ID
    assert (account.time_zone, account.data_hosting_location, account.ui_domain) == (
        "Etc/UTC",
        "na1",
        "app.hubspot.com",
    )


def test_a_search_asks_for_changes_since_a_time_ascending_on_the_modified_property(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    contact(hubspot_server, 1)
    late = contact(hubspot_server, 30)
    since = MODIFIED + timedelta(minutes=10)

    page = client.search_modified(
        "contacts", modified_property="lastmodifieddate", since=since, after="", limit=10, properties=("email",)
    )

    assert [record.id for record in page.results] == [late] and page.total == 1
    assert page.results[0].modified_at("lastmodifieddate") == MODIFIED + timedelta(minutes=30)
    fields = request_fields(hubspot_server.requests[0])
    assert fields == {
        "filterGroups": [
            {"filters": [{"propertyName": "lastmodifieddate", "operator": "GTE", "value": epoch_ms(since)}]}
        ],
        "sorts": [{"propertyName": "lastmodifieddate", "direction": "ASCENDING"}],
        "properties": ["email", "lastmodifieddate"],
        "limit": 10,
    }


@pytest.mark.parametrize(
    ("status", "fields", "transient", "wait"),
    [
        pytest.param(401, {"category": "INVALID_AUTHENTICATION"}, False, None, id="401-token-rejected"),
        pytest.param(403, {}, False, None, id="403-refused"),
        pytest.param(423, {}, True, timedelta(seconds=2), id="423-waits-two-seconds"),
        pytest.param(423, {"headers": {"Retry-After": "1"}}, True, timedelta(seconds=2), id="423-short-hint-floored"),
        pytest.param(423, {"headers": {"Retry-After": "7"}}, True, timedelta(seconds=7), id="423-longer-hint"),
        pytest.param(
            429, {"policy_name": "TEN_SECONDLY_ROLLING"}, True, timedelta(seconds=10), id="429-ten-secondly"
        ),
        pytest.param(429, {"policy_name": "DAILY"}, True, timedelta(hours=1), id="429-daily"),
        pytest.param(
            429,
            {"policy_name": "TEN_SECONDLY_ROLLING", "headers": {"Retry-After": "30"}},
            True,
            timedelta(seconds=30),
            id="429-ten-secondly-retry-after",
        ),
        pytest.param(
            429,
            {"policy_name": "DAILY", "headers": {"Retry-After": "30"}},
            True,
            timedelta(seconds=30),
            id="429-daily-retry-after",
        ),
        *(pytest.param(status, {}, True, None, id=f"{status}-transient") for status in (502, 503, 504, 521, 523, 524)),
        pytest.param(503, {"headers": {"Retry-After": "120"}}, True, timedelta(minutes=2), id="503-retry-after"),
        pytest.param(500, {}, False, None, id="500-permanent"),
        pytest.param(400, {"category": "VALIDATION_ERROR"}, False, None, id="400-permanent"),
    ],
)
def test_each_refusal_maps_to_its_transience_and_wait(
    hubspot_server: HubSpotServer,
    client: HubSpotClient,
    status: int,
    fields: dict[str, Any],
    transient: bool,
    wait: timedelta | None,
) -> None:
    hubspot_server.queue(error_response(status, **fields))

    with pytest.raises(HubSpotError) as refusal:
        client.list_page("contacts", after="", limit=10, properties=())

    assert (refusal.value.status, refusal.value.transient, refusal.value.retry_after) == (status, transient, wait)
    assert "Vendor text" not in refusal.value.public_message


def test_a_missing_scope_names_the_scopes_the_request_needed(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    hubspot_server.scopes = {read_scope("contacts")}
    with pytest.raises(HubSpotError) as associations:
        client.associations("contacts", "companies", ["101"])
    hubspot_server.scopes = set()
    with pytest.raises(HubSpotError) as listing:
        client.list_page("contacts", after="", limit=10, properties=())

    message = associations.value.public_message
    assert read_scope("contacts") in message and read_scope("companies") in message
    assert listing.value.public_message == (
        "The HubSpot service key is missing a scope this connection needs: crm.objects.contacts.read."
    )
    assert (listing.value.status, listing.value.transient) == (403, False)


def test_a_rejected_bearer_is_a_permanent_refusal(hubspot_server: HubSpotServer) -> None:
    stranger = HubSpotClient({"Authorization": "Bearer pat-na1-ffffffff-ffff-4fff-8fff-ffffffffffff"})

    with pytest.raises(HubSpotError) as refusal:
        stranger.list_page("contacts", after="", limit=10, properties=())

    assert (refusal.value.status, refusal.value.transient) == (401, False)
    assert refusal.value.public_message == "HubSpot rejected the access token. Enter a valid HubSpot service key."


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
    ("decode", "raw", "expected"),
    [
        pytest.param(decode_string, None, "", id="string-unset"),
        pytest.param(decode_string, "", "", id="string-empty"),
        pytest.param(decode_string, "  Ada Example ", "Ada Example", id="string-trimmed"),
        pytest.param(decode_string, "green", "green", id="single-enumeration-option"),
        pytest.param(decode_options, None, (), id="options-unset"),
        pytest.param(decode_options, "", (), id="options-empty"),
        pytest.param(decode_options, "red", ("red",), id="options-one"),
        pytest.param(decode_options, "red;green; blue;", ("red", "green", "blue"), id="options-joined"),
        pytest.param(decode_number, None, None, id="number-unset"),
        pytest.param(decode_number, "", None, id="number-empty"),
        pytest.param(decode_number, "42", Decimal(42), id="number-integer"),
        pytest.param(decode_number, "-3.25", Decimal("-3.25"), id="number-exact-decimal"),
        pytest.param(decode_bool, None, None, id="bool-unset"),
        pytest.param(decode_bool, "", None, id="bool-empty"),
        pytest.param(decode_bool, "true", True, id="bool-true"),
        pytest.param(decode_bool, "FALSE", False, id="bool-false"),
        pytest.param(decode_date, None, None, id="date-unset"),
        pytest.param(decode_date, "", None, id="date-empty"),
        pytest.param(decode_date, "2026-03-01", date(2026, 3, 1), id="date-iso-day"),
        pytest.param(decode_date, "2026-03-01T00:00:00.000Z", date(2026, 3, 1), id="date-midnight-utc-not-shifted"),
        pytest.param(decode_date, epoch_ms(datetime(2026, 3, 1, tzinfo=UTC)), date(2026, 3, 1), id="date-epoch-ms"),
        pytest.param(decode_date, "-86400000", date(1969, 12, 31), id="date-before-epoch"),
        pytest.param(decode_datetime, None, None, id="datetime-unset"),
        pytest.param(decode_datetime, "", None, id="datetime-empty"),
        pytest.param(
            decode_datetime,
            "2026-11-18T12:34:56.789Z",
            datetime(2026, 11, 18, 12, 34, 56, 789000, tzinfo=UTC),
            id="datetime-iso-utc",
        ),
        pytest.param(
            decode_datetime,
            "2026-11-18T08:34:56-04:00",
            datetime(2026, 11, 18, 12, 34, 56, tzinfo=UTC),
            id="datetime-offset-to-utc",
        ),
        pytest.param(
            decode_datetime,
            "2026-11-18T12:34:56",
            datetime(2026, 11, 18, 12, 34, 56, tzinfo=UTC),
            id="datetime-naive-is-utc",
        ),
        pytest.param(
            decode_datetime,
            epoch_ms(datetime(2026, 11, 18, 12, 34, 56, 789000, tzinfo=UTC)),
            datetime(2026, 11, 18, 12, 34, 56, 789000, tzinfo=UTC),
            id="datetime-epoch-ms",
        ),
    ],
)
def test_each_decoder_reads_hubspots_nullable_string(
    decode: Callable[[str | None], Any], raw: str | None, expected: Any
) -> None:
    assert decode(raw) == expected


@pytest.mark.parametrize(
    ("decode", "raw"),
    [
        pytest.param(decode_number, "twelve", id="number-words"),
        pytest.param(decode_number, "NaN", id="number-not-finite"),
        pytest.param(decode_bool, "yes", id="bool-other-word"),
        pytest.param(decode_datetime, "soon", id="datetime-words"),
        pytest.param(decode_date, "March", id="date-words"),
    ],
)
def test_a_malformed_value_is_refused(decode: Callable[[str | None], Any], raw: str) -> None:
    with pytest.raises(ValueError):
        decode(raw)
