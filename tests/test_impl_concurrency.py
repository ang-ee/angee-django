"""Two writers on one row holding an implementation key: what the stored row holds at the update wins."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, connections, models, transaction
from django.test import override_settings
from django.test.utils import isolate_apps
from rebac import system_context

from angee.base.fields.implementation import ImplClassField
from tests.conftest import VcsBridge, make_integration
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


def _retired_bridge(slug: str) -> Any:
    bridge = make_integration(slug, model=VcsBridge, backend_class="stub")
    store_column(VcsBridge, bridge.pk, "backend_class", "retired")
    with system_context(reason="test.impl.concurrency.load"):
        return VcsBridge._base_manager.get(pk=bridge.pk), VcsBridge._base_manager.get(pk=bridge.pk)


def _in_thread(write: Any) -> Any:
    def run() -> Any:
        try:
            with system_context(reason="test.impl.concurrency.other"):
                return write()
        finally:
            connections.close_all()
    return run


def test_of_two_repairs_at_once_one_wins_and_the_other_is_refused_as_create_only() -> None:
    first, second = _retired_bridge("two-repairs")

    def repair_again() -> str:
        second.backend_class = "local"
        try:
            second.save()
        except ValidationError as error:
            return str(error.message_dict)
        return "saved"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="test.impl.concurrency.first"):
            first.backend_class = "stub"
            first.save()
            other = pool.submit(_in_thread(repair_again))
            time.sleep(1)
            assert not other.done()
        outcome = other.result(timeout=30)

    assert outcome == "{'backend_class': ['Implementation selection is create-only.']}"
    with system_context(reason="test.impl.concurrency.check"):
        assert VcsBridge._base_manager.values_list("backend_class", flat=True).get(pk=first.pk) == "stub"


def test_a_repair_and_a_stale_rename_at_once_both_land() -> None:
    repairer, stale = _retired_bridge("repair-and-rename")

    def rename() -> None:
        stale.display_name = "Renamed while repaired"
        stale.save()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="test.impl.concurrency.repair"):
            repairer.backend_class = "stub"
            repairer.save()
            renamed = pool.submit(_in_thread(rename))
            time.sleep(1)
            assert not renamed.done()
        renamed.result(timeout=30)

    with system_context(reason="test.impl.concurrency.check"):
        stored = VcsBridge._base_manager.values_list("backend_class", "display_name").get(pk=repairer.pk)
    assert stored == ("stub", "Renamed while repaired")
