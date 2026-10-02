"""Holder-only preferences and the live, constant-query roster audience."""

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import PermissionDenied, actor_context, system_context

from angee.base.checks import check_ownership
from angee.messaging.models import NotificationPolicy
from angee.messaging.testing.models import MessageSubtype, Party, Person, ThreadFollower
from angee.spaces.testing.models import Membership
from tests.conftest import create_user, execute_schema, result_data
from tests.spaces_campaign_helpers import ROLES
from tests.spaces_campaign_helpers import roster as roster
from tests.spaces_campaign_helpers import spaces_console as spaces_console
from tests.spaces_campaign_helpers import spaces_storage as spaces_storage
from tests.test_productivity_write_behavior import Queue
from tests.test_spaces import spaces_tables as spaces_tables


def test_only_confirmed_undismissed_holder_can_set_preferences_in_model_and_graphql(roster, spaces_console):
    for target_seat in (*ROLES, "pending", "dismissed"):
        for actor_seat, actor in roster.actors.items():
            row = Membership._base_manager.get(pk=roster.rows[target_seat].pk)
            before = (row.notification_policy, row.subtype_keys, row.updated_at)
            allowed = actor_seat == target_seat and target_seat in ROLES
            with actor_context(actor):
                if allowed:
                    row.with_actor(actor).set_notifications(NotificationPolicy.EMAIL, [])
                else:
                    with pytest.raises(PermissionDenied):
                        row.with_actor(actor).set_notifications(NotificationPolicy.EMAIL, [])
            result = execute_schema(
                spaces_console,
                '''mutation($id: ID!) { set_membership_notifications(
                    id: $id, policy: MUTED, subtype_keys: ["comment"]
                ) { id notification_policy subtype_keys } }''',
                {"id": row.sqid}, user=actor,
            )
            if allowed:
                assert result_data(result)["set_membership_notifications"] == {
                    "id": row.sqid, "notification_policy": "MUTED", "subtype_keys": ["comment"],
                }
            else:
                assert result.errors
                assert all(error.original_error is not None for error in result.errors)
            row.refresh_from_db()
            if not allowed:
                assert (row.notification_policy, row.subtype_keys, row.updated_at) == before
            if target_seat in {"pending", "dismissed"}:
                assert row.notification_policy == "inbox"
                assert row.subtype_keys == []


def test_holder_requires_both_review_flags_for_every_role(roster):
    for role in ROLES:
        actor = roster.actors[role]
        for confirmed, dismissed in ((False, False), (False, True), (True, True), (True, False)):
            Membership._base_manager.filter(pk=roster.rows[role].pk).update(
                is_confirmed=confirmed, is_dismissed=dismissed,
            )
            row = Membership._base_manager.get(pk=roster.rows[role].pk).with_actor(actor)
            assert row.has_access("set_notifications") == (confirmed and not dismissed)
            assert Membership.objects.with_actor(actor).with_action("set_notifications").filter(pk=row.pk).exists() == (
                confirmed and not dismissed
            )


def test_relinking_person_revokes_old_holder_without_rewriting_roster(roster, spaces_console):
    old = roster.actors["member"]
    new = create_user("replacement-holder")
    row = roster.rows["member"]
    person = roster.people["member"]
    with system_context(reason="relink roster identity"):
        person.user = new
        person.save(update_fields=["user"])
    for actor, allowed in ((old, False), (new, True)):
        with actor_context(actor):
            if allowed:
                row.with_actor(actor).set_notifications(NotificationPolicy.EMAIL, [])
            else:
                with pytest.raises(PermissionDenied):
                    row.with_actor(actor).set_notifications(NotificationPolicy.EMAIL, [])
        result = execute_schema(spaces_console, '''mutation($id: ID!) {
            set_membership_notifications(id: $id, policy: INBOX, subtype_keys: []) { id }
        }''', {"id": row.sqid}, user=actor)
        assert bool(result.errors) == (not allowed)
    row.refresh_from_db()
    assert row.party_id == person.pk
    assert row.is_confirmed and row.role == "member"


