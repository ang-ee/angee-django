"""HubSpot's wire client against the scripted account: paging, reads, refusals and property parsing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest

from angee.integrate.http import HttpClient
from angee.integrate.testing.http import request_fields
from angee.parties_integrate_hubspot.client import (
    EPOCH,
    SERVICE_KEY_SHAPE,
    HubSpotClient,
    HubSpotError,
    HubSpotObject,
    parse_bool,
    parse_date,
    parse_datetime,
    parse_number,
    parse_options,
    parse_string,
)
from angee.parties_integrate_hubspot.testing.server import (
    HUB_ID,
    TOKEN,
    VENDOR_TEXT,
    HubSpotServer,
    error_response,
    service_key,
)
from angee.parties_integrate_hubspot.testing.server import hubspot_server as hubspot_server

MODIFIED = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
NAMES = ("firstname", "lastname", "email")
CONTACTS_READ = "crm.objects.contacts.read"
DEALS_READ = "crm.objects.deals.read"


OTHER_KEY = service_key("f")
"""A key of the usual shape that the scripted account does not accept."""


@pytest.fixture
def client() -> HubSpotClient:
    return HubSpotClient(HttpClient(), {"Authorization": f"Bearer {TOKEN}"})


def test_the_client_sends_through_the_http_client_its_caller_owns(hubspot_server: HubSpotServer) -> None:
    http = HttpClient()
    client = HubSpotClient(http, {"Authorization": f"Bearer {TOKEN}"})

    client.account()

    assert client.http is http
    assert len(hubspot_server.requests) == 1


def test_no_file_of_the_addon_or_its_tests_holds_a_literal_of_a_service_keys_shape() -> None:
    """Invented keys are built at runtime (``service_key``), so a secret scanner on a public remote finds none."""

    root = Path(__file__).resolve().parents[1]
    addon = root / "src" / "angee" / "parties_integrate_hubspot"
    files = [*addon.rglob("*"), *(root / "tests").glob("test_parties_integrate_hubspot_*.py")]
    texts = {".py", ".ts", ".tsx", ".md", ".json", ".yaml", ".toml"}
    shaped = [
        str(path.relative_to(root))
        for path in files
        if path.is_file() and path.suffix in texts and SERVICE_KEY_SHAPE.search(path.read_text(encoding="utf-8"))
    ]
    assert shaped == []


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
    assert (first.headers["authorization"], first.headers["accept"]) == (f"Bearer {TOKEN}", "application/json")
    assert len(hubspot_server.requests) == 3


def test_a_page_states_hubspots_time_from_its_answers_date_header(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    """HubSpot states when it sent each answer, to the second; an answer whose ``Date`` cannot be read states none."""

    hubspot_server.clock = MODIFIED + timedelta(seconds=1, milliseconds=750)

    listed = client.list_page("contacts", after="", limit=10, properties=())
    searched = client.search_modified("contacts", since=EPOCH, limit=10, properties=())

    assert (listed.sent_at, searched.sent_at) == (MODIFIED + timedelta(seconds=1),) * 2
    for stated in ("", "not a date", "Thu, 19 Nov 2026 03:10:31 -0000"):
        hubspot_server.queue(httpx2.Response(200, json={"results": []}, headers={"Date": stated}))
        assert client.list_page("contacts", after="", limit=10, properties=()).sent_at is None


def test_record_ids_seek_strictly_past_an_id_in_numeric_order(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    for key in ("9", "10", "100", "11"):
        hubspot_server.add("contacts", modified=MODIFIED, record_id=key)

    walk = client.record_ids("contacts", after="10", limit=1)
    assert hubspot_server.requests == []
    assert next(walk) == "11"
    assert hubspot_server.requests[0].url.params["after"] == "11"
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
        "properties": ["email", "hs_merged_object_ids"],
        "propertiesWithHistory": [],
    }


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param({"status": "COMPLETE", "results": []}, id="omitted"),
        pytest.param(
            {"status": "COMPLETE", "results": [], "numErrors": 1, "errors": [{"category": "OBJECT_NOT_FOUND"}]},
            id="not-found-naming-no-id",
        ),
    ],
)
def test_an_id_a_batch_read_leaves_unanswered_without_hubspot_naming_it_gone_is_a_transient_refusal(
    hubspot_server: HubSpotServer, client: HubSpotClient, answer: dict[str, Any]
) -> None:
    """Only HubSpot's word makes a record gone: a not-found error naming its id, or a survivor naming it merged."""

    kept = contact(hubspot_server, 1)
    hubspot_server.queue(httpx2.Response(207, json=answer))

    with pytest.raises(HubSpotError) as unanswered:
        client.batch_read("contacts", [kept], ())

    assert (unanswered.value.public_message, unanswered.value.transient) == (
        "HubSpot did not answer for some records.",
        True,
    )


