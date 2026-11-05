"""Delete collection keeps fast paths and resolves generic edge policy once."""

import json
import os
import subprocess
import sys

import pytest
from django.contrib.sessions.models import Session
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.db.models.signals import pre_delete
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import system_context

from angee.iam.models.refs import ProtectedRecordRefMixin
from tests.recordrefdemo.models import CustomColumnEdge, RecordRefNullableEdge, RecordRefTypedTarget


def test_unrelated_model_uses_one_fast_delete_statement() -> None:
    """An app set without generic edges leaves native Session deletes fast."""

    script = """
import json
from django.conf import settings
settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.sessions', 'angee.base'],
    DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
    SECRET_KEY='delete-batch-test',
)
import django
django.setup()
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.db import connection
from django.db.models.deletion import Collector
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
with connection.schema_editor() as editor:
    editor.create_model(ContentType)
    editor.create_model(Session)
for size in (1, 10, 100):
    Session.objects.bulk_create([
        Session(session_key=f'{size}-{index}', session_data='', expire_date=timezone.now())
        for index in range(size)
    ])
    rows = Session.objects.filter(session_key__startswith=f'{size}-')
    fast = Collector(using=connection.alias).can_fast_delete(rows)
    with CaptureQueriesContext(connection) as queries:
        rows.delete()
    print(json.dumps({'size': size, 'fast': fast, 'count': len(queries),
                      'deletes': sum(query['sql'].startswith('DELETE') for query in queries)}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "DATABASE_URL"},
    )
    measurements = {row["size"]: row for row in map(json.loads, result.stdout.splitlines())}
    for size in (1, 10, 100):
        assert measurements[size]["fast"]
        assert measurements[size]["deletes"] == 1
        assert measurements[size]["count"] <= 3


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("size", [1, 10, 100])
def test_rebac_delete_batches_edges_without_reindexing(
    size: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting many protected targets applies one edge policy over the collected ids."""

    with system_context(reason="tests.delete_batch.seed"):
        targets = [RecordRefTypedTarget.objects.create(name=f"target-{index}") for index in range(size)]
        for target in targets:
            RecordRefNullableEdge.objects.create(target=target)

    provider_calls = 0
    original = ProtectedRecordRefMixin.target_index_source

    def source(*, declared: bool = False):
        nonlocal provider_calls
        provider_calls += 1
        return original(declared=declared)

    monkeypatch.setattr(ProtectedRecordRefMixin, "target_index_source", staticmethod(source))
    with system_context(reason="tests.delete_batch.remove"), CaptureQueriesContext(connection) as queries:
        RecordRefTypedTarget.objects.filter(pk__in=[target.pk for target in targets]).delete()

    assert provider_calls == 0
    assert not RecordRefNullableEdge.objects.filter(object_id__in=[target.pk for target in targets]).exists()
    assert len(queries) <= 40


@pytest.mark.django_db(transaction=True)
def test_plain_session_still_protects_a_generic_edge() -> None:
    """The sender-bound fallback keeps the existing opaque-key protection."""

    assert pre_delete.has_listeners(Session)
    session = Session.objects.create(session_key="batch-plain-session", session_data="", expire_date=timezone.now())
    with system_context(reason="tests.delete_batch.plain"):
        edge = CustomColumnEdge.objects.create(target=session)
        with pytest.raises(ProtectedError):
            Session.objects.filter(pk=session.pk).delete()
        edge.delete()
        Session.objects.filter(pk=session.pk).delete()
