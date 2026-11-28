"""Mbox history against composed channels, Storage and the real archive workflow."""

from __future__ import annotations

import hashlib
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TransactionTestCase
from rebac import actor_context, system_context
from rebac.roles import grant as grant_role

from angee.messaging_integrate_imap import extractor
from angee.messaging_integrate_imap.parser import parse_message
from angee.workflows.context import StepContext
from angee.workflows.steps import NextPage
from angee.workflows.testing.drivers import (
    capture_tasks,
    decide,
    deliver_decision_wakes,
    load_workflow,
    run_until,
    start_run,
)
from angee.workflows_integrate.archive_steps import ArchiveExecutionReporter


def email_bytes(identity="one", *, parent="", sender="sender@example.test", attachment=False):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "recipient@example.test" if sender == "owner@example.test" else "owner@example.test"
    message["Date"] = "Thu, 02 Jul 2026 10:00:00 +0000"
    message["Subject"] = f"Unique subject {identity}"
    if identity:
        message["Message-ID"] = f"<{identity}@example.test>"
    if parent:
        message["In-Reply-To"] = f"<{parent}@example.test>"
    message.set_content(f"Body for {identity}\n")
    if attachment:
        message.add_attachment(b"PDFDATA", maintype="application", subtype="pdf", filename="document.pdf")
    return message.as_bytes()


def mbox_bytes(*messages):
    return b"".join(b"From sender@example.test Thu Jul  2 10:00:00 2026\n" + raw.rstrip(b"\n") + b"\n\n"
                    for raw in messages)


