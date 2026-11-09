"""ChannelWebform submission against the observed published form, on the composed channel."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest import skipUnless

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, transaction
from django.test import TransactionTestCase
from rebac import system_context

from angee.messaging.webforms import WebformChanged, WebformUnavailable
from tests.lock_waits import submit, wait_for_lock


class WebformFixture(TransactionTestCase):
    """One published webform channel and the HTTP view's observe-validate-submit sequence."""

    def setUp(self) -> None:
        call_command("rebac", "sync", verbosity=0)
        self.Channel = apps.get_model("messaging", "Channel")
        self.Message = apps.get_model("messaging", "Message")
        with system_context(reason="test webform submission setup"):
            owner = get_user_model().objects.create_user(username="webform-submission-owner")
            apps.get_model("integrate", "Vendor").objects.get_or_create(
                slug="webform",
                defaults={"display_name": "Webform"},
            )
            self.channel = self.Channel.objects.create_disconnected(
                owner,
                name="Request form",
                backend_class="webform",
                slug="request-form",
                is_published=True,
                form_schema={"type": "object", "required": ["subject"], "properties": {"subject": {"type": "string"}}},
            )

    def observed(self) -> Any:
        """Read the published form the way the view does before validating answers."""

        with system_context(reason="test webform observed"):
            return self.Channel.objects.published_webforms("request-form").get()

    @staticmethod
    def submission(channel: Any, submission_id: str) -> Any:
        return channel.webform_message(submission_id=submission_id, answers={"subject": "Request"})

    def change(self, **values: Any) -> None:
        self.Channel._base_manager.filter(pk=self.channel.pk).update(**values)

    def landed(self) -> int:
        return self.Message._base_manager.filter(channel_id=self.channel.pk).count()


class WebformSubmissionTests(WebformFixture):
    def test_submission_lands_on_the_observed_published_form(self) -> None:
        channel = self.observed()
        channel.submit_webform(self.submission(channel, "landed-1"))
        self.assertEqual(self.landed(), 1)

    def test_submission_refuses_a_form_unpublished_after_it_was_observed(self) -> None:
        channel = self.observed()
        self.change(is_published=False)
        with self.assertRaises(WebformUnavailable):
            channel.submit_webform(self.submission(channel, "unpublished-1"))
        self.assertEqual(self.landed(), 0)

    def test_submission_refuses_a_form_changed_after_it_was_observed(self) -> None:
        channel = self.observed()
        self.change(form_schema_version=2)
        with self.assertRaises(WebformChanged):
            channel.submit_webform(self.submission(channel, "changed-1"))
        self.assertEqual(self.landed(), 0)


@skipUnless(connection.vendor == "postgresql", "Real PostgreSQL row locks are required.")
class WebformSubmissionLockTests(WebformFixture):
    def race(self, error: type[Exception], **values: Any) -> None:
        """Hold the channel row while a submission validated against the old form arrives."""

        channel = self.observed()
        parsed = self.submission(channel, "raced-1")
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                self.Channel.system_queryset(lock=("self",)).get(pk=self.channel.pk)
                contender, pid = submit(pool, lambda: channel.submit_webform(parsed))
                wait_for_lock(pid, contender)
                self.change(**values)
            with self.assertRaises(error):
                contender.result(timeout=10)
        self.assertEqual(self.landed(), 0)

    def test_submission_waits_for_a_concurrent_unpublish(self) -> None:
        self.race(WebformUnavailable, is_published=False)

    def test_submission_waits_for_a_concurrent_form_change(self) -> None:
        self.race(WebformChanged, max_field_bytes=1_024)

