"""IAM principal policy at the Django-auth and human-authority boundaries."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rebac import SubjectRef, system_context, to_subject_ref

from angee.iam.auth import ModelBackend, can_authenticate_user


@pytest.mark.parametrize("kind,is_active,allowed", [
    ("person", True, True),
    ("person", False, False),
    ("service", True, False),
    ("service", False, False),
])
def test_login_requires_an_active_person(kind: str, is_active: bool, allowed: bool) -> None:
    user = get_user_model()(kind=kind, is_active=is_active)
    assert user.is_person is (kind == "person")
    assert can_authenticate_user(user) is allowed


@override_settings(AUTHENTICATION_BACKENDS=["angee.iam.auth.ModelBackend"])
def test_login_backend_does_not_grant_django_codenames() -> None:
    """Even a superuser has no codename grants without an authorization backend."""

    user = get_user_model()(kind="person", is_active=True, is_superuser=True)
    backend = ModelBackend()
    assert backend.get_user_permissions(user) == set()
    assert backend.get_group_permissions(user) == set()
    assert backend.get_all_permissions(user) == set()
    assert not user.has_perm("iam.change_user")
    assert not user.has_module_perms("iam")


@pytest.mark.django_db
def test_human_authority_resolves_current_user_state() -> None:
    users = get_user_model().objects
    with system_context(reason="test.iam.principal_policy"):
        person = users.create_user(username="human-authority", kind="person")
        service = users.create_user(username="service-authority", kind="service")
    subject = to_subject_ref(person)
    assert users.active_person_for_subject(subject).pk == person.pk
    assert users.active_person_for_subject(to_subject_ref(service)) is None
    assert users.active_person_for_subject(SubjectRef.of("auth/user", "*")) is None
    assert users.active_person_for_subject(SubjectRef.of("auth/group", "1", "member")) is None
    assert users.active_person_for_subject(SubjectRef(subject.object, "member")) is None
    with system_context(reason="test.iam.principal_policy.service"):
        users.filter(pk=person.pk).update(kind="service")
    assert users.active_person_for_subject(subject) is None
    with system_context(reason="test.iam.principal_policy.deactivate"):
        users.filter(pk=person.pk).update(kind="person", is_active=False)
    assert users.active_person_for_subject(subject) is None
