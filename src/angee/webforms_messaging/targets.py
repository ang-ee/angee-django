"""A web form's responses posted as messages on a messaging channel.

The ``message`` target hands each response to messaging's one ingest path
(:meth:`~angee.messaging.managers.MessageManager.ingest`) as a message of the
channel its form names. The message carries the ``webform`` envelope that
:meth:`~angee.messaging.models.Message.webform_submission` reads: the answers,
and the address the respondent claims when the version's contact question asks
for an email. Whoever consumes the message reads that envelope, never the
response.
"""

from __future__ import annotations

import json
from typing import Any, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart, ParsedThread
from angee.webforms.models import SLUG
from angee.webforms.targets import DeliveryRefused, FormTarget
from angee.webforms.validators import OTHER, OTHER_SUFFIX, questions
from angee.webforms_messaging.backends import WebformChannelBackend


class MessageTargetConfig(BaseModel):
    """The channel a form's responses are posted on, and the name its messages carry there."""

    model_config = ConfigDict(extra="forbid")

    channel: str = Field(
        min_length=1,
        description="A channel with the Public Webform backend.",
        json_schema_extra={"relation": {"resource": "messaging.Channel"}},
    )
    slug: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "The name this form's messages carry on the channel. They share one conversation, "
            "webform:<slug>; keep it once messages exist."
        ),
        json_schema_extra={"widget": "slug"},
    )

    @field_validator("slug")
    @classmethod
    def address_shaped(cls, slug: str) -> str:
        """Hold the slug to the rule of a form's address, which it usually repeats."""

        try:
            SLUG(slug)
        except ValidationError as error:
            raise ValueError(error.messages[0]) from error
        return slug


class MessageTarget(FormTarget):
    """Post each response as a message on a channel with the ``webform`` backend.

    The message's id on its channel is ``webform:<slug>:<submission id>``, so
    delivering one response again finds the message it already posted. Every
    response of the form lands in the conversation ``webform:<slug>``, titled
    with the form's name.
    """

    key = "message"
    label = "Post to a messaging channel"
    config_model = MessageTargetConfig

    def permits(self, actor: Any) -> bool:
        """Whether ``actor`` may post on the channel this configuration names.

        Messaging decides: the answer is the channel's own ``write`` check, on a
        webform channel the actor can read.
        """

        config = cast(MessageTargetConfig, self.config)
        channel = (
            apps.get_model("messaging", "Channel").objects.with_actor(actor)
            .for_backend(WebformChannelBackend.key).from_public_id(config.channel)
        )
        return channel is not None and bool(channel.with_actor(actor).has_access("write"))

    def deliver(self, response: Any) -> Any:
        """Post ``response`` on the form's channel and return its message.

        A response is refused in three ways, and none is tried again by
        itself: ``channel_missing`` when the channel the form names is gone or
        is no webform channel; ``message_exists`` when the channel already holds
        a message under this submission's id that this response did not make
        (one received before forms had responses), which posting would replace;
        and ``message_refused`` when messaging will not take the message as it
        is (an envelope past its size bound). Any other error is a failure.
        """

        config = cast(MessageTargetConfig, self.config)
        messages = apps.get_model("messaging", "Message").objects
        try:
            channel = apps.get_model("messaging", "Channel").objects.require_backend_target(
                config.channel, backend_class=WebformChannelBackend.key,
            )
        except ValidationError:
            channel = None
        if channel is None:
            raise DeliveryRefused("channel_missing")
        parsed = self.message(response)
        held = messages.with_external_ids([parsed.external_id]).filter(channel=channel).first()
        if held is not None and not type(response).objects.for_record(held).filter(pk=response.pk).exists():
            raise DeliveryRefused("message_exists")
        try:
            landed = messages.ingest([parsed], channel=channel, quote_edges=WebformChannelBackend.quote_edges)
        except ValidationError:
            landed = None
        if landed is None:
            # Raised outside the handler: what messaging refused may quote an answer; a refusal keeps its code alone.
            raise DeliveryRefused("message_refused")
        (message,) = landed
        return message

    def message(self, response: Any) -> ParsedMessage:
        """Map ``response`` onto messaging's neutral ingest shape.

        An answer to the version's contact question is the respondent's claimed
        address when that question asks for an email: it leaves the answers and
        the body and travels as ``unverified_submitter_email``, which proves no
        identity.
        """

        slug = cast(MessageTargetConfig, self.config).slug
        version = response.version
        asked = questions(version.definition)
        answers = dict(response.answers)
        contact = next((question for question in asked if question["name"] == version.contact_question), None)
        email = ""
        if contact is not None and contact["type"] == "text" and contact.get("inputType") == "email":
            email = str(answers.pop(contact["name"], "") or "").strip()
        stable_id = f"webform:{slug}:{response.submission_id}"
        return ParsedMessage(
            external_id=stable_id,
            platform="other",
            sender=ParsedHandle(platform="other", value=stable_id, external_id=stable_id, display_name="Anonymous"),
            sent_at=response.created_at,
            received_at=response.created_at,
            thread=ParsedThread(
                external_id=f"webform:{slug}", title=response.form.name, metadata={"webform_slug": slug},
            ),
            body=ParsedPart(type="text/plain", role="body", text=_body(asked, answers)),
            metadata={
                "webform": {
                    "slug": slug,
                    "schema_version": version.number,
                    "submission_id": response.submission_id,
                    "answers": answers,
                    "unverified_submitter_email": email or None,
                }
            },
        )


def _body(asked: list[dict[str, Any]], answers: dict[str, Any]) -> str:
    """Render the answers under their questions, in the order the form asks them."""

    blocks: list[str] = []
    for question in asked:
        name = question["name"]
        if name not in answers:
            continue
        title = question.get("title", name)
        heading = " ".join(str(title["default"] if isinstance(title, dict) else title).split()) or name
        blocks.append(f"## {heading}\n\n{_answer(answers[name], answers.get(f'{name}{OTHER_SUFFIX}', ''))}")
    return "\n\n".join(blocks)


def _answer(value: Any, other: str) -> str:
    """Render one answer; the text a respondent gave for "Other" stands in for its code."""

    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(_answer(item, other) for item in value)
    if value == OTHER and other:
        return other
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)