MALFORMED: tuple[Any, ...] = (
    {"id": "205", "properties": {"email": "person205@example.com", "phone": 5550100}},
    {"id": "206", "properties": ["not", "a", "mapping"]},
    {"properties": {"email": "unkeyed@example.com"}},
    {"id": None, "properties": {}},
    {"id": "not a record id", "properties": {}},
    "not a record",
)
"""Records as HubSpot never states them: a number where it states strings, no properties object, no usable id."""


def test_one_bad_record_never_fails_its_page_it_is_kept_by_its_id_or_counted_without_one(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    good = contact(hubspot_server, 1)
    stated = {"email": "person1@example.com", "lastmodifieddate": "2026-11-02T12:01:00.000Z"}
    records = [{"id": good, "properties": stated}, *MALFORMED]
    hubspot_server.queue(
        httpx2.Response(200, json={"results": records, "paging": {"next": {"after": "207"}}}),
        httpx2.Response(207, json={"status": "COMPLETE", "results": records[:3]}),
    )

    page = client.list_page("contacts", after="", limit=10, properties=())
    read = client.batch_read("contacts", [good, "205", "206"], ())

    assert [(record.id, record.unreadable) for record in page.results] == [
        (good, False),
        ("205", True),
        ("206", True),
    ]
    assert (page.results[0].properties, page.results[1].properties) == (stated, {})
    assert (page.unkeyed, page.next_after) == (4, "207")
    assert [(record.id, record.unreadable) for record in read.found] == [(good, False), ("205", True), ("206", True)]
    assert read.missing == ()


def test_a_batch_read_splits_ids_into_hubspots_batch_size(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    ids = [contact(hubspot_server, number) for number in range(101)]

    read = client.batch_read("contacts", ids, ())

    assert [record.id for record in read.found] == ids and read.missing == ()
    assert [len(request_fields(request)["inputs"]) for request in hubspot_server.requests] == [100, 1]


def test_a_requested_property_the_account_lacks_is_left_out_and_one_it_has_unset_is_null(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    """HubSpot ignores a property name the account does not define; it answers one it defines, null when unset."""

    stated = hubspot_server.add("contacts", {"preferred_colour": "green"}, modified=MODIFIED)
    unset = contact(hubspot_server, 1)
    names = ("preferred_colour", "no_such_property")

    listed = client.list_page("contacts", after="", limit=10, properties=names).results
    searched = client.search_modified("contacts", since=EPOCH, limit=10, properties=names).results
    read = client.batch_read("contacts", [stated, unset], names).found

    for records in (listed, searched, read):
        by_id = {record.id: record.properties for record in records}
        assert (by_id[stated]["preferred_colour"], by_id[unset]["preferred_colour"]) == ("green", None)
        assert all("no_such_property" not in properties for properties in by_id.values())
    listing, search, batch = hubspot_server.requests
    assert "no_such_property" in listing.url.params["properties"].split(",")
    assert all("no_such_property" in request_fields(request)["properties"] for request in (search, batch))


def test_a_batch_read_of_a_merged_id_reports_it_missing_beside_the_survivor(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    kept, folded = contact(hubspot_server, 1), contact(hubspot_server, 2)
    survivor = hubspot_server.merge("contacts", kept, folded, modified=MODIFIED + timedelta(hours=1))

    read = client.batch_read("contacts", [kept], ())

    assert [record.id for record in read.found] == [survivor] and read.missing == (kept,)
    assert parse_options(read.found[0].properties["hs_merged_object_ids"]) == (kept, folded)
    assert request_fields(hubspot_server.requests[0])["properties"] == ["hs_merged_object_ids"]

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

    assert (refusal.value.public_message, refusal.value.transient) == ("HubSpot could not read some records.", True)
    assert "VALIDATION_ERROR" not in refusal.value.public_message
    assert VENDOR_TEXT not in refusal.value.public_message


def test_token_info_states_the_hub_the_key_belongs_to(hubspot_server: HubSpotServer, client: HubSpotClient) -> None:
    info = client.token_info(TOKEN)

    assert info.hub_id == HUB_ID
    assert request_fields(hubspot_server.requests[0]) == {"tokenKey": TOKEN}
    with pytest.raises(HubSpotError) as rejected:
        client.token_info(OTHER_KEY)
    assert (rejected.value.status, rejected.value.transient) == (401, False)


def test_account_details_state_the_portal_and_its_web_host_when_it_is_a_host_name(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    account = client.account()

    assert (account.portal_id, account.ui_domain) == (HUB_ID, "app.hubspot.com")
    assert hubspot_server.requests[0].url.path == "/account-info/2026-09/details"
    for answer in ({"portalId": HUB_ID, "uiDomain": "example.com/elsewhere"}, {"portalId": HUB_ID}):
        hubspot_server.queue(httpx2.Response(200, json=answer))
        unknown = client.account()
        assert (unknown.portal_id, unknown.ui_domain) == (HUB_ID, None)


def test_a_search_asks_for_changes_strictly_after_a_time_ascending_on_the_contacts_modified_property(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    contact(hubspot_server, 1)
    contact(hubspot_server, 10)
    late = contact(hubspot_server, 30)
    since = MODIFIED + timedelta(minutes=10)

    page = client.search_modified("contacts", since=since, limit=10, properties=("email",))

    assert [record.id for record in page.results] == [late]
    assert page.results[0].modified_at("contacts") == MODIFIED + timedelta(minutes=30)
    assert request_fields(hubspot_server.requests[0]) == {
        "filterGroups": [
            {"filters": [{"propertyName": "lastmodifieddate", "operator": "GT", "value": epoch_ms(since)}]}
        ],
        "sorts": [{"propertyName": "lastmodifieddate", "direction": "ASCENDING"}],
        "properties": ["email", "lastmodifieddate"],
        "limit": 10,
    }


def test_a_search_at_one_modified_time_reads_its_records_above_an_id_in_id_order(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    moment = MODIFIED + timedelta(minutes=5)
    contact(hubspot_server, 4)
    block = [hubspot_server.add("contacts", modified=moment) for _ in range(3)]
    contact(hubspot_server, 6)

    first = client.search_modified_at("contacts", moment=moment, after="", limit=2, properties=())
    rest = client.search_modified_at("contacts", moment=moment, after=block[1], limit=2, properties=())

    assert ([record.id for record in first.results], bool(first.next_after)) == (block[:2], True)
    assert ([record.id for record in rest.results], rest.next_after) == (block[2:], "")
    at_moment = {"propertyName": "lastmodifieddate", "operator": "EQ", "value": epoch_ms(moment)}
    by_id = [{"propertyName": "hs_object_id", "direction": "ASCENDING"}]
    assert [request_fields(request) for request in hubspot_server.requests] == [
        {"filterGroups": [{"filters": [at_moment]}], "sorts": by_id, "properties": ["lastmodifieddate"], "limit": 2},
        {
            "filterGroups": [
                {"filters": [at_moment, {"propertyName": "hs_object_id", "operator": "GT", "value": block[1]}]}
            ],
            "sorts": by_id,
            "properties": ["lastmodifieddate"],
            "limit": 2,
        },
    ]


def deal(server: HubSpotServer, name: str, *, minutes: int = 0) -> str:
    """Add an invented deal named ``name``, modified ``minutes`` after :data:`MODIFIED`."""

    return server.add("deals", {"dealname": name}, modified=MODIFIED + timedelta(minutes=minutes))


def test_a_search_of_another_object_type_reads_it_on_that_types_own_modified_property(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    """Every type but contacts states its last change in ``hs_lastmodifieddate``."""

    hubspot_server.scopes.add(DEALS_READ)
    deal(hubspot_server, "Early", minutes=1)
    late = deal(hubspot_server, "Late", minutes=30)
    since = MODIFIED + timedelta(minutes=10)

    page = client.search_modified("deals", since=since, limit=10, properties=("dealname",))

    assert [record.id for record in page.results] == [late]
    assert page.results[0].modified_at("deals") == MODIFIED + timedelta(minutes=30)
    assert request_fields(hubspot_server.requests[0]) == {
        "filterGroups": [
            {"filters": [{"propertyName": "hs_lastmodifieddate", "operator": "GT", "value": epoch_ms(since)}]}
        ],
        "sorts": [{"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}],
        "properties": ["dealname", "hs_lastmodifieddate"],
        "limit": 10,
    }


@pytest.mark.parametrize("object_type", ["deals", "notes"])
def test_deals_and_notes_state_their_merged_ids_and_their_own_modified_property(
    hubspot_server: HubSpotServer, client: HubSpotClient, object_type: str
) -> None:
    """A real account defines ``hs_merged_object_ids`` on both, unset until a merge, and no ``lastmodifieddate``."""

    hubspot_server.scopes.add(DEALS_READ)
    hubspot_server.add(object_type, modified=MODIFIED)

    (record,) = client.list_page(object_type, after="", limit=10, properties=("hs_merged_object_ids",)).results
    searched = client.search_modified(object_type, since=EPOCH, limit=10, properties=("lastmodifieddate",))

    assert record.properties["hs_merged_object_ids"] is None
    assert (record.modified_at(object_type), searched.total) == (MODIFIED, 1)
    assert "lastmodifieddate" not in searched.results[0].properties


def test_associations_follow_each_records_own_paging_and_one_without_any_has_none(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    """A result with more associations names its own position, sent back with its id alone; HubSpot answers 207."""

    hubspot_server.scopes.add(DEALS_READ)
    hubspot_server.association_page_size = 2
    ada, grace, alan = (contact(hubspot_server, number) for number in range(3))
    party, dinner, alone = (deal(hubspot_server, name) for name in ("Party", "Dinner", "Alone"))
    for person in (ada, grace, alan):
        hubspot_server.associate("deals", party, "contacts", person)
    hubspot_server.associate("deals", dinner, "contacts", grace)

    found = client.associations("deals", "contacts", [party, dinner, alone, party])

    assert found == {party: (ada, grace, alan), dinner: (grace,), alone: ()}
    path = "/crm/associations/2026-09/deals/contacts/batch/read"
    assert [(request.url.path, request_fields(request)) for request in hubspot_server.requests] == [
        (path, {"inputs": [{"id": party}, {"id": dinner}, {"id": alone}]}),
        (path, {"inputs": [{"id": party, "after": "2"}]}),
    ]


def test_association_ids_go_in_batches_of_hubspots_limit(hubspot_server: HubSpotServer, client: HubSpotClient) -> None:
    hubspot_server.scopes.add(DEALS_READ)

    found = client.associations("deals", "contacts", [str(number) for number in range(1001, 2002)])

    assert set(found.values()) == {()}
    assert [len(request_fields(request)["inputs"]) for request in hubspot_server.requests] == [1000, 1]


def no_associations(*ids: str, sub_category: str = "crm.associations.NO_ASSOCIATIONS_FOUND") -> dict[str, Any]:
    """HubSpot's error for ids an association read found nothing for, as a real account states it."""

    return {
        "status": "error",
        "category": "OBJECT_NOT_FOUND",
        "subCategory": sub_category,
        "context": {"fromObjectId": list(ids), "fromObjectType": ["0-3"], "toObjectType": ["0-1"]},
        "message": VENDOR_TEXT,
    }


@pytest.mark.parametrize(
    ("errors", "refusal"),
    [
        pytest.param([no_associations("101")], None, id="named-has-none"),
        pytest.param([], "HubSpot did not answer for some records.", id="omitted"),
        pytest.param([no_associations("999")], "HubSpot did not answer for some records.", id="another-id-named"),
        pytest.param(
            [{**no_associations(), "context": {}}], "HubSpot did not answer for some records.", id="naming-no-id"
        ),
        pytest.param(
            [no_associations("101", sub_category="crm.associations.SOMETHING_ELSE")],
            "HubSpot could not read some records.",
            id="another-error",
        ),
    ],
)
def test_only_an_id_hubspot_names_in_its_no_associations_error_has_none_and_any_other_is_a_transient_refusal(
    hubspot_server: HubSpotServer, client: HubSpotClient, errors: list[dict[str, Any]], refusal: str | None
) -> None:
    """A record is never read without contacts HubSpot did not state; that error says nothing about the record."""

    answer = {"status": "COMPLETE", "results": [], "errors": errors, "numErrors": len(errors)}
    hubspot_server.queue(httpx2.Response(207 if errors else 200, json=answer))

    if refusal is None:
        assert client.associations("deals", "contacts", ["101"]) == {"101": ()}
        return
    with pytest.raises(HubSpotError) as refused:
        client.associations("deals", "contacts", ["101"])
    assert (refused.value.public_message, refused.value.transient) == (refusal, True)


def test_the_scripted_account_answers_an_association_read_in_hubspots_own_shape(
    hubspot_server: HubSpotServer,
) -> None:
    """A real account's 207: a string ``from.id``, an integer ``toObjectId``, and the error naming its ids."""

    hubspot_server.scopes.add(DEALS_READ)
    ada = contact(hubspot_server, 1)
    party, alone = deal(hubspot_server, "Party"), deal(hubspot_server, "Alone")
    hubspot_server.associate("deals", party, "contacts", ada)

    response = HttpClient().request(
        "POST",
        "https://api.hubapi.com/crm/associations/2026-09/deals/contacts/batch/read",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"inputs": [{"id": party}, {"id": alone}]},
    )

    answer = response.json()
    assert (response.status_code, set(answer)) == (
        207,
        {"status", "results", "errors", "numErrors", "startedAt", "completedAt"},
    )
    ((result,), (error,)) = answer["results"], answer["errors"]
    assert (result["from"], [target["toObjectId"] for target in result["to"]]) == ({"id": party}, [int(ada)])
    assert (error["category"], error["subCategory"], error["context"]["fromObjectId"]) == (
        "OBJECT_NOT_FOUND",
        "crm.associations.NO_ASSOCIATIONS_FOUND",
        [alone],
    )
    assert {"fromObjectType", "toObjectType"} <= set(error["context"]) and {"message", "status"} <= set(error)


def test_an_association_read_needs_the_read_scope_of_both_object_types(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    with pytest.raises(HubSpotError) as refusal:
        client.associations("deals", "contacts", ["101"])

    assert refusal.value.public_message == (
        f"The HubSpot service key is missing a scope this connection needs: {DEALS_READ}, {CONTACTS_READ}."
    )


REJECTED ="HubSpot rejected this service key: enter a valid one."
CHECK_THE_KEY = "This key lacks a service key's usual shape: check it for extra or missing characters."
RATE_LIMITED = "HubSpot is rate limiting requests (HTTP 429)."
DAILY_LIMIT = "This HubSpot account has used its daily API request limit."


def unavailable(status: int) -> str:
    """Return integrate's operator message for a transient refused answer of HubSpot's."""

    return f"HubSpot is temporarily unavailable (HTTP {status})."


@pytest.mark.parametrize(
    ("status", "fields", "transient", "wait", "message"),
    [
        pytest.param(401, {"category": "INVALID_AUTHENTICATION"}, False, None, REJECTED, id="401-token-rejected"),
        pytest.param(403, {}, False, None, "HubSpot refused the request (HTTP 403).", id="403-refused"),
        pytest.param(423, {}, True, timedelta(seconds=2), None, id="423-waits-two-seconds"),
        pytest.param(423, {"headers": {"Retry-After": "1"}}, True, timedelta(seconds=2), None, id="423-short-hint"),
        pytest.param(423, {"headers": {"Retry-After": "7"}}, True, timedelta(seconds=7), None, id="423-longer-hint"),
        pytest.param(
            429,
            {"policy_name": "TEN_SECONDLY_ROLLING"},
            True,
            timedelta(seconds=10),
            RATE_LIMITED,
            id="429-ten-secondly",
        ),
        pytest.param(429, {"policy_name": "DAILY"}, True, timedelta(hours=1), DAILY_LIMIT, id="429-daily"),
        pytest.param(
            429,
            {"policy_name": "TEN_SECONDLY_ROLLING", "headers": {"Retry-After": "30"}},
            True,
            timedelta(seconds=30),
            RATE_LIMITED,
            id="429-ten-secondly-retry-after",
        ),
        pytest.param(
            429,
            {"policy_name": "DAILY", "headers": {"Retry-After": "30"}},
            True,
            timedelta(seconds=30),
            DAILY_LIMIT,
            id="429-daily-retry-after",
        ),
        *(
            pytest.param(status, {}, True, None, unavailable(status), id=f"{status}-transient")
            for status in (500, 502, 503, 504, 521, 523, 524, 599)
        ),
        # Integrate's status rule: a 501 refuses the request itself, so the same request brings the same answer.
        pytest.param(501, {}, False, None, "HubSpot refused the request (HTTP 501).", id="501-permanent"),
        pytest.param(
            503,
            {"headers": {"Retry-After": "120"}},
            True,
            timedelta(minutes=2),
            unavailable(503),
            id="503-retry-after",
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
    stranger = HubSpotClient(HttpClient(), {"Authorization": f"Bearer {OTHER_KEY}"})

    with pytest.raises(HubSpotError) as refusal:
        stranger.list_page("contacts", after="", limit=10, properties=())

    assert (refusal.value.status, refusal.value.transient) == (401, False)
    assert refusal.value.public_message == REJECTED


def test_a_rejected_key_of_an_unusual_shape_adds_the_hint_and_nothing_else_does(hubspot_server: HubSpotServer) -> None:
    unusual = HubSpotClient(HttpClient(), {"Authorization": f"Bearer {TOKEN}7"}, usual_key=False)

    with pytest.raises(HubSpotError) as refusal:
        unusual.list_page("contacts", after="", limit=10, properties=())
    assert (refusal.value.status, refusal.value.public_message) == (401, f"{REJECTED} {CHECK_THE_KEY}")

    hubspot_server.scopes = set()
    with pytest.raises(HubSpotError) as missing:
        HubSpotClient(HttpClient(), {"Authorization": f"Bearer {TOKEN}"}, usual_key=False).list_page(
            "contacts", after="", limit=10, properties=()
        )
    assert CHECK_THE_KEY not in missing.value.public_message


@pytest.mark.parametrize(
    ("key", "usual"),
    [
        pytest.param(TOKEN, True, id="the-scripted-key"),
        pytest.param(service_key("a", region="eu1"), True, id="another-region"),
        pytest.param(f"{TOKEN}7", False, id="a-stray-digit"),
        pytest.param(TOKEN[:-1], False, id="a-missing-digit"),
        pytest.param(f" {TOKEN}", False, id="a-leading-space"),
        pytest.param("not a key", False, id="not-a-key"),
    ],
)
def test_a_service_keys_usual_shape_is_pat_a_region_and_a_uuid(key: str, usual: bool) -> None:
    assert (SERVICE_KEY_SHAPE.fullmatch(key) is not None) is usual


@pytest.mark.parametrize(
    ("answer", "message", "transient"),
    [
        pytest.param(
            httpx2.ConnectError("connection refused"), "HubSpot could not be reached.", True, id="unreachable"
        ),
        pytest.param(
            httpx2.Response(200, content=b"{}", headers={"content-length": str(9 * 1024 * 1024)}),
            "HubSpot returned an oversized response.",
            False,
            id="oversized-is-permanent",
        ),
        pytest.param(
            httpx2.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx2.ByteStream(b"not gzip")),
            "HubSpot returned an unreadable response.",
            False,
            id="undecodable-is-permanent",
        ),
    ],
)
def test_a_request_without_a_usable_answer_follows_integrates_rule(
    hubspot_server: HubSpotServer,
    client: HubSpotClient,
    answer: httpx2.Response | Exception,
    message: str,
    transient: bool,
) -> None:
    hubspot_server.queue(answer)

    with pytest.raises(HubSpotError) as refusal:
        client.account()

    assert (refusal.value.public_message, refusal.value.status, refusal.value.transient) == (message, None, transient)


def test_an_answer_that_fails_validation_is_a_permanent_vendor_error(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    hubspot_server.queue(httpx2.Response(200, json={"portalId": "not a number", "uiDomain": "app.hubspot.com"}))

    with pytest.raises(HubSpotError) as refusal:
        client.account()

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
        pytest.param(parse_number, None, None, id="number-unset"),
        pytest.param(parse_number, "", None, id="number-empty"),
        pytest.param(parse_number, "42", Decimal(42), id="number-integer"),
        pytest.param(parse_number, "-3.25", Decimal("-3.25"), id="number-exact-decimal"),
        pytest.param(parse_bool, None, None, id="bool-unset"),
        pytest.param(parse_bool, "", None, id="bool-empty"),
        pytest.param(parse_bool, "true", True, id="bool-true"),
        pytest.param(parse_bool, "FALSE", False, id="bool-false"),
        pytest.param(parse_date, None, None, id="date-unset"),
        pytest.param(parse_date, "", None, id="date-empty"),
        pytest.param(parse_date, "2026-03-01", date(2026, 3, 1), id="date-iso-day"),
        pytest.param(parse_date, "2026-03-01T00:00:00.000Z", date(2026, 3, 1), id="date-midnight-utc-not-shifted"),
        pytest.param(parse_date, epoch_ms(datetime(2026, 3, 1, tzinfo=UTC)), date(2026, 3, 1), id="date-epoch-ms"),
        pytest.param(parse_date, "-86400000", date(1969, 12, 31), id="date-before-epoch"),
        pytest.param(parse_datetime, None, None, id="datetime-unset"),
        pytest.param(
            parse_datetime,
            "2026-11-18T08:34:56-04:00",
            datetime(2026, 11, 18, 12, 34, 56, tzinfo=UTC),
            id="datetime-offset-to-utc",
        ),
        pytest.param(
            parse_datetime,
            epoch_ms(datetime(2026, 11, 18, 12, 34, 56, 789000, tzinfo=UTC)),
            datetime(2026, 11, 18, 12, 34, 56, 789000, tzinfo=UTC),
            id="datetime-epoch-ms",
        ),
    ],
)
def test_each_parser_reads_hubspots_nullable_string(
    parse: Callable[[str | None], Any], raw: str | None, expected: Any
) -> None:
    assert parse(raw) == expected


@pytest.mark.parametrize(
    ("parse", "raw"),
    [
        pytest.param(parse_number, "twelve", id="number-words"),
        pytest.param(parse_number, "NaN", id="number-not-finite"),
        pytest.param(parse_bool, "yes", id="bool-other-word"),
        pytest.param(parse_datetime, "soon", id="datetime-words"),
        pytest.param(parse_datetime, "9" * 30, id="datetime-past-the-calendar"),
        pytest.param(parse_date, "March", id="date-words"),
    ],
)
def test_a_value_not_of_the_parsers_type_is_refused(parse: Callable[[str | None], Any], raw: str) -> None:
    with pytest.raises(ValueError):
        parse(raw)


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


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        pytest.param(
            "2024-03-01T08:30:00.250Z", datetime(2024, 3, 1, 8, 30, 0, 250000, tzinfo=UTC), id="iso-utc"
        ),
        pytest.param("2024-03-01T09:30:00+01:00", datetime(2024, 3, 1, 8, 30, tzinfo=UTC), id="offset-to-utc"),
        pytest.param("soon", None, id="words"),
        pytest.param(None, None, id="null"),
        pytest.param(1709281800000, None, id="not-a-string"),
    ],
)
def test_a_records_creation_time_is_hubspots_created_at_and_an_unreadable_one_is_unset_not_unreadable(
    stated: Any, expected: datetime | None
) -> None:
    record = HubSpotObject.model_validate({"id": "101", "properties": {}, "createdAt": stated})

    assert (record.created_at, record.unreadable) == (expected, False)
    assert HubSpotObject.model_validate({"id": "101", "properties": {}}).created_at is None


def test_every_read_states_a_records_creation_time_without_asking_for_a_property(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    created = datetime(2024, 3, 1, 8, 30, tzinfo=UTC)
    record_id = hubspot_server.add("contacts", {"email": "ada@example.com"}, modified=MODIFIED, created=created)

    (listed,) = client.list_page("contacts", after="", limit=10, properties=()).results
    (searched,) = client.search_modified("contacts", since=EPOCH, limit=10, properties=()).results
    (read,) = client.batch_read("contacts", [record_id], ()).found

    assert [record.created_at for record in (listed, searched, read)] == [created] * 3
