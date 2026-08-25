"""Actor-scoped relation fields must honor the optimizer's batched prefetch.

``actor_scoped_to_many`` reads the parent's prefetched, actor-stamped relation
cache on its fast path. Returning that cache object (a ``QuerySet``) makes
Strawberry-Django's list ``qs_hook`` clone it — dropping ``_result_cache`` — and
re-fetch it once per parent, silently turning the batched prefetch into an N+1.
The resolver must hand back the materialized rows so the batched prefetch stands.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import strawberry
import strawberry_django
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from rebac import ObjectRef, SubjectRef, actor_context, system_context
from rebac.graphql.strawberry import RebacExtension
from rebac.graphql.strawberry_django import RebacDjangoOptimizerExtension
from rebac.relationships import write_relationships
from rebac.types import RelationshipTuple
from strawberry import auto

from angee.base.models import AngeeModel
from angee.graphql.relations import actor_scoped_to_many


class PrefetchParent(AngeeModel):
    """REBAC-gated parent whose children are read through an actor-scoped field."""

    name = models.CharField(max_length=32)

    class Meta(AngeeModel.Meta):
        """Django model options for the prefetch-test parent."""

        abstract = False
        app_label = "auth"
        rebac_resource_type = "mtidemo/parent"


class PrefetchChild(AngeeModel):
    """REBAC-gated child reached through the reverse ``children`` accessor."""

    parent = models.ForeignKey(PrefetchParent, on_delete=models.CASCADE, related_name="children")
    name = models.CharField(max_length=32)

    class Meta(AngeeModel.Meta):
        """Django model options for the prefetch-test child."""

        abstract = False
        app_label = "auth"
        rebac_resource_type = "mtidemo/child"


@strawberry_django.type(PrefetchChild)
class PrefetchChildType:
    """GraphQL projection of a child row."""

    id: auto
    name: auto


@strawberry_django.type(PrefetchParent)
class PrefetchParentType:
    """GraphQL projection of a parent with its actor-scoped children."""

    id: auto
    name: auto
    children: list[PrefetchChildType] = actor_scoped_to_many("children")


@strawberry.type
class PrefetchQuery:
    """Root query exposing the parent list."""

    @strawberry.field
    def parents(self) -> list[PrefetchParentType]:
        """Return every parent the ambient actor may read."""

        return PrefetchParent.objects.all()


def _grant_reader(resource_type: str, resource_id: str, user_id: str) -> None:
    """Grant the user the ``reader`` relation on one resource row."""

    write_relationships(
        [
            RelationshipTuple(
                resource=ObjectRef(resource_type, resource_id),
                relation="reader",
                subject=SubjectRef.of("auth/user", user_id),
            )
        ]
    )


@pytest.mark.django_db(transaction=True)
def test_actor_scoped_to_many_keeps_the_batched_prefetch() -> None:
    """A nested ``parents { children }`` query hits the child table exactly once."""

    parent_count, child_count = 5, 3
    schema = strawberry.Schema(
        query=PrefetchQuery,
        extensions=[RebacExtension, RebacDjangoOptimizerExtension],
    )
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(PrefetchParent)
        schema_editor.create_model(PrefetchChild)
    try:
        call_command("rebac", "sync", "--yes")

        user = get_user_model().objects.create_user(username="prefetch-reader")
        user_id = str(user.pk)
        with system_context(reason="test.relation-prefetch.setup"):
            parents = [PrefetchParent.objects.create(name=f"p{i}") for i in range(parent_count)]
            for parent in parents:
                for j in range(child_count):
                    PrefetchChild.objects.create(parent=parent, name=f"{parent.name}-c{j}")

        with system_context(reason="test.relation-prefetch.grant"):
            for parent in parents:
                _grant_reader("mtidemo/parent", str(parent.pk), user_id)
                for child in parent.children.all():
                    _grant_reader("mtidemo/child", str(child.pk), user_id)

        query = "{ parents { name children { name } } }"
        context = SimpleNamespace(request=SimpleNamespace(user=user))
        with actor_context(user):
            with CaptureQueriesContext(connection) as captured:
                result = schema.execute_sync(query, context_value=context)

        assert result.errors is None, result.errors
        assert result.data is not None
        assert len(result.data["parents"]) == parent_count
        assert sum(len(p["children"]) for p in result.data["parents"]) == parent_count * child_count

        child_selects = [
            q for q in captured.captured_queries if 'FROM "auth_prefetchchild"' in q["sql"]
        ]
        # One batched prefetch for the whole parent list — never one query per parent.
        assert len(child_selects) == 1, (
            f"expected a single batched child prefetch, got {len(child_selects)} "
            f"(N+1 across {parent_count} parents)"
        )
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(PrefetchChild)
            schema_editor.delete_model(PrefetchParent)
