"""The complete roster permission matrix through SQL, models, and GraphQL."""

import pytest
from django.db import transaction
from rebac import PermissionDenied, actor_context, system_context, to_object_ref, to_subject_ref
from rebac.backends import backend
from rebac.backends.local_query import LocalQueryScope
from rebac.evaluator import evaluator_scope
from rebac.models import SchemaPermission

from tests.conftest import execute_schema, result_data
from tests.spaces_campaign_helpers import MANAGERS, READERS, ROLES, SEATS, target_party
from tests.spaces_campaign_helpers import roster as roster
from tests.spaces_campaign_helpers import spaces_console as spaces_console
from tests.spaces_campaign_helpers import spaces_storage as spaces_storage
from tests.spaces_models import Group, Membership
from tests.test_spaces import spaces_tables as spaces_tables


def assert_permission(row, actor, permission, allowed):
    """Compare the evaluator and native actor-scoped SQL for the same permission."""
    assert backend().check_access(
        subject=to_subject_ref(actor), action=permission, resource=to_object_ref(row),
    ).allowed == allowed, (actor.username, permission, row.pk)
    assert type(row).objects.with_actor(actor).with_action(permission).filter(pk=row.pk).exists() == allowed


def test_every_group_permission_matches_all_nine_seats(roster):
    expected = {
        "create": set(SEATS), "read": READERS, "post": READERS - {"viewer"},
        "write": MANAGERS | {"moderator"}, "delete": MANAGERS,
        "transfer": {"column_owner", "administrator"}, "manage_roster": MANAGERS,
        "write__owner": {"column_owner", "administrator"},
    }
    assert set(SchemaPermission.objects.filter(definition__resource_type="spaces/group")
               .values_list("name", flat=True)) == set(expected)
    for permission, holders in expected.items():
        for seat, actor in roster.actors.items():
            assert_permission(roster.group, actor, permission, seat in holders)


def test_every_membership_permission_matches_all_roles_and_nine_seats(roster):
    permissions = {"create", "read", "write", "delete", "write__role", "write__group", "set_notifications"}
    assert set(SchemaPermission.objects.filter(definition__resource_type="spaces/membership")
               .values_list("name", flat=True)) == permissions
    for role in ROLES:
        row = roster.rows[role]
        managers = MANAGERS | ({"moderator"} if role in {"member", "viewer"} else set())
        expected = {
            "create": managers, "read": READERS, "write": managers, "delete": managers,
            "write__role": MANAGERS, "write__group": {"administrator"}, "set_notifications": {role},
        }
        for permission, holders in expected.items():
            for seat, actor in roster.actors.items():
                assert_permission(row, actor, permission, seat in holders)


def test_roster_and_holder_permissions_compile_to_sql(roster, django_assert_num_queries):
    actor = roster.actors["moderator"]
    with actor_context(actor), evaluator_scope():
        backend().schema()
        for row, permission, allowed in (
            (roster.group, "read", True), (roster.group, "post", True),
            (roster.group, "write", True), (roster.group, "manage_roster", False),
            (roster.rows["member"], "write", True), (roster.rows["moderator"], "set_notifications", True),
            (roster.rows["pending"], "set_notifications", False),
        ):
            predicate = LocalQueryScope(backend(), to_subject_ref(actor), "default").predicate(
                type(row), permission, to_object_ref(row).resource_type,
            )
            assert predicate is not None
            with django_assert_num_queries(1):
                assert list(type(row)._base_manager.filter(predicate, pk=row.pk).values_list("pk", flat=True)) == (
                    [row.pk] if allowed else []
                )


