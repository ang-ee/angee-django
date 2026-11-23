"""The inbox reads threads and messages under ``read_inbox``, which leaves record chatter's arms out.

The inbox lists no record chatter, so within its population ``read_inbox`` answers
exactly as ``read`` does for every actor. Generic reads keep ``read``, and a record's
readers keep reading its chatter through it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.core.checks.registry import registry
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import MissingActorError, PermissionDenied, actor_context, system_context
from rebac.backends import backend
from rebac.evaluator import evaluator_scope
from rebac.links import is_generated
from rebac.models import SchemaOverride, SchemaPermission
from rebac.schema import FieldBinding, PermBinOp, PermRef, permission_sources
from rebac.testing import install_schema

from angee.base.build import extension_source_map, merged_schemas
from angee.base.models.identity import public_id_for
from angee.base.permissions import contextual_action_holds, purpose_read_holds
from angee.base.testing.permissions import installed_field_owners
from angee.graphql.testing.documents import execute_schema, result_data, unreadable_rows
from angee.messaging.checks import check_derived_reads
from angee.messaging.inbox_results import InboxResultOptions, InboxResults
from angee.messaging.testing.models import (
    Channel,
    Message,
    Part,
    Person,
    Thread,
    ThreadAttachment,
    ThreadFollower,
    ThreadNotification,
)
from angee.projects.testing.models import Project, ProjectBinding, Task
from tests.chatterdemo.models import ChatterDoc
from tests.conftest import Page, Vault, create_platform_admin, make_integration
from tests.messaging_campaign import grant
from tests.t3_campaign import campaign_access as campaign_access
from tests.t3_campaign import campaign_user as campaign_user
from tests.test_messaging_graphql import _schema as messaging_console_schema
from tests.test_zed_extensions import _addon, _apply_emitted_schema_paths, _contrib_addon

T0 = datetime(2026, 1, 1, tzinfo=UTC)

# Each installed contributor's thread arm and the extension point it widens.
_THREAD_CONTRIBUTIONS = (
    ("angee.projects", "project", "read_granted"),
    ("angee.spaces", "selected_group", "read_granted"),
    ("angee.projects", "record_project", "chatter_read"),
    ("angee.projects", "record_task", "chatter_read"),
    ("angee.knowledge", "record_page", "chatter_read"),
    ("tests.extcontrib", "chatter_record", "chatter_read"),
)

_ACTORS = ("admin", "owner", "other_owner", "direct_reader", "delegate", "project_reader", "record_reader", "outsider")


def _pks(rows: Any) -> set[Any]:
    return set(rows.values_list("pk", flat=True))


def _composed_messaging() -> Any:
    configs = list(apps.get_app_configs())
    return merged_schemas(configs, field_owners=installed_field_owners(configs))["angee.messaging"]


def _expression(schema: Any, resource_type: str, permission: str) -> Any:
    definition = schema.get_definition(resource_type)
    return next(row.expression for row in definition.permissions if row.name == permission)


@pytest.mark.parametrize(("package", "relation", "extension"), _THREAD_CONTRIBUTIONS)
def test_contributors_widen_ordinary_or_chatter_thread_access(package: str, relation: str, extension: str) -> None:
    """Ordinary grants reach both reads; a record's chatter grant reaches generic read alone."""

    schema = _composed_messaging()
    assert f"{package}@" in schema.headers["rebac_extended_by"]
    other = "chatter_read" if extension == "read_granted" else "read_granted"
    arm = (relation, "read")
    assert arm in permission_sources(schema, "messaging/thread", extension).arrows
    assert arm not in permission_sources(schema, "messaging/thread", other).arrows
    assert arm in permission_sources(schema, "messaging/thread", "read").arrows
    assert (arm in permission_sources(schema, "messaging/thread", "read_inbox").arrows) is (extension == "read_granted")


