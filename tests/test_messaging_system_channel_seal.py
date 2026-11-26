"""Administrators alone read, send on, write, annotate and delete the system channel, and no fragment unseals it."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    system_context,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model
from rebac.testing import assert_decisions

from angee.base.build import extension_source_map
from angee.messaging.checks import check_messaging_seals
from angee.messaging.testing.models import Channel, Message, Part, Person, Thread
from angee.projects.testing.models import Project, ProjectBinding
from angee.spaces.testing.models import Group, Membership
from tests.conftest import create_platform_admin, create_user, make_integration
from tests.messaging_campaign import grant
from tests.test_iam_account_actions import _manager
from tests.test_messaging_system_mail import LOCMEM, SENDER, _install
from tests.test_zed_extensions import _addon, _apply_emitted_schema_paths, _contrib_addon

# A fragment arm on each sealed channel permission would reach the system channel past its
# seal. Fragments widen the parent integration instead, which the seal subtracts: the
# installed spaces and projects fragments do, and the installed composition passes.
_SEALED_CHANNEL_PERMISSIONS = ("read", "reply", "write", "annotate", "delete")


def test_the_installed_fragments_widen_the_integration_and_keep_the_seal() -> None:
    assert check_messaging_seals() == []


@pytest.mark.parametrize("permission", _SEALED_CHANNEL_PERMISSIONS)
def test_check_refuses_fragment_arms_on_the_sealed_channel_permissions(tmp_path: Path, permission: str) -> None:
    declaration = Path(apps.get_app_config("messaging").path, "permissions.zed").read_text(encoding="utf-8")
    messaging = _addon(tmp_path, "messaging", "permissions.zed", declaration)
    contrib = _contrib_addon(
        tmp_path,
        f"definition messaging/channel {{\n    relation helper: auth/user\n    permission {permission} = helper\n}}\n",
        name="channel_contrib",
    )
    assert check_messaging_seals([messaging]) == []
    sources = extension_source_map([messaging, contrib])
    runtime = tmp_path / "runtime"
    for relative, text in sources.items():
        output = runtime / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    _apply_emitted_schema_paths([messaging, contrib], runtime, sources=sources)

    [error] = check_messaging_seals([messaging, contrib])
    assert error.id == "messaging.E003"
    assert f"messaging/channel#{permission}" in error.msg
    assert "channel_contrib" in error.msg


# Every seat that reaches an ordinary channel by a grant, a binding or an ownership: the
# administrator's own acts open each path, and the seal closes each on the system channel.
_BOUND_SEATS = ("channel_reader", "channel_replier", "integration_reader", "team_member", "project_reader", "owner")
_UNBOUND_SEATS = ("member", "people_manager", "other_channel_reader", "recipient", "cause", "anonymous")
_CHECKED = ("read", "reply", "write")


def _bind(channel: Any, admin: Any, seats: SimpleNamespace, team: Any, project: Any) -> None:
    """Open every way a channel is read, each through an administrator's own act."""

    channel.with_actor(admin).grant_record_access("reader", seats.channel_reader)
    channel.with_actor(admin).grant_record_access("replier", seats.channel_replier)
    channel.integration_ptr.with_actor(admin).grant_record_access("reader", seats.integration_reader)
    with system_context(reason="tests.messaging.seal.bind"):
        Channel._base_manager.filter(pk=channel.pk).update(team=team)
        ProjectBinding.objects.bind(project=project, target=channel.integration_ptr)
    with actor_context(admin):
        Channel._base_manager.get(pk=channel.pk).with_actor(admin).transfer_ownership(seats.owner)


