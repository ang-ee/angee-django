"""Focused boundary contracts for generic record sharing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from rebac import (
    RelationshipTuple,
    SubjectRef,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
)
from rebac.models import active_relationship_model
from rebac.relationships import write_relationships

from angee.graphql.sharing import RecordAccessMutation, _grant_subject
from angee.iam.models import Group
from tests.conftest import Folder, _clear_model_tables, _create_missing_tables
from tests.projects_models import PROJECT_TEST_MODELS, Project
from tests.test_project_access import project_access_schema as project_access_schema


@pytest.mark.django_db
def test_grant_subject_resolves_user_and_group_subject_set() -> None:
    """Concrete users and group-member sets resolve through native REBAC models."""

    with system_context(reason="test.graphql.sharing.subjects"):
        user = get_user_model().objects.create_user(username="sharing-subject")
        group = Group.objects.create(name="Sharing group")

    user_subject = to_subject_ref(user)
    group_subject = SubjectRef.of("auth/group", str(group.pk), "member")
    assert _grant_subject(str(user_subject)) == user_subject
    assert _grant_subject(str(group_subject)) == group_subject


@pytest.mark.django_db
def test_grant_subject_rejects_missing_malformed_and_wildcard_subjects() -> None:
    """New grants require one parseable, concrete subject row."""

    with pytest.raises(ValueError, match="not found"):
        _grant_subject("auth/user:missing")
    with pytest.raises(ValueError):
        _grant_subject("not-a-subject")
    with pytest.raises(ValueError, match="concrete subject"):
        _grant_subject("auth/user:*")


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("project_access_schema")
@override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
def test_record_access_mutations_are_atomic_and_delegate_subject_policy() -> None:
    """The mutation preflights all targets and delegates tuple validity to REBAC."""

    models = (Folder, *PROJECT_TEST_MODELS)
    created = _create_missing_tables(models)
    user_model = get_user_model()
    try:
        owner = user_model.objects.create_user(username="sharing-owner")
        other = user_model.objects.create_user(username="sharing-other")
        service = user_model.objects.create_user(username="sharing-service", kind="service")
        group = Group.objects.create(name="Sharing reviewers")
        with actor_context(owner):
            first = Project.objects.create(title="First")
        with actor_context(other):
            denied = Project.objects.create(title="Denied")
        info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=owner)))
        fields = {
            field.python_name: field
            for field in RecordAccessMutation.__strawberry_definition__.fields
        }
        grant = fields["grant_record_access"].base_resolver.wrapped_func
        revoke = fields["revoke_record_access"].base_resolver.wrapped_func

        with actor_context(owner):
            result = grant(
                object(),
                info,
                "projects/project",
                [first.sqid, denied.sqid],
                "reader",
                str(to_subject_ref(service)),
            )
        assert result.ok is False
        assert not _has_access_tuple(first, "reader", to_subject_ref(service))

        with actor_context(owner):
            assert grant(
                object(),
                info,
                "projects/project",
                [first.sqid],
                "reader",
                str(to_subject_ref(service)),
            ).ok
            assert grant(
                object(),
                info,
                "projects/project",
                [first.sqid],
                "reader",
                str(to_subject_ref(group)),
            ).ok
        assert _has_access_tuple(first, "reader", to_subject_ref(service))
        assert _has_access_tuple(first, "reader", to_subject_ref(group))

        with system_context(reason="test.graphql.sharing.invalid_species"):
            folder = Folder.objects.create(name="Invalid subject", owner=owner)
        with actor_context(owner), pytest.raises(ValueError, match="is not allowed"):
            grant(
                object(),
                info,
                "projects/project",
                [first.sqid],
                "reader",
                str(SubjectRef(to_object_ref(folder))),
            )
        assert not _has_access_tuple(first, "reader", SubjectRef(to_object_ref(folder)))

        stale = SubjectRef.of("auth/user", "missing-service")
        with actor_context(owner):
            write_relationships([RelationshipTuple(to_object_ref(first), "reader", stale)])
            assert revoke(
                object(), info, "projects/project", [first.sqid], "reader", str(stale)
            ).ok
        assert not _has_access_tuple(first, "reader", stale)
    finally:
        _clear_model_tables(models)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


def _has_access_tuple(target: object, relation: str, subject: SubjectRef) -> bool:
    resource = to_object_ref(target)
    return active_relationship_model().objects.filter(
        resource_type=resource.resource_type,
        resource_id=resource.resource_id,
        relation=relation,
        subject_type=subject.subject_type,
        subject_id=subject.subject_id,
        optional_subject_relation=subject.optional_relation,
    ).exists()
