"""Open decisions mailed to the people they can be put to: who, what each reads, when, and what reaches the outbox."""

from __future__ import annotations

import email.policy
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from email import message_from_bytes
from types import SimpleNamespace
from typing import Any

import pytest
from celery.exceptions import Retry, SoftTimeLimitExceeded
from celery.signals import task_prerun
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.db.models.signals import pre_save
from django.utils import timezone
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.relationships import delete_relationships
from rebac.types import RelationshipFilter

from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.decisions_messaging import models as decision_mail
from angee.decisions_messaging.constants import MAIL_DECISION_TASK, MAIL_DIGESTS_TASK
from angee.decisions_messaging.models import (
    DIGEST_QUESTION_LIMIT,
    RETRIED_REFUSALS,
    DecisionNoticeOccasion,
    refusal_codes,
)
from angee.decisions_messaging.tasks import mail_decision, mail_digests
from angee.decisions_messaging.testing.drivers import addressed_user, install_decision_mail
from angee.decisions_messaging.testing.models import DecisionNotice, DigestNotice
from angee.jobs.enqueue import celery_app
from angee.messaging.backends import OutboundEmailChannelMissing, SystemChannelMissing
from angee.messaging.tasks import deliver_message
from angee.messaging.testing.models import Message
from angee.messaging_templates.testing.models import MessageTemplate
from angee.projects.testing.models import Task
from tests.conftest import create_platform_admin, vault_for

ORIGIN = "https://app.example.test"
READ = "tests.decisions_messaging.read"
CHOICES = ("Confirm the order", "Decline the order")
LOCMEM = {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}
SENDER = "Angee <noreply@example.test>"


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Every task send, recorded when its transaction commits and never made."""

    tasks: list[tuple[str, Any]] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: tasks.append((name, options["kwargs"])))
    return tasks


@pytest.fixture
def world(db: None, settings: Any, sent: list[Any]) -> SimpleNamespace:
    """System mail on an in-memory transport, the shipped rows, and the people around one order."""

    del db, sent
    settings.MAILERS = {"transport": LOCMEM}
    settings.DEFAULT_FROM_EMAIL = SENDER
    settings.ANGEE_PUBLIC_ORIGIN = ORIGIN
    install_decision_mail()
    requester, reviewer, reader = (addressed_user(name) for name in ("dm-requester", "dm-reviewer", "dm-reader"))
    admin = create_platform_admin("dm-admin")
    subject = vault_for(requester, name="Catering order")
    write_relationships([
        RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(user)) for user in (reviewer, reader)
    ])
    return SimpleNamespace(requester=requester, reviewer=reviewer, reader=reader, admin=admin, subject=subject)


def ask(world: Any, *assignees: Any, records: tuple[Any, ...] | None = None, **changes: Any) -> Any:
    """Ask one question about the world's order, as its requester."""

    return Decision.objects.ask(
        DecisionRequest(
            kind="order_review",
            records=records or (world.subject,),
            assignees=assignees,
            requester=changes.pop("requester", world.requester),
            proposal=DecisionProposal(alternatives=[
                {"key": "confirm", "label": CHOICES[0], "outcome": "confirmed"},
                {"key": "decline", "label": CHOICES[1], "outcome": "declined"},
            ]),
            **changes,
        ),
        actor=world.requester,
    )


def mailed(decision: Any = None) -> list[tuple[Any, str]]:
    """Whom the question ledger says was mailed, and why, in the order they were mailed."""

    with system_context(reason=READ):
        rows = DecisionNotice.objects.order_by("pk")
        if decision is not None:
            rows = rows.filter(decision=decision)
        return [(row.user_id, str(row.occasion)) for row in rows]


def mails(ledger: Any, user: Any, **identity: Any) -> list[tuple[str, str]]:
    """The subject and plain text of each mail ``ledger`` recorded for ``user``, as its channel writes them."""

    with system_context(reason=READ):
        notices = ledger.objects.filter(user=user, **identity).values("message_id")
        rows = Message._base_manager.select_related("channel").filter(pk__in=notices).order_by("pk")
        return [(written.subject, written.body) for row in rows if (written := row.channel.backend.email_message(row))]


