"""A form's responses posted as messages: the ``message`` target.

A response is received and delivered through the web forms' own verbs; what the
target made is read back from messaging. Every name and address is invented.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from rebac import system_context

from angee.messaging.models import WebformSubmission
from angee.messaging.testing.models import Channel, Message
from angee.webforms.models import Delivery
from angee.webforms.testing.models import Form, FormVersion, Response
from tests.conftest import Vendor, create_user

pytestmark = pytest.mark.django_db

DEFINITION: dict[str, Any] = {
    "elements": [
        {"type": "text", "name": "name", "isRequired": True, "title": {"default": "Your name", "es": "Tu nombre"}},
        {"type": "text", "name": "email", "inputType": "email"},
        {"type": "text", "name": "phone", "inputType": "tel"},
        {
            "type": "checkbox", "name": "topics", "showOtherItem": True,
            "choices": [{"value": "billing"}, {"value": "delivery"}],
        },
        {"type": "boolean", "name": "urgent", "title": "Is it urgent?"},
    ],
}
ANSWERS = {
    "name": "Ada Example", "email": "ada@example.test", "phone": "555 0100",
    "topics": ["billing", "other"], "topics-Comment": "A refund", "urgent": True,
}


def channel(backend: str = "webform", name: str = "Enquiries") -> Any:
    """A channel of ``backend``, owned by a user made for it."""

    with system_context(reason="webforms messaging tests setup"):
        Vendor.objects.get_or_create(slug=backend, defaults={"display_name": backend.title()})
        return Channel.objects.create_disconnected(create_user(f"{name}-owner"), name=name, backend_class=backend)


def form_on(target_channel: Any, *, contact: str = "email", slug: str = "enquiries", name: str = "Enquiry form") -> Any:
    """A published form whose responses are posted on ``target_channel``."""

    with system_context(reason="webforms messaging tests setup"):
        form = Form.objects.create(
            name=name, target_class="message", target_config={"channel": target_channel.sqid, "slug": slug},
        )
        FormVersion.objects.create(
            form=form, definition=DEFINITION, languages=["en", "es"], default_language="en",
            personal_questions=["name", "email", "phone"], contact_question=contact,
        ).publish()
    form.refresh_from_db()
    return form


def delivered(form: Any, receipt: str = "r-1", **answers: Any) -> tuple[Any, Any]:
    """Receive one submission and hand it to the form's target; return the response and what it became."""

    row, _made = Response.objects.receive(
        form, version=1, submission_id=receipt, language="es", answers={**ANSWERS, **answers},
    )
    return row, row.deliver()


def test_a_response_becomes_one_message_on_its_forms_channel_under_the_submissions_stable_id() -> None:
    target = channel()
    form = form_on(target)

    row, message = delivered(form)
    again = row.form.target().deliver(row)

    with system_context(reason="webforms messaging tests read"):
        (stored,) = Message._base_manager.filter(channel=target)
        body = stored.body_text()
        title = stored.thread.title.text
    assert (message.pk, again.pk, row.record().pk) == (stored.pk, stored.pk, stored.pk)
    assert stored.external_id == "webform:enquiries:r-1"
    assert title == "Enquiry form"
    assert body == (
        "## Your name\n\nAda Example\n\n## phone\n\n555 0100\n\n## topics\n\nbilling, A refund\n\n"
        "## Is it urgent?\n\nYes"
    )
    assert Response._base_manager.get(pk=row.pk).delivery == Delivery.DELIVERED


def test_the_message_carries_the_envelope_messaging_reads_and_every_response_shares_one_conversation() -> None:
    target = channel()
    form = form_on(target)

    _first, message = delivered(form)
    _second, other = delivered(form, "r-2", name="Grace Example")

    content = {name: value for name, value in ANSWERS.items() if name != "email"}
    assert message.webform_submission() == WebformSubmission(
        answers=content, unverified_submitter_email="ada@example.test",
    )
    assert message.metadata["webform"] == {
        "slug": "enquiries", "schema_version": 1, "submission_id": "r-1", "answers": content,
        "unverified_submitter_email": "ada@example.test",
    }
    assert (other.thread_id, other.external_id) == (message.thread_id, "webform:enquiries:r-2")


@pytest.mark.parametrize(("contact", "answers", "claimed", "kept"), [
    pytest.param("email", {}, "ada@example.test", False, id="an-email-question"),
    pytest.param("email", {"email": ""}, None, False, id="an-email-question-left-empty"),
    pytest.param("phone", {}, None, True, id="a-contact-question-that-is-no-email"),
    pytest.param("", {}, None, True, id="no-contact-question"),
])
def test_the_claimed_address_is_the_answer_to_a_contact_question_that_asks_for_an_email(
    contact: str, answers: dict[str, Any], claimed: str | None, kept: bool,
) -> None:
    form = form_on(channel(), contact=contact)

    _row, message = delivered(form, **answers)

    envelope = message.webform_submission()
    assert envelope.unverified_submitter_email == claimed
    assert ("email" in envelope.answers) is kept
    assert envelope.answers["phone"] == "555 0100"


def test_a_target_that_names_no_webform_channel_fails_the_delivery_where_staff_see_it() -> None:
    mailbox = channel("manual", "Mailbox")
    form = form_on(mailbox)

    row, _made = Response.objects.receive(form, version=1, submission_id="r-1", language="en", answers=ANSWERS)
    with pytest.raises(ValidationError):
        row.deliver()

    stored = Response._base_manager.get(pk=row.pk)
    assert stored.delivery == Delivery.FAILED
    assert f"No Public Webform channel '{mailbox.sqid}'" in stored.delivery_error
    assert not Message._base_manager.filter(channel=mailbox).exists()


@pytest.mark.parametrize("config", [
    pytest.param({}, id="nothing"),
    pytest.param({"channel": "int_1"}, id="no-slug"),
    pytest.param({"slug": "enquiries"}, id="no-channel"),
    pytest.param({"channel": "int_1", "slug": "Not A Slug"}, id="a-slug-that-is-no-slug"),
    pytest.param({"channel": "int_1", "slug": "enquiries", "thread": "other"}, id="a-key-it-does-not-have"),
])
def test_a_form_is_refused_a_message_target_that_does_not_name_its_channel_and_slug(config: dict[str, Any]) -> None:
    form = Form(name="Enquiry form", target_class="message", target_config=config)

    with pytest.raises(ValidationError) as refused:
        form.clean()

    assert "target_config" in refused.value.message_dict

