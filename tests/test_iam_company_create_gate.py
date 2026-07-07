"""The auto-CRUD create gate honors the ``CompanyScopedMixin`` input default.

``CompanyScopedMixin.company`` is blank-on-input and defaulted from the acting
user's sole membership on ``save()`` (§3.7). The Hasura write backend's create
preflight evaluates the REBAC ``create`` permission against the *unsaved* row —
before ``save()`` runs — so a company-arm-gated model (``create =
company->member``, the shape ``arp.calendar.event`` is the first consumer of)
would fail-close on a create a single-membership member is entitled to unless the
gate sees the company the row will persist with. These drive the real gate over a
built schema (real ``check_new`` against the ``scopedemo`` zed):

1. single-membership actor, no company input → gate passes, row persists with the
   defaulted company;
2. multi-membership actor, no company → field-named ``ValidationError`` (loud, not
   a REBAC denial);
3. caller-supplied company the actor is not a member of → denied by the gate (no
   bypass through the default path);
4. caller-supplied company the actor is a member of → allowed.
"""

from __future__ import annotations

from typing import Any

import pytest
import strawberry
import strawberry_django
from django.core.management import call_command
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
from tests.iam_models import Company
from tests.scopedemo.models import ScopedDoc


@strawberry_django.type(ScopedDoc)
class ScopedDocType(AngeeNode):
    """GraphQL projection of a company-scoped document."""

    title: auto


# A company-scoped resource whose writable ``company`` is exposed as a public id:
# ``field_id_decode`` types it ``ID`` on the insert input, the write backend's
# ``public_id_fields`` decodes it under the actor-scoped write owner and folds it
# into the create preflight relations — the shape a real company-scoped resource
# (arp.calendar.event) declares.
_RESOURCE = hasura_model_resource(
    ScopedDocType,
    model=ScopedDoc,
    name="scoped_docs",
    filterable=["id", "title"],
    sortable=["title"],
    aggregatable=["id"],
    insertable=["title", "company"],
    updatable=["title"],
    field_id_decode={"company": public_pk_decoder(Company)},
    write_backend=AngeeHasuraWriteBackend(ScopedDoc, public_id_fields=("company",)),
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


def _grant(company: Any, relation: str, user: Any) -> None:
    """Write one direct relationship tuple for ``user`` on ``company``."""

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(company),
                relation=relation,
                subject=to_subject_ref(user),
            )
        ]
    )


@pytest.mark.django_db
def test_single_membership_create_defaults_company_and_passes_gate() -> None:
    """A sole member creates with no company input — the gate sees the default."""

    call_command("rebac", "sync", verbosity=0)
    member = create_user("scope-sole")
    with system_context(reason="test scope gate sole setup"):
        company = Company.objects.create(name="Sole Co")
    _grant(company, "direct_member", member)

    result = execute_schema(_SCHEMA, _INSERT, {"object": {"title": "Solo"}}, user=member)
    result_data(result)

    with system_context(reason="test scope gate read"):
        doc = ScopedDoc.objects.get()
    assert doc.company_id == company.pk


@pytest.mark.django_db
def test_multi_membership_create_without_company_raises_field_validation() -> None:
    """An ambiguous default fails loudly naming ``company`` — not a REBAC denial."""

    call_command("rebac", "sync", verbosity=0)
    member = create_user("scope-multi")
    with system_context(reason="test scope gate multi setup"):
        company_a = Company.objects.create(name="Co A")
        company_b = Company.objects.create(name="Co B")
    _grant(company_a, "direct_member", member)
    _grant(company_b, "direct_member", member)

    result = execute_schema(_SCHEMA, _INSERT, {"object": {"title": "Ambiguous"}}, user=member)

    assert result.errors is not None
    assert "company" in str(result.errors)
    with system_context(reason="test scope gate read"):
        assert ScopedDoc.objects.count() == 0


@pytest.mark.django_db
def test_supplied_cross_company_create_is_denied_by_the_gate() -> None:
    """A supplied company the actor is not a member of is denied by the gate.

    The company id decodes through the identity owner (``field_id_decode``), yet
    ``create = company->member`` walks membership and denies — the supplied id
    rides the gate exactly as any other, with no bypass through the
    membership-default path.
    """

    call_command("rebac", "sync", verbosity=0)
    member = create_user("scope-cross")
    with system_context(reason="test scope gate cross setup"):
        home = Company.objects.create(name="Home Co")
        other = Company.objects.create(name="Other Co")
    _grant(home, "direct_member", member)

    result = execute_schema(
        _SCHEMA,
        _INSERT,
        {"object": {"title": "Cross", "company": other.public_id}},
        user=member,
    )

    assert result.errors is not None
    with system_context(reason="test scope gate read"):
        assert ScopedDoc.objects.count() == 0


@pytest.mark.django_db
def test_supplied_member_company_create_is_allowed() -> None:
    """An explicit company the actor is a member of passes, resolving the ambiguity."""

    call_command("rebac", "sync", verbosity=0)
    member = create_user("scope-explicit")
    with system_context(reason="test scope gate explicit setup"):
        company_a = Company.objects.create(name="Co A")
        company_b = Company.objects.create(name="Co B")
    _grant(company_a, "direct_member", member)
    _grant(company_b, "direct_member", member)

    result = execute_schema(
        _SCHEMA,
        _INSERT,
        {"object": {"title": "Explicit", "company": company_a.public_id}},
        user=member,
    )
    result_data(result)

    with system_context(reason="test scope gate read"):
        doc = ScopedDoc.objects.get()
    assert doc.company_id == company_a.pk