def revoke(record: Any, user: Any) -> None:
    """Take ``user``'s ``viewer`` relation on ``record`` away."""

    subject, reference = to_subject_ref(user), to_object_ref(record)
    delete_relationships(RelationshipFilter(
        resource_type=reference.resource_type, resource_id=reference.resource_id, relation="viewer",
        subject_type=subject.object.resource_type, subject_id=subject.object.resource_id,
    ))


def link(question: Any) -> str:
    return f"{ORIGIN}{question.get_absolute_url()}"


def speak(user: Any, language: str) -> None:
    """Give ``user`` a Person who reads ``language``."""

    person = apps.get_model("parties", "Person")
    with system_context(reason="tests.decisions_messaging.language"):
        row = person.objects.for_user(user)
        person.system_queryset().filter(pk=row.pk).update(languages=[language])


def break_in(language: str, key: str) -> None:
    """Write a deployment's own row of ``key`` in ``language`` that refuses at render, as one past ``clean`` does."""

    with system_context(reason="tests.decisions_messaging.broken_row"):
        MessageTemplate(
            kind="template", key=key, language=language, name="Broken", format="markdown", layout="base",
            subject="Broken", preheader="Broken", source="{{ missing }}", variables={"missing": "Never given."},
        ).save()


@contextmanager
def runs_of(task: Any) -> Iterator[list[Any]]:
    """Each run of ``task`` Celery starts inside the block, eager or not, retries included."""

    started: list[Any] = []

    def record(sender: Any = None, task_id: Any = None, **kwargs: Any) -> None:
        if sender is not None and sender.name == task.name:
            started.append(task_id)

    task_prerun.connect(record, weak=False)
    try:
        yield started
    finally:
        task_prerun.disconnect(record)


def test_the_ledgers_pass_their_model_checks_and_both_tasks_are_registered() -> None:
    assert DecisionNotice.check() == [] and DigestNotice.check() == []
    assert {MAIL_DECISION_TASK, MAIL_DIGESTS_TASK} <= set(celery_app.tasks)


# ---------- who is mailed ----------


def test_a_question_is_mailed_to_its_deciders(world: Any) -> None:
    question = ask(world, world.reviewer)
    assert mail_decision(question.pk, "asked") == 2
    assert mailed(question) == [(world.reviewer.pk, "asked"), (world.admin.pk, "asked")]


def test_a_holder_of_act_who_may_not_read_the_question_is_not_mailed(world: Any) -> None:
    sharer = addressed_user("dm-sharer")
    with actor_context(sharer):
        task = Task.objects.create(title="Access request")
        need = apps.get_model("intake", "Need").objects.capture(target=task, body="Please review access")
    question = Decision.system_queryset().get(pk=need.access_decision_id)
    with system_context(reason="tests.decisions_messaging.act_without_read"):
        question.assignees.clear()
    assert question.with_actor(sharer).has_access("act") and not question.with_actor(sharer).has_access("read")
    mail_decision(question.pk, "asked")
    assert sharer.pk not in [user for user, _occasion in mailed(question)]


# ---------- what each person reads ----------


def test_a_read_revoked_before_the_asked_mail_drops_the_label_from_that_persons_mail(world: Any) -> None:
    question = ask(world, world.reviewer)
    revoke(world.subject, world.reviewer)
    assert mail_decision(question.pk, "asked") == 2
    ((_subject, reviewer_text),) = mails(DecisionNotice, world.reviewer, decision=question)
    ((_subject, admin_text),) = mails(DecisionNotice, world.admin, decision=question)
    assert "Catering order" not in reviewer_text and link(question) in reviewer_text
    assert "Catering order" in admin_text


def test_a_read_revoked_later_drops_the_label_from_the_reminder_and_the_digest(world: Any) -> None:
    question = ask(world, world.reviewer)
    assert mail_decision(question.pk, "asked") == 2
    ((_subject, asked_text),) = mails(DecisionNotice, world.reviewer, decision=question)
    assert "Catering order" in asked_text
    revoke(world.subject, world.reviewer)
    assert question.remind()
    assert mail_decision(question.pk, "reminder") == 2
    ((_subject, reminder_text),) = mails(DecisionNotice, world.reviewer, decision=question, occasion="reminder")
    assert "Catering order" not in reminder_text
    assert DigestNotice.objects.mail_digests(now=timezone.now() + timedelta(days=2)) == 1
    ((_subject, digest_text),) = mails(DigestNotice, world.reviewer)
    assert link(question) in digest_text and "Catering order" not in digest_text


