"""Concealment, assignment and comments follow the live stage and queue owners."""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Count
from django.test.utils import CaptureQueriesContext
from rebac import PermissionDenied, actor_context, system_context
from rebac.roles import grant as grant_role

from angee.messaging.testing.models import Message, Person, ThreadFollower, ThreadNotification
from angee.projects.testing.models import Link, Project, Queue, Stage, Task
from angee.spaces.testing.models import Membership
from tests.conftest import Need, Page, RecordBinding, Vault
from tests.messaging_campaign import grant
from tests.t3_campaign import campaign_access as campaign_access
from tests.t3_campaign import campaign_user as campaign_user
from tests.t3_campaign import messaging_access_schema as messaging_access_schema
from tests.t3_campaign import relationship_snapshot


@pytest.fixture
def queue_case(campaign_access, campaign_user):
    people = {role: campaign_user(role) for role in ("owner", "assignee", "member", "manager", "reader", "admin")}
    with system_context(reason="tests.t3.queue"):
        grant_role(actor=people["admin"], role="angee/role:admin")
        queue = Queue.objects.create(name="Requests", key="T3", owner=people["manager"])
        removed = Stage.objects.create(queue=queue, name="Removed", category="canceled", conceals=True)
        for role, membership in (("member", "member"), ("manager", "moderator")):
            Membership.objects.create(
                group=queue.group_ptr,
                party=Person.objects.for_user(people[role]),
                role=membership,
                is_confirmed=True,
            )
        task = Task.objects.create(title="Request", queue=queue, owner=people["owner"], assignee=people["assignee"])
    grant(task, "reader", people["reader"])
    return people, queue, removed, task


@pytest.mark.parametrize("role", ("owner", "assignee", "member", "manager", "reader", "admin"))
def test_concealment_revokes_held_instances_and_all_list_shapes(queue_case, role):
    people, _, removed, task = queue_case
    actor = people[role]
    held = Task.objects.with_actor(actor).get(pk=task.pk)
    with system_context(reason="tests.t3.evidence"):
        # Legacy evidence predates decision admission. The bare Decision fixture
        # has no intake donor; current admission is exercised in the emitted host.
        [need] = Need._base_manager.bulk_create([Need(task=task, body="Legacy evidence")])
        thread = task.message_thread()
        message = Message._base_manager.filter(thread=thread).first()
    with actor_context(people["owner"]):
        changing = Task.objects.get(pk=task.pk)
        changing.stage = removed
        changing.save(update_fields=("stage",))
    allowed = role == "admin"
    for permission in ("read", "write", "share", "comment", "delete"):
        assert held.has_access(permission) is allowed
        assert Task.objects.with_actor(actor).with_action(permission).filter(pk=task.pk).exists() is allowed
    with actor_context(actor):
        rows = Task.objects.filter(pk=task.pk)
        assert rows.aggregate(total=Count("pk")) == {"total": int(allowed)}
        assert list(rows.values("stage_id").annotate(total=Count("pk"))) == (
            [{"stage_id": removed.pk, "total": 1}] if allowed else []
        )
        for dependent in (need, thread, message):
            assert type(dependent).objects.filter(pk=dependent.pk).exists() is allowed
        held.title = "Administrative edit"
        if allowed:
            held.save(update_fields=("title",))
            held.message_post("Administrative comment")
        else:
            with pytest.raises(PermissionDenied):
                held.save(update_fields=("title",))
            with pytest.raises(PermissionDenied):
                held.message_post("Refused comment")
    with actor_context(people["admin"]):
        restored = Task.objects.get(pk=task.pk)
        restored.stage = Stage.resolve_default(restored.queue)
        restored.save(update_fields=("stage",))
    assert Task.objects.with_actor(actor).filter(pk=task.pk).exists()


@pytest.mark.parametrize("method", ("clean", "save"))
@pytest.mark.parametrize(
    "mutation", ("default_becomes_concealing", "concealing_default", "rule_and_conceal", "promoted")
)
def test_stage_owner_rejects_concealment_that_breaks_its_invariants(queue_case, method, mutation):
    _, queue, removed, task = queue_case
    with system_context(reason="tests.t3.stage_invariant"):
        if mutation == "concealing_default":
            candidate = Queue._base_manager.get(pk=queue.pk)
            candidate.default_stage = removed
        elif mutation == "rule_and_conceal":
            candidate = Stage._base_manager.get(pk=removed.pk)
            candidate.rule_owned = True
        elif mutation == "promoted":
            candidate = Stage.objects.create(queue=queue, name="Promoted", category="started")
            Task._base_manager.filter(pk=task.pk).update(stage=candidate)
            Project.objects.create(title="Promoted project", converted_from=task)
            candidate.conceals = True
        else:
            candidate = Stage._base_manager.get(pk=queue.default_stage_id)
            candidate.conceals = True
        before = type(candidate)._base_manager.filter(pk=candidate.pk).values().get()
        with pytest.raises(ValidationError):
            getattr(candidate, method)()
        assert type(candidate)._base_manager.filter(pk=candidate.pk).values().get() == before


@pytest.mark.parametrize("role,allowed", (("member", False), ("manager", True)))
def test_assignment_is_a_share_and_old_assignee_loses_access(queue_case, role, allowed):
    people, _, _, task = queue_case
    with actor_context(people[role]):
        candidate = Task.objects.get(pk=task.pk)
        candidate.assignee = people["reader"]
        if allowed:
            candidate.save(update_fields=("assignee",))
        else:
            with pytest.raises(PermissionDenied):
                candidate.save(update_fields=("assignee",))
    assert Task.objects.with_actor(people["assignee"]).filter(pk=task.pk).exists() is (not allowed)


