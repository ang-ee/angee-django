"""The people directory's read scope.

Everyone signed in reads a person's name wherever a record embeds the account.
The restricted fields (sign-in name, email, last sign-in, staff and active
state, preferences) are field gates for people managers and the person. People
managers — platform admins, named directory readers and the roles a consumer
unions into ``auth/user#list`` — list every account; anyone else lists no one
and is offered their colleagues in pickers.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rebac import (
    ObjectRef,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.backends.local import mark_relationships_changed
from rebac.models import active_relationship_model

from tests.conftest import (
    Page,
    addon_schema,
    create_platform_admin,
    execute_schema,
    result_data,
    vault_for,
)
from tests.test_iam_account_actions import _manager
from tests.test_iam_graphql import _schema
from tests.test_messaging import ThreadedTicket
from tests.test_spaces import spaces_tables as spaces_tables

User = get_user_model()
knowledge_schema = importlib.import_module("angee.knowledge.schema")
_DIRECTORY = ObjectRef("iam/directory", "main")
_FIELDS = "username email is_active is_staff last_login display_name"
_PEOPLE = "{ users(where: {directory: {_eq: true}}, limit: 100) { " + _FIELDS + " } }"
_OFFERED = "{ users(limit: 100) { " + _FIELDS + " } }"
_ACTIVE = "query($active: Boolean!) { users(where: {active: {_eq: $active}}, limit: 100) { username } }"
_COLLEAGUES = "{ colleagues(limit: 100) { display_name } }"
_CURRENT = "{ current_user { username email is_active } }"
_PERMITTED = 'query { current_user { permitted(refs: ["iam.User#list", "iam.User#read__email"]) } }'
_WITHHELD = {"username": None, "email": None, "is_active": None, "is_staff": None, "last_login": None}


def _person(username: str, **fields: Any) -> Any:
    """Create one person with an email and a recorded sign-in."""

    user = User.objects.create_user(username, f"{username}@example.com", **fields)
    with system_context(reason="test.directory.sign_in"):
        User.objects.filter(pk=user.pk).update(last_login=timezone.now())
    return user


def _run(viewer: Any, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    return result_data(execute_schema(_schema("console"), query, variables, user=viewer))


def _people(viewer: Any) -> list[dict[str, Any]]:
    """The managed people list as IAM's users page asks for it."""

    return list(_run(viewer, _PEOPLE)["users"])


def _offered(viewer: Any) -> dict[str, dict[str, Any]]:
    """The users a relation picker offers ``viewer``, by display name."""

    return {row["display_name"]: row for row in _run(viewer, _OFFERED)["users"]}


def _colleagues(viewer: Any) -> set[str]:
    return {row["display_name"] for row in _run(viewer, _COLLEAGUES)["colleagues"]}


def test_everyone_signed_in_reads_a_name_but_not_the_restricted_fields(composed_tables: None) -> None:
    viewer = _person("name-viewer")
    other = _person("name-other", first_name="Ada", last_name="Lovelace")

    with actor_context(viewer):
        assert other.with_actor(viewer).has_access("read")
        assert not other.with_actor(viewer).has_access("read__email")
        seen = User.objects.with_actor(viewer).get(pk=other.pk)
    assert (seen.first_name, seen.last_name) == ("Ada", "Lovelace")
    assert (seen.username, seen.email, seen.is_active, seen.is_staff, seen.last_login, seen.preferences) == (
        None, None, None, None, None, None,
    )


def test_a_person_reads_their_own_restricted_fields(composed_tables: None) -> None:
    viewer = _person("own-viewer")

    own = User.objects.with_actor(viewer).get(pk=viewer.pk)
    assert (own.username, own.email, own.is_active) == ("own-viewer", "own-viewer@example.com", True)
    assert own.last_login is not None
    assert result_data(execute_schema(_schema("console"), _CURRENT, user=viewer))["current_user"] == {
        "username": "own-viewer", "email": "own-viewer@example.com", "is_active": True,
    }
    assert _offered(viewer)["own-viewer"]["email"] == "own-viewer@example.com"
    # The person reads their own fields, which makes no people manager at type level.
    assert _run(viewer, _PERMITTED)["current_user"]["permitted"] == []


def test_a_person_who_manages_no_one_lists_nobody_without_an_error(composed_tables: None) -> None:
    viewer = _person("list-viewer")
    _person("list-other")

    assert _people(viewer) == []
    assert _run(viewer, _ACTIVE, {"active": True})["users"] == [{"username": "list-viewer"}]
    assert _run(viewer, _ACTIVE, {"active": False})["users"] == []


