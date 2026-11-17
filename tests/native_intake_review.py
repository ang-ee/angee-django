"""Intake review on the emitted host: asking the requester, taking their reply, and a request's position."""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rebac import actor_context, system_context

from angee.base.models.locks import StaleRevisionError
from angee.graphql.schema import GraphQLSchemas
from angee.jobs.enqueue import celery_app
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart
from angee.work.events import task_woken
from tests.native_intake_capture import ChannelIntakeCaptureTests, IntakeAccessCase

REQUESTER = "requester@example.test"


@contextmanager
def woken():
    """Collect every ``task_woken`` as ``(task pk, reason, message pk)``."""

    events = []

    def observe(sender, task, reason, message, **kwargs):
        events.append((task.pk, reason, message.pk if message is not None else None))

    task_woken.connect(observe, weak=False, dispatch_uid="tests.intake.review.observe")
    try:
        yield events
    finally:
        task_woken.disconnect(dispatch_uid="tests.intake.review.observe")


def email(channel, external_id, *, sender=REQUESTER, subject="Room booking", in_reply_to=""):
    """Ingest one email on ``channel`` as messaging's transports do."""

    parsed = ParsedMessage(
        external_id=external_id,
        platform="email",
        subject=subject,
        in_reply_to=in_reply_to,
        sender=ParsedHandle(platform="email", value=sender),
        body=ParsedPart(type="text/plain", role="body", text=f"Body of {external_id}"),
    )
    with system_context(reason="tests.intake.review.ingest"):
        (message,) = apps.get_model("messaging", "Message").objects.ingest([parsed], channel=channel)
    return message


class RequesterReplyTests(TransactionTestCase):
    """A requester's message on a conversation intake captured continues that request."""

    def setUp(self):
        ChannelIntakeCaptureTests.setUp(self)
        with system_context(reason="tests.intake.review.queue"):
            self.channel.intake_queue = self.queue
            self.channel.save(update_fields=["intake_queue"])

    def test_the_requesters_follow_up_files_no_new_request_and_stays_on_the_conversation(self):
        first = email(self.channel, "booking-1")
        follow_up = email(self.channel, "booking-2", subject="Re: Room booking")
        need = self.Need._base_manager.get()
        self.assertEqual(need.source_message_id, first.pk)
        self.assertEqual(follow_up.thread_id, first.thread_id)
        self.assertEqual(apps.get_model("projects", "Task")._base_manager.count(), 1)

    def test_another_sender_on_the_conversation_files_their_own_request(self):
        email(self.channel, "booking-1")
        email(self.channel, "booking-2", sender="colleague@example.test", subject="Re: Room booking")
        self.assertEqual(self.Need._base_manager.count(), 2)

    def test_the_reply_wakes_a_hold_awaiting_nobody_with_the_message(self):
        email(self.channel, "booking-1")
        task = self.Need._base_manager.get().task
        with actor_context(self.actor):
            type(task)._base_manager.get(pk=task.pk).with_actor(self.actor).snooze()
        with woken() as events:
            reply = email(self.channel, "booking-2", subject="Re: Room booking")
        self.assertEqual(events, [(task.pk, "reply", reply.pk)])
        task.refresh_from_db()
        self.assertIsNone(task.snoozed_at)


class RequesterDomainTests(TransactionTestCase):
    """``["*"]`` gives every claimed webform address an account, and only alone."""

    webform = ChannelIntakeCaptureTests.webform
    submit = ChannelIntakeCaptureTests.submit

    def setUp(self):
        ChannelIntakeCaptureTests.setUp(self)

    def test_every_domain_gets_one_passwordless_unadmitted_account(self):
        channel = self.webform(domains=("*",))
        self.submit(channel)
        self.submit(channel, "form-2")
        user = get_user_model()._base_manager.get(email="alex@example.com")
        self.assertFalse(user.has_usable_password())
        needs = list(self.Need._base_manager.order_by("pk"))
        self.assertEqual(len(needs), 2)
        self.assertEqual({need.party_id for need in needs}, {needs[0].party_id})
        self.assertEqual([need.admitted_user_id for need in needs], [None, None])
        self.assertTrue(channel.creates_requester_account("anywhere.test"))

    def test_the_wildcard_stands_alone(self):
        channel = self.webform()
        channel.intake_requester_domains = ["*", "example.com"]
        with system_context(reason="tests.intake.review.wildcard"), self.assertRaisesMessage(
            ValidationError, 'Use "*" alone'
        ):
            channel.clean()