def test_composed_reads_derive_from_the_declared_extension_points() -> None:
    """The thread's inbox read is its ordinary access; the message's follows the thread's purpose."""

    schema = _composed_messaging()
    assert _expression(schema, "messaging/thread", "read_inbox") == PermRef("read_granted")
    assert _expression(schema, "messaging/thread", "read") == PermBinOp(
        "+", PermRef("read_granted"), PermRef("chatter_read"),
    )
    message_read = permission_sources(schema, "messaging/message", "read")
    message_inbox = permission_sources(schema, "messaging/message", "read_inbox")
    assert ("thread", "read") in message_read.arrows and ("thread", "read_inbox") not in message_read.arrows
    assert ("thread", "read_inbox") in message_inbox.arrows and ("thread", "read") not in message_inbox.arrows
    # Both withhold a trashed message and keep it for its channel's managers alike.
    for sources in (message_read, message_inbox):
        assert {"read_granted", "withheld", "withheld_keeper"} <= sources.subpermissions
        assert {("trashed", "conceal"), ("channel", "read"), ("channel", "write")} <= sources.arrows


_DERIVED_ARM_FRAGMENTS = {
    "thread_ordinary": ("messaging/thread", "read_granted", None),
    "thread_chatter": ("messaging/thread", "chatter_read", None),
    "message_ordinary": ("messaging/message", "read_granted", None),
    "thread_read": ("messaging/thread", "read", "messaging/thread#read"),
    "thread_read_inbox": ("messaging/thread", "read_inbox", "messaging/thread#read_inbox"),
    "message_read": ("messaging/message", "read", "messaging/message#read"),
    "message_read_inbox": ("messaging/message", "read_inbox", "messaging/message#read_inbox"),
}


@pytest.mark.parametrize("fragment", sorted(_DERIVED_ARM_FRAGMENTS))
def test_check_refuses_fragment_arms_on_derived_reads(tmp_path: Path, fragment: str) -> None:
    resource_type, permission, refused = _DERIVED_ARM_FRAGMENTS[fragment]
    declaration = Path(apps.get_app_config("messaging").path, "permissions.zed").read_text(encoding="utf-8")
    messaging = _addon(tmp_path, "messaging", "permissions.zed", declaration)
    contrib = _contrib_addon(
        tmp_path,
        f"definition {resource_type} {{\n    relation helper: auth/user\n    permission {permission} = helper\n}}\n",
        name="inbox_contrib",
    )
    assert check_derived_reads([messaging]) == []
    sources = extension_source_map([messaging, contrib])
    runtime = tmp_path / "runtime"
    for relative, text in sources.items():
        output = runtime / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    _apply_emitted_schema_paths([messaging, contrib], runtime, sources=sources)

    errors = check_derived_reads([messaging, contrib])
    if refused is None:
        assert errors == []
    else:
        [error] = errors
        assert error.id == "messaging.E003"
        assert refused in error.msg
        assert "inbox_contrib" in error.msg


