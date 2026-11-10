"""PostgreSQL first-answer concurrency through independent connections."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, transaction
from rebac import system_context

from angee.base.models.locks import StaleRevisionError
from angee.decisions.testing.drivers import seed_decision
from angee.decisions.testing.models import Decision
from tests.conftest import create_user, vault_for
from tests.lock_waits import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def test_competing_assignees_cannot_replace_the_first_answer(composed_tables):
    requester = create_user("concurrent-requester")
    reviewer = create_user("concurrent-reviewer")
    subject = vault_for(requester)
    from rebac import RelationshipTuple, to_object_ref, to_subject_ref, write_relationships

    write_relationships([RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(reviewer))])
    decision = seed_decision(actor=requester, assignees=(requester, reviewer), reference=subject, requester=None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="test.answer_race"):
            Decision.system_queryset().select_for_update().get(pk=decision.pk)
            contender, pid = submit(
                pool,
                lambda: decision.decide(actor=reviewer, chosen=["reject"], revision=decision.revision),
            )
            wait_for_lock(pid, contender)
            decision.decide(actor=requester, chosen=["accept"], revision=decision.revision)
        with pytest.raises(StaleRevisionError):
            contender.result(timeout=10)
    retained = Decision.system_queryset().get(pk=decision.pk)
    assert retained.verdict == ["accept"] and retained.answered_by_id == requester.pk
