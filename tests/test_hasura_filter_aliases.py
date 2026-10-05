"""Model-declared scalar aliases compose native requests and stored conditions."""

import pytest
import strawberry
import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.models import AngeeDataModel
from angee.graphql.data.hasura import _declared_aliases, _declared_filter_expressions, hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from tests.composed_host import run_composed_tests
from tests.conftest import create_user, execute_schema, make_addon, result_data
from tests.scopedemo.models import Scope


@pytest.fixture(autouse=True)
def clear_declared_aliases():
    """Each test changes model declarations before building its isolated schema."""

    _declared_aliases.cache_clear()
    yield
    _declared_aliases.cache_clear()


@strawberry_django.type(Scope)
class AliasScopeType(AngeeNode):
    """Reuse an installed test model without adding a model or registry entry."""

    name: strawberry.auto


def build_resource(*, filterable=("id", "name"), **kwargs):
    """Compose each declaration afresh so monkeypatched model facts remain isolated."""
    return hasura_model_resource(
        AliasScopeType, model=Scope, name="alias_scopes", filterable=filterable,
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
    with pytest.raises(ImproperlyConfigured, match=r"duplicate filter aliases: \['parent_name'\]"):
        build_resource(filter_expressions={"parent_name": models.Value("computed", output_field=models.CharField())})


def test_contributors_cannot_claim_the_same_alias(monkeypatch):
    """Conflicting inherited declarations fail instead of following Python MRO precedence."""
    monkeypatch.setattr(AngeeDataModel, "hasura_aliases", {"parent_name": "name"}, raising=False)
    monkeypatch.setattr(Scope, "hasura_aliases", {"parent_name": "parent__name"}, raising=False)
    with pytest.raises(ImproperlyConfigured, match="duplicate sortable alias 'parent_name'"):
        build_resource()


def constant_filter(queryset):
    """A provider whose scalar type can be inspected without reading rows."""
    return models.Value("computed", output_field=models.CharField())


@pytest.mark.parametrize("declaration", ("hasura_filter_expressions", "hasura_aliases"))
@pytest.mark.parametrize(("name", "filterable", "container_scopes", "reason"), [
    ("name", ("id", "name"), (), "shadows a model field"),
    ("parent", ("id", "name"), (), "shadows a model field"),
    ("parent__name", ("id", "name", "parent__name"), (), "shadows a resource filter"),
    ("caller_filter", ("id", "name", "caller_filter"), (), "shadows a resource filter"),
    ("parent__name", ("id", "name"), ("parent__name",), "shadows a resource filter"),
    ("_or", ("id", "name"), (), "is a reserved Hasura combinator"),
    ("_and", ("id", "name"), (), "is a reserved Hasura combinator"),
    ("_not", ("id", "name"), (), "is a reserved Hasura combinator"),
])
def test_model_filter_declarations_cannot_shadow_resource_filters(
    monkeypatch, declaration, name, filterable, container_scopes, reason,
):
    """Both model seams reject occupied names before augmenting the resource's filters."""
    value = constant_filter if declaration == "hasura_filter_expressions" else models.Value("alias")
    monkeypatch.setattr(Scope, declaration, {name: value}, raising=False)
    monkeypatch.setattr(Scope, "hasura_container_scope_fields", container_scopes, raising=False)
    with pytest.raises(ImproperlyConfigured) as error:
        build_resource(filterable=filterable)
    assert str(error.value) == f"scopedemo.Scope filter expression {name!r} {reason}."


@pytest.mark.parametrize("declaration", ({"computed": None}, {"computed": models.Value(True)}, ["computed"]))
def test_filter_expression_providers_are_checked_at_composition(monkeypatch, declaration):
    monkeypatch.setattr(Scope, "hasura_filter_expressions", declaration, raising=False)
    diagnostic = (
        "must be a provider taking the target queryset" if isinstance(declaration, dict) else "must be a mapping"
    )
    with pytest.raises(ImproperlyConfigured, match=diagnostic):
        build_resource()


@pytest.mark.parametrize("existing", ("caller", "alias"))
def test_filter_expression_cannot_override_an_existing_computed_filter(monkeypatch, existing):
    monkeypatch.setattr(Scope, "hasura_filter_expressions", {"computed": constant_filter}, raising=False)
    kwargs = {}
    if existing == "caller":
        kwargs["filter_expressions"] = {"computed": models.Value(True)}
    else:
        monkeypatch.setattr(Scope, "hasura_aliases", {"computed": models.Value(True)}, raising=False)
    with pytest.raises(
        ImproperlyConfigured, match=r"scopedemo.Scope declares duplicate filter expressions: \['computed'\]",
    ):
        build_resource(**kwargs)


@pytest.fixture
def filter_extension_bases():
    """Abstract contributors preserve the source seam without registering another table."""
    class FirstExtension(models.Model):
        class Meta:
            abstract = True
            app_label = "scopedemo"

    class SecondExtension(models.Model):
        class Meta:
            abstract = True
            app_label = "scopedemo"

    class ExtendedScope(FirstExtension, SecondExtension, Scope):
        class Meta:
            abstract = True
            app_label = "scopedemo"

    return FirstExtension, SecondExtension, ExtendedScope


def test_two_extension_bases_cannot_claim_the_same_filter(monkeypatch, filter_extension_bases):
    first, second, model = filter_extension_bases
    for base in (first, second):
        monkeypatch.setattr(base, "hasura_filter_expressions", {"computed": constant_filter}, raising=False)
    with pytest.raises(
        ImproperlyConfigured, match="scopedemo.ExtendedScope declares duplicate filter expression 'computed'",
    ):
        hasura_model_resource(AliasScopeType, model=model, filterable=(), sortable=(), aggregatable=())


def test_valid_filter_declarations_follow_deterministic_mro_and_mapping_order(monkeypatch, filter_extension_bases):
    first, second, model = filter_extension_bases
    monkeypatch.setattr(
        first, "hasura_filter_expressions", {"first_z": constant_filter, "first_a": constant_filter}, raising=False,
    )
    monkeypatch.setattr(second, "hasura_filter_expressions", {"second": constant_filter}, raising=False)
    assert list(_declared_filter_expressions(model)) == ["second", "first_z", "first_a"]
    monkeypatch.setattr(Scope, "hasura_filter_expressions", {"computed": constant_filter}, raising=False)
    resource = build_resource()
    owner = GraphQLSchemas([make_addon(schemas={"console": {"query": [resource.query], "types": resource.types}})])
    [metadata] = owner.resources("console")
    assert metadata.query.fields["computed"].filter is not None
    # An explicitly named caller-owned expression remains a valid declaration.
    build_resource(filterable=("id", "name", "caller_filter"), filter_expressions={"caller_filter": models.Value(True)})


def test_existing_addon_filter_declarations_still_compose(tmp_path):
    """Build all current addon declarations together in the native isolated SQLite host."""
    run_composed_tests(
        tmp_path, "tests.native_intake_capture.HasuraFilterDeclarationTests",
        app=("angee.intake", "angee.workflows"),
    )
