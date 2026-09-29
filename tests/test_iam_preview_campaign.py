"""Preview authority and role exclusions are scoped in SQL before the cap."""

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import (
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.roles import grant as grant_role

from tests.conftest import create_platform_admin
from tests.iam_campaign import iam_admin as iam_admin
from tests.iam_models import Group

User = get_user_model()


def test_viewable_people_excludes_self_inactive_service_staff_superuser_and_effective_admin(iam_admin):
    target = User.objects.create_user("ordinary")
    for name, attrs in [
        ("inactive", {"is_active": False}),
        ("service", {"kind": "service"}),
        ("staff", {"is_staff": True}),
        ("superuser", {"is_superuser": True}),
    ]:
        User.objects.create_user(name, **attrs)
    direct_admin = create_platform_admin("direct-admin", password=None)
    inherited_admin = User.objects.create_user("inherited-admin")
    with system_context(reason="test.preview.group-admin"):
        group = Group.objects.create(name="Administrators")
        group.add_member(str(to_subject_ref(inherited_admin)))
        grant_role(actor=group, role="angee/role:admin")
    assert not direct_admin.is_staff and not direct_admin.is_superuser
    assert not inherited_admin.is_staff and not inherited_admin.is_superuser
    assert User.objects.admit_view_as(iam_admin, str(target.sqid)) == target
    assert User.objects.admit_view_as(iam_admin, str(direct_admin.sqid)) is None
    assert User.objects.admit_view_as(iam_admin, str(inherited_admin.sqid)) is None
    assert User.objects.viewable_people(iam_admin) == [target]


def test_directory_read_does_not_grant_view_as(iam_admin):
    actor = User.objects.create_user("reader")
    target = User.objects.create_user("target")
    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(target), relation="directory_reader", subject=to_subject_ref(actor)
            ),
        ]
    )
    assert User.objects.visible_people(actor) == [target]
    assert User.objects.viewable_people(actor) == []
    with actor_context(actor):
        assert not target.with_actor(actor).has_access("view_as")


def test_viewable_people_search_and_ordering_precede_limit(iam_admin):
    for username, first, last, email in [
        ("zoe", "Zoe", "Target", "zoe@example.com"),
        ("amy", "Amy", "Target", "amy@example.com"),
        ("other", "Other", "Person", "unique@example.com"),
    ]:
        User.objects.create_user(username, email, first_name=first, last_name=last)
    assert [u.username for u in User.objects.viewable_people(iam_admin, search=" target ", limit=1)] == ["amy"]
    assert [u.username for u in User.objects.viewable_people(iam_admin, search="unique@")] == ["other"]


@pytest.mark.parametrize("limit,cap", [(0, 1), (2, 2), (1000, 25)])
def test_picker_applies_sql_admission_before_the_capped_limit(iam_admin, monkeypatch, limit, cap):
    User._base_manager.bulk_create([User(username=f"candidate-{i:03}", password="!") for i in range(80)])
    for name, attrs in [
        ("000-staff", {"is_staff": True}),
        ("000-superuser", {"is_superuser": True}),
        ("000-inactive", {"is_active": False}),
        ("000-service", {"kind": "service"}),
    ]:
        User.objects.create_user(name, **attrs)
    def readmission(*args):
        pytest.fail("The picker must not re-admit individual candidates")

    monkeypatch.setattr(User._default_manager, "admit_view_as", readmission)
    with CaptureQueriesContext(connection) as captured:
        assert [person.username for person in User.objects.viewable_people(iam_admin, limit=limit)] == [
            f"candidate-{i:03}" for i in range(cap)
        ]
    selections = [
        q["sql"] for q in captured if q["sql"].startswith("SELECT") and f'FROM "{User._meta.db_table}"' in q["sql"]
    ]
    assert len(selections) == 1
    sql = selections[0]
    assert f"LIMIT {cap}" in sql
    where = sql.split(" WHERE ", 1)[1]
    assert all(f'"{field}"' in where for field in ("kind", "is_active", "is_staff", "is_superuser"))
    assert "person" in where and "NOT" in where


def test_viewable_people_returns_at_most_twenty_five_people(iam_admin):
    User._base_manager.bulk_create([User(username=f"person-{i:03}", password="!") for i in range(30)])
    assert [u.username for u in User.objects.viewable_people(iam_admin, limit=1000)] == [
        f"person-{i:03}" for i in range(25)
    ]
