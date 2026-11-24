"""Hasura insertion composes prepared factories and candidate-aware gates."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import Mock

import pytest
import strawberry
import strawberry_django
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model

from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.node import AngeeNode
from angee.graphql.testing.documents import execute_schema, result_data
from tests.conftest import create_platform_admin, create_user
from tests.scopedemo.models import FactoryCompanion, FactoryDoc, ProjectionDoc, Scope, SharedDoc, ValidatedDoc


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


@strawberry_django.type(ValidatedDoc)
class ValidatedDocType(AngeeNode):
    """Public projection for a row whose validation waits for its create decision."""

    title: strawberry.auto


_VALIDATED_RESOURCE = hasura_model_resource(
    ValidatedDocType,
    model=ValidatedDoc,
    name="validated_docs",
    filterable=["id", "title"],
    sortable=["title"],
    aggregatable=["id"],
    insertable=["title", "code"],
    update=False,
    delete=False,
)
_VALIDATED_SCHEMA = strawberry.Schema(query=_VALIDATED_RESOURCE.query, mutation=_VALIDATED_RESOURCE.mutation)
_VALIDATED_INSERT = """
mutation($title: String!, $code: String!) {
  insert_validated_docs_one(object: {title: $title, code: $code}) { id }
}
"""
_REFUSED_CALLERS = ("anonymous", "member")


@pytest.mark.django_db
@pytest.mark.parametrize("allowed", (True, False), ids=("allowed", "denied"))
def test_hasura_prepared_factory_creates_one_companion_or_neither_row(allowed: bool) -> None:
    """A readable relation reaches the insert gate, which authorizes the aggregate."""

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
@pytest.mark.parametrize("hidden", (False, True), ids=("missing", "unreadable"))
def test_hasura_rejected_relation_reports_its_field_without_disclosing_the_target(hidden: bool) -> None:
    """An unavailable foreign key points to its input with the same safe refusal."""

    actor = create_user("missing-scope-author")
    with system_context(reason="test.field_validation.hidden_scope"):
        scope = Scope.objects.create(name="Private scope")

    result = execute_schema(
        _FACTORY_SCHEMA,
        _FACTORY_INSERT,
        {"scope": scope.public_id if hidden else "missing-scope"},
        user=actor,
    )

    assert result.errors
    error = result.errors[0].original_error
    assert isinstance(error, ValidationError)
    assert error.message_dict == {"scope": ["The requested record was not found."]}
    assert error.error_dict["scope"][0].code == "not_found"
    assert FactoryDoc._base_manager.count() == 0
    assert FactoryCompanion.objects.count() == 0


@pytest.mark.django_db
def test_hasura_create_unknown_relation_denies_even_in_exclusion() -> None:
    """Insert-dependent siblings cannot be mistaken for an empty exclusion arm."""

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
@pytest.mark.parametrize("eligible", (True, False), ids=("shared", "private"))
def test_hasura_create_uses_filtered_constant(eligible: bool) -> None:
    """The create gate and persisted read agree without a wildcard tuple."""

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
        assert not relationships.exists()
    else:
        assert result.errors
        assert isinstance(result.errors[0].original_error, PermissionDenied)
        assert SharedDoc._base_manager.count() == 0
        assert not relationships.exists()


def _refused_caller(caller: str) -> Any | None:
    """Return the signed-in member without create, or ``None`` for an anonymous request."""

    return None if caller == "anonymous" else create_user(f"validated-{caller}")


def _insert_validated(user: Any | None, *, title: str, code: str) -> Any:
    """Insert one validated document through the generic Hasura root as ``user``."""

    return execute_schema(_VALIDATED_SCHEMA, _VALIDATED_INSERT, {"title": title, "code": code}, user=user)


def _save_refusal(user: Any | None) -> str:
    """The refusal the row's own save gives ``user``: the answer the insert has always ended with."""

    with actor_context(user or AnonymousUser()), pytest.raises(PermissionDenied) as refused:
        ValidatedDoc.objects.create(title="aaa", code="through-save")
    return str(refused.value)


def _refusal(result: Any) -> str:
    """Return the insert's one error, asserting it is a REBAC refusal rather than a validation answer."""

    assert result.errors is not None and len(result.errors) == 1, result.errors
    error = result.errors[0].original_error
    assert isinstance(error, PermissionDenied), repr(error)
    return str(error)


@pytest.mark.django_db
@pytest.mark.parametrize("caller", _REFUSED_CALLERS)
def test_hasura_create_refuses_before_any_validator_runs(caller: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller who may not create gets the save's refusal, and the row's validators never run."""

    user = _refused_caller(caller)
    spy = Mock(side_effect=ValidationError("Not a run of a.", code="invalid"))
    monkeypatch.setattr(ValidatedDoc._meta.get_field("title"), "validators", [spy])

    result = _insert_validated(user, title="refused", code="refused")

    assert _refusal(result) == _save_refusal(user)
    spy.assert_not_called()
    assert ValidatedDoc._base_manager.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("caller", _REFUSED_CALLERS)
def test_hasura_create_refuses_a_pathological_value_without_matching_it(caller: str) -> None:
    """A value that backtracks catastrophically in the row's validator is refused at once."""

    user = _refused_caller(caller)

    started = time.perf_counter()
    result = _insert_validated(user, title="a" * 28 + "!", code="slow")
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"the refused insert ran for {elapsed:.1f}s"
    assert _refusal(result) == _save_refusal(user)
    assert ValidatedDoc._base_manager.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("caller", _REFUSED_CALLERS)
def test_hasura_create_refuses_before_uniqueness_and_constraints_answer(caller: str) -> None:
    """A stored code and a broken check constraint give a refused caller no message, only the refusal."""

    user = _refused_caller(caller)
    with system_context(reason="test.validated_doc.stored_code"):
        ValidatedDoc.objects.create(title="a", code="aaa")

    result = _insert_validated(user, title="aaa", code="aaa")

    assert _refusal(result) == _save_refusal(user)
    assert list(ValidatedDoc._base_manager.values_list("code", flat=True)) == ["aaa"]


@pytest.mark.django_db
def test_hasura_create_validates_an_admitted_row_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller who may create still gets each field's own validation messages, then creates."""

    admin = create_platform_admin("validated-admin")
    with system_context(reason="test.validated_doc.stored_code"):
        ValidatedDoc.objects.create(title="a", code="aaa")
    title = ValidatedDoc._meta.get_field("title")
    spy = Mock(wraps=title.validators[0])
    monkeypatch.setattr(title, "validators", [spy])

    result = _insert_validated(admin, title="b", code="aaa")

    assert result.errors is not None and len(result.errors) == 1, result.errors
    error = result.errors[0].original_error
    assert isinstance(error, ValidationError)
    assert sorted(error.message_dict) == ["code", "title"]
    spy.assert_called_once_with("b")
    created = result_data(_insert_validated(admin, title="aa", code="fresh"))["insert_validated_docs_one"]
    assert ValidatedDoc._base_manager.get(sqid=created["id"]).title == "aa"