def test_a_question_names_its_first_records_and_counts_the_rest(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(decision_mail, "QUESTION_RECORD_LIMIT", 1)
    second = vault_for(world.requester, name="Delivery note")
    write_relationships([RelationshipTuple(to_object_ref(second), "viewer", to_subject_ref(world.reviewer))])
    question = ask(world, world.reviewer, records=(world.subject, second))
    assert mail_decision(question.pk, "asked") == 2
    ((_subject, text),) = mails(DecisionNotice, world.reviewer, decision=question)
    assert "Concerns: Catering order and 1 more" in text and "Delivery note" not in text


# ---------- asked ----------


def test_asking_mails_each_decider_once_after_commit(
    world: Any, sent: list[Any], django_capture_on_commit_callbacks: Any,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        question = ask(world, world.reviewer)
    assert [(name, kwargs) for name, kwargs in sent if name == MAIL_DECISION_TASK] == [
        (MAIL_DECISION_TASK, {"decision_id": question.pk, "occasion": DecisionNoticeOccasion.ASKED}),
    ]
    assert mail_decision(question.pk, "asked") == 2
    with system_context(reason=READ):
        messages = Message._base_manager.count()
    assert mail_decision(question.pk, "asked") == 0
    with system_context(reason=READ):
        assert Message._base_manager.count() == messages
    assert mailed(question) == [(world.reviewer.pk, "asked"), (world.admin.pk, "asked")]


def test_a_rolled_back_ask_mails_nothing(world: Any, sent: list[Any], django_capture_on_commit_callbacks: Any) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError), transaction.atomic():
            ask(world, world.reviewer)
            raise RuntimeError("roll the ask back")
    assert [name for name, _kwargs in sent if name == MAIL_DECISION_TASK] == []
    assert mailed() == []


def test_a_question_closed_before_its_mail_is_not_mailed(world: Any) -> None:
    question = ask(world, world.reviewer)
    question.decide(actor=world.reviewer, chosen=["confirm"])
    assert mail_decision(question.pk, "asked") == 0 and mailed() == []


def test_a_supersede_mails_the_replacement_from_each_persons_own_reads(
    world: Any, sent: list[Any], django_capture_on_commit_callbacks: Any,
) -> None:
    question = ask(world, world.reviewer)
    with django_capture_on_commit_callbacks(execute=True), system_context(reason="tests.decisions_messaging.rephrase"):
        replacement = Decision.objects.supersede(
            question, question.replacement_request(due_at=None, remind_after=None), actor=world.requester,
            reason="Rephrased",
        )
    assert [kwargs["decision_id"] for name, kwargs in sent if name == MAIL_DECISION_TASK] == [replacement.pk]
    revoke(world.subject, world.reviewer)
    assert mail_decision(replacement.pk, "asked") == 2
    ((_subject, text),) = mails(DecisionNotice, world.reviewer, decision=replacement)
    assert link(replacement) in text and "Catering order" not in text


# ---------- reminder ----------


def test_a_reminder_mails_each_decider_once(
    world: Any, sent: list[Any], django_capture_on_commit_callbacks: Any,
) -> None:
    question = ask(world, world.reviewer)
    assert mail_decision(question.pk, "asked") == 2
    with django_capture_on_commit_callbacks(execute=True):
        assert question.remind()
    assert (MAIL_DECISION_TASK, {"decision_id": question.pk, "occasion": DecisionNoticeOccasion.REMINDER}) in sent
    assert mail_decision(question.pk, "reminder") == 2
    assert mail_decision(question.pk, "reminder") == 0
    assert mailed(question) == [
        (world.reviewer.pk, "asked"), (world.admin.pk, "asked"),
        (world.reviewer.pk, "reminder"), (world.admin.pk, "reminder"),
    ]


# ---------- what a failure stops, and what it does not ----------


def test_a_refused_address_is_skipped_and_the_others_still_go(world: Any) -> None:
    question = ask(world, world.reviewer)
    with system_context(reason="tests.decisions_messaging.address"):
        get_user_model().objects.filter(pk=world.reviewer.pk).update(email="")
    assert mail_decision(question.pk, "asked") == 1
    assert mailed(question) == [(world.admin.pk, "asked")]


def test_one_persons_failure_stops_nobody_else_and_still_fails_the_run(world: Any) -> None:
    question = ask(world, world.reviewer, world.reader)
    speak(world.reader, "fr")
    break_in("fr", DecisionNoticeOccasion.ASKED.template_key)
    with pytest.raises(ValidationError):
        mail_decision(question.pk, "asked")
    assert world.reviewer.pk < world.reader.pk < world.admin.pk, "the failing person sits between two others"
    assert mailed(question) == [(world.reviewer.pk, "asked"), (world.admin.pk, "asked")]
    with system_context(reason="tests.decisions_messaging.repair"):
        MessageTemplate.objects.filter(language="fr").delete()
    assert mail_decision(question.pk, "asked") == 1
    assert mailed(question)[-1] == (world.reader.pk, "asked")


def test_one_persons_failed_digest_stops_nobody_elses(world: Any) -> None:
    ask(world, world.reviewer)
    ask(world, world.reader)
    speak(world.reviewer, "fr")
    break_in("fr", decision_mail.DIGEST_TEMPLATE_KEY)
    with pytest.raises(ValidationError):
        DigestNotice.objects.mail_digests(now=timezone.now())
    assert mails(DigestNotice, world.reviewer) == [] and len(mails(DigestNotice, world.reader)) == 1


def _no_system_channel(settings: Any) -> None:
    channels = apps.get_model("messaging", "Channel")
    channels._base_manager.filter(pk=channels.objects.system_channel().pk).delete()


def _system_channel_disconnected(settings: Any) -> None:
    channels = apps.get_model("messaging", "Channel")
    channels._base_manager.filter(pk=channels.objects.system_channel().pk).update(
        lifecycle=channels.Lifecycle.DISCONNECTED,
    )


@pytest.mark.parametrize(
    ("break_it", "refusal", "runs"),
    [
        pytest.param(
            lambda settings: setattr(settings, "ANGEE_PUBLIC_ORIGIN", ""), ImproperlyConfigured, 1, id="no-origin",
        ),
        pytest.param(
            lambda settings: MessageTemplate.objects.filter(key=DecisionNoticeOccasion.ASKED.template_key).delete(),
            MessageTemplate.DoesNotExist, 1, id="no-template-row",
        ),
        pytest.param(lambda settings: setattr(settings, "MAILERS", {}), ValidationError, 1, id="mail-not-configured"),
        pytest.param(_no_system_channel, SystemChannelMissing, 4, id="system-channel-missing"),
        pytest.param(_system_channel_disconnected, OutboundEmailChannelMissing, 4, id="system-channel-disconnected"),
    ],
)
def test_a_deployment_defect_is_retried_only_when_a_retry_may_cure_it(
    world: Any, settings: Any, break_it: Any, refusal: type[Exception], runs: int,
) -> None:
    """Permanent defects fail one run; a missing system channel, which an operator can restore, is retried."""

    question = ask(world, world.reviewer)
    with system_context(reason="tests.decisions_messaging.defect"):
        break_it(settings)
    with runs_of(mail_decision) as started:
        result = mail_decision.apply(kwargs={"decision_id": question.pk, "occasion": "asked"})
    assert len(started) == runs and mailed() == []
    if issubclass(refusal, RETRIED_REFUSALS):
        # Celery keeps a retried refusal as the plain ValidationError it pickles to; its code survives.
        assert refusal_codes(result.result) == {refusal.code}
    else:
        assert isinstance(result.result, refusal)


@pytest.mark.parametrize(("retries", "minutes"), [(0, 5), (1, 15), (2, 45)])
def test_a_channel_refusal_is_retried_minutes_later(
    world: Any, settings: Any, monkeypatch: pytest.MonkeyPatch, retries: int, minutes: int,
) -> None:
    """Each retry Celery is asked for waits long enough for an operator to restore the channel."""

    question = ask(world, world.reviewer)
    with system_context(reason="tests.decisions_messaging.no_channel"):
        _no_system_channel(settings)
    asked: list[Any] = []

    def retry(*, exc: Exception, countdown: int) -> Retry:
        asked.append((type(exc), countdown))
        return Retry(exc=exc, when=countdown)

    monkeypatch.setattr(mail_decision, "retry", retry)
    mail_decision.push_request(id=f"retry-{retries}", retries=retries)
    try:
        with pytest.raises(Retry):
            mail_decision.run(question.pk, "asked")
    finally:
        mail_decision.pop_request()
    assert asked == [(SystemChannelMissing, minutes * 60)]


def test_a_soft_time_limit_stops_the_run_at_once(world: Any) -> None:
    question = ask(world, world.reviewer)
    stopped: list[Any] = []

    def stop(sender: Any, instance: Any, **kwargs: Any) -> None:
        stopped.append(instance.user_id)
        raise SoftTimeLimitExceeded()

    pre_save.connect(stop, sender=DecisionNotice, weak=False)
    try:
        with pytest.raises(SoftTimeLimitExceeded):
            mail_decision(question.pk, "asked")
    finally:
        pre_save.disconnect(stop, sender=DecisionNotice)
    assert stopped == [world.reviewer.pk], "the next person is never tried"
    assert mailed() == []
    with system_context(reason=READ):
        assert not Message._base_manager.exists()


# ---------- the daily digest ----------


def test_each_decider_gets_one_digest_listing_what_waits_on_them(world: Any) -> None:
    first = ask(world, world.reviewer)
    unassigned = ask(world)
    due = ask(world, world.reviewer, due_at=timezone.now() + timedelta(days=2))
    answered = ask(world, world.reviewer)
    answered.decide(actor=world.reviewer, chosen=["confirm"])
    now = timezone.now()
    assert DigestNotice.objects.mail_digests(now=now) == 2
    ((subject, text),) = mails(DigestNotice, world.reviewer)
    assert subject == "2 decisions waiting for you" and "2 questions are waiting for your decision." in text
    assert link(due) in text and link(first) in text and text.index(link(due)) < text.index(link(first))
    assert link(unassigned) not in text and link(answered) not in text
    ((_subject, admin_text),) = mails(DigestNotice, world.admin)
    assert link(unassigned) in admin_text and link(first) not in admin_text and link(due) not in admin_text
    assert mails(DigestNotice, world.requester) == mails(DigestNotice, world.reader) == []
    with system_context(reason=READ):
        assert set(DigestNotice.objects.values_list("user_id", "day")) == {
            (world.reviewer.pk, now.astimezone(UTC).date()), (world.admin.pk, now.astimezone(UTC).date()),
        }


def test_a_second_run_the_same_day_mails_nobody_and_the_next_day_mails_again(world: Any) -> None:
    ask(world, world.reviewer)
    now = datetime.combine(timezone.now().astimezone(UTC).date(), datetime.min.time(), UTC) + timedelta(hours=1)
    assert DigestNotice.objects.mail_digests(now=now) == 1
    assert DigestNotice.objects.mail_digests(now=now + timedelta(hours=20)) == 0
    assert DigestNotice.objects.mail_digests(now=now + timedelta(days=1)) == 1
    assert len(mails(DigestNotice, world.reviewer)) == 2


def test_a_person_with_no_open_question_gets_nothing(world: Any) -> None:
    question = ask(world, world.reviewer)
    question.decide(actor=world.reviewer, chosen=["decline"])
    assert DigestNotice.objects.mail_digests(now=timezone.now()) == 0
    with system_context(reason=READ):
        assert not DigestNotice.objects.exists()


def test_a_person_mailed_every_waiting_question_within_a_day_gets_no_digest_until_one_is_new(world: Any) -> None:
    first, second = ask(world, world.reviewer), ask(world, world.reviewer)
    assert mail_decision(first.pk, "asked") == 2
    assert second.remind() and mail_decision(second.pk, "reminder") == 2
    now = timezone.now()
    assert DigestNotice.objects.mail_digests(now=now) == 0
    third = ask(world, world.reviewer)
    assert DigestNotice.objects.mail_digests(now=now) == 1
    ((_subject, text),) = mails(DigestNotice, world.reviewer)
    assert all(link(question) in text for question in (first, second, third))
    assert text.index(link(third)) < text.index(link(first)), "the question not yet mailed comes first"


def test_a_question_not_yet_mailed_is_listed_even_when_the_limit_leaves_others_out(
    world: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(decision_mail, "DIGEST_QUESTION_LIMIT", 1)
    told = ask(world, world.reviewer, due_at=timezone.now() + timedelta(days=1))
    assert mail_decision(told.pk, "asked") == 2
    new = ask(world, world.reviewer)
    assert DigestNotice.objects.mail_digests(now=timezone.now()) == 1
    ((_subject, text),) = mails(DigestNotice, world.reviewer)
    assert link(new) in text and link(told) not in text and "1 more is waiting in the app." in text


def test_an_asked_mail_older_than_a_day_leaves_its_question_in_the_digest(world: Any) -> None:
    question = ask(world, world.reviewer)
    assert mail_decision(question.pk, "asked") == 2
    assert DigestNotice.objects.mail_digests(now=timezone.now()) == 0
    assert DigestNotice.objects.mail_digests(now=timezone.now() + timedelta(days=2)) == 1
    ((_subject, text),) = mails(DigestNotice, world.reviewer)
    assert link(question) in text


def test_a_refused_address_gets_no_digest_and_the_others_still_go(world: Any) -> None:
    ask(world, world.reviewer)
    ask(world)
    with system_context(reason="tests.decisions_messaging.address"):
        get_user_model().objects.filter(pk=world.reviewer.pk).update(email="")
    assert DigestNotice.objects.mail_digests(now=timezone.now()) == 1
    assert mails(DigestNotice, world.reviewer) == [] and len(mails(DigestNotice, world.admin)) == 1


def test_a_digest_lists_at_most_its_limit_due_first_and_counts_the_rest(world: Any) -> None:
    undated = [ask(world, world.reviewer) for _ in range(DIGEST_QUESTION_LIMIT)]
    due = ask(world, world.reviewer, due_at=timezone.now() + timedelta(days=3))
    assert DigestNotice.objects.mail_digests(now=timezone.now()) == 1
    ((subject, text),) = mails(DigestNotice, world.reviewer)
    total = DIGEST_QUESTION_LIMIT + 1
    assert subject == f"{total} decisions waiting for you"
    assert f"{total} questions are waiting for your decision." in text and "1 more is waiting in the app." in text
    assert text.index(link(due)) < text.index(link(undated[0]))
    assert all(link(question) in text for question in undated[:-1]) and link(undated[-1]) not in text


def test_the_beat_runs_the_digest_task_and_its_timestamp_names_the_day(world: Any, settings: Any) -> None:
    assert settings.CELERY_BEAT_SCHEDULE[MAIL_DIGESTS_TASK]["task"] == mail_digests.name
    ask(world, world.reviewer)
    assert mail_digests(timestamp=int(datetime(2027, 1, 2, 7, tzinfo=UTC).timestamp())) == 1
    with system_context(reason=READ):
        assert DigestNotice.objects.get().day == date(2027, 1, 2)


# ---------- what reaches the outbox ----------


def test_the_mail_reaches_the_outbox_with_the_question_link_and_its_choices(world: Any) -> None:
    question = ask(world, world.reviewer)
    assert mail_decision(question.pk, "asked") == 2
    with system_context(reason=READ):
        notice = DecisionNotice.objects.get(decision=question, user=world.reviewer)
        message = Message._base_manager.get(pk=notice.message_id)
    assert message.created_by_id is None, "unattended system work names no causing actor"
    assert deliver_message(message._meta.label_lower, message.pk, message.external_id)["delivered"]
    wire = message_from_bytes(mail.outbox[-1].message().as_bytes(), policy=email.policy.default)
    assert (str(wire["From"]), str(wire["To"])) == (SENDER, world.reviewer.email)
    assert str(wire["Subject"]) == "A decision is waiting for you: Order review"
    for form in (part.get_content() for part in wire.iter_parts()):
        assert link(question) in form and "Catering order" in form
        assert all(choice in form for choice in CHOICES)


def test_a_purged_message_keeps_its_notice_so_nobody_is_mailed_again(world: Any) -> None:
    question = ask(world, world.reviewer)
    assert mail_decision(question.pk, "asked") == 2
    with system_context(reason="tests.decisions_messaging.purge"):
        Message._base_manager.filter(pk__in=DecisionNotice.objects.values("message_id")).delete()
        assert set(DecisionNotice.objects.values_list("message_id", flat=True)) == {None}
    assert mail_decision(question.pk, "asked") == 0