class ClarifyTests(IntakeAccessCase):
    """``Task.clarify`` asks the requester where they can reply, then holds the task for their reply."""

    def task(self, need):
        return self.Task._base_manager.get(pk=need.task_id).with_actor(self.owner)

    def clarify(self, need, question="Which dates do you need the room for?", **kwargs):
        with actor_context(self.owner):
            task = self.task(need)
            return task.clarify(question, expected_revision=kwargs.pop("expected_revision", task.revision), **kwargs)

    def comments(self, task):
        return apps.get_model("messaging", "Message")._base_manager.filter(
            thread=task.message_thread(create=False), message_type="comment",
        ).order_by("pk")

    def email_channel(self):
        with system_context(reason="tests.intake.review.email_channel"):
            vendor, _created = apps.get_model("integrate", "Vendor").objects.get_or_create(
                slug="email", defaults={"display_name": "Email"},
            )
            return apps.get_model("messaging", "Channel").objects.create(
                vendor=vendor, owner=self.owner, display_name="Bookings", backend_class="email",
                lifecycle="connected", config={"from_email": "Bookings <bookings@example.test>"},
                intake_queue=self.queue,
            )

    def test_an_admitted_requester_reads_the_question_and_their_reply_wakes_the_task(self):
        need = self.need(email="", party=self.party(self.reader))
        self.as_user(need).decide_access("intake.approve")
        task = self.clarify(need)
        self.assertEqual(task.snoozed_awaiting_id, self.reader.pk)
        self.assertEqual(task.stage.category, "triage")
        (question,) = self.comments(task)
        self.assertTrue(task.with_actor(self.reader).has_access("comment"))
        notified = apps.get_model("messaging", "ThreadNotification")._base_manager.filter(message=question)
        self.assertIn(self.reader.pk, set(notified.values_list("user_id", flat=True)))
        self.assertFalse(apps.get_model("messaging", "Message")._base_manager.filter(direction="outbound").exists())
        with woken() as events:
            with actor_context(self.owner):
                self.task(need).message_post("Any dates in March work too.")
            self.assertEqual(events, [])
            with actor_context(self.reader):
                reply = self.Task._base_manager.get(pk=task.pk).with_actor(self.reader).message_post("The 4th.")
        self.assertEqual(events, [(task.pk, "reply", reply.pk)])

    @override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
    def test_an_emailed_requester_without_an_account_is_asked_and_answers_by_email(self):
        self.enterContext(patch.object(celery_app, "send_task"))
        channel = self.email_channel()
        request = email(channel, "booking-1")
        need = self.Need._base_manager.get(source_message=request)
        self.assertIsNone(need.party_id)
        reply_by = timezone.now() + timedelta(days=3)
        task = self.clarify(need, reply_by=reply_by)
        self.assertEqual((task.snoozed_until, task.snoozed_awaiting_id), (reply_by, None))
        self.assertEqual(self.comments(task).count(), 1)
        outbound = apps.get_model("messaging", "Message")._base_manager.get(direction="outbound")
        self.assertEqual((outbound.parent_id, outbound.thread_id), (request.pk, request.thread_id))
        self.assertEqual(outbound.sender.value, "bookings@example.test")
        self.assertEqual(outbound.preview, "Which dates do you need the room for?")
        with woken() as events:
            answer = email(channel, "booking-2", subject="Re: Room booking", in_reply_to="booking-1")
        self.assertEqual(events, [(task.pk, "reply", answer.pk)])
        self.assertEqual(self.Need._base_manager.count(), 1)

    def test_a_requester_who_can_neither_reply_nor_be_emailed_is_refused(self):
        need = self.need(email="", party=self.party(self.reader))
        before = self.task(need).revision
        with self.assertRaisesMessage(ValidationError, "neither reply on this task nor be reached by email"):
            self.clarify(need)
        task = self.task(need)
        self.assertEqual((task.revision, task.snoozed_at), (before, None))
        self.assertFalse(self.comments(task).exists())
        with self.assertRaisesMessage(ValidationError, "This task has no requester to ask."):
            self.clarify(self.need(email="new@example.test"))

    def test_several_requesters_are_told_apart_by_party(self):
        need = self.need(email="", party=self.party(self.reader))
        self.as_user(need).decide_access("intake.approve")
        with system_context(reason="tests.intake.review.second_requester"):
            other = self.User.objects.create_user(username="second-requester", email="second@example.test")
            self.Need.objects.create(task=need.task, party=self.party(other), body="Another request")
        with self.assertRaisesMessage(ValidationError, "choose the party to ask"):
            self.clarify(need)
        with self.assertRaisesMessage(ValidationError, "Choose a party that filed this task"):
            self.clarify(need, to=self.party(self.writer))
        task = self.clarify(need, to=self.party(self.reader))
        self.assertEqual(task.snoozed_awaiting_id, self.reader.pk)

    def test_a_stale_revision_asks_nothing(self):
        need = self.need(email="", party=self.party(self.reader))
        self.as_user(need).decide_access("intake.approve")
        stale = self.task(need).revision + 1
        with self.assertRaises(StaleRevisionError):
            self.clarify(need, expected_revision=stale)
        task = self.task(need)
        self.assertIsNone(task.snoozed_at)
        self.assertFalse(self.comments(task).exists())

    def test_the_mutation_asks_and_holds(self):
        need = self.need(email="", party=self.party(self.reader))
        self.as_user(need).decide_access("intake.approve")
        task = self.task(need)
        data = self.graphql(
            """
            mutation($task: ID!, $question: String!, $revision: Int) {
              clarify_task(task: $task, question: $question, expected_revision: $revision) { ok message }
            }
            """,
            {"task": task.sqid, "question": "Which room?", "revision": task.revision},
            user=self.owner,
        )
        self.assertTrue(data["clarify_task"]["ok"], data)
        self.assertEqual(self.task(need).snoozed_awaiting_id, self.reader.pk)


