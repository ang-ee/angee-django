"""PostgreSQL interleaving of two forms saved on one channel and slug.

A form's messages are named by its channel and slug, so the table holds one
form per pair. ``Form.clean`` refuses a second one it can see; two saved at once
cannot see each other, and the constraint decides. Every name is invented.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from rebac import system_context

from angee.webforms.testing.models import Form
from tests.lock_waits import submit, wait_for_lock
from tests.test_webforms_messaging import channel

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL index locks are required."),
]


def test_two_forms_saved_at_once_on_one_channel_and_slug_leave_one() -> None:
    """The second insert waits on the first's uncommitted row and is refused once it commits."""

    target = channel()
    config = {"channel": target.sqid, "slug": "shared"}

    def save(name: str) -> Any:
        with system_context(reason="webforms messaging concurrency test"):
            form = Form(name=name, target_class="message", target_config=config)
            form.full_clean()
            form.save()
            return form

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            first = save("First form")
            contender, pid = submit(pool, lambda: save("Second form"))
            wait_for_lock(pid, contender)
        with pytest.raises(IntegrityError):
            contender.result(timeout=10)

    with system_context(reason="webforms messaging concurrency test"):
        assert list(Form._base_manager.filter(target_class="message").values_list("pk", flat=True)) == [first.pk]