@pytest.fixture
def sealed(db: None, settings: Any) -> SimpleNamespace:
    """The system channel and an ordinary email channel, each with one message, bound to the same seats."""

    del db
    settings.MAILERS = {"default": LOCMEM, "transport": LOCMEM}
    settings.DEFAULT_FROM_EMAIL = SENDER
    system = _install()
    admin = create_platform_admin("seal-admin")
    seats = SimpleNamespace(
        admin=admin, anonymous=AnonymousUser(), people_manager=_manager("seal-people-manager"),
        **{name: create_user(f"seal-{name.replace('_', '-')}") for name in (*_BOUND_SEATS, "member", "cause")},
        other_channel_reader=create_user("seal-other-channel-reader"),
    )
    with system_context(reason="tests.messaging.seal.recipient"):
        seats.recipient = type(admin).objects.create_user("seal-recipient", "seal-recipient@example.test")
        team = Group.objects.create(name="Seal team", slug="seal-team")
        Membership.objects.create(group=team, party=Person.objects.for_user(seats.team_member), role="member",
                                  is_confirmed=True)
        project = Project.objects.create(title="Seal project")
    grant(project, "reader", seats.project_reader)
    control = make_integration("seal-control", model=Channel, backend_class="email", owner=None)
    other = make_integration("seal-other", model=Channel, backend_class="email")
    grant(other, "reader", seats.other_channel_reader)
    for channel in (system, control):
        _bind(channel, admin, seats, team, project)
    with actor_context(seats.cause):
        mail = Message.objects.compose_as_system(
            reason="tests.messaging.seal", to=["seal-recipient@example.test"], subject="Sign in", text="Your link.",
        )
    control_mail = Message.objects.compose(channel=control, to=["seal-recipient@example.test"], text="Hello.",
                                           actor=admin)
    with system_context(reason="tests.messaging.seal.rows"):
        rows = {
            name: (Channel._base_manager.get(pk=message.channel_id), Thread._base_manager.get(pk=message.thread_id),
                   Message._base_manager.get(pk=message.pk), Part._base_manager.filter(message=message).first())
            for name, message in (("system", mail), ("control", control_mail))
        }
    return SimpleNamespace(seats=seats, rows=rows, other=other)


def test_administrators_alone_read_send_on_and_write_the_system_channel(sealed: SimpleNamespace) -> None:
    """The decision table: every path that opens an ordinary channel is closed on the system channel.

    The control channel, bound to the same seats by the same acts, proves each seat's path is open,
    so no refused cell on the system channel passes for want of a grant.
    """

    seats = sealed.seats

    def cells(name: str) -> list[tuple[Any, str, Any, bool]]:
        channel, thread, message, part = sealed.rows[name]
        allowed_on = {"admin"} | (set(_BOUND_SEATS) if name == "control" else set())
        targets = (
            *((channel, action) for action in _CHECKED),
            (thread, "read"), (message, "read"), (message, "read_inbox"), (part, "read"),
            # ``delete`` is the shared trash verb's permission (``TRASH_PERMISSION``).
            (message, "delete"),
        )
        return [
            (getattr(seats, seat), action, target, seat in allowed_on and (seat, action) not in _CONTROL_GAPS)
            for seat in ("admin", *_BOUND_SEATS, *_UNBOUND_SEATS)
            for target, action in targets
        ]

    assert_decisions(checks=[*cells("system"), *cells("control")])


_CONTROL_GAPS = {
    # On an ordinary channel these seats read but do not send or manage: the table states each.
    *((seat, action) for seat in ("channel_reader", "integration_reader", "team_member", "project_reader")
      for action in ("reply", "write", "delete")),
    ("channel_replier", "write"), ("channel_replier", "delete"),
}


def test_a_system_message_is_never_trashed_through_a_records_moderation(sealed: SimpleNamespace) -> None:
    """System mail sits in the system channel's own thread: a record's moderator never takes it off.

    The shared trash verb decides ``delete`` on it, which the table gives administrators alone.
    """

    message = sealed.rows["system"][2]
    assert message.trash_error() is not None
    assert not message.can_trash(moderate_access=True)


