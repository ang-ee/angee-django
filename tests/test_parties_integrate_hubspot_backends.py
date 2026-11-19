"""HubSpot's sync cursor driven over the scripted account, and the neutral contact mapping."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from angee.integrate.http import HttpClient
from angee.integrate.states import StreamPhase
from angee.integrate.testing.http import request_fields
from angee.parties.backends import ParsedAddress, ParsedContact
from angee.parties_integrate_hubspot.backends import OVERLAP, HubSpotCursor, contact_from_properties
from angee.parties_integrate_hubspot.client import EPOCH, HubSpotClient, HubSpotError, HubSpotPage
from angee.parties_integrate_hubspot.testing.server import TOKEN, HubSpotServer, serve
from angee.parties_integrate_hubspot.testing.server import hubspot_server as hubspot_server

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
BASELINE, DELTA = StreamPhase.BASELINE, StreamPhase.DELTA
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client() -> HubSpotClient:
    return HubSpotClient(HttpClient(), HEADERS)


def run_pass(
    server: HubSpotServer,
    cursor: HubSpotCursor,
    phase: StreamPhase,
    *,
    now: datetime,
    limit: int = 2,
    between: Callable[[list[str]], None] | None = None,
    drains: dict[datetime, list[str]] | None = None,
) -> tuple[HubSpotCursor, list[str]]:
    """Drive one pass page by page at HubSpot's time ``now``, as the backend's extract does.

    Returns the closed cursor and every id read. The cursor goes through its stored
    JSON between pages, as the driver persists it. ``between`` runs before each
    following page with the ids read so far; ``drains`` collects the ids each drain
    read, by the modified time of its block.
    """

    server.clock = now
    client = HubSpotClient(HttpClient(), HEADERS)
    cursor = cursor.begin(phase)
    read: list[str] = []
    while True:
        page = cursor.fetch(client, "contacts", limit=limit, properties=())
        if drains is not None and cursor.block is not None:
            drains.setdefault(cursor.block, []).extend(record.id for record in page.results)
        read += [record.id for record in page.results]
        cursor = cursor.advance(page, object_type="contacts")
        cursor = HubSpotCursor.model_validate(cursor.model_dump(mode="json"))
        if not cursor.is_open:
            return cursor, read
        if between is not None:
            between(read)


def searches(server: HubSpotServer) -> list[tuple[str, datetime, str]]:
    """Return every search the account answered, in order.

    Each is its modified-time operator and time, and the record id it reads above
    (``""`` for none).
    """

    asked = [request_fields(request) for request in server.requests if request.url.path.endswith("/search")]
    stated = [{item["propertyName"]: item for item in fields["filterGroups"][0]["filters"]} for fields in asked]
    return [
        (
            filters["lastmodifieddate"]["operator"],
            EPOCH + timedelta(milliseconds=int(filters["lastmodifieddate"]["value"])),
            filters.get("hs_object_id", {}).get("value", ""),
        )
        for filters in stated
    ]


def page_of(*stated: str, after: str = "", sent_at: datetime | None = None) -> HubSpotPage:
    """Return a page of records stating these modified times, with ``after`` as its following position."""

    records = [{"id": str(101 + key), "properties": {"lastmodifieddate": value}} for key, value in enumerate(stated)]
    paging = {"paging": {"next": {"after": after}}} if after else {}
    return HubSpotPage.model_validate({"results": records, **paging, "sent_at": sent_at})


def test_a_baseline_records_its_start_and_walks_every_record_by_id(hubspot_server: HubSpotServer) -> None:
    ids = [hubspot_server.add("contacts", modified=T0 - timedelta(days=day)) for day in (3, 1, 2, 5, 4)]

    opening = HubSpotCursor().begin(BASELINE)
    first = opening.advance(page_of("2026-10-30T12:00:00Z", after="102", sent_at=T0), object_type="contacts")
    closed, read = run_pass(hubspot_server, HubSpotCursor(), BASELINE, now=T0)

    assert (opening, opening.is_open) == (HubSpotCursor(), False)
    assert (first.started, first.after, first.is_open) == (T0, "102", True)
    assert read == ids
    assert closed == HubSpotCursor(watermark=T0 - timedelta(days=1), caught_up=True)
    assert not closed.is_open


def test_the_watermark_never_passes_the_start_of_the_pass_that_read_it(hubspot_server: HubSpotServer) -> None:
    hubspot_server.add("contacts", modified=T0 - timedelta(hours=1))
    hubspot_server.add("contacts", modified=T0 + timedelta(minutes=3))
    baseline, _ = run_pass(hubspot_server, HubSpotCursor(), BASELINE, now=T0)
    later = T0 + timedelta(hours=1)
    hubspot_server.add("contacts", modified=later + timedelta(minutes=10))

    delta, _ = run_pass(hubspot_server, baseline, DELTA, now=later)

    assert baseline.watermark == T0
    assert delta.watermark == later


def test_a_delta_searches_from_the_watermark_minus_the_overlap(hubspot_server: HubSpotServer) -> None:
    hubspot_server.add("contacts", modified=T0 - timedelta(hours=2))
    recent = hubspot_server.add("contacts", modified=T0 - timedelta(minutes=1))
    baseline, _ = run_pass(hubspot_server, HubSpotCursor(), BASELINE, now=T0)
    changed = hubspot_server.add("contacts", modified=T0 + timedelta(minutes=10))
    later = T0 + timedelta(hours=1)

    opened = baseline.begin(DELTA)
    delta, read = run_pass(hubspot_server, baseline, DELTA, now=later)

    assert not baseline.caught_up
    assert (opened.since, opened.started, opened.after) == (T0 - timedelta(minutes=1) - OVERLAP, None, "")
    assert read == [recent, changed]
    assert searches(hubspot_server) == [("GT", T0 - timedelta(minutes=1) - OVERLAP, "")]
    assert delta == HubSpotCursor(watermark=T0 + timedelta(minutes=10), caught_up=True)


def test_a_change_the_search_shows_late_within_the_overlap_is_read_by_the_next_pass(
    hubspot_server: HubSpotServer,
) -> None:
    """A change HubSpot's search index shows four minutes late, made two minutes before a pass, misses that pass."""

    start = T0 + timedelta(minutes=30)
    seen = hubspot_server.add("contacts", modified=start - timedelta(minutes=1))
    lagging = hubspot_server.add("contacts", modified=start - timedelta(minutes=2), lag=timedelta(minutes=4))

    after_first, first_read = run_pass(hubspot_server, HubSpotCursor(watermark=T0), DELTA, now=start)
    newer = hubspot_server.add("contacts", modified=start + timedelta(minutes=3))
    _, second_read = run_pass(hubspot_server, after_first, DELTA, now=start + timedelta(minutes=3))

    assert first_read == [seen]
    assert after_first.watermark == start - timedelta(minutes=1)
    assert list(dict.fromkeys(second_read)) == [lagging, seen, newer]


