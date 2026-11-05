"""Consumer query shape for the largest composed permission plans."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import system_context
from rebac.resources import model_for_resource_type

from tests.conftest import Backend, Drive, Folder, Page, Vault, create_user
from tests.extraction_models import Extraction as _Extraction  # noqa: F401 -- register source model
from tests.queries import is_rebac_revision_read


@pytest.mark.parametrize(
    ("resource_type", "permission"),
    (
        ("messaging/message_edge", "read"),
        ("projects/task_relation", "read"),
        ("knowledge/record_binding", "read"),
        ("projects/task", "comment"),
        ("decisions/decision", "act"),
        ("extraction/extraction", "read"),
        ("storage/file", "read"),
        ("storage/file_attachment", "read"),
        ("projects/link", "read"),
        ("messaging/thread", "read"),
    ),
)
def test_large_permission_reads_keep_one_application_statement(
    composed_tables: None,
    resource_type: str,
    permission: str,
) -> None:
    """A compiled scope adds bounded policy reads around one application query."""

    del composed_tables
    model = model_for_resource_type(resource_type)
    assert model is not None, resource_type
    actor = create_user(f"query-{resource_type.replace('/', '-')}")
    counts = []
    for limit in (1, 50):
        queryset = model.objects.with_actor(actor).with_action(permission).scoped()
        with CaptureQueriesContext(connection) as queries:
            list(queryset.values_list("pk", flat=True)[:limit])
        # Recovery REBAC folds arrow targets and recursive seeds into the
        # application SELECT. The revision and actor-set reads stay bounded.
        assert sum(is_rebac_revision_read(item["sql"]) for item in queries) == 1
        assert sum(item["sql"].startswith("SELECT DISTINCT ") for item in queries) == 1
        table = connection.ops.quote_name(model._meta.db_table)
        application = [item["sql"] for item in queries if item["sql"].startswith(f"SELECT {table}.")]
        assert len(application) == 1, (resource_type, permission, queries.captured_queries)
        if resource_type == "knowledge/record_binding":
            # composed_tables syncs only the base schema: target_read is nil,
            # so read = (page->read + vault->read) & nil is constant false
            # (knowledge/permissions.zed). Composed extends add target arms.
            assert " WHERE (1 = 0)" in application[0] and "EXISTS(" not in application[0]
        else:
            assert "EXISTS(" in application[0]
        counts.append(len(queries))
    assert counts == [3, 3], (resource_type, permission, counts)


@pytest.mark.postgresql
@pytest.mark.parametrize(
    "resource_type",
    ("storage/folder", "knowledge/page"),
)
def test_recursive_reads_stay_bounded_across_nineteen_levels(
    composed_tables: None,
    resource_type: str,
) -> None:
    """Hierarchy reads stay correct and bounded as predecided row keys grow."""

    del composed_tables
    actor = create_user(f"depth-{resource_type.replace('/', '-')}")
    model = model_for_resource_type(resource_type)
    assert model is not None
    rows = model.objects.all()
    if resource_type == "storage/folder":
        # Each user also owns a virtual Trash folder outside this hierarchy.
        rows = rows.filter(is_virtual=False)

    def scope_pks(viewer) -> set:
        with CaptureQueriesContext(connection) as queries:
            pks = set(rows.with_actor(viewer).with_action("read").scoped().values_list("pk", flat=True))
        # Include native policy reads as well as the final query.
        # Both plans measured four statements at depths 0, 1, 5, 10 and 19
        # on PostgreSQL and SQLite, independent of the application row count.
        assert len(queries) == 4, queries.captured_queries
        assert max(len(query["sql"]) for query in queries) <= 32_768, queries.captured_queries
        return pks

    assert scope_pks(actor) == set()
    expected = set()
    with system_context(reason="tests.rebac.query.depth"):
        # HierarchyMixin.path holds at most 19 padded segments in varchar(255).
        if resource_type == "storage/folder":
            storage = Backend.objects.create(slug="depth", label="Depth", backend_class="local")
            drive = Drive.objects.create(slug="depth", name="Depth", prefix="depth", backend=storage, owner=actor)
            parent = None
            for level in range(19):
                parent = Folder.objects.create(drive=drive, parent=parent, name=f"level-{level}")
                expected.add(parent.pk)
        elif resource_type == "knowledge/page":
            vault = Vault.objects.create(name="Depth", owner=actor)
            parent = None
            for level in range(19):
                parent = Page.objects.create(vault=vault, parent=parent, title=f"level-{level}")
                expected.add(parent.pk)
    assert scope_pks(actor) == expected
    assert scope_pks(create_user(f"outsider-{resource_type.replace('/', '-')}")) == set()