def test_a_missing_messaging_declaration_is_a_check_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sealed reads are never compared against an empty declaration; the check reports, never raises."""

    monkeypatch.setattr("angee.base.checks.declared_schema_path", lambda config: None)
    [error] = check_derived_reads()
    assert error.id == "messaging.E003"
    assert "messaging declares no permissions schema" in error.msg
    assert "messaging/thread#read_inbox" in error.msg


def test_installed_composition_keeps_the_derived_reads() -> None:
    assert check_derived_reads() == []


def test_derived_read_check_is_registered_once_by_messaging() -> None:
    assert sum(candidate is check_derived_reads for candidate in registry.registered_checks) == 1


def _inbox_world(campaign_user: Any) -> SimpleNamespace:
    """Channel, personal, shared, project-bound, merged, trashed, chatter and source conversations."""

    owner, other_owner = campaign_user("channel-owner"), campaign_user("other-owner")
    world = SimpleNamespace(
        admin=create_platform_admin(f"{owner.username}-admin"),
        owner=owner,
        other_owner=other_owner,
        direct_reader=campaign_user("direct-reader"),
        delegate=campaign_user("delegate", kind="service"),
        project_reader=campaign_user("project-reader"),
        record_reader=campaign_user("record-reader"),
        outsider=campaign_user("outsider"),
    )
    world.channel_a = make_integration(f"{owner.username}-a", model=Channel, owner=owner)
    world.channel_b = make_integration(f"{owner.username}-b", model=Channel, owner=other_owner)
    with system_context(reason="tests.messaging.inbox_read.seed"):
        world.thread_a = Thread._base_manager.create(created_by=owner, channel=world.channel_a, platform="email")
        world.thread_b = Thread._base_manager.create(created_by=other_owner, channel=world.channel_b, platform="email")
        world.owned = Thread._base_manager.create(created_by=owner, owner=owner)
        world.shared = Thread._base_manager.create(created_by=other_owner, owner=other_owner)
        world.bound = Thread._base_manager.create(created_by=other_owner, owner=other_owner)

        def message(thread: Any, channel: Any = None, *, days: int = 0, **fields: Any) -> Any:
            return Message._base_manager.create(
                created_by=thread.created_by if thread is not None else owner, channel=channel, thread=thread,
                status="synced", sent_at=T0 + timedelta(days=days), **fields,
            )

        world.a1 = message(world.thread_a, world.channel_a)
        world.trashed = message(world.thread_a, world.channel_a, days=1, message_type="comment")
        world.restored = message(world.thread_a, world.channel_a, days=2, message_type="comment")
        world.standalone = message(None, world.channel_a, days=3)
        world.b1 = message(world.thread_b, world.channel_b)
        # A channel-B copy folded into a channel-A survivor: neither reader reaches the other's row.
        world.copy = message(world.thread_b, world.channel_b, days=1, merged_into=world.a1)
        world.owned_message = message(world.owned)
        world.shared_message = message(world.shared)
        world.bound_message = message(world.bound)
        world.trashed.trash(reason="Off topic")
        world.restored.trash(reason="Mistaken removal")
        world.restored.restore()

        world.record = ChatterDoc.objects.create(title="Inbox record")
        world.chatter = ThreadAttachment.objects.ensure_for_record(world.record).thread
        world.source = ThreadAttachment.objects.ensure_for_record(world.record, role="source").thread
        world.chatter_message = message(world.chatter, message_type="comment")
        world.source_message = message(world.source)

        world.project = Project.objects.create(title="Inbox project")
        ProjectBinding.objects.bind(project=world.project, target=world.bound)
    grant(world.shared, "reader", world.direct_reader)
    grant(world.project, "reader", world.project_reader)
    grant(world.record, "reader", world.record_reader)
    world.channel_a.with_actor(owner).grant_record_access("reader", world.delegate)
    return world


def _assert_inbox_equals_read(world: SimpleNamespace) -> dict[str, tuple[set[Any], set[Any]]]:
    """Every actor's inbox answer is its ``read`` answer within the inbox population, never wider."""

    with system_context(reason="tests.messaging.inbox_read.population"):
        threads = _pks(Thread.objects.inbox())
        messages = _pks(Message.objects.inbox())
    assert world.chatter.pk not in threads and world.source.pk in threads
    assert world.chatter_message.pk not in messages and world.source_message.pk in messages
    answers = {}
    for name in _ACTORS:
        actor = getattr(world, name)
        rows = []
        for model, population in ((Thread, threads), (Message, messages)):
            inbox = _pks(model.objects.with_actor(actor).inbox())
            readable = model.objects.with_actor(actor).with_action("read")
            assert inbox == _pks(readable.filter(pk__in=population)), (name, model)
            assert _pks(model.objects.with_actor(actor).with_action("read_inbox")) <= _pks(readable), (name, model)
            rows.append(inbox)
        answers[name] = (rows[0], rows[1])
    return answers


def test_inbox_read_answers_read_within_the_inbox_population(campaign_access: Any, campaign_user: Any) -> None:
    world = _inbox_world(campaign_user)
    answers = _assert_inbox_equals_read(world)
    w = world
    with system_context(reason="tests.messaging.inbox_read.population"):
        everything = (_pks(Thread.objects.inbox()), _pks(Message.objects.inbox()))
    assert answers["admin"] == everything
    # The channel owner manages channel A, so its trashed comment stays readable to it.
    assert answers["owner"] == (
        {w.thread_a.pk, w.owned.pk}, {w.a1.pk, w.trashed.pk, w.restored.pk, w.standalone.pk, w.owned_message.pk},
    )
    assert answers["other_owner"] == (
        {w.thread_b.pk, w.shared.pk, w.bound.pk}, {w.b1.pk, w.copy.pk, w.shared_message.pk, w.bound_message.pk},
    )
    assert answers["direct_reader"] == ({w.shared.pk}, {w.shared_message.pk})
    # A delegated service account reads through its channel grant, which withholds the trashed comment.
    assert answers["delegate"] == ({w.thread_a.pk}, {w.a1.pk, w.restored.pk, w.standalone.pk})
    assert answers["project_reader"] == ({w.bound.pk}, {w.bound_message.pk})
    assert answers["record_reader"] == (set(), set())
    assert answers["outsider"] == (set(), set())
    # Generic reads keep the record's chatter for its readers.
    assert w.chatter.with_actor(w.record_reader).has_access("read")
    assert not w.chatter.with_actor(w.record_reader).has_access("read_inbox")
    assert Message.objects.with_actor(w.record_reader).with_action("read").filter(pk=w.chatter_message.pk).exists()


