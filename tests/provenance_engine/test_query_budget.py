import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from angee.provenance.engine.context import changeset
from tests.provenance_engine.replay_app.models import Folder


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("count", [1, 50, 200])
def test_batch_history_update_does_not_query_per_record(count):
    with changeset():
        Folder.objects.bulk_create([Folder(name="before") for _ in range(count)])
    with CaptureQueriesContext(connection) as captured:
        with changeset():
            Folder.objects.update(name="after")
    # SQLite may split a 200-row history INSERT for its parameter limit.
    assert len(captured) <= 18
    assert Folder.history.filter(name="after").count() == count
