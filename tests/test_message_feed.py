"""Native Django/REBAC contracts for stable messaging and Nexus feed cursors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory
from rebac import actor_context, system_context

from tests.conftest import Backend, Drive, File, MimeType, execute_schema, result_data
from tests.test_messaging import (
    Circle,
    CircleMember,
    Fragment,
    Handle,
    Message,
    Part,
    Participant,
    Party,
    Reaction,
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


def _query(
    kind: str,
    owner: Any,
    root: Any,
    *,
    selection: str = "id preview sent_at created_at feed_order_key",
    **variables: Any,
) -> Any:
    """Execute the real composed GraphQL field under its current request actor."""

    request = RequestFactory().post("/graphql/console/")
    request.user = owner
    return execute_schema(
        _schema(),
        f"""
        query Feed(
          $root: ID!, $before: String, $after: String, $through: String, $limit: Int!, $search: String!
        ) {{
          page: {kind}_message_feed(
            {kind}_id: $root, before_cursor: $before, after_cursor: $after,
            through_cursor: $through, limit: $limit, search: $search
          ) {{
            messages {{ {selection} }}
            count older_cursor newer_cursor has_older has_newer has_more_in_window has_older_than_through
          }}
        }}
        """,
        {"root": str(root.sqid), "before": None, "after": None, "through": None, "limit": 2, "search": "", **variables},
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


def _revalidate(
    kind: str,
    owner: Any,
    root: Any,
    ids: list[str],
    *,
    search: str = "",
    selection: str = "id preview sent_at created_at feed_order_key",
) -> Any:
    """Read the complete survivor partition through the composed schema."""

    request = RequestFactory().post("/graphql/console/")
    request.user = owner
    return execute_schema(
        _schema(),
        f"""query Revalidate($root: ID!, $ids: [ID!]!, $search: String!) {{
          result: {kind}_message_feed_revalidate({kind}_id: $root, ids: $ids, search: $search) {{
            messages {{ {selection} }} absent_ids
          }}
        }}""",
        {"root": str(root.sqid), "ids": ids, "search": search},
        request=request,
    )


def test_fixed_window_keeps_inclusive_lower_cut_and_separate_older_history() -> None:
    owner = User.objects.create_user(username="feed-window")
    thread, rows = _messages(owner, size=8)
    first = _page("thread", owner, thread)
    cut = first["older_cursor"]
    window = _page("thread", owner, thread, through=cut, limit=1)
    assert _ids(window) == [str(rows[-1].sqid)]
    assert window["has_more_in_window"] and window["has_older_than_through"]
    tail = _page("thread", owner, thread, before=window["older_cursor"], through=cut, limit=1)
    assert _ids(tail) == [str(rows[-2].sqid)]
    assert not tail["has_more_in_window"]
    assert tail["has_older"] and tail["has_older_than_through"]
    with system_context(reason="test empty fixed feed interval"):
        Message._base_manager.filter(pk__in=[rows[-1].pk, rows[-2].pk]).delete()
    empty = _page("thread", owner, thread, through=cut)
    assert empty["messages"] == [] and empty["older_cursor"] is None
    assert not empty["has_more_in_window"] and empty["has_older_than_through"]
    assert _ids(_page("thread", owner, thread, before=cut)) == [str(rows[-3].sqid), str(rows[-4].sqid)]


def test_fixed_head_window_enumerates_all_arrivals_and_rejects_foreign_lower_cut() -> None:
    owner = User.objects.create_user(username="feed-window-arrivals")
    thread, rows = _messages(owner)
    cut = _page("thread", owner, thread)["older_cursor"]
    with system_context(reason="test moving fixed head"):
        arrivals = [
            Message._base_manager.create(thread=thread, created_by=owner, sent_at=T0 + timedelta(days=1))
            for _ in range(5)
        ]
    before = None
    collected = []
    while True:
        page = _page("thread", owner, thread, before=before, through=cut)
        collected.extend(_ids(page))
        if not page["has_more_in_window"]:
            break
        before = page["older_cursor"]
    assert collected == [str(row.sqid) for row in reversed(arrivals)] + [str(row.sqid) for row in rows[:2:-1]]
    other, _ = _messages(owner)
    assert _query("thread", owner, other, through=cut).errors
    assert _query("thread", owner, thread, after=cut, through=cut).errors


@pytest.mark.parametrize("kind", ["thread", "party", "circle"])
def test_revalidation_partitions_current_scope_search_and_moved_rows(kind: str) -> None:
    owner = User.objects.create_user(username=f"feed-revalidate-{kind}")
    other = User.objects.create_user(username=f"feed-revalidate-other-{kind}")
    thread, rows = _messages(owner)
    party, circle, _ = _party_scope(owner, rows)
    root = {"thread": thread, "party": party, "circle": circle}[kind]
    ids = [str(row.sqid) for row in rows]
    with system_context(reason="test retained row changes"):
        rows[0].delete()
        Message._base_manager.filter(pk=rows[1].pk).update(created_by=other)
        Message._base_manager.filter(pk=rows[2].pk).update(preview="excluded")
        Message._base_manager.filter(pk=rows[3].pk).update(sent_at=T0 - timedelta(days=10), preview="needle moved")
    result = result_data(_revalidate(kind, owner, root, [*ids, ids[-1]], search="  needle\t"))["result"]
    assert _ids(result) == [ids[4], ids[3]]
    assert result["absent_ids"] == ids[:3]
    assert result["messages"][1]["preview"] == "needle moved"
    assert [row["feed_order_key"] for row in result["messages"]] == sorted(
        [row["feed_order_key"] for row in result["messages"]], reverse=True
    )
    with system_context(reason="test revalidation root denial"):
        type(root)._base_manager.filter(pk=root.pk).update(created_by=other)
    assert _revalidate(kind, owner, root, ids).errors


def test_revalidation_rechecks_circle_membership_and_visible_participant_edges() -> None:
    owner = User.objects.create_user(username="feed-revalidate-edges")
    other = User.objects.create_user(username="feed-revalidate-edges-other")
    thread, rows = _messages(owner)
    party, circle, member = _party_scope(owner, rows)
    ids = [str(row.sqid) for row in rows]
    with system_context(reason="test retained participant visibility"):
        Participant._base_manager.filter(message=rows[-1]).update(created_by=other)
    result = result_data(_revalidate("party", owner, party, ids))["result"]
    assert result["absent_ids"] == [ids[-1]]
    with system_context(reason="test retained handle visibility"):
        Handle._base_manager.filter(party=party).update(created_by=other)
    assert result_data(_revalidate("party", owner, party, ids))["result"]["absent_ids"] == ids
    with system_context(reason="test retained membership loss"):
        member.delete()
    assert result_data(_revalidate("circle", owner, circle, ids))["result"]["absent_ids"] == ids
    assert len(result_data(_revalidate("thread", owner, thread, ids))["result"]["messages"]) == 5


def test_revalidation_enforces_submitted_limit_and_empty_partition() -> None:
    owner = User.objects.create_user(username="feed-revalidation-bound")
    thread, rows = _messages(owner)
    ids = [str(row.sqid) for row in rows]
    assert _revalidate("thread", owner, thread, [ids[0]] * 201).errors
    assert result_data(_revalidate("thread", owner, thread, []))["result"] == {"messages": [], "absent_ids": []}
    assert len(result_data(_revalidate("thread", owner, thread, [ids[0]] * 200))["result"]["messages"]) == 1


def test_order_key_preserves_full_pk_microseconds_timezone_and_null_send_order() -> None:
    from datetime import timezone as datetime_timezone

    rows = [
        Message(pk=pk, sent_at=at, created_at=T0)
        for pk, at in [
            (2**63 - 1, T0),
            (9, T0),
            (10, T0),
            (11, None),
            (12, (T0 + timedelta(microseconds=1)).astimezone(datetime_timezone(timedelta(hours=5)))),
        ]
    ]
    assert sorted(rows, key=lambda row: row.feed_order_key) == sorted(rows, key=lambda row: row.chronological_key)
    assert all(len(row.feed_order_key) == len(rows[0].feed_order_key) for row in rows)


@pytest.mark.parametrize("size", [50, 200, 1000])
def test_revalidation_sql_cost_with_native_authorization(size: int, capsys: Any) -> None:
    """Measure fresh-request native scope work and real transcript projections."""

    import json
    from unittest.mock import patch

    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from rebac.backends import backend

    owner = User.objects.create_user(username=f"feed-cost-{size}")
    with system_context(reason="test retained feed cost seed"):
        thread = Thread._base_manager.create(created_by=owner, platform="email")
        party = Party._base_manager.create(display_name="Cost party", created_by=owner)
        handle = Handle._base_manager.create(
            platform="email", value=f"cost-{size}@example.com", party=party, created_by=owner
        )
        fragment = Fragment.objects.upsert(text=f"Cost fragment {size}", owner_id=owner.pk)
        thread.title = fragment
        thread.save(update_fields=["title"])
        rows = Message._base_manager.bulk_create(
            [
                Message(
                    thread=thread,
                    sender=handle,
                    created_by=owner,
                    platform="email",
                    sent_at=T0,
                    preview=f"cost {index}",
                )
                for index in range(size)
            ]
        )
        storage_backend = Backend._base_manager.create(slug="feed-cost", backend_class="local")
        drive = Drive._base_manager.create(backend=storage_backend, slug="feed-cost", created_by=owner)
        mime, _ = MimeType._base_manager.get_or_create(
            mime_type="application/pdf", defaults={"category": "document", "label": "PDF"}
        )
        files = File._base_manager.bulk_create(
            [
                File(
                    drive=drive,
                    mime_type=mime,
                    filename=f"{index}.pdf",
                    content_hash=f"{index:064x}",
                    storage_path=f"feed/{index}.pdf",
                    created_by=owner,
                )
                for index in range(size)
            ]
        )
        Part._base_manager.bulk_create(
            [Part(message=row, role="body", fragment=fragment, created_by=owner) for row in rows]
            + [
                Part(message=row, position=1, disposition="attachment", file=file, created_by=owner)
                for row, file in zip(rows, files, strict=True)
            ]
        )
        Reaction._base_manager.bulk_create(
            [Reaction(message=row, handle=handle, reaction="like", created_by=owner) for row in rows]
        )
    ids = [str(row.sqid) for row in rows]
    active_backend = backend()
    original = active_backend.accessible
    selection = """id feed_order_key preview direction message_type sent_at created_at
        sender { id display_name value party_link_confirmed party { display_name } }
        parts {
          id role disposition cid fragment { text }
          file { id filename title size_bytes url mime_type { mime_type label } }
        }
        reaction_groups { reaction count self_reacted handles { id display_name value } }
        thread { id title { text } }
    """
    before = None
    while True:
        discovery = _page("thread", owner, thread, before=before, limit=200)
        through = discovery["older_cursor"]
        if not discovery["has_older"]:
            break
        before = through
    for operation in ["revalidate", "window"]:
        for projection in ["id feed_order_key", selection]:
            calls = []

            def counted(**kwargs: Any) -> Any:
                result = list(original(**kwargs))
                calls.append({"resource": kwargs["resource_type"], "allowed": len(result)})
                return result

            returned = []
            before = None
            with (
                patch.object(active_backend, "accessible", side_effect=counted),
                CaptureQueriesContext(connection) as captured,
            ):
                for start in range(0, size, 200):
                    if operation == "revalidate":
                        result = result_data(
                            _revalidate(
                                "thread",
                                owner,
                                thread,
                                ids[start : start + 200],
                                selection=projection,
                            )
                        )["result"]
                        assert result["absent_ids"] == []
                    else:
                        result = _page(
                            "thread",
                            owner,
                            thread,
                            before=before,
                            through=through,
                            limit=200,
                            selection=projection,
                        )
                        before = result["older_cursor"]
                        assert result["has_more_in_window"] == (start + 200 < size)
                    assert len(result["messages"]) <= 200
                    if projection == selection:
                        for message in result["messages"]:
                            attachments = [part["file"] for part in message["parts"] if part["file"]]
                            assert len(attachments) == 1
                            assert attachments[0]["mime_type"] == {"mime_type": "application/pdf", "label": "PDF"}
                    returned.extend(_ids(result))
            assert set(returned) == set(ids) and len(returned) == size
            # Protected prefixes must retain both unprotected terminal lookups;
            # nested fragments and MIME types are one batched SELECT per request.
            for model in (Fragment, MimeType):
                table = connection.ops.quote_name(model._meta.db_table)
                reads = sum(f"FROM {table}" in query["sql"] for query in captured)
                assert reads <= (size + 199) // 200
            with capsys.disabled():
                print(
                    "FEED_SQL_COST "
                    + json.dumps(
                        {
                            "vendor": connection.vendor,
                            "retained": size,
                            "operation": operation,
                            "projection": "scalar" if projection == "id feed_order_key" else "full",
                            "requests": (size + 199) // 200,
                            "sql": len(captured),
                            "accessible": calls,
                        }
                    )
                )


def test_revalidation_projection_prefetch_preserves_related_permissions() -> None:
    owner = User.objects.create_user(username="feed-prefetch-owner")
    other = User.objects.create_user(username="feed-prefetch-other")
    thread, rows = _messages(owner, size=1)
    with system_context(reason="test mixed visibility message children"):
        fragment = Fragment.objects.upsert(text="Shared text", owner_id=owner.pk)
        visible = Part._base_manager.create(message=rows[0], fragment=fragment, created_by=owner)
        Part._base_manager.create(message=rows[0], fragment=fragment, created_by=other)
        handle = Handle._base_manager.create(platform="email", value="reaction@example.com", created_by=owner)
        Reaction._base_manager.create(message=rows[0], handle=handle, reaction="visible", created_by=owner)
        Reaction._base_manager.create(message=rows[0], handle=handle, reaction="hidden", created_by=other)
    result = result_data(
        _revalidate(
            "thread",
            owner,
            thread,
            [str(rows[0].sqid)],
            selection="id parts { id fragment { text } } reaction_groups { reaction count }",
        )
    )["result"]
    assert result["absent_ids"] == []
    assert result["messages"][0]["parts"] == [{"id": str(visible.sqid), "fragment": {"text": "Shared text"}}]
    assert result["messages"][0]["reaction_groups"] == [{"reaction": "visible", "count": 1}]


def test_revalidation_keeps_record_attached_chatter_behind_its_record_gate() -> None:
    owner = User.objects.create_user(username="feed-retained-record")
    thread, rows = _messages(owner)
    with system_context(reason="test retained attached record"):
        record = ThreadedTicket._base_manager.create(title="Private record", created_by=owner)
        ThreadAttachment._base_manager.create(
            thread=thread,
            content_type=ContentType.objects.get_for_model(ThreadedTicket),
            object_id=record.pk,
            created_by=owner,
        )
    assert _revalidate("thread", owner, thread, [str(row.sqid) for row in rows]).errors
