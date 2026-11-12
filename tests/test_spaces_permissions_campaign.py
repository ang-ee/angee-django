"""The spaces decision table and its enforcement points: roster verbs, GraphQL capabilities and writes."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from rebac import PermissionDenied, actor_context, system_context, to_object_ref
from rebac.models import SchemaPermission
from rebac.testing import assert_decisions

from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging.testing.models import Channel, Message, Thread
from angee.spaces.testing.models import Group, Membership
from tests.conftest import Integration, Vault, make_integration
from tests.decision_table import seat_cells
from tests.spaces_campaign_helpers import MANAGERS, ROLES, target_party
from tests.spaces_campaign_helpers import roster as roster
from tests.spaces_campaign_helpers import spaces_console as spaces_console


def test_the_policy_decides_every_spaces_permission(roster):
    """One roster world, every declared spaces permission and extension arm with an allow and a deny."""

    actors, rows = roster.actors, roster.rows
    channel = make_integration("spaces-policy", model=Channel, backend_class="manual", team=roster.group)
    with system_context(reason="spaces policy world"):
        public = Group.objects.create(name="Public", visibility="public")
        ownerless = Group.objects.create(name="Ownerless")
        Membership.objects.create(
            group=ownerless, party=roster.people["moderator"], role="moderator", is_confirmed=True,
        )
        thread = Thread.objects.create(modality=Thread.Modality.GROUP)
        thread.groups.add(roster.group)
        channel_thread = Thread.objects.create(channel=channel)
        message = Message.objects.create(channel=channel, thread=channel_thread)
        vault = Vault.objects.create(name="Handbook", team=roster.group)
    integration = Integration._base_manager.get(pk=channel.pk)
    seats = {**actors, "anonymous": AnonymousUser(), "channel_owner": channel.owner}

    group, member_row, owner_row = roster.group, rows["member"], rows["owner"]
    roster_writers = "owner moderator column_owner administrator"
    cells = seat_cells(
        seats.__getitem__,
        # spaces/group
        (group, "shared_reader", "outsider", "anonymous"),
        (group, "create", "outsider", "anonymous"),
        (group, "read", "owner moderator member viewer column_owner administrator",
         "pending dismissed outsider anonymous"),
        (public, "read", "outsider", "anonymous"),
        (group, "post", "owner moderator member column_owner administrator", "viewer pending outsider"),
        (group, "write", roster_writers, "member viewer"),
        (group, "delete", "owner column_owner administrator", "moderator"),
        (group, "transfer", "column_owner administrator", "owner"),
        (group, "write__owner", "column_owner administrator", "owner"),
        (group, "manage_roster", "owner column_owner administrator", "moderator dismissed"),
        (ownerless, "write", "moderator administrator", "outsider"),
        (ownerless, "manage_roster", "administrator", "moderator"),
        # spaces/membership: moderators manage member and viewer rows, never owner rows
        *((member_row, action, roster_writers, "member viewer") for action in ("create", "write", "delete")),
        *((owner_row, action, "owner column_owner administrator", "moderator")
          for action in ("create", "write", "delete")),
        (member_row, "read", "viewer column_owner administrator", "pending outsider"),
        (member_row, "write__role", "owner column_owner administrator", "moderator"),
        (member_row, "write__group", "administrator", "owner column_owner"),
        (member_row, "set_notifications", "member", "moderator owner column_owner administrator"),
        (rows["viewer"], "set_notifications", "viewer", "member"),
        (rows["pending"], "set_notifications", "", "pending"),
        (rows["dismissed"], "set_notifications", "", "dismissed"),
        # extension arms: group threads, team channels, team vaults
        (thread, "read_granted", "viewer", "outsider"),
        (thread, "read", "viewer", "outsider pending"),
        (thread, "write", "member", "viewer"),
        (integration, "read", "member", "viewer"),
        (integration, "write", "moderator", "member"),
        (integration, "delete", "moderator", "member"),
        (channel, "write__team", "channel_owner administrator", "owner moderator column_owner"),
        (channel_thread, "read", "member", "viewer"),
        (message, "read", "member", "viewer"),
        (vault, "read_granted", "member", "viewer"),
        (vault, "write_granted", "moderator", "member"),
        (vault, "write__team", "administrator", "owner moderator"),
    )
    declared = SchemaPermission.objects.filter(definition__resource_type__in=("spaces/group", "spaces/membership"))
    covered = {(to_object_ref(resource).resource_type, action) for _, action, resource, _ in cells}
    assert set(declared.values_list("definition__resource_type", "name")) <= covered
    assert_decisions(
        checks=cells,
        accessible=(
            (actors["outsider"], "read", Group, (public,)),
            (actors["moderator"], "write", Membership, (member_row, rows["viewer"])),
            (actors["viewer"], "read", Thread, (thread,)),
            (actors["viewer"], "read", Vault, ()),
        ),
    )


def test_roster_graphql_returns_role_choices_and_row_permissions(roster, spaces_console):
    """The UI receives each seat's decisions: a manager, a moderator and a member."""

    document = """
        query RosterCapabilities($group: String!) {
          space_groups_by_pk(id: $group) { permissions membership_roles }
          space_memberships { id permissions }
        }
    """
    for seat in ("column_owner", "moderator", "member"):
        variables = {"group": roster.group.sqid}
        data = result_data(execute_schema(spaces_console, document, variables, user=roster.actors[seat]))
        group = data["space_groups_by_pk"]
        expected_roles = list(ROLES) if seat in MANAGERS else ["member", "viewer"] if seat == "moderator" else []
        assert group["membership_roles"] == [role.upper() for role in expected_roles]
        assert set(group["permissions"]) == (
            {"manage_roster", "write"} if seat in MANAGERS else {"write"} if seat == "moderator" else set()
        )
        rows = {row["id"]: set(row["permissions"]) for row in data["space_memberships"]}
        for target, membership in roster.rows.items():
            expected = set()
            if seat in MANAGERS:
                expected.update(("write", "write__role", "delete"))
            elif seat == "moderator" and target in {"member", "viewer"}:
                expected.update(("write", "delete"))
            if seat == target:
                expected.add("set_notifications")
            assert rows[membership.sqid] == expected, (seat, target)