def test_a_record_embeds_a_name_for_a_reader_who_manages_no_one(composed_tables: None) -> None:
    reader = _person("record-reader")
    author = _person("record-author", first_name="Grace", last_name="Hopper")
    vault = vault_for(reader)
    with actor_context(reader):
        page = Page.objects.create_in(vault, title="Shared reading")
    with system_context(reason="test.directory.author"):
        Page.objects.filter(pk=page.pk).update(created_by=author, updated_by=author)

    rows = result_data(execute_schema(
        addon_schema(knowledge_schema.schemas, "public"), "{ pages { created_by created_by_label } }", user=reader,
    ))["pages"]

    assert rows == [{"created_by": author.public_id, "created_by_label": "Grace Hopper"}]


def test_pickers_offer_a_non_manager_their_colleagues(composed_permissions: None) -> None:
    del composed_permissions
    viewer = _person("picker-viewer", first_name="Viewer")
    follower = _person("picker-follower", first_name="Follower")
    former = _person("picker-former", first_name="Former")
    _person("picker-stranger", first_name="Stranger")
    with actor_context(viewer):
        ticket = ThreadedTicket.objects.create(title="Shared record")
    with system_context(reason="test.directory.followers"):
        for person in (follower, former):
            ticket.message_subscribe(user=person)
        User.objects.filter(pk=former.pk).update(is_active=False)

    # Their own account and the active people following a record they read.
    assert _colleagues(viewer) == {"Viewer", "Follower"}
    offered = _offered(viewer)
    assert set(offered) == {"Viewer", "Follower"}
    assert offered["Follower"] == {**_WITHHELD, "display_name": "Follower"}
    # Still no directory, and no probing a colleague's restricted fields by search.
    assert _people(viewer) == []
    assert User.objects.visible_people(viewer, search="picker-follower") == []
    assert [person.first_name for person in User.objects.visible_people(viewer, search="Follow")] == ["Follower"]


@pytest.mark.parametrize("manager", ["platform-admin", "directory-reader", "consumer-role"])
def test_people_managers_list_every_account_and_filter_by_activity(spaces_tables: None, manager: str) -> None:
    del spaces_tables
    if manager == "platform-admin":
        viewer = create_platform_admin("people-manager")
    elif manager == "directory-reader":
        viewer = _person("people-manager")
        write_relationships([RelationshipTuple(resource=_DIRECTORY, relation="reader", subject=to_subject_ref(viewer))])
    else:
        viewer = _manager("people-manager")
    _person("person-active")
    _person("person-former", is_active=False)

    rows = {row["username"]: row for row in _people(viewer)}

    assert {"people-manager", "person-active", "person-former"} <= set(rows)
    assert rows["person-active"]["email"] == "person-active@example.com"
    assert rows["person-active"]["last_login"] is not None
    assert rows["person-former"]["is_active"] is False
    active = {row["username"] for row in _run(viewer, _ACTIVE, {"active": True})["users"]}
    former = {row["username"] for row in _run(viewer, _ACTIVE, {"active": False})["users"]}
    assert {"person-active", "people-manager"} <= active and "person-former" not in active
    assert former == {"person-former"}
    assert "person-active" in {person.username for person in User.objects.visible_people(viewer, search="active")}
    assert _run(viewer, _PERMITTED)["current_user"]["permitted"] == ["iam.User#list", "iam.User#read__email"]


def test_one_account_named_in_the_directory_is_listed_with_its_fields(composed_tables: None) -> None:
    viewer = _person("account-reader")
    named = _person("named-account")
    _person("unnamed-account")
    write_relationships([
        RelationshipTuple(resource=to_object_ref(named), relation="directory_reader", subject=to_subject_ref(viewer)),
    ])

    assert [(row["username"], row["email"]) for row in _people(viewer)] == [
        ("named-account", "named-account@example.com"),
    ]
    # One account does not make a people manager at type level.
    assert _run(viewer, _PERMITTED)["current_user"]["permitted"] == []


def test_the_directory_is_never_everyones(composed_tables: None) -> None:
    viewer = _person("wildcard-viewer")
    _person("wildcard-other")
    everyone = SubjectRef.of("auth/user", "*")

    for resource, relation in ((_DIRECTORY, "reader"), (to_object_ref(viewer), "directory_reader")):
        with pytest.raises(ValueError, match="not allowed"):
            write_relationships([RelationshipTuple(resource=resource, relation=relation, subject=everyone)])

    # A wildcard reader stored before this schema admits no one.
    active_relationship_model().objects.create(
        resource_type="iam/directory", resource_id="main", relation="reader",
        subject_type="auth/user", subject_id="*",
    )
    mark_relationships_changed()
    assert _people(viewer) == []
    assert set(_offered(viewer)) == {"wildcard-viewer"}
