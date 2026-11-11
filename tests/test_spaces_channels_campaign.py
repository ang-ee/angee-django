"""A channel's team binding is a field gate held by the integration owner, never by the team's roster."""

import pytest
from django.db import transaction
from rebac import PermissionDenied, actor_context, system_context

from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging.testing.models import Channel
from angee.spaces.testing.models import Group
from tests.conftest import make_integration
from tests.spaces_campaign_helpers import roster as roster
from tests.spaces_campaign_helpers import spaces_console as spaces_console


@pytest.mark.parametrize("path", ("save", "graphql"))
def test_team_rebind_and_clear_require_integration_owner_or_administrator(roster, spaces_console, path):
    channel = make_integration("team-gate", model=Channel, backend_class="manual", team=roster.group)
    with system_context(reason="replacement team"):
        replacement = Group.objects.create(name="Replacement", visibility="public")
    for actor, allowed in (
        (channel.owner, True), (roster.actors["administrator"], True), (roster.actors["moderator"], False),
    ):
        for target in (replacement, None):
            with system_context(reason="reset channel binding"):
                Channel.objects.filter(pk=channel.pk).update(team=roster.group)
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
                row = Channel._base_manager.get(pk=channel.pk).with_actor(actor)
                row.team = target
                with actor_context(actor):
                    if allowed:
                        row.save(update_fields=["team"])
                    else:
                        with pytest.raises(PermissionDenied), transaction.atomic():
                            row.save(update_fields=["team"])
            expected_team = (target.pk if target is not None else None) if allowed else roster.group.pk
            assert Channel._base_manager.get(pk=channel.pk).team_id == expected_team
