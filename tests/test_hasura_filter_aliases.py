"""Model-declared scalar aliases compose native requests and stored conditions."""

import pytest
import strawberry
import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.models import AngeeDataModel
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import create_user, execute_schema, make_addon, result_data
from tests.scopedemo.models import Scope


@strawberry_django.type(Scope)
class AliasScopeType(AngeeNode):
    """Reuse an installed test model without adding a model or registry entry."""

    name: strawberry.auto


def build_resource(**kwargs):
    """Compose each declaration afresh so monkeypatched model facts remain isolated."""
    return hasura_model_resource(
        AliasScopeType, model=Scope, name="alias_scopes", filterable=("id", "name"),
        sortable=("name",), aggregatable=("id",), insert=False, update=False, delete=False, **kwargs,
    )


@pytest.fixture(params=("parent__name", "parent.name"))
def alias_owner(monkeypatch, request):
    monkeypatch.setattr(Scope, "hasura_aliases", {
        "parent_name": request.param, "display_title": "name",
    }, raising=False)
    resource = build_resource()
    return GraphQLSchemas([make_addon(schemas={"console": {
        "query": [resource.query], "types": resource.types,
    }})])


def test_scalar_alias_queries_aggregates_and_stored_filters_retain_the_same_read_scope(composed_tables, alias_owner):
    """A readable child cannot filter on an unreadable parent's protected scalar."""
    viewer, other = create_user("alias-viewer"), create_user("alias-other")
    with system_context(reason="filter alias fixtures"):
        visible = Scope.objects.create(name="Visible parent")
        hidden = Scope.objects.create(name="Hidden parent")
        visible_child = Scope.objects.create(name="Visible child", parent=visible)
        hidden_child = Scope.objects.create(name="Hidden child", parent=hidden)
    write_relationships([
        RelationshipTuple(to_object_ref(scope), "direct_member", to_subject_ref(viewer))
        for scope in (visible, hidden_child)
    ])
    write_relationships([
        RelationshipTuple(to_object_ref(visible_child), "direct_member", to_subject_ref(other)),
    ])
    schema = alias_owner.build("console")
    query = """query($where: alias_scopes_bool_exp!) {
      alias_scopes(where: $where, order_by: {name: asc}) { id name }
      alias_scopes_aggregate(where: $where) { aggregate { count } }
    }"""
    where = {"_and": [
        {"parent_name": {"_eq": visible.name}}, {"display_title": {"_eq": visible_child.name}},
    ]}
    condition = alias_owner.resource_filter(Scope, where)
    assert result_data(execute_schema(schema, query, {"where": where}, user=viewer)) == {
        "alias_scopes": [{"id": visible_child.sqid, "name": visible_child.name}],
        "alias_scopes_aggregate": {"aggregate": {"count": 1}},
    }
    assert list(condition(Scope.objects.with_actor(viewer)).values_list("pk", flat=True)) == [visible_child.pk]
    # The other reader sees that same child, but has no access to its parent.
    assert Scope.objects.with_actor(other).filter(pk=visible_child.pk).exists()
    assert result_data(execute_schema(schema, query, {"where": where}, user=other)) == {
        "alias_scopes": [], "alias_scopes_aggregate": {"aggregate": {"count": 0}},
    }
    assert not condition(Scope.objects.with_actor(other)).exists()
    hidden_filter = {"parent_name": {"_eq": hidden.name}}
    assert Scope.objects.with_actor(viewer).filter(pk=hidden_child.pk).exists()
    assert result_data(execute_schema(schema, query, {"where": hidden_filter}, user=viewer)) == {
        "alias_scopes": [], "alias_scopes_aggregate": {"aggregate": {"count": 0}},
    }
    assert not alias_owner.resource_filter(Scope, hidden_filter)(Scope.objects.with_actor(viewer)).exists()


def test_alias_metadata_exposes_native_scalar_comparisons(alias_owner):
    """Filter-only aliases are discoverable without inventing output columns."""
    [resource] = alias_owner.resources("console")
    graphql_filter = alias_owner.graphql_schema("console").get_type(resource.type_names.filter)
    for alias in ("parent_name", "display_title"):
        assert str(graphql_filter.fields[alias].type) == "String_comparison_exp"
        assert resource.query.fields[alias].filter is not None
        assert resource.query.fields[alias].row is None


@pytest.mark.parametrize(("alias", "path", "diagnostic"), [
    ("name", "parent__name", "shadows a model field"),
    ("parent_name", "missing__name", "parent_name.*missing__name"),
    ("parent_name", "children__name", "parent_name.*children__name"),
    ("parent_name", "parent", "must target a scalar"),
])
def test_invalid_alias_declarations_fail_during_resource_composition(monkeypatch, alias, path, diagnostic):
    monkeypatch.setattr(Scope, "hasura_aliases", {alias: path}, raising=False)
    with pytest.raises(ImproperlyConfigured, match=diagnostic):
        build_resource()


def test_alias_cannot_override_a_computed_filter(monkeypatch):
    monkeypatch.setattr(Scope, "hasura_aliases", {"parent_name": "parent__name"}, raising=False)
    with pytest.raises(ImproperlyConfigured, match="Duplicate filter alias 'parent_name'"):
        build_resource(filter_expressions={"parent_name": models.Value("computed", output_field=models.CharField())})


def test_contributors_cannot_claim_the_same_alias(monkeypatch):
    """Conflicting inherited declarations fail instead of following Python MRO precedence."""
    monkeypatch.setattr(AngeeDataModel, "hasura_aliases", {"parent_name": "name"}, raising=False)
    monkeypatch.setattr(Scope, "hasura_aliases", {"parent_name": "parent__name"}, raising=False)
    with pytest.raises(ImproperlyConfigured, match="Duplicate filter alias 'parent_name'"):
        build_resource()
