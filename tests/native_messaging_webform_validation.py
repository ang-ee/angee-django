"""Messaging's native HTTP contracts for an isolated composed webform host."""

from __future__ import annotations

import json
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TransactionTestCase
from example.webform_semantics.models import ChannelSemanticValidation
from rebac import system_context

from angee.messaging.models import Channel as AbstractChannel
from angee.messaging.models import ChannelWebform


class ComposedWebformValidationTests(TransactionTestCase):
    def setUp(self) -> None:
        call_command("rebac", "sync", verbosity=0)
        self.Channel = apps.get_model("messaging", "Channel")
        self.Message = apps.get_model("messaging", "Message")
        self.Need = apps.get_model("intake", "Need")
        self.Task = apps.get_model("projects", "Task")
        with system_context(reason="test public form setup"):
            actor = get_user_model().objects.create_user(username="form-owner")
            apps.get_model("integrate", "Vendor").objects.get_or_create(
                slug="webform", defaults={"display_name": "Webform"},
            )
            queue = apps.get_model("work", "Queue").objects.personal_for(actor, provision=True)
            self.channel = self.Channel.objects.create_disconnected(
                actor,
                name="Request form",
                backend_class="webform",
                slug="request-form",
                is_published=True,
                form_schema={
                    "type": "object",
                    "required": ["subject"],
                    "properties": {
                        "subject": {"type": "string"},
                        "when": {"type": "string"},
                        "amount": {"type": "number"},
                    },
                    "success": {"title": "Request received", "body": "We will review it."},
                },
            )
            self.channel.intake_queue = queue
            self.channel.intake_field_map = {"title": "subject"}
            self.channel.full_clean()
            self.channel.save()

    def submit(self, submission_id: str, answers: dict[str, object], *, slug: str = "request-form"):
        return self.client.post(
            f"/forms/{slug}",
            data=json.dumps({"submission_id": submission_id, "answers": answers}),
            content_type="application/json",
        )

    def test_composed_donor_validates_semantics_before_any_write_and_replay_is_idempotent(self) -> None:
        mro = self.Channel.__mro__
        self.assertLess(mro.index(ChannelWebform), mro.index(ChannelSemanticValidation))
        self.assertLess(mro.index(ChannelSemanticValidation), mro.index(AbstractChannel))
        description = self.client.get("/forms/request-form")
        self.assertEqual(description.status_code, 200)
        self.assertEqual(description.json()["success"], {
            "title": "Request received", "body": "We will review it.",
        })

        for field, value in (("when", "2026-02-30"), ("amount", -0.5)):
            response = self.submit(f"bad-{field}", {"subject": "Request", field: value})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"], "invalid_submission")
            self.assertIn(field, response.json()["fields"])
            self.assertEqual(self.Message._base_manager.count(), 0)
            self.assertEqual(self.Need._base_manager.count(), 0)
            self.assertEqual(self.Task._base_manager.count(), 0)

        valid = {"subject": "Request", "when": "2026-02-28", "amount": 1.25}
        self.assertEqual(self.submit("valid-1", valid).status_code, 202)
        counts = (
            self.Message._base_manager.count(),
            self.Need._base_manager.count(),
            self.Task._base_manager.count(),
        )
        self.assertGreaterEqual(counts[0], 1)
        self.assertEqual(counts[1:], (1, 1))
        self.assertEqual(self.submit("valid-1", valid).status_code, 202)
        self.assertEqual((
            self.Message._base_manager.count(),
            self.Need._base_manager.count(),
            self.Task._base_manager.count(),
        ), counts)

    def test_mapping_errors_use_structured_boundary_and_other_channels_are_unaffected(self) -> None:
        with patch.object(self.Channel, "webform_message", side_effect=ValidationError({"when": "Mapping rejected."})):
            response = self.submit("mapping-error", {"subject": "Request"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["fields"], {"when": ["Mapping rejected."]})
        self.assertEqual(self.Message._base_manager.count(), 0)

        with system_context(reason="test unrelated webform"):
            self.Channel.objects.create_disconnected(
                self.channel.created_by,
                name="Other form",
                backend_class="webform",
                slug="other-form",
                is_published=True,
                form_schema=self.channel.form_schema,
            )
        response = self.submit(
            "other-1", {"subject": "Other request", "when": "2026-02-30", "amount": -0.5},
            slug="other-form",
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.Message._base_manager.count(), 1)

    def test_success_copy_is_checked_with_the_published_form_spec(self) -> None:
        self.channel.form_schema = {**self.channel.form_schema, "success": {"title": "", "body": 4}}
        with self.assertRaises(ValidationError):
            self.channel.webform_spec()
