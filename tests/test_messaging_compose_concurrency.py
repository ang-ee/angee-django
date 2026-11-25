"""PostgreSQL: two composes of one keyed message race on the idempotency claim and land one message."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.db import connection, transaction
from django.test import override_settings
from rebac import to_subject_ref

from angee.messaging.testing.models import Channel, Message
from angee.provenance.context import changeset_model
from tests.conftest import make_integration
from tests.lock_waits import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


@override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
def test_a_second_keyed_compose_waits_on_the_first_claim_and_replays_its_message() -> None:
    """The contender has no earlier lock to wait on: it parks on the claim and then returns the first's message."""

    channel: Any = make_integration(
        "compose-race", model=Channel, backend_class="email", config={"from_email": "Desk <desk@example.test>"},
    )
    owner = channel.owner

    def compose() -> Any:
        return Message.objects.compose(
            channel=channel, to=["reader@example.test"], subject="Once", text="Once.", actor=owner,
            idempotency_key="k",
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            first = compose()
            contender, pid = submit(pool, compose)
            wait_for_lock(pid, contender)
        second = contender.result(timeout=10)
    assert first.pk == second.pk
    assert Message._base_manager.filter(direction="outbound").count() == 1
    claims = changeset_model()._base_manager.filter(scope=f"messaging.compose:{to_subject_ref(owner)}", key="k")
    assert claims.count() == 1
