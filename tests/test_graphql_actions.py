"""Tests for shared GraphQL action helpers."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

import angee.graphql.actions as actions_module
from angee.graphql.actions import action_target, resolve_action_target


@pytest.mark.django_db
def test_resolve_action_target_elevates_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Action target lookup runs inside the shared elevated context."""

    group = Group.objects.create(name="operators")
    reasons: list[str | None] = []

    class Context:
        """Small context manager that records entry."""

        def __enter__(self) -> None:
            return None

        def __exit__(self, *exc: object) -> None:
            return None

    def system_context(*, reason: str | None = None) -> Context:
        """Return a recording system context."""

        reasons.append(reason)
        return Context()

    monkeypatch.setattr(actions_module, "system_context", system_context)

    target = resolve_action_target(
        Group,
        str(group.pk),
        reason="tests.action",
    )

    assert target == group
    assert reasons == ["tests.action"]


@pytest.mark.django_db
def test_resolve_action_target_raises_clear_not_found() -> None:
    """Missing action targets fail with the model name and public id."""

    with pytest.raises(ValueError, match="Group 'missing' was not found."):
        resolve_action_target(
            Group,
            "missing",
            reason="tests.action.missing",
        )


@pytest.mark.django_db
def test_resolve_action_target_applies_select_related() -> None:
    """Action callers can join related rows before elevated resolution."""

    content_type = ContentType.objects.get_for_model(Group)
    permission = Permission.objects.create(
        content_type=content_type,
        codename="can_operate",
        name="Can operate",
    )

    target = resolve_action_target(
        Permission,
        str(permission.pk),
        reason="tests.action.select_related",
        select_related=("content_type",),
    )

    assert target == permission
    assert target.content_type == content_type
    assert "content_type" in target._state.fields_cache


@pytest.mark.django_db
def test_action_target_wraps_lookup_and_body_in_system_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Action target contexts reuse the same audited reason for lookup and body."""

    group = Group.objects.create(name="operators")
    reasons: list[str | None] = []
    active_depth = 0

    class Context:
        """Small context manager that records active elevation depth."""

        def __init__(self, reason: str | None) -> None:
            self.reason = reason

        def __enter__(self) -> None:
            nonlocal active_depth
            reasons.append(self.reason)
            active_depth += 1
            return None

        def __exit__(self, *exc: object) -> None:
            nonlocal active_depth
            active_depth -= 1
            return None

    def system_context(*, reason: str | None = None) -> Context:
        """Return a recording system context."""

        return Context(reason)

    monkeypatch.setattr(actions_module, "system_context", system_context)

    with action_target(Group, str(group.pk), reason="tests.action.context") as target:
        assert target == group
        assert active_depth == 1

    assert active_depth == 0
    assert reasons == ["tests.action.context", "tests.action.context"]
