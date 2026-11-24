"""PostgreSQL interleaving of a brand kit delete with a change of the default kit.

The delete receiver rereads the kit through ``lock_for_delete``, so a delete that
races a change of default parks on the row lock the change holds and judges the
committed flag. Row locks do nothing on SQLite, so this module runs only on
PostgreSQL.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from rebac import system_context

from angee.brandkit.testing.models import BrandKit
from tests.lock_waits import submit, wait_for_lock
from tests.test_brandkit import _default_names, _kit

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def test_a_delete_waits_for_a_change_of_default_and_refuses_the_new_default() -> None:
    """Making B the default locks B; an ORM delete of B read before the change waits, then refuses it."""

    _kit("A")
    b = _kit("B")

    def delete_b() -> None:
        with system_context(reason="brandkit concurrency test delete"):
            BrandKit.objects.get(pk=b.pk).delete()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="brandkit concurrency test default"):
            kit = BrandKit.objects.get(pk=b.pk)
            kit.is_default = True
            kit.save()
            contender, pid = submit(pool, delete_b)
            wait_for_lock(pid, contender)
        with pytest.raises(ValidationError, match="Make another kit the default"):
            contender.result(timeout=10)
    assert _default_names() == ["B"]
    with system_context(reason="brandkit concurrency test read"):
        assert list(BrandKit.objects.order_by("name").values_list("name", flat=True)) == ["A", "B"]
