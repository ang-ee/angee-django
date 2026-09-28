"""Audience union, live identity, and notification acknowledgement contracts."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from rebac import PermissionDenied, actor_context, system_context
from rebac.models import active_relationship_model

from angee.messaging.models import NotificationPolicy, NotificationPreference
from tests.messaging_campaign import add_member, fanout, grant, make_user
from tests.messaging_campaign import audience_record as audience_record
from tests.messaging_models import Message, MessageSubtype, Party, ThreadFollower, ThreadNotification
from tests.projects_models import Project
from tests.spaces_models import Membership
from tests.test_messaging import Person
from tests.test_spaces import spaces_tables as spaces_tables


@pytest.mark.parametrize("source", ["follow", "team", "named", "direct"])
@pytest.mark.parametrize("readable", [False, True])
def test_every_audience_source_requires_the_record_read_permission(audience_record, source, readable, monkeypatch):
    case = audience_record
    # A record may require stronger read access than its team's default grant.
    monkeypatch.setattr(Project, "thread_read_access", "write")
    with system_context(reason="test.audience.read-gate"):
        if source == "follow":
            ThreadFollower.objects.subscribe(case.record, party=case.party)
        elif source == "team":
            add_member(case)
        elif source == "named":
            case.record.lead = case.recipient
            case.record.save(update_fields=("lead",))
    if readable:
        grant(case.record, "editor", case.recipient)
    message = fanout(case, direct=source == "direct")
    assert ThreadNotification._base_manager.filter(message=message, user=case.recipient).count() == int(readable)


@pytest.mark.parametrize("source", ["follow", "team", "named"])
def test_inbox_delivery_always_creates_one_independently_acknowledged_row(audience_record, source):
    case = audience_record
    grant(case.record, "reader", case.recipient)
    with system_context(reason="test.audience.inbox"):
        if source == "follow":
            follower = ThreadFollower.objects.subscribe(case.record, party=case.party)
        elif source == "team":
            add_member(case)
        else:
            case.record.lead = case.recipient
            case.record.save(update_fields=("lead",))
    message = fanout(case)
    (notice,) = ThreadNotification._base_manager.filter(message=message)
    assert notice.user_id == case.recipient.pk
    assert notice.notification_type == "inbox" and notice.read_at is None
    instant = datetime(2026, 1, 2, tzinfo=timezone.utc)
    with actor_context(case.recipient), patch("angee.messaging.managers.timezone.now", return_value=instant):
        first = ThreadNotification.objects.mark_read(notice, case.recipient)
    with actor_context(case.recipient):
        second = ThreadNotification.objects.mark_read(notice, case.recipient)
    assert first.read_at == second.read_at == instant
    if source == "follow":
        follower.refresh_from_db()
        assert follower.last_read_message_id != message.pk
    else:
        assert not ThreadFollower._base_manager.filter(thread=message.thread, party=case.party).exists()
    with system_context(reason="test.audience.retry"):
        assert ThreadNotification.objects.fanout_for_message(
            message, attachment=case.attachment, created_by_id=case.author.pk,
        ) == 0
    notice.refresh_from_db()
    assert notice.read_at == instant
    assert ThreadNotification._base_manager.filter(message=message).count() == 1


@pytest.mark.parametrize("policy", [None, "inbox", "email", "muted"])
def test_matching_follow_selects_delivery_otherwise_email_wins_derived_sources(audience_record, policy):
    case = audience_record
    add_member(case, policy="email")
    with system_context(reason="test.audience.union"):
        case.record.lead = case.recipient
        case.record.save(update_fields=("lead",))
        if policy is not None:
            ThreadFollower.objects.subscribe(case.record, party=case.party, notification_policy=policy)
    message = fanout(case)
    actual = list(ThreadNotification._base_manager.filter(message=message).values_list("notification_type", flat=True))
    assert actual == ([] if policy == "muted" else [policy or "email"])


def test_direct_address_bypasses_mute_but_never_notifies_the_author(audience_record):
    case = audience_record
    add_member(case)
    with system_context(reason="test.audience.direct"):
        ThreadFollower.objects.subscribe(case.record, party=case.party, notification_policy="muted")
    assert not ThreadNotification._base_manager.filter(message=fanout(case)).exists()
    message = fanout(case, direct=True)
    assert list(ThreadNotification._base_manager.filter(message=message).values_list("user_id", flat=True)) == [
        case.recipient.pk,
    ]
    case.recipient = case.author
    assert not ThreadNotification._base_manager.filter(message=fanout(case, direct=True)).exists()


def test_subtype_rejection_by_a_nonmuted_follow_does_not_silence_the_team(audience_record):
    case = audience_record
    add_member(case, policy="email")
    with system_context(reason="test.audience.subtypes"):
        ThreadFollower.objects.subscribe(case.record, party=case.party, notification_policy="inbox",
                                         subtype_keys=("record_updated",))
    message = fanout(case)
    assert ThreadNotification._base_manager.get(message=message).notification_type == "email"


def test_roster_add_dismiss_and_remove_take_effect_without_copying_followers(audience_record):
    case = audience_record
    assert not ThreadNotification._base_manager.filter(message=fanout(case)).exists()
    member = add_member(case)
    assert ThreadNotification._base_manager.filter(message=fanout(case)).count() == 1
    with system_context(reason="test.audience.dismiss"):
        Membership._base_manager.filter(pk=member.pk).update(is_dismissed=True)
    assert not ThreadNotification._base_manager.filter(message=fanout(case)).exists()
    with system_context(reason="test.audience.remove"):
        Membership._base_manager.filter(pk=member.pk).delete()
    assert not ThreadNotification._base_manager.filter(message=fanout(case)).exists()
    assert not ThreadFollower._base_manager.filter(thread=case.attachment.thread, party=case.party).exists()


def test_changing_the_record_named_account_moves_the_audience(audience_record):
    case = audience_record
    replacement = make_user("named-replacement")
    with system_context(reason="test.audience.named"):
        Party.objects.for_user(replacement)
        case.record.lead = case.recipient
        case.record.save(update_fields=("lead",))
    for user in (case.recipient, replacement):
        grant(case.record, "reader", user)
    with actor_context(case.author):
        first = case.record.message_post("Before reassignment")
    with system_context(reason="test.audience.reassign"):
        case.record.lead = replacement
        case.record.save(update_fields=("lead",))
    with actor_context(case.author):
        second = case.record.message_post("After reassignment")
    assert ThreadNotification._base_manager.get(message=first).user_id == case.recipient.pk
    assert ThreadNotification._base_manager.get(message=second).user_id == replacement.pk
    assert not ThreadFollower._base_manager.filter(thread=case.attachment.thread,
                                                  party__person__user__in=(case.recipient, replacement)).exists()


def test_accountless_party_follows_and_unfollows_without_delivery_or_access_grants(audience_record):
    case = audience_record
    with system_context(reason="test.audience.external"):
        external = Party.objects.create(display_name="External party")
        before = list(active_relationship_model().objects.order_by("pk").values())
        follower = ThreadFollower.objects.subscribe(case.record, party=external)
        assert follower.party_id == external.pk
        assert ThreadFollower.objects.is_following(case.record, party=external)
    assert not ThreadNotification._base_manager.filter(message=fanout(case)).exists()
    with system_context(reason="test.audience.unfollow"):
        ThreadFollower.objects.unsubscribe(case.record, party=external)
        assert not ThreadFollower.objects.is_following(case.record, party=external)
        assert list(active_relationship_model().objects.order_by("pk").values()) == before


def test_relinking_a_person_moves_the_follow_and_preserves_its_receipt(audience_record):
    case = audience_record
    replacement = make_user("relinked-account")
    grant(case.record, "reader", replacement)
    with system_context(reason="test.audience.relink"):
        follower = ThreadFollower.objects.subscribe(case.record, party=case.party)
        previous_receipt = follower.last_read_message_id
        Person._base_manager.filter(pk=case.party.pk).update(user=replacement)
    assert not ThreadFollower.objects.is_following(case.record, user=case.recipient)
    assert ThreadFollower.objects.is_following(case.record, user=replacement)
    message = fanout(case)
    assert ThreadNotification._base_manager.get(message=message).user_id == replacement.pk
    follower.refresh_from_db()
    assert follower.party_id == case.party.pk
    assert follower.last_read_message_id == previous_receipt


def test_reading_follow_state_never_creates_a_person(audience_record):
    newcomer = make_user("no-person-yet")
    assert not Person._base_manager.filter(user=newcomer).exists()
    assert not ThreadFollower.objects.is_following(audience_record.record, user=newcomer)
    assert not Person._base_manager.filter(user=newcomer).exists()


@pytest.mark.parametrize("declaration", ["missing", "title", "lead"])
def test_team_declaration_refuses_unknown_nonforeign_and_nonteam_fields(audience_record, monkeypatch, declaration):
    monkeypatch.setattr(Project, "thread_team_field", declaration)
    assert len([error for error in Project.check() if error.id == "messaging.E001"]) == 1


@pytest.mark.parametrize("declaration", [None, "team"])
def test_undeclared_or_null_team_keeps_only_explicit_followers(audience_record, monkeypatch, declaration):
    case = audience_record
    add_member(case)
    with system_context(reason="test.audience.no-team"):
        if declaration:
            case.record.team = None
            case.record.save(update_fields=("team",))
        ThreadFollower.objects.subscribe(case.record, party=case.party)
    monkeypatch.setattr(Project, "thread_team_field", declaration)
    grant(case.record, "reader", case.recipient)
    assert ThreadNotification._base_manager.filter(message=fanout(case)).count() == 1


def test_system_creation_logs_a_note_without_an_author_follow(audience_record):
    with system_context(reason="test.audience.system-create"):
        record = Project.objects.create(title="System-created record")
        attachment = record.thread_attachments.get(role="chatter")
    assert Message._base_manager.filter(thread=attachment.thread, message_type="notification",
                                        created_by__isnull=True).count() == 1
    assert not ThreadFollower._base_manager.filter(thread=attachment.thread).exists()


@pytest.mark.parametrize("size", [1, 10])
def test_roster_expansion_uses_one_query_at_team_size(audience_record, django_assert_num_queries, size):
    case = audience_record
    with system_context(reason="test.audience.query-budget"):
        for index in range(size):
            party = Party.objects.create(display_name=f"Member {index}")
            Membership.objects.create(group=case.team, party=party, is_confirmed=True)
    with django_assert_num_queries(1):
        members = list(case.team.thread_audience())
        assert len(members) == size
        assert all(member.notification_policy == NotificationPolicy.INBOX for member in members)


def test_fanout_checks_each_distinct_account_once_even_when_all_sources_match(audience_record):
    case = audience_record
    add_member(case)
    with system_context(reason="test.audience.read-budget"):
        case.record.lead = case.recipient
        case.record.save(update_fields=("lead",))
        ThreadFollower.objects.subscribe(case.record, party=case.party)
    with patch.object(Project, "thread_reader_allowed", autospec=True,
                      side_effect=Project.thread_reader_allowed) as check:
        message = fanout(case, direct=True)
    assert check.call_count == 1
    assert check.call_args.args[1].pk == case.recipient.pk
    assert ThreadNotification._base_manager.filter(message=message).count() == 1


def test_acknowledgement_refuses_wrong_recipient_even_under_system_context(audience_record):
    case = audience_record
    add_member(case)
    notice = ThreadNotification._base_manager.get(message=fanout(case))
    with system_context(reason="test.audience.wrong-recipient"), pytest.raises(PermissionDenied):
        ThreadNotification.objects.mark_read(notice, case.author)
    notice.refresh_from_db()
    assert notice.read_at is None


def test_follow_and_unfollow_do_not_grant_or_remove_a_read_share(audience_record):
    case = audience_record
    assert not case.record.thread_reader_allowed(case.recipient)
    with system_context(reason="test.audience.follow-without-access"):
        before = list(active_relationship_model().objects.order_by("pk").values())
        ThreadFollower.objects.subscribe(case.record, party=case.party)
        assert list(active_relationship_model().objects.order_by("pk").values()) == before
    assert not case.record.thread_reader_allowed(case.recipient)
    grant(case.record, "reader", case.recipient)
    with actor_context(case.recipient):
        ThreadFollower.objects.unsubscribe(case.record, party=case.party)
    assert case.record.thread_reader_allowed(case.recipient)


def test_preference_predicate_and_sql_agree_for_default_internal_and_explicit_subtypes(composed_tables):
    with system_context(reason="test.audience.preference-matrix"):
        subtypes = [None]
        for key, default, internal in (("public", True, False), ("internal", True, True), ("opt-in", False, False)):
            subtypes.append(MessageSubtype.objects.create(key=key, name=key, default=default, internal=internal))
        messages = [Message.objects.create(subtype=subtype) for subtype in subtypes]
    for policy in NotificationPolicy:
        for keys in ((), ("internal",), ("public", "opt-in")):
            preference = NotificationPreference(notification_policy=policy, subtype_keys=keys)
            expected = {row.pk for row in messages if preference.is_subscribed_to(row.subtype)}
            selected = Message._base_manager.filter(preference.subscribed_subtype_q()).values_list("pk", flat=True)
            assert set(selected) == expected
