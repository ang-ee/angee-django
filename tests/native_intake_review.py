"""Intake review on the emitted host: asking the requester, taking their reply, and a request's position."""

from contextlib import contextmanager
from datetime import timedelta
from email.utils import parseaddr
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context

from angee.base.models.locks import StaleRevisionError
from angee.graphql.schema import GraphQLSchemas
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart
from angee.messaging.tasks import deliver_message
from angee.work.events import task_woken
from tests.native_intake_capture import ChannelIntakeCaptureTests, FormCaptureTests, IntakeAccessCase

REQUESTER = "requester@example.test"
CLARIFY = """
mutation($task: ID!, $question: String!, $revision: Int) {
  clarify_task(task: $task, question: $question, expected_revision: $revision) { ok message }
}
"""


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


@contextmanager
def refusing(task):
    """Make a consumer's re-review of ``task`` fail, as a broken ``task_woken`` receiver would."""

    def refuse(sender, task, **kwargs):
        if task.pk == refused_pk:
            raise RuntimeError("The re-review failed.")

    refused_pk = task.pk
    task_woken.connect(refuse, weak=False, dispatch_uid="tests.intake.review.refuse")
    try:
        yield
    finally:
        task_woken.disconnect(dispatch_uid="tests.intake.review.refuse")


def email(channel, external_id, *, sender=REQUESTER, subject="Room booking", in_reply_to="", headers=()):
    """Ingest one email on ``channel`` as messaging's transports do."""

    parsed = ParsedMessage(
        external_id=external_id,
        platform="email",
        subject=subject,
        in_reply_to=in_reply_to,
        headers=tuple(headers),
        sender=ParsedHandle(platform="email", value=sender),
        body=ParsedPart(type="text/plain", role="body", text=f"Body of {external_id}"),
    )
    with system_context(reason="tests.intake.review.ingest"):
        (message,) = apps.get_model("messaging", "Message").objects.ingest([parsed], channel=channel)
    return message


class RequesterReplyTests(TransactionTestCase):
    """Only a reply into a request's conversation, while the request is under review, continues it."""

    def setUp(self):
        ChannelIntakeCaptureTests.setUp(self)
        self.Task = apps.get_model("projects", "Task")
        with system_context(reason="tests.intake.review.queue"):
            self.channel.intake_queue = self.queue
            self.channel.save(update_fields=["intake_queue"])

    def task(self, need):
        return self.Task._base_manager.get(pk=need.task_id).with_actor(self.actor)

    def confirmed_requester(self):
        """A requester account whose email address its party has confirmed."""

        with system_context(reason="tests.intake.review.requester"):
            requester = get_user_model().objects.create_person_as_system(
                username="booking-requester",
                email=REQUESTER,
                reason="tests.intake.review.requester",
            )
            party = apps.get_model("parties", "Party").objects.for_user(requester)
            handle = apps.get_model("parties", "Handle").objects.upsert(
                platform="email",
                value=REQUESTER,
                created_by_id=requester.pk,
            )
            party.with_actor(requester).confirm_statement(aspect="handle", handle=handle, values={})
        return requester

    def test_a_message_is_a_reply_only_when_it_answers_a_request_still_in_triage(self):
        cases = (
            ("triage", True, False),
            ("triage", False, True),
            ("accepted", True, True),
            ("accepted", False, True),
            ("declined", True, True),
            ("declined", False, True),
        )
        for index, (review, replies, files) in enumerate(cases):
            with self.subTest(review=review, replies=replies):
                subject = f"Room booking {index}"
                first = email(self.channel, f"booking-{index}@example.test", subject=subject)
                need = self.Need._base_manager.get(source_message=first)
                with actor_context(self.actor):
                    if review == "accepted":
                        self.task(need).accept()
                    elif review == "declined":
                        self.task(need).decline("declined")
                before = (self.Need._base_manager.count(), self.Task._base_manager.count())
                later = email(
                    self.channel,
                    f"later-{index}@example.test",
                    subject=f"Re: {subject}" if replies else subject,
                    in_reply_to=first.external_id if replies else "",
                )
                self.assertEqual(later.thread_id, first.thread_id)
                after = (self.Need._base_manager.count(), self.Task._base_manager.count())
                self.assertEqual(after, (before[0] + files, before[1] + files))

    def test_another_sender_answering_the_conversation_files_their_own_request(self):
        first = email(self.channel, "booking@example.test")
        email(
            self.channel,
            "colleague@example.test",
            sender="colleague@example.test",
            subject="Re: Room booking",
            in_reply_to=first.external_id,
        )
        self.assertEqual(self.Need._base_manager.count(), 2)

    def test_the_reply_wakes_only_a_hold_awaiting_the_requesters_account(self):
        requester = self.confirmed_requester()
        held, plain = (email(self.channel, f"{name}@example.test", subject=name) for name in ("Held", "Plain"))
        held_need, plain_need = (self.Need._base_manager.get(source_message=message) for message in (held, plain))
        self.assertEqual(held_need.requester_account_id(), requester.pk)
        with actor_context(self.actor):
            self.task(held_need).snooze(awaiting=requester)
            self.task(plain_need).snooze()
        with woken() as events:
            answer = email(self.channel, "held-answer@example.test", subject="Re: Held", in_reply_to=held.external_id)
            email(self.channel, "plain-answer@example.test", subject="Re: Plain", in_reply_to=plain.external_id)
        self.assertEqual(events, [(held_need.task_id, "reply", answer.pk)])
        self.assertIsNotNone(self.task(plain_need).snoozed_at)
        self.assertEqual(self.Need._base_manager.count(), 2)

    def test_a_failing_re_review_never_rejects_the_requesters_email(self):
        requester = self.confirmed_requester()
        first = email(self.channel, "booking@example.test")
        need = self.Need._base_manager.get(source_message=first)
        with actor_context(self.actor):
            self.task(need).snooze(awaiting=requester)
        with refusing(self.task(need)), self.assertLogs("angee.work.models", "ERROR"):
            answer = email(self.channel, "answer@example.test", subject="Re: Booking", in_reply_to=first.external_id)
        self.assertTrue(apps.get_model("messaging", "Message")._base_manager.filter(pk=answer.pk).exists())
        self.assertIsNotNone(self.task(need).snoozed_at)
        self.assertEqual(self.Need._base_manager.count(), 1)


