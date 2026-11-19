"""HubSpot's sync cursor driven over the scripted account, and the neutral contact mapping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from angee.integrate.states import StreamPhase
from angee.integrate.streams import CursorInvalid
from angee.integrate.testing.http import request_fields
from angee.parties.backends import ParsedAddress, ParsedContact
from angee.parties_integrate_hubspot.backends import OVERLAP, HubSpotCursor, contact_from_properties
from angee.parties_integrate_hubspot.client import (
    EPOCH,
    MODIFIED_PROPERTY,
    SEARCH_RESULT_LIMIT,
    HubSpotClient,
    HubSpotPage,
)
from angee.parties_integrate_hubspot.testing.server import TOKEN, HubSpotServer, serve
from angee.parties_integrate_hubspot.testing.server import hubspot_server as hubspot_server

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
MODIFIED = MODIFIED_PROPERTY["contacts"]
BASELINE, DELTA = StreamPhase.BASELINE, StreamPhase.DELTA
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client() -> HubSpotClient:
    return HubSpotClient(HEADERS)


def run_pass(
    client: HubSpotClient,
    cursor: HubSpotCursor,
    phase: StreamPhase,
    *,
    now: datetime,
    limit: int = 2,
    ceiling: int = SEARCH_RESULT_LIMIT,
) -> tuple[HubSpotCursor, list[str]]:
    """Drive one pass page by page, as the backend's extract will; return the closed cursor and the ids read.

    The cursor goes through its stored JSON between pages, as the driver persists it.
    """

    cursor = cursor.begin(phase, now=now)
    read: list[str] = []
    page: HubSpotPage
    while cursor.is_open:
        if phase == DELTA:
            assert cursor.since is not None
            page = client.search_modified(
                "contacts",
                modified_property=MODIFIED,
                since=cursor.since,
                after=cursor.after,
                limit=limit,
                properties=(),
            )
        else:
            page = client.list_page("contacts", after=cursor.after, limit=limit, properties=(MODIFIED,))
        read += [record.id for record in page.results]
        cursor = cursor.advance(phase, page, modified_property=MODIFIED, limit=limit, ceiling=ceiling)
        cursor = HubSpotCursor.model_validate(cursor.model_dump(mode="json"))
    return cursor, read


def searched_since(server: HubSpotServer) -> list[datetime]:
    """Return the lower bound of every search the account answered, in order."""

    return [
        EPOCH + timedelta(milliseconds=int(request_fields(request)["filterGroups"][0]["filters"][0]["value"]))
        for request in server.requests
        if request.url.path.endswith("/search")
    ]


def test_a_baseline_records_its_start_and_walks_every_record_by_id(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    ids = [hubspot_server.add("contacts", modified=T0 - timedelta(days=day)) for day in (3, 1, 2, 5, 4)]

    opened = HubSpotCursor().begin(BASELINE, now=T0)
    closed, read = run_pass(client, HubSpotCursor(), BASELINE, now=T0)

    assert opened == HubSpotCursor(started=T0)
    assert read == ids
    assert closed == HubSpotCursor(watermark=T0 - timedelta(days=1))
    assert not closed.is_open


def test_the_watermark_never_passes_the_start_of_the_pass_that_read_it(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    hubspot_server.add("contacts", modified=T0 - timedelta(hours=1))
    hubspot_server.add("contacts", modified=T0 + timedelta(minutes=3))
    baseline, _ = run_pass(client, HubSpotCursor(), BASELINE, now=T0)
    later = T0 + timedelta(hours=1)
    hubspot_server.add("contacts", modified=later + timedelta(minutes=10))

    delta, _ = run_pass(client, baseline, DELTA, now=later)

    assert baseline.watermark == T0
    assert delta.watermark == later


def test_a_delta_searches_from_the_watermark_minus_the_overlap(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    hubspot_server.add("contacts", modified=T0 - timedelta(hours=2))
    recent = hubspot_server.add("contacts", modified=T0 - timedelta(minutes=1))
    baseline, _ = run_pass(client, HubSpotCursor(), BASELINE, now=T0)
    changed = hubspot_server.add("contacts", modified=T0 + timedelta(minutes=10))
    later = T0 + timedelta(hours=1)

    opened = baseline.begin(DELTA, now=later)
    delta, read = run_pass(client, baseline, DELTA, now=later)

    assert (opened.since, opened.started, opened.after) == (T0 - timedelta(minutes=1) - OVERLAP, later, "")
    assert read == [recent, changed]
    assert searched_since(hubspot_server) == [T0 - timedelta(minutes=1) - OVERLAP]
    assert delta == HubSpotCursor(watermark=T0 + timedelta(minutes=10))


def test_a_record_inside_the_overlap_appears_in_both_windows_and_a_lagging_one_is_caught(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    first = HubSpotCursor(watermark=T0)
    seen = hubspot_server.add("contacts", modified=T0 + timedelta(minutes=20))
    lagging = hubspot_server.add("contacts", modified=T0 + timedelta(minutes=18))
    hubspot_server.unindexed.add(lagging)

    after_first, first_read = run_pass(client, first, DELTA, now=T0 + timedelta(minutes=30))
    hubspot_server.unindexed.clear()
    newer = hubspot_server.add("contacts", modified=T0 + timedelta(minutes=40))
    _, second_read = run_pass(client, after_first, DELTA, now=T0 + timedelta(hours=1))

    assert first_read == [seen]
    assert after_first.watermark == T0 + timedelta(minutes=20)
    assert second_read == [lagging, seen, newer]


def test_a_delta_query_at_the_search_ceiling_restarts_from_the_last_modified_time_it_saw(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    hubspot_server.search_ceiling = 4
    ids = [hubspot_server.add("contacts", modified=T0 + timedelta(minutes=minute)) for minute in range(1, 8)]

    closed, read = run_pass(client, HubSpotCursor(watermark=T0), DELTA, now=T0 + timedelta(hours=1), ceiling=4)

    assert read == [*ids[:4], *ids[3:]]
    assert searched_since(hubspot_server) == [T0 - OVERLAP] * 2 + [T0 + timedelta(minutes=4)] * 2
    assert closed == HubSpotCursor(watermark=T0 + timedelta(minutes=7))


def test_a_ceiling_of_one_modified_time_asks_for_a_new_baseline_seeded_with_the_watermark(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    hubspot_server.search_ceiling = 4
    moment = T0 + timedelta(minutes=1)
    ids = [hubspot_server.add("contacts", modified=moment) for _ in range(6)]

    with pytest.raises(CursorInvalid) as invalid:
        run_pass(client, HubSpotCursor(watermark=T0), DELTA, now=T0 + timedelta(hours=1), ceiling=4)
    seed = HubSpotCursor.model_validate(invalid.value.cursor)
    _, read = run_pass(client, seed, BASELINE, now=T0 + timedelta(hours=2))

    assert seed == HubSpotCursor(watermark=moment)
    assert read == ids


def test_begin_continues_an_open_pass_of_the_stream_phase_and_reopens_for_another() -> None:
    walking = HubSpotCursor().begin(BASELINE, now=T0).model_copy(update={"after": "205"})
    later = T0 + timedelta(hours=1)

    assert walking.begin(BASELINE, now=later) is walking
    assert walking.begin(DELTA, now=later) == HubSpotCursor(since=EPOCH, started=later)
    assert HubSpotCursor(watermark=T0).begin(DELTA, now=later).since == T0 - OVERLAP
    with pytest.raises(ValueError):
        HubSpotCursor().advance(BASELINE, HubSpotPage(), modified_property=MODIFIED, limit=2)


@settings(deadline=None, max_examples=25)
@given(
    offsets=st.lists(st.integers(min_value=-600, max_value=600), max_size=12),
    limit=st.integers(min_value=1, max_value=5),
)
def test_a_baseline_then_a_delta_reads_every_change_without_passing_either_start(
    offsets: list[int], limit: int
) -> None:
    later = T0 + timedelta(hours=1)
    with serve(HubSpotServer()) as server:
        client = HubSpotClient(HEADERS)
        modified = {server.add("contacts", modified=T0 + timedelta(minutes=offset)): offset for offset in offsets}
        times = [T0 + timedelta(minutes=offset) for offset in offsets]

        baseline, baseline_read = run_pass(client, HubSpotCursor(), BASELINE, now=T0, limit=limit)
        delta, delta_read = run_pass(client, baseline, DELTA, now=later, limit=limit)

    assert baseline_read == list(modified)
    assert baseline.watermark == (min(max(times), T0) if times else None)
    since = baseline.watermark - OVERLAP if baseline.watermark is not None else EPOCH
    changed = [key for key, offset in modified.items() if T0 + timedelta(minutes=offset) >= since]
    assert sorted(delta_read, key=int) == changed
    assert delta.watermark is None or delta.watermark <= later


FULL = {
    "firstname": "Ada",
    "lastname": "Example",
    "email": "ada@example.com",
    "hs_additional_emails": "ada.work@example.com;ADA@example.com",
    "phone": "+1 555 0100",
    "mobilephone": "+1 555 0101",
    "jobtitle": "Engineer",
    "company": "Example Analytical Engines",
    "address": "1 Example Street",
    "city": "Springfield",
    "state": "Example State",
    "zip": "00000",
    "country": "United States",
    "favourite_colour": "green",
}


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        pytest.param(
            FULL,
            ParsedContact(
                display_name="Ada Example",
                given_name="Ada",
                family_name="Example",
                title="Engineer",
                organization="Example Analytical Engines",
                emails=(("ada@example.com", "", True), ("ada.work@example.com", "", False)),
                phones=(("+1 555 0100", "", False), ("+1 555 0101", "cell", False)),
                addresses=(
                    ParsedAddress(
                        street="1 Example Street",
                        city="Springfield",
                        region="Example State",
                        postal_code="00000",
                        country="United States",
                    ),
                ),
            ),
            id="full",
        ),
        pytest.param(
            {"email": "grace@example.com", "firstname": None, "lastname": ""},
            ParsedContact(display_name="grace@example.com", emails=(("grace@example.com", "", True),)),
            id="minimal",
        ),
        pytest.param(
            {"firstname": "Ada", "lastname": None, "phone": "+1 555 0100", "email": None},
            ParsedContact(display_name="Ada", given_name="Ada", phones=(("+1 555 0100", "", False),)),
            id="no-email",
        ),
        pytest.param(
            {"hs_additional_emails": "one@example.com; two@example.com"},
            ParsedContact(emails=(("one@example.com", "", False), ("two@example.com", "", False))),
            id="additional-emails-without-a-primary",
        ),
        pytest.param(
            {"firstname": "Ada", "address": "", "city": None, "zip": " "},
            ParsedContact(display_name="Ada", given_name="Ada"),
            id="no-address",
        ),
        pytest.param(
            {"country": "Iceland"},
            ParsedContact(addresses=(ParsedAddress(country="Iceland"),)),
            id="one-postal-field-is-an-address",
        ),
    ],
)
def test_the_contact_mapper_builds_the_neutral_contact(
    properties: dict[str, str | None], expected: ParsedContact
) -> None:
    assert contact_from_properties(properties) == expected
