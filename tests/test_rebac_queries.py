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
    ("resource_type", "permission", "expected_queries"),
    (
        # Message read adds the trash manager arm's channel seed read.
        ("messaging/message_edge", "read", 9),
        ("projects/task_relation", "read", 6),
        # The base schema declares no target type: the knowledge end still seeds.
        ("knowledge/record_binding", "read", 7),
        ("projects/task", "comment", 5),
        ("decisions/decision", "act", 5),
        # Its message arm adds the same trash manager seed read.
        ("extraction/extraction", "read", 13),
        ("storage/file", "read", 7),
        ("storage/file_attachment", "read", 8),
        ("projects/link", "read", 6),
        ("messaging/thread", "read", 6),
    ),
)
def test_large_permission_reads_keep_one_application_statement(
    composed_tables: None, resource_type: str, permission: str, expected_queries: int,
) -> None:
    """A compiled scope adds bounded policy reads around one application query."""

    del composed_tables
    model = model_for_resource_type(resource_type)
    assert model is not None, resource_type
    actor = create_user(f"query-{resource_type.replace('/', '-')}")
    queryset = model.objects.with_actor(actor).with_action(permission).scoped()
    with CaptureQueriesContext(connection) as queries:
        list(queryset.values_list("pk", flat=True)[:1])
    # The projection guard runs only when fetching and shares one schema read
    # across columns; reading a filled cache does not run it again. The guard and
    # scope read two revision witnesses around one consumer-row query, plus the
    # compiler's role/constant facts, arrow-target IDs, and recursive seed reads.
    assert len(queries) == expected_queries, (resource_type, permission, queries.captured_queries)
    assert sum(is_rebac_revision_read(item["sql"]) for item in queries) == 2
    table = connection.ops.quote_name(model._meta.db_table)
    assert sum(item["sql"].startswith(f"SELECT {table}.") for item in queries) == 1


@pytest.mark.parametrize(
    ("resource_type", "statement_bound"),
    # A page also seeds its trash manager arm (vault delete) beside its grants.
    (("storage/folder", 8), ("knowledge/page", 9)),
)
def test_recursive_reads_stay_bounded_across_fifty_levels(
    composed_tables: None, resource_type: str, statement_bound: int,
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
        # Include the compiler's policy/seed reads as well as the final query.
        # Bound statements independently of the fifty application rows.
        assert len(queries) <= statement_bound, queries.captured_queries
        assert max(len(query["sql"]) for query in queries) <= 32_768, queries.captured_queries
        return pks

    assert scope_pks(actor) == set()
    expected = set()
    with system_context(reason="tests.rebac.query.depth"):
        if resource_type == "storage/folder":
            storage = Backend.objects.create(slug="depth", label="Depth", backend_class="local")
            drive = Drive.objects.create(slug="depth", name="Depth", prefix="depth", backend=storage, owner=actor)
            parent = None
            for level in range(50):
                parent = Folder.objects.create(drive=drive, parent=parent, name=f"level-{level}")
                expected.add(parent.pk)
        elif resource_type == "knowledge/page":
            vault = Vault.objects.create(name="Depth", owner=actor)
            parent = None
            for level in range(50):
                parent = Page.objects.create(vault=vault, parent=parent, title=f"level-{level}")
                expected.add(parent.pk)
    assert scope_pks(actor) == expected
    assert scope_pks(create_user(f"outsider-{resource_type.replace('/', '-')}")) == set()