def test_inbox_owner_pages_and_counts_match_read(campaign_access: Any, campaign_user: Any) -> None:
    """The query owner's messages, threads, counts and groups are the old ``read`` population."""

    world = _inbox_world(campaign_user)
    with system_context(reason="tests.messaging.inbox_read.population"):
        threads = _pks(Thread.objects.inbox())
        messages = _pks(Message.objects.inbox())
    for name in ("admin", "owner", "other_owner", "delegate", "outsider"):
        actor = getattr(world, name)
        old = (
            Message.objects.with_actor(actor).with_action("read").filter(pk__in=messages)
            .published().exclude(thread__modality="public_thread").for_feed(all_copies=True)
        )
        inbox = Message.objects.with_actor(actor).explorer()
        assert _pks(inbox.messages) == _pks(old), name
        assert _pks(inbox.threads) == _pks(
            Thread.objects.with_actor(actor).with_action("read").filter(pk__in=threads)
        ), name
        results = InboxResults(inbox, inbox.messages, InboxResultOptions())
        assert results.page().count == len(_pks(old)), name
        assert results.groups("conversation").page().message_count == len(_pks(old)), name


def test_inbox_reads_observe_grant_changes_on_the_next_statement(campaign_access: Any, campaign_user: Any) -> None:
    world = _inbox_world(campaign_user)
    world.channel_a.with_actor(world.owner).revoke_record_access("reader", world.delegate)
    assert not Thread.objects.with_actor(world.delegate).inbox().exists()
    assert not Message.objects.with_actor(world.delegate).inbox().exists()
    with system_context(reason="tests.messaging.inbox_read.unbind"):
        ProjectBinding.objects.unbind(project=world.project, target=world.bound)
    assert not Thread.objects.with_actor(world.project_reader).inbox().exists()
    assert not Message.objects.with_actor(world.project_reader).inbox().exists()
    with system_context(reason="tests.messaging.inbox_read.transfer"):
        Channel._base_manager.filter(pk=world.channel_a.pk).update(owner=world.other_owner)
    assert not Thread.objects.with_actor(world.owner).inbox().filter(pk=world.thread_a.pk).exists()
    assert Thread.objects.with_actor(world.other_owner).inbox().filter(pk=world.thread_a.pk).exists()
    # Channel management moved with the channel, and the trashed comment with it.
    assert not Message.objects.with_actor(world.owner).inbox().filter(pk=world.trashed.pk).exists()
    assert Message.objects.with_actor(world.other_owner).inbox().filter(pk=world.trashed.pk).exists()
    _assert_inbox_equals_read(world)


@pytest.fixture
def tightened_thread_read(campaign_access: Any) -> Iterator[SchemaOverride]:
    """A live override narrowing the generic thread read to thread owners; it does not reach ``read_inbox``."""

    read = SchemaPermission.objects.get(definition__resource_type="messaging/thread", name="read")
    override = SchemaOverride.objects.create(
        kind=SchemaOverride.KIND_TIGHTEN, target=read, expression="owner", reason="test: narrow generic thread reads",
    )
    yield override
    SchemaOverride.objects.filter(pk=override.pk).delete()


