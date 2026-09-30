"""The one-record reader read and follower authority boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from tests.chatterdemo.models import ChatterDoc
from tests.messaging_models import Person, ThreadFollower
from tests.projects_models import Project, Task

pytest_plugins = ("tests.test_messaging_access",)


def test_task_visibility_projects_its_audience_label() -> None:
    """The task owner names inherited and narrowed audiences for the widget."""

    task = Task(visibility="inherited")
    assert task.visibility_audience_label() == "Project readers and task participants"
    task.visibility = "restricted"
    assert task.visibility_audience_label() == "Task participants"
    task.clarification_round_id = 1
    assert task.visibility_audience_label() == "Question participants"


def test_record_readers_use_effective_index_and_following_subjects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A derived reader remains visible; direct shares come from record_access."""

    from angee.iam import schema

    viewer = SubjectRef.of("auth/user", "viewer")
    direct = SubjectRef.of("auth/user", "direct")
    derived = SubjectRef.of("auth/user", "derived")
    service = SubjectRef.of("auth/user", "service")
    users = {
        direct: SimpleNamespace(name="Direct", kind="person"),
        derived: SimpleNamespace(name="Derived", kind="person"),
        service: SimpleNamespace(name="Robot", kind="service"),
    }

    class TargetModel:
        @classmethod
        def get_rebac_grantable(cls) -> dict[str, str]:
            return {"reader": "share"}

    target = SimpleNamespace()
    monkeypatch.setattr(schema, "AngeeModel", TargetModel)
    monkeypatch.setattr(schema, "model_for_resource_type", lambda resource: TargetModel)
    calls: list[tuple[object, ...]] = []
    def authorized(*args: object) -> tuple[object, list[str]]:
        calls.append(args)
        return target, ["reader"]
    monkeypatch.setattr(schema, "authorized_record_access", authorized)
    monkeypatch.setattr(schema.apps, "get_model", lambda *_args: SimpleNamespace(objects=SimpleNamespace(
        subjects_for_record=lambda _target: (direct,),
    )))
    monkeypatch.setattr(schema, "rebac_backend", lambda: SimpleNamespace(
        lookup_subjects=lambda **_kwargs: (direct, derived, service),
    ))
    monkeypatch.setattr(schema, "to_object_ref", lambda value: value)
    monkeypatch.setattr(schema, "resolve_subjects", lambda _refs: users)
    monkeypatch.setattr(schema, "session_user", lambda _info: viewer)
    monkeypatch.setattr(schema, "to_subject_ref", lambda value: value)
    monkeypatch.setattr(schema, "public_subject_ref", lambda ref: ref)
    monkeypatch.setattr(schema, "user_label", lambda user: user.name)
    resolver = next(field for field in cast(Any, schema.IAMConsoleQuery).__strawberry_definition__.fields
                    if field.python_name == "record_readers").base_resolver.wrapped_func
    rows = resolver(object(), object(), "projects/project", "project-1")
    assert [(row.label, row.following) for row in rows] == [
        ("Direct", True), ("Derived", False),
    ]
    assert calls and calls[0][2:] == ("project-1",)


