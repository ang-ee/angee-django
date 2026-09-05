"""Draft native regressions for actor-scoped related grouping axes.

Promote this file to ``angee/tests/`` after the framework consumes the
published aggregate/Hasura expression seam. It intentionally exercises only
the public ``hasura_model_resource`` contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import strawberry_django
from django.db import connection, models
from rebac import (
    RelationshipTuple,
    SubjectRef,
    system_context,
    to_object_ref,
    write_relationships,
)
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.schema import parse_zed
from strawberry import auto

from angee.base.models import AngeeDataModel
from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import (
    SchemaAddon,
    _clear_model_tables,
    _create_missing_tables,
    execute_schema,
    result_data,
)


class GroupLabel(AngeeDataModel):
    """Protected target whose scalar fields must follow its own read scope."""

    sqid_prefix = "grl_"
    external_key = models.CharField(max_length=32, unique=True)
    display_name = models.CharField(max_length=64)
    rank = models.IntegerField()

    class Meta(AngeeDataModel.Meta):
        abstract = False
        app_label = "tests"
        rebac_resource_type = "tests/group_label"
        rebac_id_attr = "sqid"


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
        app_label = "tests"
        rebac_resource_type = "tests/group_middle"
        rebac_id_attr = "sqid"


class PlainGroupLabel(models.Model):
    """Permission-naive Django target retained as a negative control."""

    display_name = models.CharField(max_length=64)

    class Meta:
        app_label = "tests"


class GroupParent(AngeeDataModel):
    """Readable parent spanning direct, scalar, nested, and plain axes."""

    sqid_prefix = "grp_"
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
        related_name="+",
    )
    plain = models.ForeignKey(
        PlainGroupLabel,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta(AngeeDataModel.Meta):
        abstract = False
        app_label = "tests"
        rebac_resource_type = "tests/group_parent"
        rebac_id_attr = "sqid"


@strawberry_django.type(GroupParent)
class GroupParentType(AngeeNode):
    kind: auto
    amount: auto


@pytest.fixture
def relation_grouping_case(transactional_db: None):
    """Build one schema and two actors over stable parent group identities."""

    del transactional_db
    reset_backend()
    active = backend()
    assert isinstance(active, LocalBackend)
    active.set_schema(
        parse_zed(
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
    )
    models_in_order = (GroupLabel, GroupMiddle, PlainGroupLabel, GroupParent)
    created = _create_missing_tables(models_in_order)
    try:
        alice = SubjectRef.of("auth/user", "alice")
        bob = SubjectRef.of("auth/user", "bob")
        resource = hasura_model_resource(
            GroupParentType,
            model=GroupParent,
            name="group_parents",
            filterable=["kind"],
            sortable=["kind"],
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
            filterable=["kind"],
            sortable=["kind"],
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
        )
    finally:
        _clear_model_tables(models_in_order)
        if created:
            with connection.schema_editor() as editor:
                for model in reversed(created):
                    editor.delete_model(model)
        reset_backend()


def _query(case: Any, actor: SubjectRef, document: str) -> dict[str, Any]:
    return result_data(execute_schema(case.schema, document, user=actor))


def test_related_axes_follow_actor_without_changing_group_identity(
    relation_grouping_case: Any,
) -> None:
    """Labels/scalars redact per actor while identity/count semantics stay stable."""

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

    assert alice["exact"] == bob["exact"] == 5
    assert alice["having_exact"] == bob["having_exact"] == 1

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
        case.beta.sqid: (None, 1),
        case.duplicate_one.sqid: ("Duplicate", 1),
        case.duplicate_two.sqid: ("Duplicate", 1),
        None: (None, 1),
    }
    assert bob_groups == {
        case.alpha.sqid: (None, 2),
        case.beta.sqid: ("Beta", 1),
        case.duplicate_one.sqid: ("Duplicate", 1),
        case.duplicate_two.sqid: ("Duplicate", 1),
        None: (None, 1),
    }
    assert alice["having"] == [
        {
            "key": {
                "target_id": case.alpha.sqid,
                "target__display_name": "Alpha",
            },
            "aggregate": {"count": 2},
        }
    ]
    assert bob["having"] == [
        {
            "key": {
                "target_id": case.alpha.sqid,
                "target__display_name": None,
            },
            "aggregate": {"count": 2},
        }
    ]
    alice_ranks = {row["key"]["metric_target_id"]: row["key"]["metric_target__rank"] for row in alice["ranks"]}
    bob_ranks = {row["key"]["metric_target_id"]: row["key"]["metric_target__rank"] for row in bob["ranks"]}
    assert alice_ranks[case.alpha.sqid] == 10
    assert alice_ranks[case.beta.sqid] is None
    assert bob_ranks[case.alpha.sqid] is None
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
        case.hidden_middle.sqid: None,
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
    assert labels[case.beta.sqid] is None