def test_an_override_on_a_derived_read_keeps_the_inbox_on_read(
    campaign_user: Any, tightened_thread_read: SchemaOverride,
) -> None:
    """The inbox follows an override on thread ``read``: it reads ``read`` until the override moves to ``read_granted``.

    Without the fallback, ``read_inbox`` would still admit the channel thread the override hides.
    """

    world = _inbox_world(campaign_user)
    # The thread's proof fails, and with it every read whose arrow reaches the thread's inbox read.
    for model in (Thread, Message):
        assert not purpose_read_holds(model, "read_inbox"), model
    assert not contextual_action_holds(Part, "read_inbox")
    with system_context(reason="tests.messaging.inbox_read.population"):
        population = {Thread: _pks(Thread.objects.inbox()), Message: _pks(Message.objects.inbox())}
    for name in _ACTORS:
        actor = getattr(world, name)
        for model, rows in population.items():
            readable = model.objects.with_actor(actor).with_action("read").filter(pk__in=rows)
            assert _pks(model.objects.with_actor(actor).inbox()) == _pks(readable), (name, model)
    # The channel owner's channel thread left its generic read, so it leaves the inbox too.
    assert _pks(Thread.objects.with_actor(world.owner).inbox()) == {world.owned.pk}
    assert world.thread_a.with_actor(world.owner).has_access("read_inbox")
    tightened_thread_read.delete()
    assert purpose_read_holds(Thread, "read_inbox")
    assert _pks(Thread.objects.with_actor(world.owner).inbox()) == {world.thread_a.pk, world.owned.pk}


def test_inbox_reads_follow_the_pinned_actor_and_fail_closed(campaign_access: Any, campaign_user: Any) -> None:
    world = _inbox_world(campaign_user)
    owner = world.owner
    expected = _pks(Message.objects.with_actor(owner).inbox())
    assert expected
    with actor_context(world.outsider):
        assert _pks(Message.objects.with_actor(owner).inbox()) == expected
        assert _pks(Thread.objects.with_actor(owner).inbox()) == {world.thread_a.pk, world.owned.pk}
        assert not Message.objects.inbox().exists()
    pinned = _pks(Message.objects.with_actor(owner).explorer().messages)
    with actor_context(owner):
        assert _pks(Message.objects.inbox()) == expected
        assert _pks(Message.objects.all().explorer().messages) == pinned
    assert {row.pk for row in Message.objects.with_actor(owner).inbox().iterator(chunk_size=1)} == expected
    with pytest.raises(MissingActorError):
        list(Thread.objects.inbox())


def _statements(rows: Callable[[], Any]) -> tuple[int, int]:
    """Count one warm evaluation's statements and SQL bytes, REBAC's own reads included."""

    with evaluator_scope():
        list(rows().values_list("pk", flat=True))
    with evaluator_scope(), CaptureQueriesContext(connection) as queries:
        list(rows().values_list("pk", flat=True))
    return len(queries), sum(len(query["sql"]) for query in queries)


def test_inbox_statements_leave_the_chatter_arms_out(campaign_access: Any, campaign_user: Any) -> None:
    """The same population under ``read`` also resolves every record's chatter read."""

    world = _inbox_world(campaign_user)
    for name in ("owner", "project_reader", "record_reader"):
        actor = getattr(world, name)
        for model in (Thread, Message):
            inbox = _statements(lambda: model.objects.with_actor(actor).inbox())
            generic = _statements(lambda: model.objects.with_actor(actor).inbox().with_action("read"))
            assert inbox[0] <= generic[0] and inbox[1] < generic[1], (name, model, inbox, generic)


def _record_of(kind: str, owner: Any) -> tuple[Any, Any, str]:
    """A record of ``kind``, the row whose grant admits its readers, and that grant's relation."""

    if kind == "knowledge/page":
        with actor_context(owner):
            vault = Vault.objects.create_for(owner, name="Chatter vault")
            page = Page.objects.create_in(vault, title="Chatter page")
        return page, page, "viewer"
    with system_context(reason="tests.messaging.inbox_read.record"):
        if kind == "chatterdemo/doc":
            record = ChatterDoc.objects.create(title="Chatter record")
            return record, record, "reader"
        project = Project.objects.create(title="Chatter project")
        if kind == "projects/project":
            return project, project, "reader"
        return Task.objects.create(project=project), project, "reader"