def test_member_comment_edit_and_delete_end_when_task_is_restricted(queue_case):
    people, _, _, task = queue_case
    with actor_context(people["member"]):
        record = Task.objects.get(pk=task.pk)
        comment = record.message_post("Member's comment")
        record.message_update_content(comment, body="Edited while readable")
    with actor_context(people["owner"]):
        Task.objects.get(pk=task.pk).set_visibility("restricted")
    with actor_context(people["member"]):
        for operation in (
            lambda: record.message_update_content(comment, body="Must fail"),
            lambda: record.message_unlink(comment),
        ):
            with pytest.raises((PermissionDenied, ValueError)):
                operation()
    assert Message._base_manager.filter(pk=comment.pk).exists()


def test_team_audience_reads_parent_roster_once_without_followers(queue_case, django_assert_num_queries):
    people, queue, _, _ = queue_case
    before = ThreadFollower._base_manager.count()
    # One system-queryset audit INSERT and one parent roster SELECT.
    with django_assert_num_queries(2):
        audience = list(queue.thread_audience())
    assert {member.party_id for member in audience} == set(
        Person._base_manager.filter(user__in=(people["member"], people["manager"])).values_list("pk", flat=True)
    )
    assert ThreadFollower._base_manager.count() == before


def test_link_retargeting_reads_live_without_writing_either_tuple_store(queue_case):
    people, _, _, source = queue_case
    with system_context(reason="tests.t3.link"):
        target = Task.objects.create(title="Canonical", owner=people["reader"], visibility="restricted")
    with actor_context(people["owner"]):
        link = Link.objects.create(target=source, url="https://example.test/reference")
    assert link.with_actor(people["member"]).has_access("read")
    before = relationship_snapshot()
    with system_context(reason="tests.t3.move_links"), CaptureQueriesContext(connection) as queries:
        source._move_links_to(target)
    link.refresh_from_db()
    assert link.object_id == target.pk
    assert not link.with_actor(people["member"]).has_access("read")
    assert link.with_actor(people["reader"]).has_access("read")
    assert relationship_snapshot() == before
    assert not any(
        item["sql"].startswith(("INSERT", "UPDATE", "DELETE")) and "rebac_relationship" in item["sql"]
        for item in queries
    )


def test_null_stage_fails_closed_for_roster_without_removing_direct_owner_and_assignee(queue_case):
    people, _, _, task = queue_case
    with system_context(reason="tests.t3.legacy_null_stage"):
        Task._base_manager.filter(pk=task.pk).update(stage=None)
    for role in ("owner", "assignee", "member", "manager", "reader", "admin"):
        expected = role in {"owner", "assignee", "reader", "admin"}
        assert Task.objects.with_actor(people[role]).filter(pk=task.pk).exists() is expected


def test_concealment_revokes_public_read_bindings_and_follower_delivery(queue_case, campaign_user):
    people, queue, removed, task = queue_case
    public_reader = campaign_user("public-reader")
    with system_context(reason="tests.t3.concealed_dependents"):
        queue.visibility = "public"
        queue.save(update_fields=("visibility",))
        vault = Vault.objects.create(name="Reference", owner=people["owner"])
        page = Page.objects.create(vault=vault, title="Context")
        task.message_subscribe(party=Person.objects.for_user(public_reader), notification_policy="inbox")
    with actor_context(people["owner"]):
        binding = RecordBinding.objects.upsert(page=page, target=task)
    grant(vault, "viewer", public_reader)
    assert Task.objects.with_actor(public_reader).filter(pk=task.pk).exists()
    with actor_context(public_reader):
        assert RecordBinding.objects.for_record(task).filter(pk=binding.pk).exists()
    with actor_context(people["owner"]):
        task.with_actor(people["owner"]).stage = removed
        task.save(update_fields=("stage",))
    assert not Task.objects.with_actor(public_reader).filter(pk=task.pk).exists()
    with actor_context(public_reader):
        assert not RecordBinding.objects.for_record(task).exists()
    with actor_context(people["admin"]):
        message = Task.objects.get(pk=task.pk).message_post("Hidden update")
    assert not ThreadNotification._base_manager.filter(message=message, user=public_reader).exists()


def test_stage_position_orders_the_task_list_by_the_queue_workflow(queue_case, django_assert_num_queries):
    people, queue, _, task = queue_case
    with system_context(reason="tests.t3.stage_order"):
        early = Stage.objects.create(queue=queue, name="Earlier", category="unstarted", position=1)
        late = Stage.objects.create(queue=queue, name="Later", category="started", position=100)
        Task._base_manager.filter(pk=task.pk).update(stage=late)
        first = Task.objects.create(queue=queue, stage=early, title="First", owner=people["owner"])
    # Resolve the native permission predicate before measuring the ordered SELECT.
    query = (
        Task.objects.with_actor(people["member"])
        .scoped()
        .filter(pk__in=(first.pk, task.pk))
        .order_by("stage__position")
    )
    list(query.values_list("pk", flat=True))
    # Three revision reads, actor-set expansion, nine arrow sources, ordered SELECT.
    with django_assert_num_queries(14) as queries:
        assert list(query.values_list("pk", flat=True)) == [first.pk, task.pk]
    assert sum(query["sql"].startswith(f'SELECT "{Task._meta.db_table}".') for query in queries.captured_queries) == 1
