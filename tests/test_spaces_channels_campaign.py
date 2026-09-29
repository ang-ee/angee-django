"""Channel team binding grants content access but has an independent write gate."""

import pytest
from django.db import transaction
from rebac import PermissionDenied, actor_context, system_context
from rebac.backends import backend
from rebac.evaluator import evaluator_scope

from tests.conftest import execute_schema, make_integration, result_data
from tests.messaging_models import Channel, Message, Thread
from tests.spaces_campaign_helpers import roster as roster
from tests.spaces_campaign_helpers import spaces_console as spaces_console
from tests.spaces_campaign_helpers import spaces_storage as spaces_storage
from tests.spaces_models import Group
from tests.test_spaces import spaces_tables as spaces_tables


@pytest.mark.parametrize("path", ("save", "partial_save", "queryset_update", "graphql"))
def test_team_rebind_and_clear_require_integration_owner_or_administrator(roster, spaces_console, path):
    channel = make_integration("team-gate", model=Channel, backend_class="manual", team=roster.group)
    with system_context(reason="replacement team"):
        replacement = Group.objects.create(name="Replacement", visibility="public")
    actors = {seat: roster.actors[seat] for seat in ("moderator", "owner", "administrator")}
    actors["integration_owner"] = channel.owner
    for seat, actor in actors.items():
        for target in (replacement, None):
            with system_context(reason="reset channel binding"):
                Channel.objects.filter(pk=channel.pk).update(team=roster.group)
            allowed = seat in {"integration_owner", "administrator"}
            if path == "graphql":
                result = execute_schema(spaces_console, '''mutation($id: String!, $team: ID) {
                    update_channels_by_pk(pk_columns: {id: $id}, _set: {team: $team}) { id }
                }''', {"id": channel.sqid, "team": target.sqid if target else None}, user=actor)
                if allowed:
                    assert result_data(result)["update_channels_by_pk"] == {"id": channel.sqid}
                else:
                    assert result.errors
                    assert all(error.original_error is not None for error in result.errors)
            else:
                def write():
                    with actor_context(actor):
                        if path == "queryset_update":
                            return Channel.objects.with_actor(actor).filter(pk=channel.pk).update(team=target)
                        row = Channel._base_manager.get(pk=channel.pk).with_actor(actor)
                        row.team = target
                        row.save(**({"update_fields": ["team"]} if path == "partial_save" else {}))
                if allowed:
                    write()
                else:
                    with pytest.raises(PermissionDenied), transaction.atomic():
                        write()
            expected_team = (target.pk if target is not None else None) if allowed else roster.group.pk
            assert Channel._base_manager.get(pk=channel.pk).team_id == expected_team


def test_channel_team_access_reaches_threads_and_messages_and_ends_on_unbind(roster):
    channel = make_integration("team-content", model=Channel, backend_class="manual", team=roster.group)
    unrelated = make_integration("plain-integration")
    with system_context(reason="channel content"):
        roster.group.visibility = "public"
        roster.group.save(update_fields=["visibility"])
        thread = Thread.objects.create(channel=channel)
        message = Message.objects.create(channel=channel, thread=thread)
    for seat, actor in roster.actors.items():
        can_read = seat in {"owner", "moderator", "member", "column_owner", "administrator"}
        can_write = seat in {"owner", "moderator", "column_owner", "administrator"}
        for row in (channel, thread, message):
            assert row.with_actor(actor).has_access("read") == can_read, (seat, type(row))
            assert type(row).objects.with_actor(actor).filter(pk=row.pk).exists() == can_read
        assert channel.with_actor(actor).has_access("write") == can_write
        assert channel.with_actor(actor).has_access("delete") == can_write
        assert unrelated.with_actor(actor).has_access("read") == (seat == "administrator")
    with actor_context(channel.owner):
        channel.with_actor(channel.owner).team = None
        channel.save(update_fields=["team"])
    for seat in ("owner", "moderator", "member", "column_owner"):
        for row in (channel, thread, message):
            assert not type(row).objects.with_actor(roster.actors[seat]).filter(pk=row.pk).exists()


def test_channel_team_field_gate_compiles_as_one_sql_query(roster, django_assert_num_queries):
    channel = make_integration("team-sql", model=Channel, backend_class="manual", team=roster.group)
    for actor, expected in ((channel.owner, True), (roster.actors["moderator"], False)):
        with actor_context(actor), evaluator_scope():
            backend().schema()
            scoped = Channel.objects.with_actor(actor).with_action("write__team").scoped().filter(pk=channel.pk)
            assert list(scoped.values_list("pk", flat=True)) == ([channel.pk] if expected else [])