def test_notification_preferences_validate_catalogue_and_preserve_row_on_error(roster):
    actor = roster.actors["member"]
    row = roster.rows["member"]
    with system_context(reason="custom message subtype"):
        MessageSubtype.objects.create(key="review_ready", name="Review ready")
    with actor_context(actor):
        for policy, keys in (
            (NotificationPolicy.INBOX, []), (NotificationPolicy.EMAIL, ["comment"]),
            (NotificationPolicy.MUTED, ["review_ready"]),
            (NotificationPolicy.INBOX, ["comment", "review_ready"]),
        ):
            row.with_actor(actor).set_notifications(policy, keys)
            row.refresh_from_db()
            assert row.notification_policy == policy
            assert row.subtype_keys == keys
        before = (row.notification_policy, row.subtype_keys, row.updated_at)
        for policy, keys in (
            ("unknown", []), (NotificationPolicy.EMAIL, [""]),
            (NotificationPolicy.EMAIL, [None]), (NotificationPolicy.EMAIL, [1]),
            (NotificationPolicy.EMAIL, ["comment", "comment"]),
            (NotificationPolicy.EMAIL, ["not_declared"]),
        ):
            with pytest.raises(ValidationError):
                row.with_actor(actor).set_notifications(policy, keys)
            row.refresh_from_db()
            assert (row.notification_policy, row.subtype_keys, row.updated_at) == before


def test_graphql_generated_writes_do_not_accept_preference_columns(roster, spaces_console):
    actor = roster.actors["column_owner"]
    row = roster.rows["member"]
    for column, value in (("notification_policy", '"muted"'), ("subtype_keys", '["comment"]')):
        for query in (
            f'''mutation {{ update_space_memberships_by_pk(pk_columns: {{id: "{row.sqid}"}},
                _set: {{{column}: {value}}}) {{ id }} }}''',
            f'''mutation {{ insert_space_memberships_one(object: {{group: "{roster.group.sqid}",
                party: "{roster.people['outsider'].sqid}", role: "member", {column}: {value}}}) {{ id }} }}''',
        ):
            result = execute_schema(spaces_console, query, user=actor)
            assert result.errors
            assert any(column in error.message and "not defined" in error.message for error in result.errors)
    row.refresh_from_db()
    assert row.notification_policy == "inbox" and row.subtype_keys == []
    assert not Membership._base_manager.filter(group=roster.group, party=roster.people["outsider"]).exists()


def test_audience_has_one_audited_read_at_two_sizes_and_keeps_external_parties(roster, django_assert_num_queries):
    expected = {roster.people[role].pk: (NotificationPolicy.INBOX, ()) for role in ("owner", "moderator", "member")}
    for size in (3, 20):
        with system_context(reason="grow audience"):
            while len(expected) < size:
                party = Party.objects.create(display_name=f"External member {len(expected)}")
                Membership.objects.create(
                    group=roster.group, party=party, is_confirmed=True, role="member",
                    notification_policy="email", subtype_keys=["comment"],
                )
                expected[party.pk] = (NotificationPolicy.EMAIL, ("comment",))
        # One system-queryset audit INSERT and one roster SELECT at either size.
        with django_assert_num_queries(2):
            actual = {member.party_id: (member.notification_policy, member.subtype_keys)
                      for member in roster.group.thread_audience()}
        assert actual == expected
    assert not ThreadFollower._base_manager.exists()
    assert not Person._base_manager.filter(pk__in=set(expected) - {p.pk for p in roster.people.values()}).exists()


def test_queue_inherits_parent_audience_and_roster_changes_never_create_followers(roster, django_assert_num_queries):
    with system_context(reason="queue audience"):
        queue = Queue.objects.create(name="Queue", provision_stages=False)
        row = Membership.objects.create(
            group=queue, party=roster.people["member"], role="member", is_confirmed=True,
            notification_policy="muted",
        )
    assert queue._meta.get_field("owner").model is type(roster.group)
    assert not [error for error in check_ownership([apps.get_app_config("work")]) if error.obj is Queue]
    with django_assert_num_queries(2):
        audience = [(member.party_id, member.notification_policy) for member in queue.thread_audience()]
    assert audience == [(row.party_id, NotificationPolicy.MUTED)]
    with system_context(reason="remove audience member"):
        row.delete()
    with django_assert_num_queries(2):
        assert list(queue.thread_audience()) == []
    assert not ThreadFollower._base_manager.exists()
