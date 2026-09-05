"""Native SQL budgets for narrow File label selections."""

from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION
from angee.storage import schema as storage_schema
from tests.conftest import Drive, File, addon_schema, create_user, execute_schema, result_data
from tests.test_storage import drive as drive
from tests.test_storage import storage_tables as storage_tables


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("schema_name", ["public", "console"])
def test_file_labels_fetch_title_and_filename_with_authorized_rows(drive: Any, schema_name: str) -> None:
    """Title and filename fallback stay batched without exposing another owner's file."""

    outsider = create_user(f"file-label-outsider-{schema_name}")
    with system_context(reason="test.storage.label.seed"):
        files = [
            File.objects.create(
                drive=drive,
                created_by=drive.alice,
                filename=f"file-{index:02}.txt",
                title=f"File title {index:02}" if index % 2 else "",
                content_hash=f"{index:064x}",
            )
            for index in range(25)
        ]
        private_drive = Drive.objects.create(
            backend=drive.backend,
            slug="private-labels",
            name="Private labels",
            created_by=outsider,
        )
        hidden = File.objects.create(
            drive=private_drive,
            created_by=outsider,
            filename="00-hidden.txt",
            title="Hidden",
            content_hash="f" * 64,
        )
    expected = [{"id": str(row.sqid), "display_name": str(row)} for row in files]
    assert expected[0]["display_name"] == files[0].filename
    assert expected[1]["display_name"] == files[1].title
    schema = addon_schema(storage_schema.schemas, schema_name)
    assert schema._schema.get_type("FileType").fields["display_name"].description == NODE_DISPLAY_NAME_DESCRIPTION
    query = """
        query FileLabels($limit: Int!) {
          files(limit: $limit, order_by: [{filename: asc}]) { id display_name }
        }
    """
    counts = []
    for size in (1, 25):
        with CaptureQueriesContext(connection) as captured:
            rows = result_data(execute_schema(schema, query, {"limit": size}, user=drive.alice))["files"]
        assert rows == expected[:size]
        counts.append(len(captured))
    assert counts[0] == counts[1], f"File label queries grew with the page: {counts}"

    detail = "query FileLabel($id: String!) { files_by_pk(id: $id) { id display_name } }"
    for row, expected_label in ((files[0], expected[0]), (files[1], expected[1]), (hidden, None)):
        assert result_data(execute_schema(schema, detail, {"id": str(row.sqid)}, user=drive.alice)) == {
            "files_by_pk": expected_label,
        }
