"""Inbox parts read their message once: the part's contextual read, its population and its fallbacks."""

import re
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Subquery
from django.test.utils import CaptureQueriesContext
from rebac import (
    MissingActorError,
    RelationshipTuple,
    actor_context,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.models import SchemaOverride, SchemaPermission

from angee.base.permissions import contextual_action_holds
from angee.base.testing.queries import capture_planner_memory, statement_body, supports_planner_memory
from angee.messaging.inbox import InboxCoverage, InboxSearch
from angee.messaging.inbox_results import InboxResultOptions, InboxResults
from angee.messaging.testing.models import Channel, Fragment, Message, Part, Thread
from tests.chatterdemo.models import ChatterDoc
from tests.conftest import create_platform_admin, create_user, make_integration

pytestmark = pytest.mark.usefixtures("composed_tables")
User = get_user_model()
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _tuple(resource: Any, relation: str, user: Any) -> RelationshipTuple:
    return RelationshipTuple(to_object_ref(resource), relation, to_subject_ref(user))


def _ids(rows: Any) -> set[Any]:
    return {row.pk for row in rows}


def _scoped_population_parts(actor: Any) -> set[Any]:
    """The generic part read inside the separately scoped eligible messages."""

    inbox = Message.objects.with_actor(actor).explorer()
    messages = Subquery(inbox.messages.order_by().values("pk"))
    return set(Part.objects.with_actor(actor).filter(message_id__in=messages).values_list("pk", flat=True))


@pytest.fixture
def corpus() -> dict[str, Any]:
    """One owner's channel with eligible, trashed, draft, public and chatter-group messages, and a hidden one."""

    owner = create_user("part-reads-owner")
    other = create_user("part-reads-other")
    account = make_integration(owner.username, model=Channel, owner=owner)
    hidden_account = make_integration(other.username, model=Channel, owner=other)
    with system_context(reason="seed inbox part reads"):
        title = Fragment.objects.upsert(text="Quarterly figures", created_by_id=owner.pk)
        conversation = Thread._base_manager.create(created_by=owner, channel=account, title=title, platform="email")
        public = Thread._base_manager.create(created_by=owner, channel=account, modality="public_thread")
        record = ChatterDoc.objects.create(title="Chatter record")
        chatter = record.message_thread()
        survivor = Thread._base_manager.create(created_by=owner, channel=account)
        Thread._base_manager.filter(pk=chatter.pk).update(merged_into=survivor)
        rows = {
            "kept": dict(thread=conversation),
            "kept_late": dict(thread=conversation, sent_at=T0 + timedelta(hours=1)),
            "trashed": dict(thread=conversation, is_trashed=True),
            "draft": dict(thread=conversation, status="draft"),
            "public": dict(thread=public),
            "chatter_group": dict(thread=survivor),
            "hidden": dict(channel=hidden_account, created_by=other),
        }
        messages = {
            name: Message._base_manager.create(
                **{"created_by": owner, "channel": account, "status": "synced", "sent_at": T0, **values}
            )
            for name, values in rows.items()
        }
        parts = {
            name: Part._base_manager.create(
                created_by=message.created_by, message=message, name=f"{name}.pdf", disposition="attachment",
            )
            for name, message in messages.items()
        }
    return {"owner": owner, "other": other, "account": account, "hidden_account": hidden_account, "parts": parts}


def test_message_reader_lists_eligible_parts_without_a_part_grant(corpus: dict[str, Any]) -> None:
    owner, parts = corpus["owner"], corpus["parts"]
    assert contextual_action_holds(Part, "read_inbox")
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        listed = _ids(inbox.parts)
        # The owner reads the trashed copy through its channel's management, yet the
        # inbox population (published, no public thread, no chatter group) withholds it.
        assert Part.objects.filter(pk=parts["trashed"].pk).exists()
        assert listed == {parts["kept"].pk, parts["kept_late"].pk}
        assert inbox.parts.count() == len(listed)
    assert listed == _scoped_population_parts(owner)


def test_inbox_parts_equal_the_scoped_population_for_every_actor(corpus: dict[str, Any]) -> None:
    admin = create_platform_admin("part-reads-admin")
    outsider = create_user("part-reads-outsider")
    parts = corpus["parts"]
    expected = {
        corpus["owner"]: {parts["kept"].pk, parts["kept_late"].pk},
        corpus["other"]: {parts["hidden"].pk},
        admin: {parts["kept"].pk, parts["kept_late"].pk, parts["hidden"].pk},
        outsider: set(),
    }
    for actor, rows in expected.items():
        with actor_context(actor):
            assert _ids(Message.objects.all().explorer().parts) == rows
        assert _scoped_population_parts(actor) == rows


def test_direct_part_reader_keeps_generic_read_but_never_lists_an_unreadable_message(corpus: dict[str, Any]) -> None:
    reader = create_user("part-reads-direct")
    hidden = corpus["parts"]["hidden"]
    write_relationships([_tuple(hidden, "reader", reader)])
    with actor_context(reader):
        assert Part.objects.filter(pk=hidden.pk).exists()
        assert not Message.objects.filter(pk=hidden.message_id).exists()
        inbox = Message.objects.all().explorer()
        assert not inbox.parts.exists()
        assert inbox.parts.count() == 0
        assert inbox.matching(inbox.messages, InboxSearch(text="hidden.pdf")).count() == 0
        assert inbox.matching(inbox.messages, InboxSearch(attachment="any")).count() == 0
        attachments = InboxResults(inbox, inbox.messages, InboxResultOptions(lens="attachments")).page()
        assert (attachments.rows, attachments.count, attachments.message_count) == ([], 0, 0)


def test_counts_search_and_page_boundaries_match_the_listed_parts(corpus: dict[str, Any]) -> None:
    parts = corpus["parts"]
    with actor_context(corpus["owner"]):
        inbox = Message.objects.all().explorer()
        assert inbox.matching(inbox.messages, InboxSearch(text="kept.pdf")).count() == 1
        for withheld in ("trashed", "draft", "public", "chatter_group", "hidden"):
            assert inbox.matching(inbox.messages, InboxSearch(text=f"{withheld}.pdf")).count() == 0
        assert inbox.matching(inbox.messages, InboxSearch(attachment="any")).count() == 2
        results = InboxResults(inbox, inbox.messages, InboxResultOptions(lens="attachments"))
        first, second, beyond = (results.page(page=page, size=1) for page in (1, 2, 3))
        assert (first.count, second.count, beyond.count) == (2, 2, 2)
        assert {first.rows[0].part.pk, second.rows[0].part.pk} == {parts["kept"].pk, parts["kept_late"].pk}
        assert beyond.rows == []


def test_shared_text_second_use_reads_the_base_inbox_not_the_filtered_results(corpus: dict[str, Any]) -> None:
    owner, other = corpus["owner"], corpus["other"]
    account, hidden_account = corpus["account"], corpus["hidden_account"]
    with system_context(reason="seed shared text uses"):
        shared, private, repeated = (
            Fragment.objects.upsert(text=text, created_by_id=owner.pk)
            for text in ("shared outside the filter", "shared with a hidden message", "repeated in one message")
        )
        uses = {
            "covered": (account, owner, T0, (shared,)),
            "outside_coverage": (account, owner, T0 + timedelta(days=2), (shared,)),
            "beside_hidden": (account, owner, T0, (private,)),
            "hidden": (hidden_account, other, T0, (private,)),
            "repeating": (account, owner, T0, (repeated, repeated)),
        }
        for channel, author, sent_at, fragments in uses.values():
            message = Message._base_manager.create(
                created_by=author, channel=channel, status="synced", sent_at=sent_at,
            )
            for position, fragment in enumerate(fragments):
                Part._base_manager.create(created_by=author, message=message, fragment=fragment, position=position)
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        covered = inbox.results(InboxCoverage(start=date(2026, 1, 1), end=date(2026, 1, 1)), InboxSearch())
        page = InboxResults(inbox, covered, InboxResultOptions(lens="text")).page()
    # The second use outside the coverage still qualifies the covered one; a use in an
    # unreadable message, or a second part of the same message, does not.
    assert [row.fragment.pk for row in page.rows] == [shared.pk]
    assert (page.count, page.message_count) == (1, 1)


def test_explicit_actor_pin_wins_over_the_ambient_actor_and_survives_streaming(corpus: dict[str, Any]) -> None:
    owner, parts = corpus["owner"], corpus["parts"]
    expected = {parts["kept"].pk, parts["kept_late"].pk}
    with actor_context(corpus["other"]):
        inbox = Message.objects.with_actor(owner).explorer()
        assert inbox.parts.actor() == to_subject_ref(owner)
        assert {row.pk for row in inbox.parts.iterator(chunk_size=1)} == expected
        assert _ids(inbox.parts) == expected


def test_inbox_without_an_actor_fails_closed() -> None:
    with pytest.raises(MissingActorError):
        Message.objects.all().explorer()


def test_revoked_parent_grant_is_observed_by_the_next_statement(corpus: dict[str, Any]) -> None:
    member = create_user("part-reads-member")
    grant = _tuple(corpus["account"], "reader", member)
    write_relationships([grant])
    with actor_context(member):
        inbox = Message.objects.all().explorer()
        assert _ids(inbox.parts) == {corpus["parts"]["kept"].pk, corpus["parts"]["kept_late"].pk}
        delete_relationship(grant)
        # The same queryset re-evaluates the part's live message read; no admitted
        # message IDs survive from the first statement.
        assert not inbox.parts.all().exists()


@pytest.fixture
def tightened_part_read() -> Iterator[SchemaOverride]:
    """A runtime override narrowing the generic part read to direct editors."""

    read = SchemaPermission.objects.get(definition__resource_type="messaging/part", name="read")
    override = SchemaOverride.objects.create(
        kind=SchemaOverride.KIND_TIGHTEN, target=read, expression="editor", reason="test: deny the inbox listing",
    )
    yield override
    override.delete()


def test_an_override_on_the_part_read_falls_back_to_the_scoped_population(
    corpus: dict[str, Any], tightened_part_read: SchemaOverride,
) -> None:
    owner, parts = corpus["owner"], corpus["parts"]
    assert not contextual_action_holds(Part, "read_inbox")
    write_relationships([_tuple(parts["kept"], "editor", owner)])
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        # The contextual read cannot evade the deny: only the edited part survives.
        assert _ids(inbox.parts) == {parts["kept"].pk}
        assert inbox.matching(inbox.messages, InboxSearch(text="kept_late.pdf")).count() == 0
    assert _scoped_population_parts(owner) == {parts["kept"].pk}


def test_inbox_parts_compile_the_message_graph_once(record_property) -> None:
    owner = create_user("part-reads-shape")
    account = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed the parts statement shape"):
        thread = Thread._base_manager.create(created_by=owner, channel=account, platform="email")
        for index in range(30):
            message = Message._base_manager.create(
                created_by=owner, channel=account, thread=thread, status="synced",
                sent_at=T0 + timedelta(minutes=index),
            )
            Part._base_manager.create(created_by=owner, message=message, name=f"{index}.pdf", position=index)
    postgres = supports_planner_memory()
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        list(inbox.parts.order_by("pk")[:1])
        reads = {}
        for size in (1, 25):
            planner = capture_planner_memory(lambda sql: True) if postgres else nullcontext([])
            with CaptureQueriesContext(connection) as captured, planner as plans:
                assert len(list(inbox.parts.order_by("pk")[:size])) == size
                reads[size] = [query["sql"] for query in captured.captured_queries]
            if postgres:
                [plan] = plans
                record_property(f"planner_memory_{size}_used_kib", plan.used_kib)
                record_property(f"planner_memory_{size}_allocated_kib", plan.allocated_kib)
                # PostgreSQL 17: 14,999 KiB used / 16,384 KiB allocated for both page sizes.
                # The measured allocation rounds to a 16 MiB ceiling.
                assert 0 < plan.allocated_kib <= 16 * 1024, plan
    one, many = reads[1][-1], reads[25][-1]
    # REBAC 0.32's content-hash id-set names lengthen this SQLite statement to
    # 40,565 bytes: ceil(40,565 / 1,024) = 40 KiB. Sharing cuts PostgreSQL planning
    # memory; the PostgreSQL planner gate remains the binding budget.
    assert len(reads[1]) == len(reads[25]) == 1
    assert abs(len(one) - len(many)) <= 2
    assert len(many) <= 40 * 1024
    # One actor scope: the part's read carries the message graph, and eligibility
    # joins the message without a second, separately scoped message subquery.
    assert many.count("rebac_schemageneration") <= 1
    body = statement_body(many)
    [message_scope] = re.findall(r'FROM "(rebac_ids_[0-9a-f]+)"', body)
    message_table = connection.ops.quote_name(Message._meta.db_table)
    assert many.count(f'"{message_scope}" AS (SELECT {message_table}.') == 1
    # REBAC 0.32.1 materializes five distinct message permission stages on SQLite;
    # PostgreSQL folds them into one. Bound every body, including unreferenced copies.
    assert many.count(f"AS (SELECT {message_table}.") == (1 if connection.vendor == "postgresql" else 5)
    assert many.count(f'FROM "{message_scope}"') == 1
    assert f"FROM {message_table}" not in body
