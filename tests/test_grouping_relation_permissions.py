"""Native regressions for actor-scoped related grouping axes.

These tests exercise only the public ``hasura_model_resource`` contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.db.models.functions import Coalesce, NullIf
from django.test.utils import CaptureQueriesContext
from rebac import (
    RelationshipTuple,
    SubjectRef,
    system_context,
    to_object_ref,
    write_relationships,
)
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.resources import model_resource_type
from rebac.schema import parse_zed
from strawberry import auto

from angee.base.models import AngeeDataModel
from angee.graphql.capabilities import permissions_field
from angee.graphql.data import declared_hasura_resource_fields, hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_many, actor_scoped_to_one
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import (
    SchemaAddon,
    execute_schema,
    result_data,
)
from tests.tables import model_tables


class GroupLabel(AngeeDataModel):
    """Protected target whose scalar fields must follow its own read scope."""

    sqid_prefix = "grl_"
    external_key = models.CharField(max_length=32, unique=True)
    display_name = models.CharField(max_length=64)
    rank = models.IntegerField()

    class Meta(AngeeDataModel.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "tests/group_label"


class GroupMiddle(AngeeDataModel):
    """Readable first hop whose protected target can still be unreadable."""

    sqid_prefix = "grm_"
    target = models.ForeignKey(
        GroupLabel,
        to_field="external_key",
        on_delete=models.CASCADE,
    )

    class Meta(AngeeDataModel.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "tests/group_middle"


class PlainGroupLabel(models.Model):
    """Permission-naive Django target retained as a negative control."""

    display_name = models.CharField(max_length=64)

    class Meta:
        app_label = "scopedemo"


class GroupParent(AngeeDataModel):
    """Readable parent spanning direct, scalar, nested, and plain axes."""

    sqid_prefix = "grp_"
    hasura_sortable_fields = ("metric_target__rank",)
    hasura_sortable_aliases = {
        "label_name": NullIf(models.F("target__display_name"), models.Value(""), output_field=models.TextField()),
        "nested_name": Coalesce(models.F("middle__target__display_name"), models.Value("fallback")),
    }
    kind = models.CharField(max_length=16)
    amount = models.IntegerField(default=1)
    target = models.ForeignKey(
        GroupLabel,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    metric_target = models.ForeignKey(
        GroupLabel,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    middle = models.ForeignKey(
        GroupMiddle,
        null=True,
        on_delete=models.SET_NULL,
        related_name="parents",
    )
    plain = models.ForeignKey(
        PlainGroupLabel,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta(AngeeDataModel.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "tests/group_parent"


@strawberry_django.type(GroupLabel)
class GroupLabelType(AngeeNode):
    display_name: auto
    permissions = permissions_field(("read",))


@strawberry_django.type(GroupMiddle)
class GroupMiddleType(AngeeNode):
    target: GroupLabelType | None = actor_scoped_to_one("target")
    parents: list["GroupParentType"] = actor_scoped_to_many("parents")
    permissions = permissions_field(("read",))


@strawberry_django.type(GroupParent)
class GroupParentType(AngeeNode):
    kind: auto
    amount: auto
    target: GroupLabelType | None = actor_scoped_to_one("target")
    middle: GroupMiddleType | None = actor_scoped_to_one("middle")
    permissions = permissions_field(("read",))


@pytest.fixture
def relation_grouping_case(transactional_db: None):
    """Build one schema and two actors over stable parent group identities."""

    del transactional_db
    reset_backend()
    active = backend()
    assert isinstance(active, LocalBackend)
    definition = parse_zed(
        """
        definition auth/user {}
        definition tests/group_label {
            relation reader: auth/user
            permission read = reader
        }
        definition tests/group_middle {
            relation reader: auth/user
            permission read = reader
        }
        definition tests/group_parent {
            relation reader: auth/user
            permission read = reader
        }
        """
    )
    active.set_schema(definition)
    models_in_order = (GroupLabel, GroupMiddle, PlainGroupLabel, GroupParent)
    with (
        model_tables(models_in_order),
        patch(
            "angee.graphql.capabilities.effective_rebac_definition",
            side_effect=lambda model: definition.get_definition(model_resource_type(model)),
        ),
    ):
        try:
            alice = SubjectRef.of("auth/user", "alice")
            bob = SubjectRef.of("auth/user", "bob")
            resource = hasura_model_resource(
                GroupParentType,
                model=GroupParent,
                name="group_parents",
                filterable=[
                    "kind", "target", "metric_target__rank", "target__display_name", "middle__target__display_name",
                ],
                sortable=["kind", "id", *declared_hasura_resource_fields(GroupParent, "hasura_sortable_fields")],
                aggregatable=["amount"],
                groupable=[
                    "target",
                    "metric_target",
                    "metric_target__rank",
                    "middle",
                    "middle__target__display_name",
                    "plain",
                ],
                insert=False,
                update=False,
                delete=False,
            )
            schema = GraphQLSchemas(
                [
                    SchemaAddon(
                        {
                            "public": {
                                "query": [resource.query],
                                "types": [GroupParentType, *resource.types],
                            }
                        }
                    )
                ]
            ).build("public")
            pinned_resource = hasura_model_resource(
                GroupParentType,
                model=GroupParent,
                name="pinned_group_parents",
                filterable=["kind", "target", "target__display_name"],
                sortable=["kind", "id", *declared_hasura_resource_fields(GroupParent, "hasura_sortable_fields")],
                aggregatable=["amount"],
                groupable=["target"],
                get_queryset=lambda info: GroupParent.objects.with_actor(alice),
                insert=False,
                update=False,
                delete=False,
            )
            pinned_schema = GraphQLSchemas(
                [
                    SchemaAddon(
                        {
                            "public": {
                                "query": [pinned_resource.query],
                                "types": [
                                    GroupParentType,
                                    *pinned_resource.types,
                                ],
                            }
                        }
                    )
                ]
            ).build("public")
            with system_context(reason="test.grouping.relation_permissions.seed"):
                alpha = GroupLabel.objects.create(
                    external_key="alpha-key",
                    display_name="Alpha",
                    rank=10,
                )
                beta = GroupLabel.objects.create(
                    external_key="beta-key",
                    display_name="Beta",
                    rank=20,
                )
                duplicate_one = GroupLabel.objects.create(
                    external_key="duplicate-one-key",
                    display_name="Duplicate",
                    rank=30,
                )
                duplicate_two = GroupLabel.objects.create(
                    external_key="duplicate-two-key",
                    display_name="Duplicate",
                    rank=40,
                )
                alpha_middle = GroupMiddle.objects.create(target=alpha)
                beta_middle = GroupMiddle.objects.create(target=beta)
                hidden_middle = GroupMiddle.objects.create(target=duplicate_one)
                plain = PlainGroupLabel.objects.create(display_name="Plain")
                parents = [
                    GroupParent.objects.create(
                        kind="target",
                        target=alpha,
                        metric_target=alpha,
                    ),
                    GroupParent.objects.create(
                        kind="target",
                        target=alpha,
                        metric_target=alpha,
                    ),
                    GroupParent.objects.create(
                        kind="target",
                        target=beta,
                        metric_target=beta,
                    ),
                    GroupParent.objects.create(
                        kind="target",
                        target=duplicate_one,
                        metric_target=duplicate_one,
                    ),
                    GroupParent.objects.create(
                        kind="target",
                        target=duplicate_two,
                        metric_target=duplicate_two,
                    ),
                    GroupParent.objects.create(kind="target"),
                    GroupParent.objects.create(kind="nested", middle=alpha_middle),
                    GroupParent.objects.create(kind="nested", middle=beta_middle),
                    GroupParent.objects.create(kind="nested", middle=hidden_middle),
                    GroupParent.objects.create(kind="plain", plain=plain),
                ]
            write_relationships(
                [
                    *(
                        RelationshipTuple(to_object_ref(parent), "reader", actor)
                        for parent in parents
                        for actor in (alice, bob)
                    ),
                    RelationshipTuple(to_object_ref(alpha), "reader", alice),
                    RelationshipTuple(to_object_ref(beta), "reader", bob),
                    *(
                        RelationshipTuple(to_object_ref(label), "reader", actor)
                        for label in (duplicate_one, duplicate_two)
                        for actor in (alice, bob)
                    ),
                    *(
                        RelationshipTuple(to_object_ref(middle), "reader", actor)
                        for middle in (alpha_middle, beta_middle)
                        for actor in (alice, bob)
                    ),
                    RelationshipTuple(to_object_ref(hidden_middle), "reader", bob),
                ]
            )
            yield SimpleNamespace(
                schema=schema,
                pinned_schema=pinned_schema,
                alice=alice,
                bob=bob,
                alpha=alpha,
                beta=beta,
                duplicate_one=duplicate_one,
                duplicate_two=duplicate_two,
                alpha_middle=alpha_middle,
                beta_middle=beta_middle,
                hidden_middle=hidden_middle,
                plain=plain,
                parents=parents,
            )
        finally:
            reset_backend()


def _query(case: Any, actor: SubjectRef, document: str) -> dict[str, Any]:
    return result_data(execute_schema(case.schema, document, user=actor))


def test_related_axes_merge_unreadable_identities_into_null(
    relation_grouping_case: Any,
) -> None:
    """Unreadable keys merge into null while readable identities stay distinct."""

    case = relation_grouping_case
    document = """
        query {
          groups: group_parents_groups(
            group_by: [{field: TARGET}, {field: TARGET__DISPLAY_NAME}],
            where: {kind: {_eq: "target"}}, limit: 20
          ) {
            key { target_id target__display_name }
            aggregate { count }
          }
          exact: group_parents_groups_count(
            group_by: [{field: TARGET}, {field: TARGET__DISPLAY_NAME}],
            where: {kind: {_eq: "target"}}
          )
          having: group_parents_groups(
            group_by: [{field: TARGET}, {field: TARGET__DISPLAY_NAME}],
            where: {kind: {_eq: "target"}}, having: {count_gt: 1}
          ) {
            key { target_id target__display_name }
            aggregate { count }
          }
          having_exact: group_parents_groups_count(
            group_by: [{field: TARGET}, {field: TARGET__DISPLAY_NAME}],
            where: {kind: {_eq: "target"}}, having: {count_gt: 1}
          )
          ranks: group_parents_groups(
            group_by: [
              {field: METRIC_TARGET},
              {field: METRIC_TARGET__RANK}
            ],
            where: {kind: {_eq: "target"}}, limit: 20
          ) {
            key { metric_target_id metric_target__rank }
          }
        }
    """
    alice = _query(case, case.alice, document)
    bob = _query(case, case.bob, document)

    assert alice["exact"] == bob["exact"] == 4
    assert alice["having_exact"] == 2
    assert bob["having_exact"] == 1

    alice_groups = {
        row["key"]["target_id"]: (
            row["key"]["target__display_name"],
            row["aggregate"]["count"],
        )
        for row in alice["groups"]
    }
    bob_groups = {
        row["key"]["target_id"]: (
            row["key"]["target__display_name"],
            row["aggregate"]["count"],
        )
        for row in bob["groups"]
    }
    assert alice_groups == {
        case.alpha.sqid: ("Alpha", 2),
        case.duplicate_one.sqid: ("Duplicate", 1),
        case.duplicate_two.sqid: ("Duplicate", 1),
        None: (None, 2),
    }
    assert bob_groups == {
        case.beta.sqid: ("Beta", 1),
        case.duplicate_one.sqid: ("Duplicate", 1),
        case.duplicate_two.sqid: ("Duplicate", 1),
        None: (None, 3),
    }
    assert sorted(alice["having"], key=lambda row: str(row["key"]["target_id"])) == sorted(
        [
            {"key": {"target_id": None, "target__display_name": None}, "aggregate": {"count": 2}},
            {
                "key": {
                    "target_id": case.alpha.sqid,
                    "target__display_name": "Alpha",
                },
                "aggregate": {"count": 2},
            },
        ],
        key=lambda row: str(row["key"]["target_id"]),
    )
    assert bob["having"] == [
        {
            "key": {
                "target_id": None,
                "target__display_name": None,
            },
            "aggregate": {"count": 3},
        }
    ]
    alice_ranks = {row["key"]["metric_target_id"]: row["key"]["metric_target__rank"] for row in alice["ranks"]}
    bob_ranks = {row["key"]["metric_target_id"]: row["key"]["metric_target__rank"] for row in bob["ranks"]}
    assert alice_ranks[case.alpha.sqid] == 10
    assert case.beta.sqid not in alice_ranks and alice_ranks[None] is None
    assert case.alpha.sqid not in bob_ranks and bob_ranks[None] is None
    assert bob_ranks[case.beta.sqid] == 20


def test_nested_protected_hop_and_plain_django_target(
    relation_grouping_case: Any,
) -> None:
    """Every protected hop redacts; a permission-naive target stays native."""

    case = relation_grouping_case
    document = """
        query {
          nested: group_parents_groups(
            group_by: [
              {field: MIDDLE},
              {field: MIDDLE__TARGET__DISPLAY_NAME}
            ],
            where: {kind: {_eq: "nested"}}, limit: 20
          ) {
            key { middle_id middle__target__display_name }
          }
          plain: group_parents_groups(
            group_by: [{field: PLAIN}, {field: PLAIN__DISPLAY_NAME}],
            where: {kind: {_eq: "plain"}}, limit: 20
          ) {
            key { plain_id plain__display_name }
          }
        }
    """
    alice = _query(case, case.alice, document)
    bob = _query(case, case.bob, document)

    assert {row["key"]["middle_id"]: row["key"]["middle__target__display_name"] for row in alice["nested"]} == {
        case.alpha_middle.sqid: "Alpha",
        case.beta_middle.sqid: None,
        # The terminal label is readable, but its intermediate hop is not.
        None: None,
    }
    assert {row["key"]["middle_id"]: row["key"]["middle__target__display_name"] for row in bob["nested"]} == {
        case.alpha_middle.sqid: None,
        case.beta_middle.sqid: "Beta",
        case.hidden_middle.sqid: "Duplicate",
    }
    expected_plain = [
        {
            "key": {
                "plain_id": str(case.plain.pk),
                "plain__display_name": "Plain",
            }
        }
    ]
    assert alice["plain"] == bob["plain"] == expected_plain


def test_explicit_queryset_actor_owns_related_axis_scope(
    relation_grouping_case: Any,
) -> None:
    """A queryset-pinned actor wins over a different ambient request actor."""

    case = relation_grouping_case
    result = result_data(
        execute_schema(
            case.pinned_schema,
            """
            query {
              pinned_group_parents_groups(
                group_by: [
                  {field: TARGET},
                  {field: TARGET__DISPLAY_NAME}
                ],
                where: {kind: {_eq: "target"}}, limit: 20
              ) {
                key { target_id target__display_name }
              }
            }
            """,
            # Bob is ambient, while the source queryset is explicitly Alice.
            user=case.bob,
        )
    )["pinned_group_parents_groups"]
    labels = {row["key"]["target_id"]: row["key"]["target__display_name"] for row in result}
    assert labels[case.alpha.sqid] == "Alpha"
    assert case.beta.sqid not in labels and labels[None] is None


@pytest.mark.parametrize(
    ("path", "kind", "null_indexes"),
    [("target__display_name", "target", (2, 5)), ("middle__target__display_name", "nested", (7, 8))],
)
def test_scalar_relation_filters_redact_every_protected_hop(
    relation_grouping_case: Any, path: str, kind: str, null_indexes: tuple[int, int],
) -> None:
    case = relation_grouping_case
    document = """
        query {
          hidden: group_parents(where: {PATH: {_eq: "Beta"}}) { id }
          unknown: group_parents(where: {PATH: {_eq: "Absent"}}) { id }
          range: group_parents(where: {metric_target__rank: {_gte: 20, _lt: 30}}) { id }
          nulls: group_parents(where: {kind: {_eq: "KIND"}, PATH: {_is_null: true}}) { id }
          groups: group_parents_groups_count(group_by: [{field: TARGET}], where: {PATH: {_eq: "Beta"}})
        }
    """.replace("PATH", path).replace('"KIND"', f'"{kind}"')
    alice = _query(case, case.alice, document)
    assert alice["hidden"] == alice["unknown"] == alice["range"] == []
    assert alice["groups"] == 0
    assert {row["id"] for row in alice["nulls"]} == {str(case.parents[index].sqid) for index in null_indexes}
    bob = _query(case, case.bob, document)
    assert len(bob["hidden"]) == bob["groups"] == len(bob["range"]) == 1


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_scalar_relation_sort_cannot_order_by_hidden_values(relation_grouping_case: Any, direction: str) -> None:
    case = relation_grouping_case
    document = """
        query {
          group_parents(where: {kind: {_eq: "target"}}, order_by: [{metric_target__rank: DIRECTION}, {id: asc}]) { id }
        }
    """.replace("DIRECTION", direction)
    before = _query(case, case.alice, document)
    assert len(before["group_parents"]) == 6
    ids = [row["id"] for row in before["group_parents"]]
    readable_indexes = [0, 1, 3, 4] if direction == "asc" else [4, 3, 0, 1]
    null_ids = [str(case.parents[index].sqid) for index in (2, 5)]
    assert [value for value in ids if value not in null_ids] == [
        str(case.parents[index].sqid) for index in readable_indexes
    ]
    assert ids[ids.index(null_ids[0]):ids.index(null_ids[0]) + 2] == null_ids
    with system_context(reason="test.related.hidden_sort_value"):
        GroupLabel.objects.filter(pk=case.beta.pk).update(rank=-100)
    assert _query(case, case.alice, document) == before
    pinned = result_data(execute_schema(
        case.pinned_schema,
        '{ pinned_group_parents(where: {target__display_name: {_eq: "Beta"}}) { id } }',
        user=case.bob,
    ))
    assert pinned["pinned_group_parents"] == []
    pinned_order = result_data(execute_schema(
        case.pinned_schema, document.replace("group_parents(", "pinned_group_parents("), user=case.bob,
    ))
    assert pinned_order["pinned_group_parents"] == before["group_parents"]


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_declared_sort_alias_redacts_hidden_parents_and_preserves_empty_names(
    relation_grouping_case: Any, direction: str,
) -> None:
    """Declared expressions order lazily and hidden/empty/missing labels tie."""

    case = relation_grouping_case
    with system_context(reason="test.sort_alias.empty_name"):
        GroupLabel.objects.filter(pk=case.duplicate_one.pk).update(display_name="")
    document = """
        { group_parents(where: {kind: {_eq: "target"}}, order_by: [{label_name: DIRECTION}]) { id } }
    """.replace("DIRECTION", direction)
    before = _query(case, case.alice, document)["group_parents"]
    ids = [row["id"] for row in before]
    null_ids = [str(case.parents[index].sqid) for index in (2, 3, 5)]
    readable_indexes = [0, 1, 4] if direction == "asc" else [4, 0, 1]
    assert [value for value in ids if value not in null_ids] == [
        str(case.parents[index].sqid) for index in readable_indexes
    ]
    assert ids[ids.index(null_ids[0]):ids.index(null_ids[0]) + 3] == null_ids
    with system_context(reason="test.sort_alias.hidden_name"):
        GroupLabel.objects.filter(pk=case.beta.pk).update(display_name="ZZZ")
    assert _query(case, case.alice, document)["group_parents"] == before
    bob = _query(case, case.bob, document)["group_parents"]
    assert bob != before
    pinned = result_data(execute_schema(
        case.pinned_schema, document.replace("group_parents(", "pinned_group_parents("), user=case.bob,
    ))
    assert pinned["pinned_group_parents"] == before


def test_declared_sort_alias_guards_every_hop_and_its_fallback(relation_grouping_case: Any) -> None:
    case = relation_grouping_case
    document = """
        { group_parents(where: {kind: {_eq: "nested"}}, order_by: [{nested_name: desc}]) { id } }
    """
    before = _query(case, case.alice, document)
    null_ids = [str(case.parents[index].sqid) for index in (7, 8)]
    ids = [row["id"] for row in before["group_parents"]]
    assert ids[ids.index(null_ids[0]):ids.index(null_ids[0]) + 2] == null_ids
    with system_context(reason="test.sort_alias.hidden_hops"):
        GroupLabel.objects.filter(pk__in=[case.beta.pk, case.duplicate_one.pk]).update(display_name="ZZZ")
    assert _query(case, case.alice, document) == before


@pytest.mark.parametrize("path", ["middle__parents__kind", "target__absent", "kind__value"])
def test_declared_sort_paths_reject_to_many_and_unknown_fields(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setattr(GroupParent, "hasura_sortable_fields", (path,))
    with pytest.raises(ImproperlyConfigured, match="invalid field"):
        declared_hasura_resource_fields(GroupParent, "hasura_sortable_fields")


@pytest.mark.parametrize(
    "expression",
    [models.F("middle__parents__kind"), models.F("target__absent"), models.expressions.RawSQL("1", ())],
)
def test_declared_sort_alias_rejects_unverifiable_expressions(
    monkeypatch: pytest.MonkeyPatch, expression: Any,
) -> None:
    monkeypatch.setattr(GroupParent, "hasura_sortable_aliases", {"bad_alias": expression})
    with pytest.raises(ImproperlyConfigured, match="sortable alias"):
        hasura_model_resource(
            GroupParentType, model=GroupParent, filterable=[], sortable=[], aggregatable=[],
            insert=False, update=False, delete=False,
        )


def test_declared_sort_alias_rejects_field_gated_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    definition = parse_zed("""
        definition auth/user {}
        definition tests/group_label {
            relation reader: auth/user
            permission read = reader
            permission read__display_name = reader
        }
    """)
    monkeypatch.setattr(
        "angee.graphql.access.effective_rebac_definition",
        lambda model: definition.get_definition(model_resource_type(model)),
    )
    with pytest.raises(ImproperlyConfigured, match="field-gated reads"):
        hasura_model_resource(
            GroupParentType, model=GroupParent, filterable=[], sortable=[], aggregatable=[],
            insert=False, update=False, delete=False,
        )


def test_declared_sort_alias_rejects_resource_collision() -> None:
    with pytest.raises(ImproperlyConfigured, match="duplicate sortable aliases"):
        hasura_model_resource(
            GroupParentType, model=GroupParent, filterable=[], sortable=[], aggregatable=[],
            sortable_aliases={"label_name": "_other_label"}, insert=False, update=False, delete=False,
        )


def test_permissions_remain_batched_through_guarded_relations(relation_grouping_case: Any) -> None:
    """Selected to-one and reverse to-many permissions never fall back per row."""

    case = relation_grouping_case
    with system_context(reason="test.permissions.nested_rows"):
        for _index in range(50):
            middle = GroupMiddle.objects.create(target=case.alpha)
            parent = GroupParent.objects.create(kind="batch", middle=middle)
            write_relationships([
                RelationshipTuple(to_object_ref(row), "reader", case.alice) for row in (middle, parent)
            ])
    document = """
        query Permissions($limit: Int!) {
          group_parents(where: {kind: {_eq: "batch"}}, order_by: [{id: asc}], limit: $limit) {
            permissions
            middle {
              permissions
              target { permissions }
              parents { permissions }
            }
          }
        }
    """
    result_data(execute_schema(case.schema, document, {"limit": 1}, user=case.alice))
    counts = []
    for limit in (1, 10, 50):
        with (
            patch(
                "angee.graphql.capabilities.permission_annotations",
                side_effect=AssertionError("Unbatched permission check"),
            ),
            CaptureQueriesContext(connection) as queries,
        ):
            result = execute_schema(case.schema, document, {"limit": limit}, user=case.alice)
            rows = result_data(result)["group_parents"]
        assert len(rows) == limit
        assert all(row["permissions"] == ["read"] for row in rows)
        for row in rows:
            if middle := row["middle"]:
                assert middle["permissions"] == ["read"]
                assert middle["parents"] == [{"permissions": ["read"]}]
                assert middle["target"] is None or middle["target"]["permissions"] == ["read"]
        counts.append(len(queries))
    assert counts[0] == counts[1] == counts[2], counts
    print(f"Nested permission queries at 1/10/50 rows: {counts}")


def test_relation_filter_lists_keep_readable_targets_and_hide_unknowns(relation_grouping_case: Any) -> None:
    case = relation_grouping_case
    document = """
      query($readable: String!, $hidden: String!, $unknown: String!) {
        mixed: group_parents(where: {target: {_in: [$readable, $hidden, $unknown]}}) { id }
        excluded: group_parents(where: {target: {_nin: [$readable, $hidden, $unknown]}}) { id }
        hidden: group_parents(where: {target: {_eq: $hidden}}) { id }
        unknown: group_parents(where: {target: {_eq: $unknown}}) { id }
      }
    """
    data = result_data(execute_schema(case.schema, document, {
        "readable": case.alpha.sqid, "hidden": case.beta.sqid, "unknown": "unknown",
    }, user=case.alice))
    assert {row["id"] for row in data["mixed"]} == {str(row.sqid) for row in case.parents[:2]}
    assert {row["id"] for row in data["excluded"]} == {str(row.sqid) for row in case.parents[3:5]}
    assert data["hidden"] == data["unknown"] == []


@pytest.mark.parametrize("operator", ["_eq", "_neq", "_in", "_nin", "_is_null"])
def test_relation_id_predicates_use_redacted_values(relation_grouping_case: Any, operator: str) -> None:
    case = relation_grouping_case
    readable = {str(case.parents[index].sqid) for index in (0, 1, 3, 4)}
    alpha = {str(row.sqid) for row in case.parents[:2]}
    nulls = {str(row.sqid) for row in case.parents} - readable
    values = (True, False) if operator == "_is_null" else (
        str(case.alpha.sqid), str(case.beta.sqid), GroupLabel.public_id_from_pk(999999),
        "malformed", str(case.parents[0].sqid),
    )
    for value in values:
        operand = [value] if operator in {"_in", "_nin"} else value
        document = """query($where: group_parents_bool_exp!) {
            group_parents(where: $where) { id }
            group_parents_aggregate(where: $where) { aggregate { count } }
        }"""
        variables = {"where": {"target": {operator: operand}}}
        result = result_data(execute_schema(case.schema, document, variables, user=case.alice))
        if operator == "_is_null":
            expected = nulls if value else readable
        else:
            matches = alpha if value == str(case.alpha.sqid) else set()
            expected = readable - matches if operator in {"_neq", "_nin"} else matches
        assert {row["id"] for row in result["group_parents"]} == expected, (operator, value)
        assert result["group_parents_aggregate"]["aggregate"]["count"] == len(expected)
        pinned = result_data(execute_schema(
            case.pinned_schema, document.replace("group_parents", "pinned_group_parents"), variables, user=case.bob,
        ))
        assert {row["id"] for row in pinned["pinned_group_parents"]} == expected
        assert pinned["pinned_group_parents_aggregate"]["aggregate"]["count"] == len(expected)


def test_relation_in_resolves_all_operands_with_one_read(relation_grouping_case: Any) -> None:
    case = relation_grouping_case
    ids = [str(row.sqid) for row in (case.alpha, case.beta, case.duplicate_one, case.duplicate_two)]
    document = "query($ids: [String!]!) { group_parents(where: {target: {_in: $ids}}) { id } }"
    # Warm schema/permission caches before measuring database reads.
    result_data(execute_schema(case.schema, document, {"ids": ids[:1]}, user=case.alice))
    with CaptureQueriesContext(connection) as queries:
        data = result_data(execute_schema(case.schema, document, {"ids": ids + ["malformed"]}, user=case.alice))
    assert len(data["group_parents"]) == 4
    reads = [query["sql"] for query in queries if query["sql"].startswith("SELECT")
             and f'FROM "{GroupLabel._meta.db_table}"' in query["sql"]
             and f'FROM "{GroupParent._meta.db_table}"' not in query["sql"]]
    assert len(reads) == 1
    assert " IN (" in reads[0]