@pytest.mark.parametrize("kind", ("chatterdemo/doc", "projects/project", "projects/task", "knowledge/page"))
def test_record_readers_read_its_chatter_never_through_the_inbox(
    campaign_access: Any, campaign_user: Any, kind: str,
) -> None:
    """Every installed chatter target lends ``read`` to its chatter; no record admits a source conversation."""

    owner, reader, outsider = (campaign_user(role) for role in ("record-owner", "record-reader", "record-outsider"))
    record, granted, relation = _record_of(kind, owner)
    grant(granted, relation, reader)
    with system_context(reason="tests.messaging.inbox_read.record_threads"):
        chatter = ThreadAttachment.objects.ensure_for_record(record).thread
        source = ThreadAttachment.objects.ensure_for_record(record, role="source").thread
        comment = Message._base_manager.create(
            created_by=owner, thread=chatter, message_type="comment", status="synced", sent_at=T0,
        )
        evidence = Message._base_manager.create(created_by=owner, thread=source, status="synced", sent_at=T0)
    assert record.with_actor(reader).has_access("read")
    for row, readable in ((chatter, True), (comment, True), (source, False), (evidence, False)):
        rows = type(row).objects
        assert row.with_actor(reader).has_access("read") is readable, (kind, row)
        assert rows.with_actor(reader).with_action("read").filter(pk=row.pk).exists() is readable, (kind, row)
        assert not row.with_actor(reader).has_access("read_inbox"), (kind, row)
        assert not rows.with_actor(reader).with_action("read_inbox").filter(pk=row.pk).exists(), (kind, row)
        assert not rows.with_actor(reader).inbox().filter(pk=row.pk).exists(), (kind, row)
        for action in ("read", "read_inbox"):
            assert not row.with_actor(outsider).has_access(action), (kind, row, action)


RECORD_FEED = """
query RecordFeed($model: String!, $id: ID!) {
  record_thread(input: {model_label: $model, record_id: $id}) {
    error_code
    messages { id preview }
  }
}
"""


@pytest.mark.parametrize("inherits", (True, False))
@pytest.mark.parametrize("kind", ("chatterdemo/doc", "projects/project", "projects/task", "knowledge/page"))
def test_the_record_feed_shows_no_message_rebac_denies(
    campaign_access: Any, campaign_user: Any, kind: str, inherits: bool,
) -> None:
    """The feed reads chatter through the record's read only where that read reads the thread.

    ``REBAC_INHERIT_THROUGH_LINKS = False`` lends a task's chatter only the task's
    own grants, so the project's reader, who still reads the task through its
    project, is refused the feed instead of shown messages REBAC denies them.
    """

    owner, reader = (campaign_user(role) for role in ("feed-owner", "feed-reader"))
    record, granted, relation = _record_of(kind, owner)
    grant(granted, relation, reader)
    with system_context(reason="tests.messaging.inbox_read.record_feed"):
        chatter = ThreadAttachment.objects.ensure_for_record(record).thread
        comment = Message._base_manager.create(
            created_by=owner, thread=chatter, message_type="comment", status="synced", sent_at=T0,
        )
    variables = {"model": type(record)._meta.label, "id": public_id_for(type(record), record.pk)}
    schema = messaging_console_schema()
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=inherits):
        assert record.with_actor(reader).has_access("read")
        readable = comment.with_actor(reader).has_access("read")
        assert readable is (inherits or kind != "projects/task")
        assert unreadable_rows(schema, RECORD_FEED, variables, actor=reader) == []
        feed = result_data(execute_schema(schema, RECORD_FEED, variables, user=reader))["record_thread"]
        assert [message["id"] for message in feed["messages"]] == ([comment.sqid] if readable else [])
        assert (feed["error_code"] is None) is readable
        with actor_context(reader):
            if readable:
                assert Message.objects.for_record(record)[0] == [comment]
            else:
                with pytest.raises(PermissionDenied):
                    Message.objects.for_record(record)


