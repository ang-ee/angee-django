"""Historical active-superuser admin mirror cleanup coverage."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.migrations.state import ProjectState
from rebac import system_context
from rebac.models import RebacResource, Relationship, RelationshipRegistry

from angee.iam.runtime_migrations.live_admin_backing import (
    applies,
    remove_evidenced_admin_mirrors,
)


def test_live_admin_cleanup_accepts_only_the_complete_iam_user_state() -> None:
    """The pure data transition skips absent IAM and rejects partial user state."""

    state = ProjectState.from_apps(apps)
    assert applies(state)
    without_virtual_id = state.clone()
    without_virtual_id.models[("iam", "user")].fields.pop("sqid", None)
    assert applies(without_virtual_id)
    absent = state.clone()
    absent.remove_model("iam", "user")
    assert not applies(absent)
    partial = state.clone()
    partial.models[("iam", "user")].fields.pop("is_active")
    with pytest.raises(ImproperlyConfigured, match="requires User"):
        applies(partial)


@pytest.mark.django_db(transaction=True)
def test_live_admin_cleanup_removes_active_and_inactive_superuser_mirrors() -> None:
    """Historical evidence follows is_superuser regardless of current activity."""

    with system_context(reason="test historical admin"):
        user = apps.get_model("iam", "User").objects.create_superuser(
            username="historical-admin",
            email="admin@example.test",
            password="unused",
        )
        _create_admin_rows(str(user.sqid))
        inactive = apps.get_model("iam", "User").objects.create_superuser(
            username="inactive-historical-admin",
            email="inactive@example.test",
            password="unused",
        )
        inactive.is_active = False
        inactive.save(update_fields=("is_active",))
        _create_admin_rows(str(inactive.sqid))

    remove_evidenced_admin_mirrors(apps, SimpleNamespace(connection=connection))

    assert not _denormalized_admin_rows().exists()
    assert not _registry_admin_rows().exists()


@pytest.mark.django_db(transaction=True)
def test_live_admin_cleanup_rejects_unexpected_rows_before_deleting_evidence() -> None:
    """A caveated or otherwise manual admin row blocks the whole cleanup."""

    with system_context(reason="test blocked historical admin"):
        user = apps.get_model("iam", "User").objects.create_superuser(
            username="blocked-historical-admin",
            email="blocked@example.test",
            password="unused",
        )
        _create_admin_rows(str(user.sqid))
        Relationship.objects.create(
            resource_type="angee/role",
            resource_id="admin",
            relation="member",
            subject_type="auth/user",
            subject_id=str(user.sqid),
            optional_subject_relation="",
            caveat_name="manual_condition",
        )
        regular = apps.get_model("iam", "User").objects.create_user(
            username="manual-admin",
            password="unused",
        )
        Relationship.objects.create(
            resource_type="angee/role",
            resource_id="admin",
            relation="member",
            subject_type="auth/user",
            subject_id=str(regular.sqid),
            optional_subject_relation="",
            caveat_name="",
        )

    with pytest.raises(ImproperlyConfigured, match="non-mirror admin membership"):
        remove_evidenced_admin_mirrors(apps, SimpleNamespace(connection=connection))

    assert _denormalized_admin_rows().count() == 3
    assert _registry_admin_rows().count() == 1


def _create_admin_rows(subject_id: str) -> None:
    Relationship.objects.create(
        resource_type="angee/role",
        resource_id="admin",
        relation="member",
        subject_type="auth/user",
        subject_id=subject_id,
        optional_subject_relation="",
        caveat_name="",
    )
    resource, _ = RebacResource.objects.get_or_create(
        resource_type="angee/role",
        resource_id="admin",
    )
    subject, _ = RebacResource.objects.get_or_create(
        resource_type="auth/user",
        resource_id=subject_id,
    )
    RelationshipRegistry.objects.create(
        resource_fk=resource,
        relation="member",
        subject_fk=subject,
        optional_subject_relation="",
        caveat_name="",
    )


def _denormalized_admin_rows():
    return Relationship.objects.filter(
        resource_type="angee/role",
        resource_id="admin",
        relation="member",
    )


def _registry_admin_rows():
    return RelationshipRegistry.objects.filter(
        resource_fk__resource_type="angee/role",
        resource_fk__resource_id="admin",
        relation="member",
    )
