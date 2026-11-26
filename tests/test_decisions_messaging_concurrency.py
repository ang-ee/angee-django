"""PostgreSQL races of two runs mailing one person: the ledger's unique key decides, and the loser's mail rolls back."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest
from django.db import connection, transaction
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.decisions.testing.drivers import seed_decision
from angee.decisions_messaging.models import DecisionNoticeOccasion
from angee.decisions_messaging.testing.drivers import addressed_user, install_decision_mail
from angee.decisions_messaging.testing.models import DecisionNotice
from angee.messaging.testing.models import Message, Recipient
from tests.conftest import vault_for
from tests.lock_waits import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL unique-index waits are required."),
]

READ = "tests.decisions_messaging.race.read"


@pytest.fixture
def race(settings: Any) -> SimpleNamespace:
    """One question whose only decider is its assignee, with system mail on an in-memory transport."""

    settings.MAILERS = {"transport": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
    settings.DEFAULT_FROM_EMAIL = "Angee <noreply@example.test>"
    settings.ANGEE_PUBLIC_ORIGIN = "https://app.example.test"
    install_decision_mail()
    requester, reviewer = addressed_user("race-requester"), addressed_user("race-reviewer")
    subject = vault_for(requester, name="Raced order")
    write_relationships([RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(reviewer))])
    question = seed_decision(actor=requester, assignees=(reviewer,), reference=subject)
    return SimpleNamespace(reviewer=reviewer, question=question)


def mailed_to(user: Any) -> list[Any]:
    """The notices about the question naming ``user``, and the messages recorded to their address."""

    with system_context(reason=READ):
        return [
            list(DecisionNotice.objects.filter(user=user).values_list("message_id", flat=True)),
            list(Recipient._base_manager.filter(handle__value=user.email).values_list("message_id", flat=True)),
        ]


def contend(race: Any, *, holder_commits: bool) -> int:
    """Hold the reviewer's notice uncommitted while another run mails them; return what that run mailed."""

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            with system_context(reason="tests.decisions_messaging.race.holder"):
                DecisionNotice.objects.create(
                    decision=race.question, user=race.reviewer, occasion=DecisionNoticeOccasion.ASKED,
                )
            contender, pid = submit(
                pool, lambda: DecisionNotice.objects.mail_decision(race.question.pk, occasion="asked"),
            )
            wait_for_lock(pid, contender)
            if not holder_commits:
                transaction.set_rollback(True)
        return contender.result(timeout=60)


def test_a_notice_another_run_wrote_first_wins_and_this_runs_mail_rolls_back(race: Any) -> None:
    assert contend(race, holder_commits=True) == 0
    notices, messages = mailed_to(race.reviewer)
    assert notices == [None] and messages == []
    with system_context(reason=READ):
        assert not Message._base_manager.exists()


def test_a_notice_another_run_gave_up_leaves_this_run_to_mail(race: Any) -> None:
    assert contend(race, holder_commits=False) == 1
    notices, messages = mailed_to(race.reviewer)
    assert len(notices) == 1 and notices == messages