def _deliver(thread: Any, author: Any, recipient: Any) -> Any:
    """Post a comment on ``thread`` and run its delivery owner, as posting does, addressing ``recipient``."""

    with system_context(reason="tests.messaging.inbox_read.deliver"):
        message = Message._base_manager.create(
            thread=thread, message_type="comment", direction="internal", created_by=author, sent_at=T0,
        )
        ThreadNotification.objects.fanout_for_message(
            message, attachment=ThreadAttachment._base_manager.get(thread=thread, role="chatter"),
            created_by_id=author.pk, recipient_user_ids=(recipient.pk,),
        )
    return message


@pytest.mark.parametrize("inherits", (True, False))
def test_chatter_follows_notifications_and_state_reach_only_its_readers(
    campaign_access: Any, campaign_user: Any, inherits: bool,
) -> None:
    """The record rule for chatter (follow, fan-out, cleanup, state, the timeline ping) holds only where it is lent.

    A project reader reads a task through its project. ``REBAC_INHERIT_THROUGH_LINKS
    = False`` lends the task's chatter only the task's own grants, so that reader
    is no longer admitted as a follower, notified, kept as a follower by the
    cleanups, shown chatter state or pinged about chatter activity; a row written
    before the setting was turned off no longer tells them its message exists.
    """

    owner, reader, newcomer = (campaign_user(role) for role in ("audience-owner", "audience-reader", "audience-new"))
    direct = campaign_user("audience-direct")
    record, granted, relation = _record_of("projects/task", owner)
    for user in (reader, newcomer):
        grant(granted, relation, user)
    grant(record, "reader", direct)
    with system_context(reason="tests.messaging.inbox_read.audience"):
        chatter = ThreadAttachment.objects.ensure_for_record(record).thread
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=True):
        with actor_context(reader):
            ThreadFollower.objects.subscribe(record, user=reader)
        earlier = _deliver(chatter, owner, reader)
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=inherits):
        readable = chatter.with_actor(reader).has_access("read")
        assert readable is inherits
        assert record.with_actor(reader).has_access("read")
        assert (reader.pk in record.thread_reader_ids([reader])) is readable
        later = _deliver(chatter, owner, reader)
        assert ThreadNotification._base_manager.filter(message=later, user=reader).exists() is readable
        notifications = ThreadNotification.objects.with_actor(reader).with_action("read_inbox").disclosed()
        disclosed = set(notifications.values_list("message_id", flat=True))
        assert disclosed == ({earlier.pk, later.pk} if readable else set())
        # A reader of the task itself reads its chatter under either policy, and is told.
        told = _deliver(chatter, owner, direct)
        assert ThreadNotification._base_manager.filter(message=told, user=direct).exists()
        with actor_context(reader):
            if readable:
                record.with_actor(reader).message_suggested_recipients(user=reader)
            else:
                with pytest.raises(PermissionDenied):
                    record.with_actor(reader).message_suggested_recipients(user=reader)
        with system_context(reason="tests.messaging.inbox_read.audience"):
            bare = Task.objects.create(project=record.project)
        grant(bare, "reader", owner)
        for target in (record, bare):
            # A task with no chatter thread yet: the follow creates it, and REBAC decides on it.
            with actor_context(newcomer):
                if readable:
                    ThreadFollower.objects.subscribe(target, user=newcomer)
                else:
                    with pytest.raises(PermissionDenied):
                        ThreadFollower.objects.subscribe(target, user=newcomer)
        assert (bare._attached_thread() is not None) is readable
        with actor_context(owner):
            ThreadFollower.objects.subscribe(bare, user=owner)
        assert bare.thread_reader_allowed(owner)
        follows =ThreadFollower._base_manager.filter(thread=chatter, party__person__user=reader)
        ThreadFollower.objects.end_unreadable_for_record(record)
        assert follows.exists() is readable
        if not readable:
            with override_settings(REBAC_INHERIT_THROUGH_LINKS=True), actor_context(reader):
                ThreadFollower.objects.subscribe(record, user=reader)
            with system_context(reason="tests.messaging.inbox_read.audience"):
                party = Person.objects.for_user(reader)
            ThreadFollower.objects.end_unreadable_for_party(party)
            assert not follows.exists()