def test_record_readers_accepts_a_role_only_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A role-only record resolves through read, with no direct-share declaration."""

    from angee.iam import schema

    class RoleOnlyModel:
        @classmethod
        def get_rebac_grantable(cls) -> dict[str, str]:
            return {}

    target = SimpleNamespace()
    monkeypatch.setattr(schema, "AngeeModel", RoleOnlyModel)
    monkeypatch.setattr(schema, "model_for_resource_type", lambda _type: RoleOnlyModel)
    monkeypatch.setattr(schema, "authorized_permission_target", lambda *_args: target)
    monkeypatch.setattr(schema, "rebac_backend", lambda: SimpleNamespace(lookup_subjects=lambda **_kwargs: ()))
    monkeypatch.setattr(schema, "to_object_ref", lambda row: row)
    monkeypatch.setattr(schema, "session_user", lambda _info: SimpleNamespace(pk="viewer"))
    monkeypatch.setattr(schema, "to_subject_ref", lambda _user: SubjectRef.of("auth/user", "viewer"))
    monkeypatch.setattr(schema.apps, "is_installed", lambda _app: False)
    resolver = next(field for field in cast(Any, schema.IAMConsoleQuery).__strawberry_definition__.fields
                    if field.python_name == "record_readers").base_resolver.wrapped_func
    assert resolver(object(), object(), "role/only", "record-1") == []


@pytest.mark.django_db(transaction=True)
def test_accountless_party_can_follow_without_read(messaging_access_schema: str) -> None:
    """An external delivery route is outside the person-account read rule."""

    from tests.messaging_models import Party

    del messaging_access_schema
    with system_context(reason="tests.people.external_follow"):
        record = ChatterDoc.objects.create(title="External delivery")
        party = Party.objects.create(display_name="External")
        record.message_subscribe(party=party)
        assert record.message_is_follower(party=party)


@pytest.mark.django_db(transaction=True)
def test_follow_requires_read_and_revoking_read_ends_follow(messaging_access_schema: str) -> None:
    """Following is notification state; removing it leaves access untouched."""

    from django.apps import apps
    from rebac import backend

    del messaging_access_schema
    users = apps.get_model("iam", "User")
    with system_context(reason="tests.people.follow_read"):
        reader = users.objects.create_user(username="follow-reader", kind="person")
        outsider = users.objects.create_user(username="follow-outsider", kind="person")
        record = ChatterDoc.objects.create(title="Readable conversation")
        grant = RelationshipTuple(to_object_ref(record), "reader", to_subject_ref(reader))
        write_relationships([grant])
        with pytest.raises(PermissionDenied, match="read access"):
            record.message_subscribe(user=outsider)
        assert not Person._base_manager.filter(user=outsider).exists()
        record.message_subscribe(user=reader)
        assert record.message_is_follower(user=reader)
        assert record.message_unsubscribe(user=reader)
        assert backend().check_access(
            subject=to_subject_ref(reader), action="read", resource=to_object_ref(record),
        ).allowed
        record.message_subscribe(user=reader)
        delete_relationship(grant)
        ThreadFollower.objects.end_unreadable_for_record(record)
        assert list(ThreadFollower.objects.for_record(record)) == []
        assert not record.message_is_follower(user=reader)


@pytest.mark.django_db(transaction=True)
def test_direct_revoke_ends_unreadable_follow(messaging_access_schema: str) -> None:
    """The record's revoke verb ends a follow in its own transaction."""

    from django.apps import apps

    del messaging_access_schema
    users = apps.get_model("iam", "User")
    with system_context(reason="tests.people.direct_revoke"):
        reader = users.objects.create_user(username="share-follow-reader", kind="person")
        record = Project.objects.create(title="Shared project")
        record.grant_record_access("reader", reader)
        record.message_subscribe(user=reader)
        assert record.message_is_follower(user=reader)
        record.revoke_record_access("reader", reader)
        assert not record.message_is_follower(user=reader)


@pytest.mark.django_db(transaction=True)
def test_post_skips_nonreader_recipient_autofollow(messaging_access_schema: str) -> None:
    """Direct delivery does not turn a nonreading person into a follower."""

    from django.apps import apps

    del messaging_access_schema
    users = apps.get_model("iam", "User")
    with system_context(reason="tests.people.autofollow_skip"):
        recipient = users.objects.create_user(username="unreadable-recipient", kind="person")
        record = ChatterDoc.objects.create(title="Direct delivery")
        record.message_post("Please review", recipient_user_ids=(recipient.pk,), autofollow_recipients=True)
        assert not record.message_is_follower(user=recipient)
        assert not Person._base_manager.filter(user=recipient).exists()