@pytest.mark.parametrize("verb", ("confirm", "dismiss"))
def test_review_flags_require_row_write_for_every_seat_and_target_role(roster, verb):
    for seat, actor in roster.actors.items():
        for role in ROLES:
            with system_context(reason="review target"):
                party = target_party(f"review-{seat}-{role}")
                row = Membership.objects.create(group=roster.group, party=party, role=role)
            allowed = seat in MANAGERS or (seat == "moderator" and role in {"member", "viewer"})
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
def test_graphql_roster_mutations_enforce_the_seat_and_role_matrix(roster, spaces_console, operation):
    unexpected_errors = []
    missing_id = None
    if operation in {"update", "delete"}:
        with system_context(reason="GraphQL missing target"):
            missing = Membership.objects.create(
                group=roster.group, party=target_party("graphql-missing-target"), role="member",
            )
            missing_id = str(missing.sqid)
            missing.delete()
    for seat, actor in roster.actors.items():
        for role in ROLES:
            party = target_party(f"graphql-{seat}-{role}", (actor,))
            with system_context(reason="GraphQL target"):
                row = None if operation in {"add", "insert"} else Membership.objects.create(
                    group=roster.group, party=party, role=role,
                )
            allowed = seat in MANAGERS or (
                operation != "update" and seat == "moderator" and role in {"member", "viewer"}
            )
            if operation == "add":
                root = "add_space_membership"
                query = f'''mutation {{ {root}(group_id: "{roster.group.sqid}",
                    party_id: "{party.sqid}", role: {role.upper()}) {{ id }} }}'''
            elif operation == "insert":
                root = "insert_space_memberships_one"
                query = f'''mutation {{ {root}(object: {{group: "{roster.group.sqid}",
                    party: "{party.sqid}", role: "{role}"}}) {{ id }} }}'''
            elif operation in {"confirm", "dismiss"}:
                root = f"{operation}_membership"
                query = f'mutation {{ {root}(id: "{row.sqid}") {{ id }} }}'
            elif operation == "update":
                root = "update_space_memberships_by_pk"
                next_role = "viewer" if role != "viewer" else "owner"
                query = f'''mutation {{ {root}(pk_columns: {{id: "{row.sqid}"}},
                    _set: {{role: "{next_role}"}}) {{ id }} }}'''
            else:
                root = "delete_space_memberships_by_pk"
                query = f'mutation {{ {root}(id: "{row.sqid}") {{ id }} }}'
            result = execute_schema(spaces_console, query, user=actor)
            if allowed:
                assert result_data(result)[root] is not None, (seat, role, operation)
            else:
                assert result.errors or result.data[root] is None, (seat, role, operation)
                if result.errors:
                    unexpected_errors.extend(
                        (seat, role, error.message, error.extensions)
                        for error in result.errors
                        if error.extensions.get("code") not in {"VALIDATION", "PERMISSION_DENIED"}
                    )
                if missing_id is not None and seat == "outsider" and role == "member":
                    missing_result = execute_schema(
                        spaces_console, query.replace(str(row.sqid), missing_id), user=actor,
                    )
                    assert result.errors and missing_result.errors
                    assert [error.extensions["code"] for error in result.errors] == ["VALIDATION"]
                    assert [(error.message, error.extensions) for error in result.errors] == [
                        (error.message, error.extensions) for error in missing_result.errors
                    ]
            stored = Membership._base_manager.filter(group=roster.group, party=party).first()
            if operation in {"add", "insert"}:
                assert (stored is not None) == allowed
                if stored:
                    assert stored.role == role
                    assert stored.is_confirmed == (operation == "add")
            elif operation == "delete":
                assert (stored is None) == allowed
            elif operation == "update":
                assert stored.role == (next_role if allowed else role)
            else:
                assert stored.is_confirmed == (allowed and operation == "confirm")
                assert stored.is_dismissed == (allowed and operation == "dismiss")
    # Check every persisted outcome before reporting malformed denial responses;
    # one earlier response must not hide the remaining seats in the matrix.
    assert not unexpected_errors, unexpected_errors


def test_clearing_column_ownership_preserves_attribution_and_roster_owner(roster):
    creator = roster.actors["column_owner"]
    with actor_context(creator):
        roster.group.with_actor(creator).transfer_ownership(None)
    stored = Group._base_manager.get(pk=roster.group.pk)
    assert stored.owner_id is None
    assert stored.created_by_id == creator.pk
    for permission in ("read", "post", "write", "delete", "transfer", "manage_roster"):
        assert_permission(stored, creator, permission, False)
        assert_permission(stored, roster.actors["administrator"], permission, True)
        assert_permission(stored, roster.actors["owner"], permission, permission != "transfer")


def test_administrator_manages_ownerless_group_with_only_moderators(roster):
    with system_context(reason="ownerless team"):
        group = Group.objects.create(name="Ownerless")
        Membership.objects.create(
            group=group, party=roster.people["moderator"], role="moderator", is_confirmed=True,
        )
    assert group.owner_id is None
    assert_permission(group, roster.actors["moderator"], "write", True)
    assert_permission(group, roster.actors["moderator"], "manage_roster", False)
    assert_permission(group, roster.actors["administrator"], "manage_roster", True)