@pytest.mark.parametrize("inherits", (True, False))
def test_an_old_notification_changes_unseen_once_its_message_is_unreadable(
    campaign_access: Any, campaign_user: Any, inherits: bool,
) -> None:
    """A row fanned out while links lent whole, then changed by the system, tells its recipient nothing under False.

    Its recipient reads the row (it is theirs) but, under ``False``, not its message: the lists hide the row
    and its change stream does too.
    """

    from rebac import to_subject_ref

    from angee.graphql.events import ChangePayload
    from angee.messaging.schema import NotificationChangeGate

    owner, reader = campaign_user("old-row-owner"), campaign_user("old-row-reader")
    record, granted, relation = _record_of("projects/task", owner)
    grant(granted, relation, reader)
    with system_context(reason="tests.messaging.inbox_read.old_row"):
        chatter = ThreadAttachment.objects.ensure_for_record(record).thread
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=True):
        message = _deliver(chatter, owner, reader)
    notice = ThreadNotification._base_manager.get(user=reader, message=message)
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=inherits):
        with system_context(reason="tests.messaging.inbox_read.system_change"):
            notice.metadata = {"delivery": "retried"}
            notice.save(update_fields=["metadata"])
        payload = ChangePayload.from_instance(notice, action="update", update_fields=["metadata"])
        event = NotificationChangeGate(ThreadNotification, to_subject_ref(reader)).filter(payload)
        assert (event is not None) is inherits


@pytest.mark.parametrize("inherits", (True, False))
def test_a_first_post_needs_the_chatter_thread_its_poster_will_read(
    campaign_access: Any, campaign_user: Any, inherits: bool,
) -> None:
    """A project editor writes a task through its project; under ``False`` they read none of its chatter.

    Their first comment would create a chatter thread they cannot read: it is refused, and the thread with
    it. The task's owner, whom the narrowed policy still lends the chatter, posts under either policy.
    """

    owner, editor = campaign_user("first-post-owner"), campaign_user("first-post-editor")
    with system_context(reason="tests.messaging.inbox_read.first_post"):
        project = Project.objects.create(title="First post project")
        task = Task.objects.create(project=project, owner=owner)
    grant(project, "editor", editor)
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=inherits):
        assert task.with_actor(editor).can_post()
        with actor_context(editor):
            if inherits:
                task.with_actor(editor).message_post("First post by the project editor.")
            else:
                with pytest.raises(PermissionDenied, match="reading its chatter thread"):
                    task.with_actor(editor).message_post("First post by the project editor.")
        assert (task._attached_thread() is not None) is inherits
        with actor_context(owner):
            task.with_actor(owner).message_post("First post by the owner.")
        assert task._attached_thread() is not None


def _with_thread_link() -> Any:
    """The host policy with ``messaging/message#thread`` filtered over the thread's table, which makes it a link."""

    published = backend().schema()

    def relink(definition: Any) -> Any:
        if definition.resource_type != "messaging/message":
            return definition
        linked = FieldBinding("thread", (("thread__channel__isnull", False),))
        relations = tuple(
            replace(relation, backing=linked) if relation.name == "thread" else relation
            for relation in definition.relations
        )
        return replace(definition, relations=relations)

    return replace(published, definitions=[relink(definition) for definition in published.definitions])


def test_the_inbox_proofs_read_the_policy_rebac_decides_with(db: None) -> None:
    """The proofs hold while the evaluated policy states them, and fail closed where only the published one does."""

    del db
    for inherits in (True, False):
        with override_settings(REBAC_INHERIT_THROUGH_LINKS=inherits):
            # The host policy keeps every arrow the proofs read: foreign keys of the row.
            assert purpose_read_holds(Thread, "read_inbox") and purpose_read_holds(Message, "read_inbox")
            assert contextual_action_holds(Part, "read_inbox")
    install_schema(_with_thread_link(), backend=backend())
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=True):
        assert purpose_read_holds(Message, "read_inbox") and contextual_action_holds(Part, "read_inbox")
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=False):
        # Through the link the message reads what a thread lends, under a generated name.
        arrows = permission_sources(backend().evaluated_schema(), "messaging/message", "read_inbox").arrows
        assert any(via == "thread" and is_generated(target) for via, target in arrows), arrows
        assert purpose_read_holds(Thread, "read_inbox")
        assert not purpose_read_holds(Message, "read_inbox")
        assert not contextual_action_holds(Part, "read_inbox")