def test_a_change_the_search_shows_late_during_a_long_pass_is_read_by_the_next_one(
    hubspot_server: HubSpotServer,
) -> None:
    """The delta's watermark stops at its pass's start too, or a long pass would step past a late change for good.

    While the pass reads, HubSpot changes a record a minute, and the first of those
    changes reaches the search index three minutes late: by then the pass has read
    past its modified time. The pass goes on for twelve minutes and reads changes
    made then, more than the overlap after the late one.
    """

    start = T0 + timedelta(hours=1)
    for minute in (3, 2, 1):
        hubspot_server.add("contacts", modified=start - timedelta(minutes=minute))
    late: list[str] = []

    def keep_changing(read: list[str]) -> None:
        del read
        moment = hubspot_server.clock + timedelta(minutes=1)
        if moment > start + timedelta(minutes=12):
            return
        if not late:
            late.append(hubspot_server.add("contacts", modified=moment, lag=timedelta(minutes=3)))
        hubspot_server.add("contacts", modified=moment + timedelta(seconds=1))

    first, first_read = run_pass(hubspot_server, HubSpotCursor(watermark=T0), DELTA, now=start, between=keep_changing)
    _, second_read = run_pass(hubspot_server, first, DELTA, now=start + timedelta(minutes=20))

    assert late[0] not in first_read
    assert late[0] in second_read
    assert first.watermark == start


def test_a_delta_searches_again_after_the_last_time_each_page_read_in_full(hubspot_server: HubSpotServer) -> None:
    ids = [hubspot_server.add("contacts", modified=T0 + timedelta(minutes=minute)) for minute in range(1, 8)]

    closed, read = run_pass(hubspot_server, HubSpotCursor(watermark=T0), DELTA, now=T0 + timedelta(hours=1))

    assert list(dict.fromkeys(read)) == ids
    assert searches(hubspot_server) == [("GT", T0 - OVERLAP, "")] + [
        ("GT", T0 + timedelta(minutes=minute), "") for minute in range(1, 6)
    ]
    assert closed == HubSpotCursor(watermark=T0 + timedelta(minutes=7), caught_up=True)


