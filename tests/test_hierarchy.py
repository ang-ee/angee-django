"""Behaviour of :class:`~angee.base.mixins.HierarchyMixin` (F-hier, foundation R3).

The mixin gives a self-parented model a maintained materialized ``path`` so
subtree membership is a database prefix test, not a client-side ``parent`` walk.
These cover the contract the inventory ``Location`` tree (the first hard
consumer) depends on: inclusive ``subtree_of`` / exclusive ``ancestors_of``,
padded-segment prefix correctness (pk ``1`` never prefix-matches pk ``11``), the
single-``UPDATE`` reparent cascade, cycle and cross-company rejection, the derived
create-path shape, and the pattern-ops prefix index. Reads run under
``system_context`` because the demo models use the REBAC-aware manager in
strict mode, exactly as the real ``Location`` reads do server-side.
"""

from __future__ import annotations

import os

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from tests.hierdemo.models import HierNode, ScopedHierNode
from tests.iam_models import Company


def _tree() -> dict[str, HierNode]:
    """Build and return a small tree ``A → B → {C, D}``, ``C → E``."""

    a = HierNode.objects.create(name="A")
    b = HierNode.objects.create(name="B", parent=a)
    c = HierNode.objects.create(name="C", parent=b)
    d = HierNode.objects.create(name="D", parent=b)
    e = HierNode.objects.create(name="E", parent=c)
    return {"A": a, "B": b, "C": c, "D": d, "E": e}


@pytest.mark.django_db
def test_subtree_of_is_inclusive() -> None:
    """``subtree_of(node)`` returns the node itself plus every descendant."""

    with system_context(reason="test hierarchy subtree"):
        nodes = _tree()
        subtree = set(HierNode.objects.subtree_of(nodes["B"]).values_list("name", flat=True))
    assert subtree == {"B", "C", "D", "E"}


@pytest.mark.django_db
def test_ancestors_of_is_exclusive_of_self() -> None:
    """``ancestors_of(node)`` returns proper ancestors only — never the node."""

    with system_context(reason="test hierarchy ancestors"):
        nodes = _tree()
        ancestors = list(HierNode.objects.ancestors_of(nodes["E"]).order_by("path").values_list("name", flat=True))
        root_ancestors = list(HierNode.objects.ancestors_of(nodes["A"]).values_list("name", flat=True))
    assert ancestors == ["A", "B", "C"]
    assert root_ancestors == []


@pytest.mark.django_db
def test_create_under_parent_derives_path_shape() -> None:
    """A created node's path is the parent path plus a zero-padded pk segment."""

    with system_context(reason="test hierarchy path shape"):
        root = HierNode.objects.create(name="root")
        child = HierNode.objects.create(name="child", parent=root)
    width = HierNode.path_segment_width
    assert root.path == f"/{root.pk:0{width}d}/"
    assert child.path == f"{root.path}{child.pk:0{width}d}/"
    assert child.path.startswith(root.path)


@pytest.mark.django_db
def test_is_within_is_inclusive_and_descendant_aware_without_queries() -> None:
    """``is_within`` is the instance form of the maintained path-prefix test."""

    with system_context(reason="test hierarchy is_within"):
        nodes = _tree()

    with CaptureQueriesContext(connection) as ctx:
        assert nodes["B"].is_within(nodes["B"]) is True
        assert nodes["E"].is_within(nodes["B"]) is True
        assert nodes["D"].is_within(nodes["C"]) is False
        assert nodes["A"].is_within(nodes["B"]) is False
    assert ctx.captured_queries == []


def test_is_within_empty_paths_match_nothing() -> None:
    """Unmaterialized paths never prefix-match the whole hierarchy."""

    materialized = HierNode(name="materialized")
    materialized.path = "/000000000001/"
    empty_self = HierNode(name="empty-self")
    empty_other = HierNode(name="empty-other")

    assert empty_self.is_within(materialized) is False
    assert materialized.is_within(empty_other) is False
    assert empty_self.is_within(empty_other) is False


@pytest.mark.django_db
def test_padded_segments_never_false_prefix_match() -> None:
    """A root whose pk digits prefix another's (1 vs 11) shares no subtree.

    Twelve sequential roots include pks whose decimal strings prefix later ones
    (``1`` → ``10``/``11``/``12``); the terminal delimiter and zero-padding keep
    ``subtree_of`` of the lowest-pk root to itself alone, and a real child is
    still matched.
    """

    with system_context(reason="test hierarchy prefix"):
        roots = [HierNode.objects.create(name=f"r{i}") for i in range(12)]
        low = roots[0]
        child = HierNode.objects.create(name="child", parent=low)
        subtree = set(HierNode.objects.subtree_of(low).values_list("pk", flat=True))
    # The lowest-pk root (pk=1) must not swallow pk 10/11/12 by string prefix.
    assert subtree == {low.pk, child.pk}


