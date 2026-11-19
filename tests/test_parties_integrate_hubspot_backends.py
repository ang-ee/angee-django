"""HubSpot's sync cursor driven over the scripted account, and the neutral contact mapping."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from angee.integrate.states import StreamPhase
from angee.integrate.streams import CursorInvalid
from angee.integrate.testing.http import request_fields
from angee.parties.backends import ParsedAddress, ParsedContact
from angee.parties_integrate_hubspot.backends import OVERLAP, HubSpotCursor, contact_from_properties
from angee.parties_integrate_hubspot.client import (
    EPOCH,
    SEARCH_RESULT_LIMIT,
    HubSpotClient,
    HubSpotPage,
)
from angee.parties_integrate_hubspot.testing.server import TOKEN, HubSpotServer, serve
from angee.parties_integrate_hubspot.testing.server import hubspot_server as hubspot_server

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
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
    between: Callable[[list[str]], None] | None = None,
) -> tuple[HubSpotCursor, list[str]]:
    """Drive one pass page by page, as the backend's extract will; return the closed cursor and every id read.

    The cursor goes through its stored JSON between pages, as the driver persists it.
    ``between`` runs before each following page with the ids read so far.
    """

    cursor = cursor.begin(phase, now=now)
    read: list[str] = []
    page: HubSpotPage
    while cursor.is_open:
        if cursor.since is not None:
            page = client.search_modified(
                "contacts", since=cursor.since, after=cursor.after, limit=limit, properties=()
            )
        else:
            page = client.list_page("contacts", after=cursor.after, limit=limit, properties=())
        read += [record.id for record in page.results]
        cursor = cursor.advance(page, object_type="contacts", limit=limit, ceiling=ceiling)
        cursor = HubSpotCursor.model_validate(cursor.model_dump(mode="json"))
        if between is not None and cursor.is_open:
            between(read)
    return cursor, read


def searches(server: HubSpotServer) -> list[tuple[datetime, str | None]]:
    """Return the lower bound and offset of every search the account answered, in order."""

    asked = [request_fields(request) for request in server.requests if request.url.path.endswith("/search")]
    return [
        (EPOCH + timedelta(milliseconds=int(fields["filterGroups"][0]["filters"][0]["value"])), fields.get("after"))
        for fields in asked
    ]


def page_of(*stated: str, after: str = "") -> HubSpotPage:
    """Return a page of records stating these modified times, with ``after`` as its following position."""

    records = [{"id": str(101 + key), "properties": {"lastmodifieddate": value}} for key, value in enumerate(stated)]
    paging = {"paging": {"next": {"after": after}}} if after else {}
    return HubSpotPage.model_validate({"results": records, **paging})


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
    assert searches(hubspot_server) == [(T0 - timedelta(minutes=1) - OVERLAP, None)]
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
    assert list(dict.fromkeys(second_read)) == [lagging, seen, newer]


def test_a_delta_searches_again_from_each_pages_latest_modified_time(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    hubspot_server.search_ceiling = 4
    ids = [hubspot_server.add("contacts", modified=T0 + timedelta(minutes=minute)) for minute in range(1, 8)]

    closed, read = run_pass(client, HubSpotCursor(watermark=T0), DELTA, now=T0 + timedelta(hours=1), ceiling=4)

    assert list(dict.fromkeys(read)) == ids
    assert searches(hubspot_server) == [(T0 - OVERLAP, None)] + [
        (T0 + timedelta(minutes=minute), None) for minute in range(2, 7)
    ]
    assert closed == HubSpotCursor(watermark=T0 + timedelta(minutes=7))


def test_a_block_of_one_modified_time_pages_by_offset_and_the_search_moves_on_past_it(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    moment = T0 + timedelta(minutes=1)
    block = [hubspot_server.add("contacts", modified=moment) for _ in range(3)]
    beyond = [hubspot_server.add("contacts", modified=moment + timedelta(minutes=minute)) for minute in (1, 2)]

    closed, read = run_pass(client, HubSpotCursor(watermark=T0), DELTA, now=T0 + timedelta(hours=1))

    assert list(dict.fromkeys(read)) == [*block, *beyond]
    assert searches(hubspot_server) == [
        (T0 - OVERLAP, None),
        (moment, None),
        (moment, "2"),
        (moment + timedelta(minutes=1), None),
    ]
    assert closed == HubSpotCursor(watermark=moment + timedelta(minutes=2))


def test_a_block_of_one_modified_time_at_the_ceiling_asks_for_a_new_baseline_seeded_with_the_watermark(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    hubspot_server.search_ceiling = 4
    moment = T0 + timedelta(minutes=1)
    ids = [hubspot_server.add("contacts", modified=moment) for _ in range(6)]

    with pytest.raises(CursorInvalid) as invalid:
        run_pass(client, HubSpotCursor(watermark=T0), DELTA, now=T0 + timedelta(hours=1), ceiling=4)
    seed = HubSpotCursor.model_validate(invalid.value.cursor)
    _, read = run_pass(client, seed, BASELINE, now=T0 + timedelta(hours=2))

    assert searches(hubspot_server) == [(T0 - OVERLAP, None), (moment, None), (moment, "2")]
    assert seed == HubSpotCursor(watermark=moment)
    assert read == ids


def test_begin_continues_an_open_pass_of_the_stream_phase_and_reopens_for_another() -> None:
    walking = HubSpotCursor().begin(BASELINE, now=T0).model_copy(update={"after": "205"})
    later = T0 + timedelta(hours=1)

    assert walking.begin(BASELINE, now=later) is walking
    assert walking.begin(DELTA, now=later) == HubSpotCursor(since=EPOCH, started=later)
    assert HubSpotCursor(watermark=T0).begin(DELTA, now=later).since == T0 - OVERLAP
    with pytest.raises(ValueError):
        HubSpotCursor().advance(HubSpotPage(), object_type="contacts", limit=2)


def test_a_record_whose_modified_time_cannot_be_read_moves_nothing_and_fails_nothing() -> None:
    walking = HubSpotCursor().begin(BASELINE, now=T0)
    searching = HubSpotCursor(watermark=T0).begin(DELTA, now=T0 + timedelta(hours=1))

    walked = walking.advance(page_of("soon", "2026-11-02T11:00:00Z", after="103"), object_type="contacts", limit=2)
    searched = searching.advance(page_of("soon", "9" * 30, after="2"), object_type="contacts", limit=2)

    assert walked == walking.model_copy(update={"after": "103", "watermark": T0 - timedelta(hours=1)})
    assert searched == searching.model_copy(update={"after": "2"})


@settings(deadline=None, max_examples=60)
@given(
    existing=st.lists(st.integers(min_value=-600, max_value=0), unique=True, max_size=8),
    changes=st.lists(
        st.tuples(st.none() | st.integers(min_value=0, max_value=7), st.integers(min_value=1, max_value=59)),
        unique_by=lambda change: change[1],
        max_size=8,
    ),
    edits=st.lists(
        st.tuples(st.integers(min_value=0, max_value=8), st.integers(min_value=0, max_value=20)), max_size=3
    ),
    limit=st.integers(min_value=2, max_value=4),
)
@example(existing=[0], changes=[(None, 10), (None, 20), (None, 30), (None, 40)], edits=[(0, 0)], limit=2)
def test_a_delta_reads_every_change_since_the_baseline_while_read_records_change_under_it(
    existing: list[int], changes: list[tuple[int | None, int]], edits: list[tuple[int, int]], limit: int
) -> None:
    """Records change between the baseline and the delta, and records the delta already read change during it.

    ``changes`` edit a baseline record (by index) or add one, minutes after the baseline;
    ``edits`` change an already-read record after a numbered delta page, as HubSpot stamps
    an edit: after the pass began. Every modified time is distinct; a block of records
    sharing one time is paged by offset, which the block tests pin.
    """

    later = T0 + timedelta(hours=1)
    with serve(HubSpotServer()) as server:
        client = HubSpotClient(HEADERS)
        ids = [server.add("contacts", modified=T0 + timedelta(minutes=minute)) for minute in existing]
        baseline, baseline_read = run_pass(client, HubSpotCursor(), BASELINE, now=T0, limit=limit)
        changed: set[str] = set()
        for target, minute in changes:
            moment = T0 + timedelta(minutes=minute)
            if target is None or not ids:
                changed.add(server.add("contacts", modified=moment))
            else:
                changed.add(key := ids[target % len(ids)])
                server.update("contacts", key, {"jobtitle": f"Changed {minute}"}, modified=moment)
        pages, clock = count(), count(1)

        def edit_read_records(read: list[str]) -> None:
            page = next(pages)
            for key in (read[pick % len(read)] for after_page, pick in edits if after_page == page):
                server.update("contacts", key, {"jobtitle": "Edited"}, modified=later + timedelta(seconds=next(clock)))
                changed.add(key)

        delta, delta_read = run_pass(client, baseline, DELTA, now=later, limit=limit, between=edit_read_records)

    assert baseline_read == ids
    assert baseline.watermark is None or baseline.watermark <= T0
    assert changed <= set(delta_read)
    assert delta.watermark is None or delta.watermark <= later


FULL = {
    "firstname": "Ada",
    "lastname": "Example",
    "email": "ada@example.com",
    "hs_additional_emails": "ada.work@example.com;ADA@example.com;ada@example.com",
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
                emails=(
                    ("ada@example.com", "", True),
                    ("ada.work@example.com", "", False),
                    ("ADA@example.com", "", False),
                ),
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
            {"hs_additional_emails": "one@example.com; two@example.com;one@example.com"},
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
