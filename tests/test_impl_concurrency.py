"""Two writers on one row holding an implementation key: what the stored row holds at the update wins."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.db import connection, connections, models, transaction
from django.test import override_settings
from django.test.utils import isolate_apps

from angee.base.fields.implementation import ImplClassField
from tests.tables import model_tables, store_column
from tests.test_impl import _BaseImpl

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]

BOTH = {"base": "tests.test_impl._BaseImpl", "refined": "tests.test_impl._RefinedImpl"}


@isolate_apps("tests")
def test_a_stale_rename_never_writes_a_retired_key_back_over_a_key_committed_since() -> None:
    """A plain model (no save gate takes the row lock): the rename lands and the committed key stays."""

    with override_settings(ANGEE_TEST_IMPLS=BOTH):

        class Plain(models.Model):
            adapter = ImplClassField(_BaseImpl)
            label = models.CharField(max_length=40, default="")

            class Meta:
                app_label = "tests"
                db_table = "test_impl_concurrency_plain"

    with model_tables((Plain,)), override_settings(ANGEE_TEST_IMPLS=BOTH):
        row = Plain.objects.create(adapter="base", label="start")
        store_column(Plain, row.pk, "adapter", "retired")
        stale = Plain.objects.get(pk=row.pk)

        def rename() -> Any:
            try:
                with override_settings(ANGEE_TEST_IMPLS=BOTH):
                    stale.label = "renamed by stale"
                    stale.save()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Plain.objects.filter(pk=row.pk).update(adapter="refined")
                renamed = pool.submit(rename)
                # The stale save read `retired` and now waits for this transaction's row lock.
                time.sleep(1)
                assert not renamed.done()
            renamed.result(timeout=30)

        assert Plain.objects.filter(pk=row.pk).values_list("adapter", "label").get() == ("refined", "renamed by stale")