def test_review_flags_require_row_write(roster):
    """Confirm and dismiss check the target row: managers any role, moderators members and viewers only."""

    for seat, role, allowed in (
        ("column_owner", "owner", True), ("moderator", "member", True),
        ("moderator", "owner", False), ("outsider", "member", False),
    ):
        actor = roster.actors[seat]
        for verb in ("confirm", "dismiss"):
            with system_context(reason="review target"):
                row = Membership.objects.create(
                    group=roster.group, party=target_party(f"review-{seat}-{role}-{verb}"), role=role,
                )
            with actor_context(actor):
                if allowed:
                    getattr(row.with_actor(actor), verb)()
                else:
                    with pytest.raises(PermissionDenied), transaction.atomic():
                        getattr(row.with_actor(actor), verb)()
            stored = Membership._base_manager.get(pk=row.pk)
            assert stored.is_confirmed == (allowed and verb == "confirm"), (seat, role, verb)
            assert stored.is_dismissed == (allowed and verb == "dismiss"), (seat, role, verb)


def test_moderator_reconfirms_same_row_without_role_write_and_cannot_promote(roster):
    actor = roster.actors["moderator"]
    with system_context(reason="dismiss member"):
        row = roster.rows["member"]
        row.dismiss()
    with actor_context(actor):
        confirmed = Membership.objects.add_confirmed(group=roster.group, party=roster.people["member"], role="member")
        assert confirmed.pk == row.pk
        assert confirmed.is_confirmed and not confirmed.is_dismissed
        for role in ("owner", "moderator", "viewer"):
            with pytest.raises(PermissionDenied), transaction.atomic():
                Membership.objects.add_confirmed(group=roster.group, party=roster.people["member"], role=role)
    stored = Membership._base_manager.get(pk=row.pk)
    assert stored.role == "member"
    assert Membership._base_manager.filter(group=roster.group, party=roster.people["member"]).count() == 1


