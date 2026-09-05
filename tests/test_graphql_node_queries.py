"""SQL budgets for narrow, actor-scoped Node label selections."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context

from angee.knowledge import schema as knowledge_schema
from tests.conftest import Page, addon_schema, create_user, execute_schema, result_data, vault_for


def test_page_display_name_fetches_its_title_with_the_rows(knowledge_tables: None) -> None:
    """Selecting only a label must not defer and then refetch each page title."""

    owner = create_user("label-owner")
    outsider = create_user("label-outsider")
    vault = vault_for(owner)
    private_vault = vault_for(outsider)
    with actor_context(owner):
        pages = [Page.objects.create_in(vault, title=f"Page {index:02}") for index in range(25)]
    with actor_context(outsider):
        Page.objects.create_in(private_vault, title="Hidden")

    schema = addon_schema(knowledge_schema.schemas, "public")
    query = """
        query PageLabels($limit: Int!) {
          pages(limit: $limit, order_by: [{title: asc}]) { id display_name }
        }
    """
    table = connection.ops.quote_name(Page._meta.db_table)
    counts = []
    for size in (1, 25):
        with CaptureQueriesContext(connection) as captured:
            rows = result_data(execute_schema(schema, query, {"limit": size}, user=owner))["pages"]
        assert rows == [{"id": str(page.sqid), "display_name": page.title} for page in pages[:size]]
        counts.append(sum(f"FROM {table}" in item["sql"] for item in captured))

    assert counts[0] == counts[1], "Reading label titles must not add a query for each row"