def test_a_block_of_one_modified_time_is_drained_by_id_from_its_lowest_and_the_search_moves_on_past_it(
    hubspot_server: HubSpotServer,
) -> None:
    moment = T0 + timedelta(minutes=1)
    block = [hubspot_server.add("contacts", modified=moment) for _ in range(3)]
    beyond = [hubspot_server.add("contacts", modified=moment + timedelta(minutes=minute)) for minute in (1, 2)]
    drains: dict[datetime, list[str]] = {}

    later = T0 + timedelta(hours=1)
    closed, read = run_pass(hubspot_server, HubSpotCursor(watermark=T0), DELTA, now=later, drains=drains)

    assert read == [block[2], block[1], *block, *beyond]
    assert drains == {moment: block}
    assert searches(hubspot_server) == [
        ("GT", T0 - OVERLAP, ""),
        ("EQ", moment, ""),
        ("EQ", moment, block[1]),
        ("GT", moment, ""),
    ]
    assert closed == HubSpotCursor(watermark=moment + timedelta(minutes=2), caught_up=True)


def test_a_request_asks_for_no_more_records_than_hubspots_page_limits(
    hubspot_server: HubSpotServer, client: HubSpotClient
) -> None:
    searching = HubSpotCursor(watermark=T0).begin(DELTA)

    for cursor in (HubSpotCursor().begin(BASELINE), searching, searching.model_copy(update={"block": T0})):
        cursor.fetch(client, "contacts", limit=500, properties=())

    listing, search, drain = hubspot_server.requests
    assert listing.url.params["limit"] == "100"
    assert [request_fields(request)["limit"] for request in (search, drain)] == [200, 200]


def test_begin_continues_an_open_pass_of_the_stream_phase_and_reopens_for_another() -> None:
    walking = HubSpotCursor(started=T0, after="205")

    assert walking.begin(BASELINE) is walking
    assert walking.begin(DELTA) == HubSpotCursor(since=EPOCH)
    assert HubSpotCursor(watermark=T0).begin(DELTA).since == T0 - OVERLAP
    assert HubSpotCursor(watermark=T0, caught_up=True).begin(DELTA) == HubSpotCursor(
        since=T0, watermark=T0, caught_up=True
    )
    with pytest.raises(HubSpotError) as undated:
        HubSpotCursor().begin(DELTA).advance(HubSpotPage(), object_type="contacts")
    assert (undated.value.public_message, undated.value.transient) == (
        "HubSpot sent an answer without a readable Date header.",
        False,
    )


def test_an_unreadable_modified_time_moves_nothing_and_a_page_of_changes_with_none_is_refused() -> None:
    walking = HubSpotCursor(started=T0)
    searching = HubSpotCursor(since=T0 - OVERLAP, started=T0 + timedelta(hours=1), watermark=T0)
    draining = searching.model_copy(update={"block": T0, "after": "100"})

    walked = walking.advance(page_of("soon", "2026-11-02T11:00:00Z", after="103"), object_type="contacts")
    drained = draining.advance(page_of("soon", "soon", after="2"), object_type="contacts")
    searched = searching.advance(page_of("soon", "2026-11-02T13:30:00Z", "late", after="3"), object_type="contacts")

    assert walked == walking.model_copy(update={"after": "103", "watermark": T0 - timedelta(hours=1)})
    assert drained == draining.model_copy(update={"after": "102"})
    assert searched == searching.model_copy(
        update={"block": T0 + timedelta(minutes=90), "watermark": T0 + timedelta(hours=1)}
    )
    with pytest.raises(HubSpotError) as refused:
        searching.advance(page_of("soon", "9" * 30, after="2"), object_type="contacts")
    assert refused.value.public_message == (
        "HubSpot sent a page of changed records without a readable modified time."
    )
    assert not refused.value.transient
    assert searching.advance(page_of("soon"), object_type="contacts") == HubSpotCursor(watermark=T0, caught_up=True)


@settings(deadline=None, max_examples=80)
@given(
    existing=st.lists(st.integers(min_value=-600, max_value=0), max_size=6),
    changes=st.lists(
        st.tuples(st.none() | st.integers(min_value=0, max_value=5), st.integers(min_value=1, max_value=4)),
        max_size=10,
    ),
    edits=st.lists(
        st.tuples(st.integers(min_value=0, max_value=10), st.integers(min_value=0, max_value=30)), max_size=4
    ),
    limit=st.integers(min_value=2, max_value=3),
)
@example(existing=[0], changes=[(None, 10)] * 5, edits=[(1, 1)], limit=2)
@example(existing=[0], changes=[(None, 10)] * 5, edits=[(2, 0), (3, 4)], limit=2)
def test_a_delta_reads_every_change_since_the_baseline_while_read_records_change_under_it(
    existing: list[int], changes: list[tuple[int | None, int]], edits: list[tuple[int, int]], limit: int
) -> None:
    """Records change between the baseline and the delta, and records the delta already read change during it.

    ``changes`` edit a baseline record (by index) or add one, minutes after the baseline;
    a few minutes for many changes make blocks of one modified time larger than a page.
    ``edits`` change already-read records between two delta requests, inside the block
    being read or before it, stamped as HubSpot stamps an edit: after the pass began, one
    time for every edit made between the same two requests. Every record changed since
    the baseline is read by the time the delta ends, and no drain reads a record twice.
    """

    later = T0 + timedelta(hours=1)
    with serve(HubSpotServer()) as server:
        ids = [server.add("contacts", modified=T0 + timedelta(minutes=minute)) for minute in existing]
        baseline, baseline_read = run_pass(server, HubSpotCursor(), BASELINE, now=T0, limit=limit)
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
            page, moment = next(pages), later + timedelta(seconds=next(clock))
            for key in {read[pick % len(read)] for after_page, pick in edits if after_page == page}:
                server.update("contacts", key, {"jobtitle": "Edited"}, modified=moment)
                changed.add(key)

        drains: dict[datetime, list[str]] = {}
        delta, delta_read = run_pass(
            server, baseline, DELTA, now=later, limit=limit, between=edit_read_records, drains=drains
        )

    assert baseline_read == ids
    assert baseline.watermark is None or baseline.watermark <= T0
    assert changed <= set(delta_read)
    assert all(len(drained) == len(set(drained)) for drained in drains.values())
    assert delta.watermark is None or delta.watermark <= later