@pytest.mark.parametrize("operation", ("add", "insert", "confirm", "dismiss", "update", "delete"))
def test_graphql_roster_mutations_enforce_the_seat_and_target_role(roster, spaces_console, operation):
    """Each roster write admits a manager, a moderator only below its rank, and refuses an outsider
    exactly as it refuses a missing row."""

    missing_id = None
    if operation in {"update", "delete"}:
        with system_context(reason="GraphQL missing target"):
            missing = Membership.objects.create(
                group=roster.group, party=target_party("graphql-missing-target"), role="member",
            )
            missing_id = str(missing.sqid)
            missing.delete()
    for seat, role, allowed in (
        ("column_owner", "owner", True),
        ("moderator", "member", operation != "update"),
        ("moderator", "owner", False),
        ("outsider", "member", False),
    ):
        actor = roster.actors[seat]
        party = target_party(f"graphql-{seat}-{role}", (actor,))
        with system_context(reason="GraphQL target"):
            row = None if operation in {"add", "insert"} else Membership.objects.create(
                group=roster.group, party=party, role=role,
            )
        next_role = "viewer" if role != "viewer" else "owner"
        if operation == "add":
            root = "add_space_membership"
            query = f'''mutation {{ {root}(group_id: "{roster.group.sqid}",
                party_id: "{party.sqid}", role: {role.upper()}) {{ id }} }}'''
        elif operation == "insert":
            root = "insert_space_memberships_one"
            query = f'''mutation {{ {root}(object: {{group: "{roster.group.sqid}",
                party: "{party.sqid}", role: "{role}"}}) {{ id }} }}'''
        elif operation == "update":
            root = "update_space_memberships_by_pk"
            query = f'''mutation {{ {root}(pk_columns: {{id: "{row.sqid}"}},
                _set: {{role: "{next_role}"}}) {{ id }} }}'''
        else:
            root = f"{operation}_membership" if operation != "delete" else "delete_space_memberships_by_pk"
            query = f'mutation {{ {root}(id: "{row.sqid}") {{ id }} }}'
        result = execute_schema(spaces_console, query, user=actor)
        if allowed:
            assert result_data(result)[root] is not None, (seat, role)
        else:
            assert result.errors or result.data[root] is None, (seat, role)
            codes = {error.extensions.get("code") for error in result.errors or ()}
            assert codes <= {"VALIDATION", "PERMISSION_DENIED"}, codes
            if missing_id is not None and seat == "outsider":
                missing_result = execute_schema(spaces_console, query.replace(str(row.sqid), missing_id), user=actor)
                assert [(error.message, error.extensions) for error in result.errors] == [
                    (error.message, error.extensions) for error in missing_result.errors
                ]
        stored = Membership._base_manager.filter(group=roster.group, party=party).first()
        if operation in {"add", "insert"}:
            assert (stored is not None) == allowed
            if stored:
                assert (stored.role, stored.is_confirmed) == (role, operation == "add")
        elif operation == "delete":
            assert (stored is None) == allowed
        elif operation == "update":
            assert stored.role == (next_role if allowed else role)
        else:
            assert stored.is_confirmed == (allowed and operation == "confirm")
            assert stored.is_dismissed == (allowed and operation == "dismiss")


def test_clearing_column_ownership_preserves_attribution_and_roster_owner(roster):
    creator = roster.actors["column_owner"]
    with actor_context(creator):
        roster.group.with_actor(creator).transfer_ownership(None)
    stored = Group._base_manager.get(pk=roster.group.pk)
    assert stored.owner_id is None
    assert stored.created_by_id == creator.pk
    assert_decisions(checks=[
        (actor, permission, stored, allowed)
        for permission in ("read", "post", "write", "delete", "transfer", "manage_roster")
        for actor, allowed in (
            (creator, False),
            (roster.actors["administrator"], True),
            (roster.actors["owner"], permission != "transfer"),
        )
    ])
