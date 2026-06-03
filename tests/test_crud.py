"""Tests for the ``crud`` mutation-surface shortcut."""

from __future__ import annotations

from typing import Protocol, cast

import pytest
import strawberry
import strawberry_django
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from strawberry import auto, relay

from angee.base.deletion import DeletionPreview, DeletionPreviewGroup
from angee.base.graphql import crud
from angee.base.graphql.crud import _delete_resolver


@strawberry_django.type(Group)
class GroupType:
    name: auto


@strawberry.input
class GroupInput:
    name: str


@strawberry.input
class GroupPatch:
    name: str | None = None


class _StrawberryField(Protocol):
    python_name: str


class _StrawberryDefinition(Protocol):
    fields: list[_StrawberryField]


class _StrawberrySurface(Protocol):
    __strawberry_definition__: _StrawberryDefinition


def _field_names(surface: type) -> list[str]:
    definition = cast(_StrawberrySurface, surface).__strawberry_definition__
    return [field.python_name for field in definition.fields]


def test_crud_builds_named_fields_from_the_model() -> None:
    """Each requested operation becomes a model-named mutation field."""

    surface = crud(GroupType, create=GroupInput, update=GroupPatch, delete=True)

    assert _field_names(surface) == [
        "create_group",
        "update_group",
        "delete_group",
    ]


def test_crud_only_includes_requested_operations() -> None:
    """Omitted operations contribute no fields."""

    assert _field_names(crud(GroupType, create=GroupInput)) == ["create_group"]


def test_crud_name_overrides_the_singular() -> None:
    """An explicit name renames every field's subject."""

    assert _field_names(crud(GroupType, update=GroupPatch, name="team")) == ["update_team"]


def test_crud_requires_at_least_one_operation() -> None:
    """A surface with no operations fails fast."""

    with pytest.raises(ImproperlyConfigured, match="at least one"):
        crud(GroupType)


def test_crud_rejects_plain_strawberry_types() -> None:
    """A node that is not strawberry-django has no backing model."""

    @strawberry.type
    class Plain:
        name: str

    with pytest.raises(ImproperlyConfigured, match="strawberry_django type"):
        crud(Plain, create=GroupInput)


def test_crud_fields_merge_into_a_schema() -> None:
    """The surface delegates to the library factories and prints as CRUD."""

    surface = crud(GroupType, create=GroupInput, delete=True)

    @strawberry.type
    class Query:
        @strawberry.field
        def ok(self) -> bool:
            return True

    sdl = strawberry.Schema(query=Query, mutation=surface, types=[GroupType]).as_str()

    assert "createGroup(data: GroupInput!): GroupType!" in sdl
    assert "deleteGroup(id: ID!, confirm: Boolean! = true): DeletePreview!" in sdl
    assert "type DeletePreview" in sdl


@pytest.mark.django_db
def test_delete_preview_output_adapts_deletion_domain() -> None:
    """CRUD delete output serializes the deletion preview domain object."""

    from angee.base.graphql.crud import DeletePreview

    group = Group.objects.create(name="reviewers")
    preview = DeletePreview.from_domain(DeletionPreview.from_instance(group))

    assert preview.total_deleted_count == 1
    assert not preview.has_blockers
    assert any(g.count == 1 for g in preview.deleted)


@pytest.mark.django_db
def test_delete_resolver_preserves_blocked_and_removes_unblocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocked deletes leave rows present; unblocked deletes remove them."""

    blocked = Group.objects.create(name="blocked")
    removable = Group.objects.create(name="removable")
    previews = iter(
        (
            DeletionPreview(
                total_deleted_count=1,
                deleted=(),
                updated=(),
                blocked=(DeletionPreviewGroup(label="groups", count=1),),
            ),
            DeletionPreview(
                total_deleted_count=1,
                deleted=(DeletionPreviewGroup(label="groups", count=1),),
                updated=(),
                blocked=(),
            ),
        )
    )

    def preview_for(
        cls: type[DeletionPreview],
        instance: Group,
    ) -> DeletionPreview:
        del cls, instance
        return next(previews)

    monkeypatch.setattr(
        DeletionPreview,
        "from_instance",
        classmethod(preview_for),
    )
    delete = _delete_resolver(Group)

    blocked_preview = delete(relay.GlobalID(type_name="GroupType", node_id=str(blocked.pk)))
    removable_preview = delete(relay.GlobalID(type_name="GroupType", node_id=str(removable.pk)))

    assert blocked_preview.has_blockers
    assert Group.objects.filter(pk=blocked.pk).exists()
    assert not removable_preview.has_blockers
    assert not Group.objects.filter(pk=removable.pk).exists()


@pytest.mark.django_db
def test_delete_resolver_previews_and_deletes_inside_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preview and delete run inside one database transaction."""

    group = Group.objects.create(name="transactional")
    active = False
    entered = False

    class Atomic:
        """Small transaction context used to observe resolver boundaries."""

        def __enter__(self) -> None:
            nonlocal active, entered
            active = True
            entered = True

        def __exit__(self, *exc: object) -> None:
            nonlocal active
            active = False

    def atomic(*args: object, **kwargs: object) -> Atomic:
        """Return a transaction context that records entry."""

        del args, kwargs
        return Atomic()

    def preview_for(
        cls: type[DeletionPreview],
        instance: Group,
    ) -> DeletionPreview:
        del cls, instance
        assert active
        return DeletionPreview(
            total_deleted_count=1,
            deleted=(DeletionPreviewGroup(label="groups", count=1),),
            updated=(),
            blocked=(),
        )

    monkeypatch.setattr(transaction, "atomic", atomic)
    monkeypatch.setattr(
        DeletionPreview,
        "from_instance",
        classmethod(preview_for),
    )

    _delete_resolver(Group)(relay.GlobalID(type_name="GroupType", node_id=str(group.pk)))

    assert entered
    assert not active
    assert not Group.objects.filter(pk=group.pk).exists()
