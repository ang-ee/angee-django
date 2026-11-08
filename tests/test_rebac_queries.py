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
    composed_tables: None, resource_type: str, permission: str,
) -> None:
    """A compiled scope adds one revision witness to one application query."""

    del composed_tables
    model = model_for_resource_type(resource_type)
    assert model is not None, resource_type
    actor = create_user(f"query-{resource_type.replace('/', '-')}")
    queryset = model.objects.with_actor(actor).with_action(permission).scoped()
    with CaptureQueriesContext(connection) as queries:
        list(queryset.values_list("pk", flat=True)[:1])
    # The projection guard runs only when fetching and shares one schema read
    # across columns; reading a filled cache does not run it again.
    # 0.31 compiles scopes inline (4-12 -> 2): role/constant facts, arrow targets and seeds are predicates.
    assert len(queries) == 2, (resource_type, permission, queries.captured_queries)
    assert sum(is_rebac_revision_read(item["sql"]) for item in queries) == 1
    table = connection.ops.quote_name(model._meta.db_table)
    assert sum(item["sql"].startswith(f"SELECT {table}.") for item in queries) == 1


@pytest.mark.parametrize(
    ("resource_type", "sql_bound"),
    # 0.31 recursion is a CTE inside the statement (32_768 -> measured 19_421 and 25_462 bytes, plus id width).
    (("storage/folder", 20_480), ("knowledge/page", 26_624)),
)
def test_recursive_reads_stay_bounded_across_nineteen_levels(
    composed_tables: None, resource_type: str, sql_bound: int,
) -> None:
    """Hierarchy reads stay correct, with statements and SQL size independent of the rows."""

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
        # 0.31 (8/9 -> 3): the scope's and the fetch guard's revision witnesses, then one inline query.
        assert len(queries) == 3, queries.captured_queries
        assert max(len(query["sql"]) for query in queries) <= sql_bound, queries.captured_queries
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
