"""Mutation target preflight pins subsequent writes and keeps native REBAC scope."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connections, router, transaction
from rebac import SubjectRef

import angee.graphql.data.hasura as hasura_module
from angee.base.db import get_write_alias
from angee.base.scoping import write_scoped_queryset
from angee.graphql.actions import action_target, authorized_action_target, resolve_action_target
from angee.graphql.data.hasura import AngeeHasuraWriteBackend
from angee.graphql.writes import instance_for_write
from tests.linesdemo.models import SaleDoc
from tests.test_transitions import TransitionRouter


@pytest.fixture
def action_writer(
    transactional_db: None, database_alias: Callable[[str], AbstractContextManager[str]]
) -> Iterator[str]:
    """Expose the action tables through the shared configured connection factory."""

    del transactional_db
    with database_alias("graphql_action_writer") as alias:
        yield alias


@pytest.mark.parametrize("entry", ["instance", "elevated", "context"])
@pytest.mark.parametrize("explicit", [False, True])
def test_mutation_preflight_and_following_save_use_writer(
    action_writer: str, monkeypatch: pytest.MonkeyPatch, entry: str, explicit: bool
) -> None:
    """A target never acquires replica affinity before the real mutation starts."""

    with monkeypatch.context() as patch:
        group = Group.objects.create(name="before")
        routing = TransitionRouter("unselected_writer" if explicit else action_writer)
        patch.setattr(router, "routers", [routing])
        kwargs = {"using": action_writer} if explicit else {}
        committed: list[str] = []
        with transaction.atomic(using=action_writer):
            if entry == "instance":
                target = instance_for_write(Group, str(group.pk), **kwargs)
            elif entry == "elevated":
                target = resolve_action_target(Group, str(group.pk), reason="tests.write_alias", **kwargs)
            else:
                with action_target(Group, str(group.pk), reason="tests.write_alias", **kwargs) as selected:
                    target = selected
            assert target is not None
            assert target._state.db == action_writer
            alias = get_write_alias(Group, instance=target)
            target.name = "after"
            target.save(using=alias)
            transaction.on_commit(lambda: committed.append(alias), using=alias)
            assert committed == []
        assert committed == [action_writer]
        assert Group.objects.using(action_writer).get(pk=group.pk).name == "after"
        assert routing.writes == ([] if explicit else [None])


def test_elevated_target_preserves_bound_queryset_and_related_alias(
    action_writer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller narrowing and a joined related target survive writer binding."""

    with monkeypatch.context() as patch:
        content_type = ContentType.objects.get_for_model(Group)
        permission = Permission.objects.create(content_type=content_type, codename="writer", name="Writer")
        routing = TransitionRouter("unselected_writer")
        patch.setattr(router, "routers", [routing])
        target = resolve_action_target(
            Permission,
            str(permission.pk),
            reason="tests.write_alias.bound",
            queryset=Permission.objects.using(action_writer).filter(codename="writer"),
            select_related=("content_type",),
        )
        related = instance_for_write(ContentType, str(target.content_type_id), using=target._state.db)
        assert target._state.db == target.content_type._state.db == related._state.db == action_writer
        assert routing.writes == []
        with pytest.raises(ValueError, match="was not found"):
            resolve_action_target(
                Permission,
                str(permission.pk),
                reason="tests.write_alias.narrowed",
                queryset=Permission.objects.using(action_writer).none(),
            )


@pytest.mark.parametrize("scope", ["actor", "system"])
def test_write_queryset_keeps_native_rebac_scope_when_bound(
    monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    """Native cloning keeps the actor/action or elevation and disables only field redaction."""

    actor = SubjectRef.of("auth/user", "scope-owner")
    source = SaleDoc.objects.with_actor(actor).with_action("write").filter(title="visible")
    if scope == "system":
        source = source.system_context(reason="tests.write_alias.scope")
    monkeypatch.setattr(SaleDoc._default_manager, "get_queryset", lambda: source)
    routing = TransitionRouter("writer")
    monkeypatch.setattr(router, "routers", [routing])
    selected = write_scoped_queryset(SaleDoc)
    assert selected._db == "writer"
    assert selected.actor() == source.actor()
    assert selected.is_sudo() == source.is_sudo()
    assert selected._rebac_sudo_reason == source._rebac_sudo_reason
    assert selected._rebac_action == source._rebac_action
    assert selected._rebac_field_deny == "allow"
    assert selected.query.where == source.query.where
    assert source._db is None
    assert routing.writes == [None]


def test_authorized_target_passes_selected_alias_to_permission_check(
    action_writer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The authorization preflight observes the actual materialized writer row."""

    with monkeypatch.context() as patch:
        group = Group.objects.create(name="authorized")
        actor = SimpleNamespace(is_authenticated=True)
        info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=actor)))
        checked: list[tuple[str, str]] = []

        def has_access(instance: Group, permission: str) -> bool:
            """Observe the target's database at the row-permission boundary."""

            checked.append((instance._state.db, permission))
            return True

        patch.setattr(Group, "has_access", has_access, raising=False)
        routing = TransitionRouter("unselected_writer")
        patch.setattr(router, "routers", [routing])
        target = authorized_action_target(info, Group, str(group.pk), "write", using=action_writer)
        target.name = "authorized write"
        target.save(using=get_write_alias(Group, instance=target))
        assert checked == [(action_writer, "write")]
        assert Group.objects.using(action_writer).get(pk=group.pk).name == "authorized write"
        assert routing.writes == []


def test_hasura_related_preflight_reuses_parent_writer(
    action_writer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Native relation decoding and the following write use the parent's selection."""

    with monkeypatch.context() as patch:
        content_type = ContentType.objects.get_for_model(Group)
        permission = Permission.objects.create(content_type=content_type, codename="related", name="Before")
        routing = TransitionRouter(action_writer)
        patch.setattr(router, "routers", [routing])

        def update(info: Any, instance: Permission, data: dict[str, Any], **kwargs: Any) -> Permission:
            """Write the selected row without depending on native validation routing."""

            # Isolate the native full_clean contract (architect finding 2); exercise
            # the target lookup, FK decode, transaction and subsequent save for real.
            del info, kwargs
            assert instance._state.db == action_writer
            assert connections[action_writer].in_atomic_block
            assert data == {"content_type_id": content_type.pk, "name": "After"}
            instance.name = data["name"]
            instance.save(using=get_write_alias(type(instance), instance=instance))
            return instance

        patch.setattr(hasura_module.mutation_resolvers, "update", update)
        backend = AngeeHasuraWriteBackend(Permission, public_id_fields=("content_type",))
        result = backend.update(None, str(permission.pk), {"content_type": str(content_type.pk), "name": "After"})
        assert result._state.db == action_writer
        assert Permission.objects.using(action_writer).get(pk=permission.pk).name == "After"
        assert routing.writes == [None]