class RequesterDomainTests(TransactionTestCase):
    """``["*"]`` gives every claimed webform address an account, and only alone."""

    webform = FormCaptureTests.webform
    submit = FormCaptureTests.submit

    def setUp(self):
        FormCaptureTests.setUp(self)

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
        with (
            system_context(reason="tests.intake.review.wildcard"),
            self.assertRaisesMessage(ValidationError, 'Use "*" alone'),
        ):
            channel.clean()


class ClarifyTests(IntakeAccessCase):
    """``Task.clarify`` asks the requester where they can answer, then holds the task for their reply."""

    def task(self, need):
        return self.Task._base_manager.get(pk=need.task_id).with_actor(self.owner)

    def clarify(self, need, question="Which dates do you need the room for?", *, user=None, **kwargs):
        user = user or self.owner
        with actor_context(user):
            task = self.Task._base_manager.get(pk=need.task_id).with_actor(user)
            return task.clarify(question, expected_revision=kwargs.pop("expected_revision", task.revision), **kwargs)

    def comments(self, task):
        return (
            apps.get_model("messaging", "Message")
            ._base_manager.filter(
                thread=task.message_thread(create=False),
                message_type="comment",
            )
            .order_by("pk")
        )

    def admitted(self, user):
        need = self.need(email="", party=self.party(user))
        self.as_user(need).decide_access("intake.approve")
        return need

    def channel(self, backend_class="email"):
        with system_context(reason="tests.intake.review.channel"):
            vendor, _created = apps.get_model("integrate", "Vendor").objects.get_or_create(
                slug=backend_class,
                defaults={"display_name": backend_class.title()},
            )
            return apps.get_model("messaging", "Channel").objects.create(
                vendor=vendor,
                owner=self.owner,
                display_name="Bookings",
                backend_class=backend_class,
                lifecycle="connected",
                config={"from_email": "Bookings <bookings@example.test>"},
                intake_queue=self.queue,
            )

    def test_an_admitted_requester_reads_the_question_and_their_reply_wakes_the_task(self):
        need = self.admitted(self.reader)
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

    @override_settings(
        ANGEE_EMAIL_DELIVERY_CONFIGURED=True,
        MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
    )
    def test_an_emailed_requester_gets_an_account_and_the_question_and_their_answer_wakes_the_task(self):
        channel = self.channel()
        request = email(channel, "booking@example.test")
        need = self.Need._base_manager.get(source_message=request)
        self.assertIsNone(need.party_id)
        reply_by = timezone.now() + timedelta(days=3)
        task = self.clarify(need, reply_by=reply_by)

        account = self.User._base_manager.get(email=REQUESTER)
        self.assertFalse(account.has_usable_password())
        need.refresh_from_db()
        self.assertEqual((need.requester_account_id(), need.admitted_user_id), (account.pk, None))
        self.assertFalse(apps.get_model("spaces", "Membership")._base_manager.filter(party_id=need.party_id).exists())
        self.assertEqual((task.snoozed_until, task.snoozed_awaiting_id), (reply_by, account.pk))
        self.assertEqual(self.comments(task).count(), 1)

        # The worker's delivery of the question, as messaging queued it.
        question = apps.get_model("messaging", "Message")._base_manager.get(direction="outbound")
        self.assertEqual((question.parent_id, question.thread_id), (request.pk, request.thread_id))
        self.assertTrue(deliver_message(question._meta.label_lower, question.pk, question.external_id)["delivered"])
        (sent,) = mail.outbox
        wire = sent.message()
        self.assertEqual([parseaddr(address)[1] for address in sent.to], [request.sender.value])
        self.assertEqual(wire["Subject"], "Re: Room booking")
        self.assertEqual(wire["Message-ID"], f"<{question.external_id}>")
        self.assertEqual(wire["In-Reply-To"], f"<{request.external_id}>")

        # The requester answers the question they received, under a subject of their own.
        with woken() as events:
            answer = email(channel, "answer@example.test", subject="Dates", in_reply_to=question.external_id)
        self.assertEqual((answer.thread_id, answer.parent_id), (request.thread_id, question.pk))
        self.assertEqual(events, [(task.pk, "reply", answer.pk)])
        self.assertEqual(self.Need._base_manager.count(), 1)

    @override_settings(
        ANGEE_EMAIL_DELIVERY_CONFIGURED=True,
        MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
    )
    def test_a_relayed_requester_is_asked_at_their_reply_to_and_only_their_answer_wakes_the_task(self):
        channel = self.channel()
        request = email(
            channel,
            "relayed@example.test",
            sender="relay@example.test",
            headers=(("Reply-To", "Requester <requester@example.test>"),),
        )
        need = self.Need._base_manager.get(source_message=request)
        self.assertEqual((need.reply_to_addresses(), need.requester_address()), ((REQUESTER,), REQUESTER))
        task = self.clarify(need)
        account = self.User._base_manager.get(email=REQUESTER)
        self.assertFalse(self.User._base_manager.filter(email="relay@example.test").exists())
        self.assertEqual(task.snoozed_awaiting_id, account.pk)

        question = apps.get_model("messaging", "Message")._base_manager.get(direction="outbound")
        self.assertTrue(deliver_message(question._meta.label_lower, question.pk, question.external_id)["delivered"])
        (sent,) = mail.outbox
        self.assertEqual([parseaddr(address)[1].lower() for address in sent.to], [REQUESTER])

        with woken() as events:
            stranger = email(
                channel,
                "stranger@example.test",
                sender="someone@example.test",
                subject="Dates",
                in_reply_to=question.external_id,
            )
            self.assertEqual(events, [])
            self.assertTrue(self.Need._base_manager.filter(source_message=stranger).exists())
            answer = email(channel, "answer@example.test", subject="Dates", in_reply_to=question.external_id)
        self.assertEqual(events, [(task.pk, "reply", answer.pk)])
        self.assertFalse(self.Need._base_manager.filter(source_message=answer).exists())

    @override_settings(
        ANGEE_EMAIL_DELIVERY_CONFIGURED=True,
        MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
    )
    def test_an_answer_in_another_domain_spelling_of_the_reply_to_wakes_the_task(self):
        """The answer lands on the handle a contact stored in the Unicode spelling, and still answers the request."""

        channel = self.channel()
        with system_context(reason="tests.intake.review.domain_spelling"):
            stored = apps.get_model("parties", "Handle").objects.upsert(
                platform="email", value="requester@bücher.example",
            )
        request = email(
            channel,
            "relayed-idn@example.test",
            sender="relay@example.test",
            headers=(("Reply-To", "Requester <requester@xn--bcher-kva.example>"),),
        )
        task = self.clarify(self.Need._base_manager.get(source_message=request))
        question = apps.get_model("messaging", "Message")._base_manager.get(direction="outbound")

        with woken() as events:
            answer = email(
                channel,
                "idn-answer@example.test",
                sender="requester@xn--bcher-kva.example",
                subject="Dates",
                in_reply_to=question.external_id,
            )
        self.assertEqual(answer.sender_id, stored.pk)
        self.assertEqual(events, [(task.pk, "reply", answer.pk)])

    def test_a_requester_reachable_neither_way_is_refused_and_gets_no_account(self):
        need = self.need(email="", party=self.party(self.reader))
        before = self.task(need).revision
        with self.assertRaisesMessage(ValidationError, "neither reply on this task nor be reached by email"):
            self.clarify(need)
        task = self.task(need)
        self.assertEqual((task.revision, task.snoozed_at), (before, None))
        self.assertFalse(self.comments(task).exists())

        request = email(self.channel("manual"), "manual@example.test")
        with self.assertRaisesMessage(ValidationError, "neither reply on this task nor be reached by email"):
            self.clarify(self.Need._base_manager.get(source_message=request))
        self.assertFalse(self.User._base_manager.filter(email=REQUESTER).exists())

    def test_a_request_with_no_requester_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "This task has no requester to ask."):
            self.clarify(self.need(email=""))
        with self.assertRaisesMessage(ValidationError, "neither reply on this task nor be reached by email"):
            self.clarify(self.need(email="claimed@example.test"))
        self.assertFalse(self.User._base_manager.filter(email="claimed@example.test").exists())

    def test_the_request_to_ask_about_is_named_when_several_requesters_filed_the_task(self):
        need = self.admitted(self.reader)
        with system_context(reason="tests.intake.review.second_requester"):
            other = self.User.objects.create_user(username="second-requester", email="second@example.test")
            self.Need.objects.create(task=need.task, party=self.party(other), body="Another request")
        with self.assertRaisesMessage(ValidationError, "choose the request to ask about"):
            self.clarify(need)
        with self.assertRaisesMessage(ValidationError, "Choose a request filed on this task"):
            self.clarify(need, to=self.admitted(self.writer))
        task = self.clarify(need, to=need)
        self.assertEqual(task.snoozed_awaiting_id, self.reader.pk)

    def test_asking_oneself_is_refused(self):
        need = self.admitted(self.owner)
        with self.assertRaisesMessage(ValidationError, "cannot await the user who holds it"):
            self.clarify(need)
        self.assertFalse(self.comments(self.task(need)).exists())

    def test_a_stale_revision_asks_nothing(self):
        need = self.admitted(self.reader)
        with self.assertRaises(StaleRevisionError):
            self.clarify(need, expected_revision=self.task(need).revision + 1)
        task = self.task(need)
        self.assertIsNone(task.snoozed_at)
        self.assertFalse(self.comments(task).exists())

    def test_the_mutation_asks_a_writer_and_refuses_anyone_else(self):
        need = self.admitted(self.reader)
        task = self.task(need)
        with system_context(reason="tests.intake.review.outsider"):
            outsider = self.User.objects.create_user(username="clarify-outsider")
        for user in (self.reader, outsider):
            with self.subTest(user=user.username):
                data = self.graphql(CLARIFY, {"task": task.sqid, "question": "Which room?"}, user=user)
                self.assertFalse(data["clarify_task"]["ok"], data)
        task = self.task(need)
        self.assertEqual((task.snoozed_at, self.comments(task).count()), (None, 0))
        data = self.graphql(
            CLARIFY,
            {"task": task.sqid, "question": "Which room?", "revision": task.revision},
            user=self.owner,
        )
        self.assertTrue(data["clarify_task"]["ok"], data)
        self.assertEqual(self.task(need).snoozed_awaiting_id, self.reader.pk)

    @override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
    def test_a_request_whose_reply_to_names_no_address_is_never_asked_at_its_relay(self):
        request = email(
            self.channel(), "intranet@example.test", sender="relay@example.test",
            headers=(("Reply-To", "Requester <requester@intranet>"),),
        )
        with self.assertRaises(ValidationError) as refused:
            self.clarify(self.Need._base_manager.get(source_message=request))
        self.assertIn("to", refused.exception.error_dict)
        self.assertFalse(apps.get_model("messaging", "Message")._base_manager.filter(direction="outbound").exists())

    def reviewer_reading_the_channel(self, need, channel):
        """The task's assignee, made its editor, who reads the channel but holds no ``reply`` on it."""

        with system_context(reason="tests.intake.review.reviewer"):
            self.Task._base_manager.get(pk=need.task_id).grant_record_access("editor", self.writer)
        channel.with_actor(self.owner).grant_record_access("reader", self.writer)
        return self.writer

    @override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
    def test_a_reviewer_who_may_not_reply_on_the_channel_is_refused_as_the_one_who_may_not_send(self):
        channel = self.channel()
        need = self.Need._base_manager.get(source_message=email(channel, "no-reply-seat@example.test"))
        reviewer = self.reviewer_reading_the_channel(need, channel)
        before = self.task(need).revision
        with self.assertRaises(PermissionDenied):
            self.clarify(need, user=reviewer)
        task = self.task(need)
        self.assertEqual((task.revision, task.snoozed_at), (before, None))
        self.assertFalse(apps.get_model("messaging", "Message")._base_manager.filter(direction="outbound").exists())

    @override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
    def test_a_requester_who_may_post_is_asked_on_chatter_when_the_reviewer_may_not_send_the_email(self):
        channel = self.channel()
        with system_context(reason="tests.intake.review.requester_account"):
            requester = self.User.objects.create_user(username="chatter-requester", email=REQUESTER)
        need = self.Need._base_manager.get(source_message=email(channel, "chatter-only@example.test"))
        reviewer = self.reviewer_reading_the_channel(need, channel)
        with system_context(reason="tests.intake.review.requester_post"):
            self.Task._base_manager.get(pk=need.task_id).grant_record_access("editor", requester)
        task = self.clarify(need, user=reviewer)
        self.assertEqual(task.snoozed_awaiting_id, requester.pk)
        (question,) = self.comments(task)
        notified = apps.get_model("messaging", "ThreadNotification")._base_manager.filter(message=question)
        self.assertIn(requester.pk, set(notified.values_list("user_id", flat=True)))
        self.assertFalse(apps.get_model("messaging", "Message")._base_manager.filter(direction="outbound").exists())

    @override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
    def test_the_email_route_asks_what_compose_enforces_for_a_reviewer_who_does_not_own_the_channel(self):
        message_model = apps.get_model("messaging", "Message")
        channel = self.channel()
        replier = self.writer
        channel.with_actor(self.owner).grant_record_access("replier", replier)
        with system_context(reason="tests.intake.review.stranger"):
            stranger = self.User.objects.create_user(username="route-stranger")
        rows = {
            "sender": (email(channel, "row-sender@example.test"), True),
            "reply-to": (email(channel, "row-relay@example.test", sender="relay@example.test",
                               headers=(("Reply-To", REQUESTER),)), True),
            "reply-to without an address": (email(channel, "row-intranet@example.test", sender="relay@example.test",
                                                  headers=(("Reply-To", "requester@intranet"),)), True),
            "delivery not configured": (email(channel, "row-unconfigured@example.test"), False),
        }
        for name, (source, configured) in rows.items():
            for user in (replier, stranger):
                with self.subTest(row=name, user=user.username), override_settings(
                    ANGEE_EMAIL_DELIVERY_CONFIGURED=configured,
                ), actor_context(user):
                    route, _refused = self.Task._email_reply_route(
                        [SimpleNamespace(source_message=source)], actor=user,
                    )
                    try:
                        message_model.objects.compose(parent=source, text="Which dates?", actor=user)
                    except (PermissionDenied, ValidationError):
                        composed = False
                    else:
                        composed = True
                    self.assertEqual(route is source, composed)
                    self.assertEqual(composed, user == replier and name in ("sender", "reply-to"))


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
                    project=project,
                    name="Review",
                    active_stage=reviewing,
                )
            project.with_actor(self.owner).set_current_milestone(phase)
        task.refresh_from_db()
        self.assertEqual(task.stage_id, reviewing.pk)
        self.assertTrue(GraphQLSchemas.from_discovery().render_sdl())
