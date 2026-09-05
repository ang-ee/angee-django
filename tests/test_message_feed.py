"""Native Django/REBAC contracts for stable messaging and Nexus feed cursors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory
from rebac import actor_context, system_context

from tests.conftest import execute_schema, result_data
from tests.test_messaging import (
    Circle,
    CircleMember,
    Handle,
    Message,
    Participant,
    Party,
    Thread,
    ThreadAttachment,
    ThreadedTicket,
)
from tests.test_nexus import _grant, _schema, nexus_tables  # noqa: F401

pytestmark = pytest.mark.usefixtures("nexus_tables")

T0 = datetime(2026, 1, 10, 12, tzinfo=UTC)
User = get_user_model()


def _messages(owner: Any, *, size: int = 5) -> tuple[Any, list[Any]]:
    """Seed one owner-visible inbox thread with equal send times."""

    with system_context(reason="test message feed seed"):
        thread = Thread._base_manager.create(created_by=owner, platform="email")
        rows = [
            Message._base_manager.create(
                thread=thread,
                created_by=owner,
                platform="email",
                sent_at=T0,
                preview=f"needle {index}",
                external_id=f"feed-{thread.pk}-{index}",
            )
            for index in range(size)
        ]
    return thread, rows


def _query(kind: str, owner: Any, root: Any, **variables: Any) -> Any:
    """Execute the real composed GraphQL field under its current request actor."""

    request = RequestFactory().post("/graphql/console/")
    request.user = owner
    return execute_schema(
        _schema(),
        f"""
        query Feed($root: ID!, $before: String, $after: String, $limit: Int!, $search: String!) {{
          page: {kind}_message_feed(
            {kind}_id: $root, before_cursor: $before, after_cursor: $after, limit: $limit, search: $search
          ) {{
            messages {{ id preview sent_at created_at }}
            count older_cursor newer_cursor has_older has_newer
          }}
        }}
        """,
        {"root": str(root.sqid), "before": None, "after": None, "limit": 2, "search": "", **variables},
        request=request,
    )


def _page(kind: str, owner: Any, root: Any, **variables: Any) -> dict[str, Any]:
    return result_data(_query(kind, owner, root, **variables))["page"]


def _ids(page: dict[str, Any]) -> list[str]:
    return [row["id"] for row in page["messages"]]


def test_equal_timestamps_traverse_both_directions_without_gaps() -> None:
    """Database PK breaks a page boundary containing only equal timestamps."""

    owner = User.objects.create_user(username="feed-ties")
    thread, rows = _messages(owner)
    first = _page("thread", owner, thread)
    second = _page("thread", owner, thread, before=first["older_cursor"])
    third = _page("thread", owner, thread, before=second["older_cursor"])
    newer = _page("thread", owner, thread, after=third["newer_cursor"])
    newest = _page("thread", owner, thread, after=newer["newer_cursor"])

    assert _ids(first) == [str(row.sqid) for row in rows[4:2:-1]]
    assert _ids(second) == [str(row.sqid) for row in rows[2:0:-1]]
    assert _ids(third) == [str(rows[0].sqid)]
    assert _ids(newer) == _ids(second)
    assert _ids(newest) == _ids(first)
    assert first["count"] == second["count"] == third["count"] == 5
    assert (first["has_older"], first["has_newer"]) == (True, False)
    assert (second["has_older"], second["has_newer"]) == (True, True)
    assert (third["has_older"], third["has_newer"]) == (False, True)
    assert not _page("thread", owner, thread, before=third["older_cursor"])["messages"]


def test_null_sent_times_share_the_same_stable_coalesced_order() -> None:
    """A full page of null sent times still has a strictly advancing cursor."""

    owner = User.objects.create_user(username="feed-null")
    thread, rows = _messages(owner)
    with system_context(reason="test null send times"):
        Message._base_manager.filter(thread=thread).update(sent_at=None, created_at=T0)
        Message._base_manager.filter(pk=rows[1].pk).update(sent_at=T0 + timedelta(days=1))
    first = _page("thread", owner, thread)
    second = _page("thread", owner, thread, before=first["older_cursor"])
    third = _page("thread", owner, thread, before=second["older_cursor"])

    assert _ids(first) + _ids(second) + _ids(third) == [str(rows[index].sqid) for index in (1, 4, 3, 2, 0)]
    assert first["older_cursor"] and second["older_cursor"]


@pytest.mark.parametrize("change", ["delete", "edit", "revoke", "move"])
def test_cursor_position_survives_anchor_changes(change: str) -> None:
    """Continuation uses the signed tuple after an anchor is gone or moved."""

    owner = User.objects.create_user(username=f"feed-anchor-{change}")
    other = User.objects.create_user(username=f"feed-other-{change}")
    thread, rows = _messages(owner)
    first = _page("thread", owner, thread)
    anchor = rows[3]
    with system_context(reason="test changed feed cursor anchor"):
        if change == "delete":
            anchor.delete()
        elif change == "edit":
            Message._base_manager.filter(pk=anchor.pk).update(sent_at=T0 - timedelta(days=1))
        elif change == "revoke":
            Message._base_manager.filter(pk=anchor.pk).update(created_by=other)
        else:
            elsewhere = Thread._base_manager.create(created_by=owner, platform="email")
            Message._base_manager.filter(pk=anchor.pk).update(thread=elsewhere)
    second = _page("thread", owner, thread, before=first["older_cursor"])
    assert _ids(second) == [str(rows[2].sqid), str(rows[1].sqid)]


@pytest.mark.parametrize("change", ["tamper", "root", "search", "actor", "direction", "empty"])
def test_cursor_rejects_different_scope_or_invalid_input(change: str) -> None:
    """A cursor is signed for one actor/root/search and one boundary direction."""

    owner = User.objects.create_user(username=f"feed-cursor-{change}")
    thread, rows = _messages(owner)
    first = _page("thread", owner, thread)
    cursor = first["older_cursor"]
    options: dict[str, Any] = {"before": cursor}
    if change == "tamper":
        options["before"] = cursor + "x"
    elif change == "root":
        thread, _ = _messages(owner)
    elif change == "search":
        options["search"] = "needle"
    elif change == "actor":
        reader = User.objects.create_user(username="feed-cursor-reader")
        _grant(thread, "reader", reader)
        for row in rows:
            _grant(row, "reader", reader)
        owner = reader
    elif change == "direction":
        options["after"] = cursor
    else:
        options["before"] = ""
    result = _query("thread", owner, thread, **options)
    assert result.errors
    assert result.data is None


def test_search_cursor_uses_the_same_normalized_predicate() -> None:
    """Whitespace normalization does not fork identical search scopes."""

    owner = User.objects.create_user(username="feed-search")
    thread, rows = _messages(owner)
    first = _page("thread", owner, thread, search="  needle\t")
    second = _page("thread", owner, thread, search="needle", before=first["older_cursor"])
    assert _ids(second) == [str(rows[2].sqid), str(rows[1].sqid)]


def test_each_page_rechecks_message_and_root_permissions() -> None:
    """An existing cursor neither grants message access nor keeps a root readable."""

    owner = User.objects.create_user(username="feed-permissions")
    other = User.objects.create_user(username="feed-permissions-other")
    thread, rows = _messages(owner)
    first = _page("thread", owner, thread)
    with system_context(reason="test feed permission loss"):
        Message._base_manager.filter(pk=rows[2].pk).update(created_by=other)
    second = _page("thread", owner, thread, before=first["older_cursor"])
    assert _ids(second) == [str(rows[1].sqid), str(rows[0].sqid)]
    assert second["count"] == 4
    with system_context(reason="test feed root permission loss"):
        Thread._base_manager.filter(pk=thread.pk).update(created_by=other)
    assert _query("thread", owner, thread, before=first["older_cursor"]).errors


def test_record_chatter_never_enters_the_inbox_thread_feed() -> None:
    """Even a readable record-attached thread stays behind its record gate."""

    owner = User.objects.create_user(username="feed-record")
    thread, _ = _messages(owner)
    with system_context(reason="test feed record attachment"):
        record = ThreadedTicket._base_manager.create(title="Private record", created_by=owner)
        ThreadAttachment._base_manager.create(
            thread=thread,
            content_type=ContentType.objects.get_for_model(ThreadedTicket),
            object_id=record.pk,
            created_by=owner,
        )
    assert _query("thread", owner, thread).errors


def _party_scope(owner: Any, rows: list[Any]) -> tuple[Any, Any, Any]:
    """Attach visible participants and a visible circle membership to a party."""

    with system_context(reason="test party feed scope seed"):
        party = Party._base_manager.create(display_name="Feed party", created_by=owner)
        handle = Handle._base_manager.create(
            platform="email",
            value=f"party-{party.pk}@example.com",
            party=party,
            created_by=owner,
        )
        for row in rows:
            Participant._base_manager.create(
                message=row,
                thread_id=row.thread_id,
                handle=handle,
                role="to",
                created_by=owner,
            )
        circle = Circle._base_manager.create(name="Feed circle", created_by=owner)
        member = CircleMember._base_manager.create(circle=circle, party=party, created_by=owner)
    return party, circle, member


def test_party_and_circle_feeds_share_order_but_keep_cursor_scopes_separate() -> None:
    """Both timeline owners expose the same page contract and canonical order."""

    owner = User.objects.create_user(username="feed-nexus")
    thread, rows = _messages(owner)
    party, circle, _ = _party_scope(owner, rows)
    party_page = _page("party", owner, party)
    circle_page = _page("circle", owner, circle)
    thread_page = _page("thread", owner, thread)
    assert _ids(party_page) == _ids(circle_page) == _ids(thread_page)
    assert len({party_page["older_cursor"], circle_page["older_cursor"], thread_page["older_cursor"]}) == 3
    assert _query("circle", owner, circle, before=party_page["older_cursor"]).errors
    older = _page("party", owner, party, before=party_page["older_cursor"])
    assert _ids(older) == [str(rows[2].sqid), str(rows[1].sqid)]


def test_circle_membership_is_rechecked_for_every_page() -> None:
    """A root-bound cursor does not retain parties that have left its circle."""

    owner = User.objects.create_user(username="feed-circle-membership")
    _, rows = _messages(owner)
    party, circle, member = _party_scope(owner, rows)
    first = _page("circle", owner, circle)
    with system_context(reason="test remove circle membership"):
        member.delete()
    second = _page("circle", owner, circle, before=first["older_cursor"])
    assert second["messages"] == []
    assert second["count"] == 0
    assert _page("party", owner, party)["count"] == 5


def test_party_participant_and_handle_visibility_are_not_bypassed() -> None:
    """A readable message reaches a party feed only through visible scope edges."""

    owner = User.objects.create_user(username="feed-party-edges")
    other = User.objects.create_user(username="feed-party-edges-other")
    thread, rows = _messages(owner)
    party, _, _ = _party_scope(owner, rows)
    with system_context(reason="test hidden party feed edge"):
        Participant._base_manager.filter(message=rows[-1]).update(created_by=other)
    assert _ids(_page("party", owner, party)) == [str(rows[3].sqid), str(rows[2].sqid)]
    with system_context(reason="test hidden party feed handle"):
        Handle._base_manager.filter(party=party).update(created_by=other)
    assert not _page("party", owner, party)["messages"]
    assert _page("thread", owner, thread)["count"] == 5


def test_queryset_feed_clamps_native_page_size() -> None:
    """Caller limits remain bounded without changing the whole-scope count."""

    owner = User.objects.create_user(username="feed-limits")
    thread, rows = _messages(owner, size=205)
    with actor_context(owner):
        query = Message.objects.inbox().for_thread(thread)
        scope = ("thread", str(thread.sqid))
        small = query.feed_page(scope=scope, limit=0)
        large = query.feed_page(scope=scope, limit=1000)
    assert len(small["messages"]) == 1
    assert len(large["messages"]) == 200
    assert large["count"] == len(rows)
    assert large["has_older"]