class RequestPositionTests(IntakeAccessCase):
    """A request's position is its task's stage, from capture to the promoted project's phase."""

    def test_capture_triage_accept_promotion_and_phase_move_the_one_stage(self):
        stage_model = apps.get_model("work", "Stage")
        with system_context(reason="tests.intake.review.rule_stages"):
            delivering, reviewing = (
                stage_model.objects.create(queue=self.queue, name=name, category="started", rule_owned=True)
                for name in ("Delivering", "Reviewing")
            )
        need = self.need(email="", party=self.party(self.reader))
        task = self.Task._base_manager.get(pk=need.task_id)
        self.assertEqual(task.stage.category, "triage")
        with actor_context(self.owner):
            task.with_actor(self.owner).accept()
            task.refresh_from_db()
            self.assertEqual((task.stage.category, task.stage.rule_owned), ("unstarted", False))
            project = self.Task._base_manager.get(pk=task.pk).with_actor(self.owner).promote_to_project()
            task.refresh_from_db()
            self.assertEqual(task.stage_id, delivering.pk)
            with system_context(reason="tests.intake.review.phase"):
                phase = apps.get_model("projects", "Milestone").objects.create(
                    project=project, name="Review", active_stage=reviewing,
                )
            project.with_actor(self.owner).set_current_milestone(phase)
        task.refresh_from_db()
        self.assertEqual(task.stage_id, reviewing.pk)
        self.assertTrue(GraphQLSchemas.from_discovery().render_sdl())