@pytest.mark.django_db
def test_reparent_cascades_subtree_in_one_update() -> None:
    """Reparenting rewrites the whole subtree's paths in a single bulk UPDATE."""

    with system_context(reason="test hierarchy reparent"):
        nodes = _tree()
        home = HierNode.objects.create(name="H")
        with CaptureQueriesContext(connection) as ctx:
            nodes["B"].parent = home
            nodes["B"].save()

        cascade = [
            query["sql"]
            for query in ctx.captured_queries
            if query["sql"].lstrip().upper().startswith("UPDATE") and "REPLACE" in query["sql"].upper()
        ]
        assert len(cascade) == 1, "the whole subtree must cascade in exactly one UPDATE"

        for node in nodes.values():
            node.refresh_from_db()
    # Every descendant now hangs under the new home; the old root keeps nothing.
    assert nodes["B"].path.startswith(home.path)
    assert nodes["C"].path.startswith(nodes["B"].path)
    assert nodes["E"].path.startswith(nodes["C"].path)
    with system_context(reason="test hierarchy reparent read"):
        assert set(HierNode.objects.subtree_of(home).values_list("name", flat=True)) == {"H", "B", "C", "D", "E"}
        assert set(HierNode.objects.subtree_of(nodes["A"]).values_list("name", flat=True)) == {"A"}


@pytest.mark.django_db
def test_reparent_cascade_query_count_is_subtree_size_independent() -> None:
    """The cascade cost is O(1) in queries — a bulk UPDATE, never a per-row walk."""

    def reparent_count(descendants: int) -> int:
        root = HierNode.objects.create(name="root")
        leaf = HierNode.objects.create(name="branch", parent=root)
        for index in range(descendants):
            HierNode.objects.create(name=f"leaf{index}", parent=leaf)
        home = HierNode.objects.create(name="home")
        with CaptureQueriesContext(connection) as ctx:
            leaf.parent = home
            leaf.save()
        return len(ctx.captured_queries)

    with system_context(reason="test hierarchy reparent cost"):
        small = reparent_count(2)
        large = reparent_count(20)
    assert small == large


@pytest.mark.django_db
def test_reparent_persists_move_under_partial_update_fields() -> None:
    """A reparent saved with a partial ``update_fields`` still persists the move.

    The moved ``parent`` and the derived ``path`` must ride the write even when
    the caller named neither, so the FK and the cascaded subtree paths agree.
    """

    with system_context(reason="test hierarchy partial update"):
        nodes = _tree()
        home = HierNode.objects.create(name="H")
        nodes["B"].name = "B-renamed"
        nodes["B"].parent = home
        nodes["B"].save(update_fields=["name"])

        moved = HierNode.objects.get(pk=nodes["B"].pk)
        child = HierNode.objects.get(pk=nodes["C"].pk)
    assert moved.parent_id == home.pk
    assert moved.path.startswith(home.path)
    assert child.path.startswith(moved.path)


@pytest.mark.django_db
def test_deferred_parent_load_stays_single_query() -> None:
    """``.only()`` excluding ``parent`` does not trigger a per-row baseline fetch."""

    with system_context(reason="test hierarchy deferred load"):
        _tree()
        with CaptureQueriesContext(connection) as ctx:
            names = [node.name for node in HierNode.objects.only("name")]
    assert len(names) == 5
    assert len(ctx.captured_queries) == 1


@pytest.mark.django_db
def test_deferred_load_reparent_still_repaths() -> None:
    """A reparent on a ``.only()``-loaded row (no baseline) still rewrites its path.

    A deferred load excludes ``parent``, so the instance carries no reparent
    baseline. Without fetching the committed ``parent_id`` the save would compare
    the moved FK to itself, miss the move, and strand the subtree under the old
    parent. The committed-parent fetch closes that gap.
    """

    with system_context(reason="test hierarchy deferred reparent"):
        nodes = _tree()
        home = HierNode.objects.create(name="H")
        deferred = HierNode.objects.only("name").get(pk=nodes["B"].pk)
        deferred.parent = home
        deferred.save()

        moved = HierNode.objects.get(pk=nodes["B"].pk)
        child = HierNode.objects.get(pk=nodes["C"].pk)
    assert moved.parent_id == home.pk
    assert moved.path.startswith(home.path)
    assert child.path.startswith(moved.path)


@pytest.mark.django_db
def test_create_under_reparented_parent_uses_committed_path() -> None:
    """A create derives the child prefix from the parent's committed path.

    A parent reparented behind a stale in-memory instance must not leak its old
    prefix into a child created under it: ``_save_created`` re-reads the parent's
    committed path before deriving the child segment.
    """

    width = HierNode.path_segment_width
    with system_context(reason="test hierarchy create under reparent"):
        home = HierNode.objects.create(name="home")
        parent = HierNode.objects.create(name="parent")  # a root
        stale_path = parent.path
        committed_path = f"{home.path}{parent.pk:0{width}d}/"
        HierNode.objects.filter(pk=parent.pk).update(path=committed_path)
        # `parent` still carries its stale root path in memory.
        child = HierNode.objects.create(name="child", parent=parent)
    assert not child.path.startswith(stale_path)
    assert child.path == f"{committed_path}{child.pk:0{width}d}/"