class MboxImportTests(TransactionTestCase):
    def setUp(self):
        self.sent = self.enterContext(capture_tasks())
        call_command("rebac", "sync", verbosity=0)
        self.root = Path(self.enterContext(TemporaryDirectory(prefix="mbox-import-")))
        self.enterContext(patch(
            "angee.messaging_integrate_imap.backend.IMAPClient",
            side_effect=AssertionError("An offline archive must not connect to IMAP"),
        ))
        with system_context(reason="mbox fixtures"):
            self.actor = get_user_model().objects.create_user(username="archive-owner")
            grant_role(actor=self.actor, role="angee/role:admin")
            backend = apps.get_model("storage.Backend").objects.create(
                slug="local", label="Local", backend_class="local",
                backend_config={"root": str(self.root), "base_url": "/media/"},
            )
            self.drive = apps.get_model("storage.Drive").objects.create(
                backend=backend, slug="assets", name="Assets", prefix="assets", created_by=self.actor,
            )
            vendor, _ = apps.get_model("integrate.Vendor").objects.get_or_create(
                slug="imap", defaults={"display_name": "IMAP"},
            )
            self.channel = apps.get_model("messaging.Channel").objects.create(
                vendor=vendor, owner=self.actor, display_name="Mailbox", backend_class="imap", lifecycle="paused",
                config={"own_addresses": ["owner@example.test"]},
            )
        self.messages = apps.get_model("messaging.Message").objects

    def file(self, content, *, referenced=False):
        with actor_context(self.actor):
            files = apps.get_model("storage.File").objects
            if referenced:
                (self.root / "mail.mbox").write_bytes(content)
                return files.index_external(
                    drive=self.drive, storage_path="mail.mbox", filename="mail.mbox",
                    content_hash=hashlib.sha256(content).hexdigest(), size_bytes=len(content), owner_id=self.actor.pk,
                )
            return files.ingest_bytes(content, filename="mail.mbox", drive_id=str(self.drive.sqid))[0]

    def reporter(self, *, state=None, actor=None):
        context = SimpleNamespace(
            state=state or {}, actor=actor or self.actor,
            heartbeat=Mock(), raise_if_canceled=Mock(), next_page=lambda state: NextPage(state=state),
        )
        context.load = MethodType(StepContext.load, context)
        return ArchiveExecutionReporter(context)

    def page(self, file, *, state=None, actor=None):
        with actor_context(actor or self.actor):
            return self.channel.import_imap_mbox(file, reporter=self.reporter(state=state, actor=actor))

    def drain(self, file, *, state=None):
        for _ in range(100):
            result = self.page(file, state=state)
            if not isinstance(result, NextPage):
                return result
            state = result.state
        self.fail("Import did not converge")

    def test_referenced_archive_is_historical_idempotent_and_retains_mime(self):
        file = self.file(mbox_bytes(
            email_bytes("one", attachment=True), email_bytes("two", sender="owner@example.test"), email_bytes(""),
        ), referenced=True)
        with patch("angee.messaging.managers.message_ingested.send") as event:
            self.assertEqual(self.drain(file)["entries"], 3)
            self.drain(file)
            event.assert_not_called()
        with system_context(reason="mbox assertions"):
            self.assertEqual(self.messages.count(), 3)
            one = self.messages.get(external_id="one@example.test")
            two = self.messages.get(external_id="two@example.test")
            self.assertEqual(two.direction, "outbound")
            self.assertEqual(one.sent_at.year, 2026)
            self.assertEqual(one.metadata["archive"]["file"], str(file.sqid))
            self.assertNotIn("uid", one.metadata)
            self.assertNotIn("uidvalidity", one.metadata)
            part = apps.get_model("messaging.Part").objects.get(message=one, file__isnull=False)
            with part.file.open_stream() as stream:
                self.assertEqual(stream.read(), b"PDFDATA")
            streams = apps.get_model("integrate.SyncStream").objects.filter(integration_id=self.channel.pk)
            self.assertFalse(streams.exists())

    def test_existing_live_observation_is_not_replaced(self):
        raw = email_bytes("one")
        with system_context(reason="live message fixture"):
            original = self.messages.ingest(
                [parse_message(raw, mailbox="INBOX", uid=18, uidvalidity=40)], channel=self.channel,
            )[0]
            before = (original.metadata, original.thread_id, original.updated_at)
        self.drain(self.file(mbox_bytes(raw.replace(b"Body for one", b"Old archive body"))))
        with system_context(reason="preserved live message"):
            original.refresh_from_db()
            self.assertEqual(self.messages.count(), 1)
            self.assertEqual((original.metadata, original.thread_id, original.updated_at), before)

    def test_lost_checkpoint_replays_and_heals_a_reverse_reply_chain_across_pages(self):
        file = self.file(mbox_bytes(
            email_bytes("four", parent="three"), email_bytes("three", parent="two"),
            email_bytes("two", parent="one"), email_bytes("one"),
        ))
        with patch.object(extractor, "PAGE_MESSAGES", 1):
            first = self.page(file)
            self.assertIsInstance(first, NextPage)
            self.page(file)  # Writes committed, but the first checkpoint was lost.
            result = self.drain(file, state=first.state)
        self.assertEqual(result["entries"], 4)
        with system_context(reason="reply chain assertions"):
            rows = list(self.messages.order_by("external_id"))
            self.assertEqual(len(rows), 4)
            self.assertEqual(len({row.thread_id for row in rows}), 1)
            self.assertEqual(self.messages.filter(parent__isnull=False).count(), 3)

    def test_changed_mount_bytes_are_rejected_before_the_next_page(self):
        file = self.file(mbox_bytes(email_bytes("one"), email_bytes("two")), referenced=True)
        with patch.object(extractor, "PAGE_MESSAGES", 1):
            first = self.page(file)
            file.local_path().write_bytes(mbox_bytes(email_bytes("changed")))
            with self.assertRaisesMessage(ValidationError, "Re-index it first"):
                self.page(file, state=first.state)
        with system_context(reason="partial import assertion"):
            self.assertEqual(self.messages.count(), 1)

    def test_remote_storage_uses_a_temporary_copy_and_cleans_it_up(self):
        file = self.file(mbox_bytes(email_bytes()))
        paths = []
        native_mbox = extractor.mailbox.mbox

        def record_path(path, **kwargs):
            paths.append(path)
            return native_mbox(path, **kwargs)

        with patch.object(file, "local_path", return_value=None), patch.object(
            extractor.mailbox, "mbox", side_effect=record_path,
        ):
            self.drain(file)
        self.assertTrue(paths)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_parser_failure_retains_raw_mail_and_worker_cancellation_propagates(self):
        file = self.file(mbox_bytes(email_bytes()))
        with patch("angee.messaging_integrate_imap.parser.parse_message", side_effect=ValueError("broken MIME")):
            self.drain(file)
        with system_context(reason="fallback assertion"):
            part = apps.get_model("messaging.Part").objects.get(file__isnull=False)
            with part.file.open_stream() as stream:
                self.assertIn(b"Message-ID: <one@example.test>", stream.read())
        another = self.file(mbox_bytes(email_bytes("canceled")))
        with patch("angee.messaging_integrate_imap.parser.parse_message", side_effect=SoftTimeLimitExceeded):
            with self.assertRaises(SoftTimeLimitExceeded):
                self.page(another)
        with system_context(reason="canceled import assertion"):
            self.assertEqual(self.messages.count(), 1)

    def test_invalid_sources_and_oversized_messages_fail_without_writes(self):
        for content in (b"", b"Not an mbox", b"From pretend\nnot mail\n"):
            with self.subTest(content=content):
                file = self.file(content)
                self.assertFalse(extractor.MboxExtractor().recognizes(file))
                with self.assertRaises(ValidationError):
                    self.page(file)
        file = self.file(mbox_bytes(email_bytes()))
        with patch.object(extractor, "MESSAGE_BYTES", 10):
            with self.assertRaisesMessage(ValidationError, "message limit"):
                self.page(file)
        with system_context(reason="invalid import assertions"):
            self.assertEqual(self.messages.count(), 0)

    def test_channel_and_file_authorization_are_rechecked(self):
        file = self.file(mbox_bytes(email_bytes()))
        with system_context(reason="read-only user"):
            reader = get_user_model().objects.create_user(username="reader")
        self.channel.with_actor(self.actor).grant_record_access("reader", reader)
        with self.assertRaises(PermissionDenied):
            self.page(file, actor=reader)
        file.with_actor(self.actor).grant_record_access("viewer", reader)
        with self.assertRaises(PermissionDenied):
            self.page(file, actor=reader)
        for values in ({"lifecycle": "connected"}, {"lifecycle": "paused", "backend_class": "manual"}):
            with system_context(reason="changed channel"):
                type(self.channel).objects.filter(pk=self.channel.pk).update(**values)
            with self.assertRaises(ValidationError):
                self.page(file)

    def test_workflow_without_paused_targets_can_retry_into_destination_review(self):
        file = self.file(mbox_bytes(email_bytes()))
        with system_context(reason="connected mailbox"):
            type(self.channel).objects.filter(pk=self.channel.pk).update(lifecycle="connected")
        workflow = load_workflow("angee.workflows_integrate.archive_import_file", actor=self.actor, allow_non_dev=True)
        run = start_run(workflow, actor=self.actor, subject=file)
        run_until(run)
        with system_context(reason="missing destination assertion"):
            run.refresh_from_db()
            gate = run.step_runs.get(node_key="gate")
            self.assertEqual(run.status, "failed")
            self.assertIn("Pause an IMAP channel", gate.failure_reason)
            self.assertIsNone(gate.decision_id)
            self.assertEqual(self.messages.count(), 0)
            type(self.channel).objects.filter(pk=self.channel.pk).update(lifecycle="paused")
        apps.get_model("workflows.StepRun").objects.retry_step(gate, actor=self.actor)
        run_until(run)
        with system_context(reason="retried destination review"):
            gate.refresh_from_db()
            self.assertEqual(gate.waiting_kind, "decision")
            self.assertEqual(set(gate.state["mappings"]), {str(self.channel.sqid)})
            self.assertEqual(self.messages.count(), 0)

    def test_workflow_offers_only_paused_imap_targets_and_runs_native_continuations(self):
        file = self.file(mbox_bytes(email_bytes("one"), email_bytes("two")))
        with system_context(reason="ineligible targets"):
            for backend, lifecycle in (("imap", "connected"), ("manual", "paused")):
                type(self.channel).objects.create(
                    vendor=self.channel.vendor, owner=self.actor, display_name="Ineligible",
                    backend_class=backend, lifecycle=lifecycle,
                )
        workflow = load_workflow("angee.workflows_integrate.archive_import_file", actor=self.actor, allow_non_dev=True)
        run = start_run(workflow, actor=self.actor, subject=file)
        run_until(run)
        with system_context(reason="review targets"):
            gate = run.step_runs.get(node_key="gate")
            self.assertEqual(set(gate.state["mappings"]), {str(self.channel.sqid)})
            decision = apps.get_model("decisions.Decision").objects.get(pk=gate.decision_id)
        decide(decision, actor=self.actor, chosen=[str(self.channel.sqid)])
        deliver_decision_wakes(self.sent, decision.pk)
        with patch.object(extractor, "PAGE_MESSAGES", 1):
            run_until(run)
        with system_context(reason="workflow result"):
            run.refresh_from_db()
            self.assertEqual((run.status, run.outcome), ("succeeded", "complete"))
            mapped = run.step_runs.get(node_key="import_units")
            self.assertEqual(mapped.output[0]["output"]["result"]["entries"], 2)
            self.assertEqual(self.messages.count(), 2)
