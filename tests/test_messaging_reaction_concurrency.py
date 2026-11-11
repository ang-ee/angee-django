"""PostgreSQL: concurrent reaction landings on one message serialize on its row and land each reaction once."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, transaction
from rebac import system_context

from angee.messaging.testing.models import Handle, Message, Reaction, Thread
from tests.lock_waits import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def test_a_concurrent_landing_waits_on_the_message_and_skips_the_reaction_it_finds() -> None:
    """The second landing waits on the first's message lock, then sees its row instead of colliding on it."""

    with system_context(reason="test reaction landing race"):
        handle = Handle._base_manager.create(platform="youtube", value="fan")
        message = Message._base_manager.create(thread=Thread._base_manager.create())
        landing = [(message, handle, "like")]
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                assert Reaction.objects.attribute(landing) == 1
                contender, pid = submit(pool, lambda: Reaction.objects.attribute(landing))
                wait_for_lock(pid, contender)
            assert contender.result(timeout=10) == 0
    assert Reaction._base_manager.filter(message=message).count() == 1