def test_nobody_sends_from_the_system_channel_by_choice_yet_an_administrator_may_compose_on_it(
    sealed: SimpleNamespace,
) -> None:
    """The system channel is system-only, so no seat is given it to send from; ``compose`` decides ``reply``.

    No seat, administrators included, finds it on the sending selection
    (``ChannelQuerySet.outbound_email``). ``compose`` decides ``reply`` on the channel,
    which administrators alone hold there, so an administrator's
    ``compose(channel=system_channel())`` is admitted and every other seat's is refused.
    """

    seats = sealed.seats
    system = Channel.objects.system_channel()
    for seat in ("admin", *_BOUND_SEATS, *_UNBOUND_SEATS):
        actor = getattr(seats, seat)
        if seat != "anonymous":
            sendable = set(Channel.objects.with_actor(actor).outbound_email())
            assert system not in sendable, seat
        if seat == "admin":
            composed = Message.objects.compose(channel=system, to=["test-send@example.test"], text="Test.", actor=actor)
            assert composed.channel_id == system.pk
        elif seat != "anonymous":
            with pytest.raises(PermissionDenied):
                Message.objects.compose(channel=system, to=["test-send@example.test"], text="Test.", actor=actor)
    assert sealed.rows["control"][0] in set(Channel.objects.with_actor(seats.admin).outbound_email())


def test_system_threads_show_in_administrators_inbox_lists_and_nobody_elses(sealed: SimpleNamespace) -> None:
    """No filter hides system mail from the inbox: its readers, administrators alone, find its threads there."""

    seats = sealed.seats
    thread = sealed.rows["system"][1]
    replier_elsewhere = create_user("seal-replier-elsewhere")
    sealed.other.with_actor(seats.admin).grant_record_access("replier", replier_elsewhere)
    listed = {
        name: thread.pk in set(Thread.objects.with_actor(actor).inbox().values_list("pk", flat=True))
        for name, actor in (("admin", seats.admin), ("member", seats.member), ("replier", replier_elsewhere))
    }
    assert listed == {"admin": True, "member": False, "replier": False}


def test_an_administrators_share_of_system_mail_is_refused_and_the_member_reads_nothing(
    sealed: SimpleNamespace,
) -> None:
    """Nobody shares a system thread or grants on a system message, administrators included."""

    seats = sealed.seats
    _channel, thread, message, part = sealed.rows["system"]
    with pytest.raises(PermissionDenied):
        thread.with_actor(seats.admin).grant_reader(user=seats.member)
    with pytest.raises(PermissionDenied):
        message.with_actor(seats.admin).grant_record_access("reader", seats.member)
    member = to_subject_ref(seats.member)
    assert not active_relationship_model().objects.filter(
        subject_type=member.subject_type, subject_id=member.subject_id,
    ).exists()
    assert_decisions(checks=[
        (seats.member, action, target, False)
        for target, action in ((thread, "read"), (message, "read"), (message, "read_inbox"), (part, "read"))
    ])


def test_tuples_no_verb_writes_on_system_mail_read_it_but_never_write_or_share_it(sealed: SimpleNamespace) -> None:
    """A tuple on a system thread or message, which no verb writes, and the identity rule: what each holds.

    Their reads are not subtracted (the inbox's planned reads cannot afford it); nobody but an
    administrator writes the thread, nobody writes the message or shares the thread, and only an
    administrator deletes either.
    """

    seats = sealed.seats
    _channel, thread, message, part = sealed.rows["system"]
    holders = {name: create_user(f"seal-{name}") for name in ("thread_reader", "thread_editor", "message_reader",
                                                                 "message_editor")}
    identity = SubjectRef(ObjectRef("provenance/seed", "seal-identity"))
    for target, relation, holder in (
        (thread, "reader", holders["thread_reader"]), (thread, "editor", holders["thread_editor"]),
        (message, "reader", holders["message_reader"]), (message, "editor", holders["message_editor"]),
    ):
        grant(target, relation, holder)
    with system_context(reason="tests.messaging.seal.identity"):
        write_relationships([RelationshipTuple(
            ObjectRef("provenance/role", "messaging_identity_source"), "member", identity,
        )])
    seated = (*holders.values(), identity)
    assert_decisions(checks=[
        *((seat, "read", message, True) for seat in seated),
        *((seat, "read", part, True) for seat in seated),
        *((seat, action, thread, False) for seat in seated for action in ("write", "share", "delete")),
        *((seat, action, message, False) for seat in seated for action in ("write", "delete")),
        (seats.admin, "share", thread, False), (seats.admin, "write", message, False),
        (seats.admin, "write", thread, True), (seats.admin, "delete", message, True),
    ])