@pytest.mark.django_db
def test_refresh_from_db_resyncs_the_reparent_baseline() -> None:
    """A refreshed row is not misclassified as reparented on its next save.

    An external queryset ``update`` moves the FK behind the instance's back;
    after ``refresh_from_db`` a plain field save must stay a plain save — no
    forced parent/path write, no subtree cascade.
    """

    with system_context(reason="test hierarchy refresh baseline"):
        nodes = _tree()
        home = HierNode.objects.create(name="H")
        HierNode.objects.filter(pk=nodes["B"].pk).update(parent=home)

        node = nodes["B"]
        node.refresh_from_db()
        with CaptureQueriesContext(connection) as ctx:
            node.name = "B-renamed"
            node.save(update_fields=["name"])
    cascades = [query["sql"] for query in ctx.captured_queries if "REPLACE" in query["sql"].upper()]
    assert cascades == []


@pytest.mark.django_db
def test_empty_path_materialization_never_cascades() -> None:
    """Rematerializing an unset ``path`` is self-only — no ``LIKE '%'`` rewrite.

    An empty old path would prefix-match every row in the table; the save must
    rewrite only its own row and leave every other tree untouched.
    """

    with system_context(reason="test hierarchy empty path"):
        nodes = _tree()
        other_root_path = nodes["A"].path
        stray = HierNode.objects.create(name="stray")
        HierNode.objects.filter(pk=stray.pk).update(path="")

        stray = HierNode.objects.get(pk=stray.pk)
        with CaptureQueriesContext(connection) as ctx:
            stray.save()
        untouched = HierNode.objects.get(pk=nodes["A"].pk)
    cascades = [query["sql"] for query in ctx.captured_queries if "REPLACE" in query["sql"].upper()]
    assert cascades == []
    assert stray.path == f"/{stray.pk:0{HierNode.path_segment_width}d}/"
    assert untouched.path == other_root_path


@pytest.mark.django_db
def test_reparent_into_own_subtree_is_rejected_as_cycle() -> None:
    """A node cannot become its own descendant — a field-named ValidationError."""

    with system_context(reason="test hierarchy cycle"):
        nodes = _tree()
        nodes["A"].parent = nodes["E"]  # E is inside A's own subtree
        with pytest.raises(ValidationError) as excinfo:
            nodes["A"].save()
    assert "parent" in excinfo.value.message_dict


@pytest.mark.django_db
def test_self_parent_is_rejected_as_cycle() -> None:
    """A node cannot be its own parent."""

    with system_context(reason="test hierarchy self parent"):
        node = HierNode.objects.create(name="node")
        node.parent = node
        with pytest.raises(ValidationError) as excinfo:
            node.save()
    assert "parent" in excinfo.value.message_dict


@pytest.mark.django_db
def test_cross_company_parent_is_rejected() -> None:
    """A company-scoped node rejects a parent belonging to another company."""

    with system_context(reason="test hierarchy company boundary"):
        company_a = Company.objects.create(name="Company A")
        company_b = Company.objects.create(name="Company B")
        root_a = ScopedHierNode.objects.create(name="root-a", company=company_a)
        node_b = ScopedHierNode.objects.create(name="node-b", company=company_b)

        node_b.parent = root_a
        with pytest.raises(ValidationError) as excinfo:
            node_b.save()
    assert "parent" in excinfo.value.message_dict


@pytest.mark.django_db
def test_same_company_parent_is_accepted() -> None:
    """A same-company parent is accepted and the child path derives from it."""

    with system_context(reason="test hierarchy same company"):
        company = Company.objects.create(name="Company")
        root = ScopedHierNode.objects.create(name="root", company=company)
        child = ScopedHierNode.objects.create(name="child", parent=root, company=company)
    assert child.path.startswith(root.path)
    assert child.company_id == company.pk


def test_path_index_declares_pattern_ops() -> None:
    """The concrete model inherits a pattern-ops index over ``path``.

    The operator class rides ``Index.deconstruct`` into the migration, so a
    declared ``varchar_pattern_ops`` here is what makes PostgreSQL serve the
    prefix ``LIKE`` from the index (SQLite drops the class and indexes plainly).
    """

    for model in (HierNode, ScopedHierNode):
        path_indexes = [
            index
            for index in model._meta.indexes
            if index.fields == ["path"] and getattr(index, "opclasses", ()) == ("varchar_pattern_ops",)
        ]
        assert len(path_indexes) == 1, f"{model.__name__} must carry one path pattern-ops index"


@pytest.mark.skipif(
    os.environ.get("DATABASE_URL", "").split(":", 1)[0] not in {"postgres", "postgresql"},
    reason="requires DATABASE_URL backed by PostgreSQL",
)
@pytest.mark.django_db
def test_path_index_carries_operator_class_on_postgres() -> None:
    """On PostgreSQL the emitted ``path`` index carries the operator class."""

    if connection.vendor != "postgresql":
        pytest.skip("active Django connection is not PostgreSQL")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = %s AND indexdef LIKE %s",
            ["test_hierdemo_node", "%varchar_pattern_ops%"],
        )
        assert cursor.fetchone() is not None