EDIT, WAIT, POLL = "edit", "wait", "poll"


@settings(deadline=None, max_examples=120)
@given(
    existing=st.lists(st.integers(min_value=-900, max_value=0), min_size=1, max_size=4),
    steps=st.lists(
        st.tuples(st.just(EDIT), st.none() | st.integers(min_value=0, max_value=7), st.integers(0, 300_000))
        | st.tuples(st.just(WAIT), st.none(), st.integers(min_value=1, max_value=600_000))
        | st.tuples(st.just(POLL), st.none(), st.just(0)),
        max_size=30,
    ),
    limit=st.integers(min_value=2, max_value=3),
)
@example(existing=[0], steps=[(EDIT, None, 300_000), (POLL, None, 0), (WAIT, None, 300_000), (POLL, None, 0)], limit=2)
@example(existing=[0], steps=[(WAIT, None, 1), (EDIT, None, 0)], limit=2)
@example(
    existing=[-1, 0],
    steps=[(EDIT, 0, 0), (EDIT, 1, 240_000), (WAIT, None, 120_000), (POLL, None, 0), (WAIT, None, 179_999)]
    + [(POLL, None, 0)],
    limit=2,
)
def test_polls_through_bursts_late_index_entries_and_quiet_read_every_change_then_send_one_search(
    existing: list[int], steps: list[tuple[str, int | None, int]], limit: int
) -> None:
    """Polls at any times, with changes in bursts that HubSpot's search index shows up to the overlap late.

    ``existing`` are seconds before the baseline. Each step changes a record (by
    index, or a new one) at HubSpot's present time, its search entry ``amount``
    milliseconds late, at most :data:`OVERLAP`; or lets ``amount`` milliseconds pass;
    or polls. A pass runs at once, at HubSpot's time. Every record's latest change is
    read by a pass at or after it. Once the overlap and a second (the resolution of
    the ``Date`` a pass starts at) have passed since the last change, one more poll
    catches up with the watermark, and from then on a poll sends one search and reads nothing.
    """

    with serve(HubSpotServer()) as server:
        server.clock = T0
        ids = [server.add("contacts", modified=T0 + timedelta(seconds=second)) for second in existing]
        changed = {key: server.records["contacts"][key]["modified"] for key in ids}
        read_at: dict[str, datetime] = {}

        def poll(cursor: HubSpotCursor, phase: StreamPhase) -> HubSpotCursor:
            closed, read = run_pass(server, cursor, phase, now=server.clock, limit=limit)
            read_at.update(dict.fromkeys(read, server.clock))
            return closed

        cursor = poll(HubSpotCursor(), BASELINE)
        for action, target, amount in steps:
            if action == EDIT:
                moment, lag = server.clock, timedelta(milliseconds=amount)
                if target is None:
                    ids.append(key := server.add("contacts", modified=moment, lag=lag))
                else:
                    key = ids[target % len(ids)]
                    server.update("contacts", key, {"jobtitle": "Changed"}, modified=moment, lag=lag)
                changed[key] = moment
            elif action == WAIT:
                server.clock += timedelta(milliseconds=amount)
            else:
                cursor = poll(cursor, DELTA)
        server.clock += OVERLAP + timedelta(seconds=1)
        cursor = poll(cursor, DELTA)
        server.clock += timedelta(seconds=1)
        server.requests.clear()
        quiet, quiet_read = run_pass(server, cursor, DELTA, now=server.clock, limit=limit)

    assert {key: moment for key, moment in changed.items() if read_at.get(key, EPOCH) < moment} == {}
    assert (len(server.requests), quiet_read, quiet) == (1, [], cursor)


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
