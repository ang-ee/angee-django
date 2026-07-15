"""The auto-CRUD create gate honors model-owned input defaults.

``tests.scopedemo.ScopeScopedMixin.scope`` is blank-on-input and defaulted from
the acting user's sole membership on ``save()``. The Hasura write backend's
create preflight evaluates the REBAC ``create`` permission against the *unsaved*
row before ``save()`` runs, so a relation-arm-gated model (``create =
scope->member``) would fail-close unless the gate sees the scope the row will
persist with. These drive the real gate over a built schema:

1. single-membership actor, no scope input -> gate passes, row persists with the
   defaulted scope;
2. multi-membership actor, no scope -> field-named ``ValidationError``;
3. caller-supplied scope the actor is not a member of -> denied by the gate;
4. caller-supplied scope the actor is a member of -> allowed.
"""

from __future__ import annotations

from typing import Any

import pytest
import strawberry
import strawberry_django
from django.core.management import call_command
from django.test import override_settings
from rebac import (
    RelationshipTuple,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from strawberry import auto

from angee.graphql.data.hasura import (
    AngeeHasuraWriteBackend,
    hasura_model_resource,
    public_pk_decoder,
)
from angee.graphql.node import AngeeNode
from tests.conftest import create_user, execute_schema, result_data
from tests.scopedemo.models import Scope, ScopedDoc


@strawberry_django.type(ScopedDoc)
class ScopedDocType(AngeeNode):
    """GraphQL projection of a locally scoped document."""

    title: auto


# A scoped resource whose writable ``scope`` is exposed as a public id:
# ``field_id_decode`` types it ``ID`` on the insert input, the write backend's
# ``public_id_fields`` decodes it under the actor-scoped write owner and folds it
# into the create preflight relations.
_RESOURCE = hasura_model_resource(
    ScopedDocType,
    model=ScopedDoc,
    name="scoped_docs",
    filterable=["id", "title"],
    sortable=["title"],
    aggregatable=["id"],
    insertable=["title", "scope"],
    updatable=["title"],
    field_id_decode={"scope": public_pk_decoder(Scope)},
    write_backend=AngeeHasuraWriteBackend(ScopedDoc, public_id_fields=("scope",)),
    id_column="sqid",
)

_SCHEMA = strawberry.Schema(
    query=_RESOURCE.query,
    mutation=_RESOURCE.mutation,
    types=[ScopedDocType, *_RESOURCE.types],
)

_INSERT = """
mutation($object: scoped_docs_insert_input!) {
  insert_scoped_docs_one(object: $object) {
    id
  }
}
"""


def _grant(scope: Any, relation: str, user: Any) -> None:
    """Write one direct relationship tuple for ``user`` on ``scope``."""

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(scope),
                relation=relation,
                subject=to_subject_ref(user),
            )
        ]
    )


@pytest.mark.django_db
@pytest.mark.parametrize("rebac_storage", ("denormalized", "registry"))
def test_single_membership_create_defaults_scope_and_passes_gate(rebac_storage: str) -> None:
    """A sole member creates with no scope input; the gate sees the default."""

    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=rebac_storage):
        call_command("rebac", "sync", verbosity=0)
        member = create_user(f"scope-sole-{rebac_storage}")
        with system_context(reason="test scope gate sole setup"):
            scope = Scope.objects.create(name=f"Sole Scope {rebac_storage}")
        _grant(scope, "direct_member", member)

        result = execute_schema(_SCHEMA, _INSERT, {"object": {"title": "Solo"}}, user=member)
        result_data(result)

        with system_context(reason="test scope gate read"):
            doc = ScopedDoc.objects.get()
        assert doc.scope_id == scope.pk


@pytest.mark.django_db
def test_multi_membership_create_without_scope_raises_field_validation() -> None:
    """An ambiguous default fails loudly naming ``scope``, not as a REBAC denial."""

    call_command("rebac", "sync", verbosity=0)
    member = create_user("scope-multi")
    with system_context(reason="test scope gate multi setup"):
        scope_a = Scope.objects.create(name="Scope A")
        scope_b = Scope.objects.create(name="Scope B")
    _grant(scope_a, "direct_member", member)
    _grant(scope_b, "direct_member", member)

    result = execute_schema(_SCHEMA, _INSERT, {"object": {"title": "Ambiguous"}}, user=member)

    assert result.errors is not None
    assert "scope" in str(result.errors)
    with system_context(reason="test scope gate read"):
        assert ScopedDoc.objects.count() == 0


@pytest.mark.django_db
def test_supplied_unreachable_scope_create_is_denied_by_the_gate() -> None:
    """A supplied scope the actor is not a member of is denied by the gate."""

    call_command("rebac", "sync", verbosity=0)
    member = create_user("scope-cross")
    with system_context(reason="test scope gate cross setup"):
        home = Scope.objects.create(name="Home Scope")
        other = Scope.objects.create(name="Other Scope")
    _grant(home, "direct_member", member)

    result = execute_schema(
        _SCHEMA,
        _INSERT,
        {"object": {"title": "Cross", "scope": other.public_id}},
        user=member,
    )

    assert result.errors is not None
    with system_context(reason="test scope gate read"):
        assert ScopedDoc.objects.count() == 0


@pytest.mark.django_db
def test_supplied_member_scope_create_is_allowed() -> None:
    """An explicit scope the actor is a member of passes, resolving ambiguity."""

    call_command("rebac", "sync", verbosity=0)
    member = create_user("scope-explicit")
    with system_context(reason="test scope gate explicit setup"):
        scope_a = Scope.objects.create(name="Scope A")
        scope_b = Scope.objects.create(name="Scope B")
    _grant(scope_a, "direct_member", member)
    _grant(scope_b, "direct_member", member)

    result = execute_schema(
        _SCHEMA,
        _INSERT,
        {"object": {"title": "Explicit", "scope": scope_a.public_id}},
        user=member,
    )
    result_data(result)

    with system_context(reason="test scope gate read"):
        doc = ScopedDoc.objects.get()
    assert doc.scope_id == scope_a.pk
