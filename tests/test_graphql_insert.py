"""Hasura insertion composes prepared factories and candidate-aware gates."""

from __future__ import annotations

from typing import Any

import pytest
import strawberry
import strawberry_django
from django.core.management import call_command
from django.db import IntegrityError
from django.test import override_settings
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model

from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.node import AngeeNode
from tests.conftest import create_platform_admin, create_user, execute_schema, result_data
from tests.scopedemo.models import FactoryCompanion, FactoryDoc, ProjectionDoc, Scope, SharedDoc


@strawberry_django.type(FactoryDoc)
class FactoryDocType(AngeeNode):
    """Public document projection for the prepared factory contract."""

    title: strawberry.auto


@strawberry_django.type(ProjectionDoc)
class ProjectionDocType(AngeeNode):
    """Public projection for a relation that is unknown before insertion."""

    title: strawberry.auto


@strawberry_django.type(SharedDoc)
class SharedDocType(AngeeNode):
    """Public projection for create-time shared-reader authorization."""

    title: strawberry.auto


_FACTORY_RESOURCE = hasura_model_resource(
    FactoryDocType,
    model=FactoryDoc,
    name="factory_docs",
    filterable=["id", "title"],
    sortable=["title"],
    aggregatable=["id"],
    insertable=["title", "scope"],
    field_id_decode={"scope": public_pk_decoder(Scope)},
    write_backend=AngeeHasuraWriteBackend(FactoryDoc, public_id_fields=("scope",)),
    update=False,
    delete=False,
)
_FACTORY_SCHEMA = strawberry.Schema(query=_FACTORY_RESOURCE.query, mutation=_FACTORY_RESOURCE.mutation)
_FACTORY_INSERT = """
mutation($scope: ID!) {
  insert_factory_docs_one(object: {title: "Prepared", scope: $scope}) { id }
}
"""

_PROJECTION_RESOURCE = hasura_model_resource(
    ProjectionDocType,
    model=ProjectionDoc,
    name="projection_docs",
    filterable=["id", "title"],
    sortable=["title"],
    aggregatable=["id"],
    insertable=["title", "parent"],
    field_id_decode={"parent": public_pk_decoder(ProjectionDoc)},
    write_backend=AngeeHasuraWriteBackend(ProjectionDoc, public_id_fields=("parent",)),
    update=False,
    delete=False,
)
_PROJECTION_SCHEMA = strawberry.Schema(query=_PROJECTION_RESOURCE.query, mutation=_PROJECTION_RESOURCE.mutation)

_SHARED_RESOURCE = hasura_model_resource(
    SharedDocType,
    model=SharedDoc,
    name="shared_docs",
    filterable=["id", "title"],
    sortable=["title"],
    aggregatable=["id"],
    insertable=["title", "is_shared"],
    update=False,
    delete=False,
)
_SHARED_SCHEMA = strawberry.Schema(query=_SHARED_RESOURCE.query, mutation=_SHARED_RESOURCE.mutation)


@pytest.mark.django_db
@pytest.mark.parametrize("rebac_storage", ("denormalized", "registry"))
@pytest.mark.parametrize("allowed", (True, False), ids=("allowed", "denied"))
def test_hasura_prepared_factory_creates_one_companion_or_neither_row(rebac_storage: str, allowed: bool) -> None:
    """A readable relation reaches the insert gate, which authorizes the aggregate."""

    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=rebac_storage):
        call_command("rebac", "sync", verbosity=0)
        actor = create_platform_admin("factory-admin") if allowed else create_user("factory-reader")
        with system_context(reason="factory test scope"):
            scope = Scope.objects.create(name="Factory scope")
        write_relationships([RelationshipTuple(to_object_ref(scope), "direct_member", to_subject_ref(actor))])

        result = execute_schema(_FACTORY_SCHEMA, _FACTORY_INSERT, {"scope": scope.public_id}, user=actor)

        if allowed:
            public_id = result_data(result)["insert_factory_docs_one"]["id"]
            document = FactoryDoc._base_manager.get(sqid=public_id)
            assert document.scope_id == scope.pk
            assert FactoryCompanion.objects.count() == 1
            assert FactoryCompanion.objects.get().document_id == document.pk
        else:
            assert result.errors
            assert isinstance(result.errors[0].original_error, PermissionDenied)
            assert FactoryDoc._base_manager.count() == 0
            assert FactoryCompanion.objects.count() == 0


@pytest.mark.django_db
def test_hasura_factory_companion_failure_rolls_back_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure after root persistence rolls the whole factory transaction back."""

    call_command("rebac", "sync", verbosity=0)
    actor = create_platform_admin("factory-rollback")
    with system_context(reason="factory rollback scope"):
        scope = Scope.objects.create(name="Rollback scope")

    def fail_companion(self: FactoryCompanion, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        assert FactoryDoc._base_manager.filter(pk=self.document_id).exists()
        raise IntegrityError("Companion insert failed")

    monkeypatch.setattr(FactoryCompanion, "save", fail_companion)
    result = execute_schema(_FACTORY_SCHEMA, _FACTORY_INSERT, {"scope": scope.public_id}, user=actor)

    assert result.errors
    assert isinstance(result.errors[0].original_error, IntegrityError)
    assert FactoryDoc._base_manager.count() == 0
    assert FactoryCompanion.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("rebac_storage", ("denormalized", "registry"))
def test_hasura_create_unknown_relation_denies_even_in_exclusion(rebac_storage: str) -> None:
    """Insert-dependent siblings cannot be mistaken for an empty exclusion arm."""

    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=rebac_storage):
        call_command("rebac", "sync", verbosity=0)
        actor = create_user("projection-actor")
        with system_context(reason="projection parent seed"):
            parent = ProjectionDoc.objects.create(title="Parent")
        result = execute_schema(
            _PROJECTION_SCHEMA,
            """
            mutation($parent: ID!) {
              insert_projection_docs_one(object: {title: "Denied", parent: $parent}) { id }
            }
            """,
            {"parent": parent.public_id},
            user=actor,
        )

        assert result.errors
        assert isinstance(result.errors[0].original_error, PermissionDenied)
        assert ProjectionDoc._base_manager.count() == 1
        assert not ProjectionDoc._base_manager.filter(title="Denied").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("rebac_storage", ("denormalized", "registry"))
@pytest.mark.parametrize("eligible", (True, False), ids=("shared", "private"))
def test_hasura_create_uses_promised_shared_reader(rebac_storage: str, eligible: bool) -> None:
    """The create gate and persisted wildcard agree on the same eligibility rule."""

    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=rebac_storage):
        call_command("rebac", "sync", verbosity=0)
        actor = create_user("shared-create-actor")
        result = execute_schema(
            _SHARED_SCHEMA,
            """
            mutation($shared: Boolean!) {
              insert_shared_docs_one(object: {title: "Shared candidate", is_shared: $shared}) { id }
            }
            """,
            {"shared": eligible},
            user=actor,
        )
        relationships = active_relationship_model().objects.filter(resource_type="scopedemo/shared_doc")
        if eligible:
            public_id = result_data(result)["insert_shared_docs_one"]["id"]
            document = SharedDoc.objects.as_user(actor).get(sqid=public_id)
            assert document.is_shared is True
            assert SharedDoc._base_manager.count() == 1
            assert relationships.count() == 1
            assert relationships.filter(
                resource_id=str(document.pk), relation="shared", subject_type="auth/user", subject_id="*",
            ).exists()
        else:
            assert result.errors
            assert isinstance(result.errors[0].original_error, PermissionDenied)
            assert SharedDoc._base_manager.count() == 0
            assert not relationships.exists()
