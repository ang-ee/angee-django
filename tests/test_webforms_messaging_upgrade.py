"""The webform channel template, applied through Django's executor to the previous release's rows.

The previous release's rows carry the channel's form columns and its slug
constraint: the test adds both to the channel table under a migration state
derived from the installed models, so the template's callable runs on historical
models, never live ones. Every name and address is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import connection, migrations, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ProjectState
from rebac import system_context

from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart, ParsedThread
from angee.messaging.testing.models import Channel, Message
from angee.messaging.webforms import default_webform_schema
from angee.webforms.models import FormStatus
from angee.webforms.testing.models import Form, FormAlias, FormVersion, Response
from angee.webforms_messaging.compose_migrations.messaging import webform_channels_to_forms as template
from angee.webforms_messaging.targets import MessageTarget
from tests.conftest import Vendor, create_user

_NAME = "0099_webform_channels_to_forms"
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["subject", "email"],
    "propertyOrder": ["subject", "email", "details", "budget", "urgent", "topic"],
    "properties": {
        "topic": {
            "type": "string", "label": "Topic",
            "options": [
                {"value": "billing", "label": "Billing"},
                {"value": "retired", "label": "Retired", "disabled": True},
                {"value": "delivery", "label": "Delivery"},
            ],
        },
        "subject": {"type": "string", "label": "Subject", "maxLength": 120, "placeholder": "One line"},
        "email": {"type": "string", "widget": "email", "title": "Your email"},
        "details": {"type": "string", "widget": "textarea", "description": "Anything else."},
        "budget": {"type": "number", "minimum": 0, "maximum": 5000},
        "urgent": {"type": "boolean"},
    },
    "success": {"title": "Request received", "body": "We'll reply within 2 days & no later."},
}
_DEFINITION: dict[str, Any] = {
    "title": "Request form",
    "elements": [
        {
            "type": "text", "name": "subject", "title": "Subject", "placeholder": "One line", "maxLength": 120,
            "isRequired": True,
        },
        {"type": "text", "inputType": "email", "name": "email", "title": "Your email", "isRequired": True},
        {"type": "comment", "name": "details", "description": "Anything else."},
        {"type": "text", "inputType": "number", "name": "budget", "min": 0, "max": 5000},
        {"type": "boolean", "name": "urgent"},
        {
            "type": "dropdown", "name": "topic", "title": "Topic",
            "choices": [{"value": "billing", "text": "Billing"}, {"value": "delivery", "text": "Delivery"}],
        },
    ],
    "completedHtml": "<h3>Request received</h3><p>We'll reply within 2 days &amp; no later.</p>",
}


def _channels(**backends: str) -> dict[str, Any]:
    """Channels as the previous release made them, each named after its key."""

    with system_context(reason="webform upgrade tests setup"):
        owner = create_user("form-channels-owner")
        for backend in set(backends.values()):
            Vendor.objects.get_or_create(slug=backend, defaults={"display_name": backend.title()})
        return {
            key: Channel.objects.create_disconnected(
                owner, name=f"{key.replace('_', ' ').capitalize()} form", backend_class=backend,
            )
            for key, backend in backends.items()
        }


@contextmanager
def _previous_release(**held: dict[str, Any]) -> Iterator[ProjectState]:
    """Give the channel table the previous release's form columns, holding ``held`` by channel id.

    Yields the migration state that has them. Whatever the block leaves of them
    is removed afterwards, so the table always ends as the installed model
    declares it.
    """

    legacy = migrations.Migration("previous_release_form_columns", "messaging")
    legacy.operations = [
        migrations.AddField("channel", "slug", models.SlugField(max_length=80, blank=True, default="")),
        migrations.AddField("channel", "is_published", models.BooleanField(default=False)),
        migrations.AddField(
            "channel", "form_schema_version",
            models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)]),
        ),
        migrations.AddField("channel", "form_schema", models.JSONField(default=default_webform_schema, blank=True)),
        migrations.AddField(
            "channel", "max_body_bytes", models.PositiveIntegerField(default=65_536, validators=[MinValueValidator(1)]),
        ),
        migrations.AddField(
            "channel", "max_field_bytes", models.PositiveIntegerField(default=4_096, validators=[MinValueValidator(1)]),
        ),
        migrations.AddConstraint(
            "channel",
            models.UniqueConstraint(
                fields=("slug",), condition=~models.Q(slug=""), name="uq_messaging_channel_webform_slug",
            ),
        ),
    ]
    with connection.schema_editor() as editor:
        state = legacy.apply(ProjectState.from_apps(apps), editor)
    try:
        channels = state.apps.get_model("messaging", "Channel")._base_manager
        for pk, values in held.items():
            channels.filter(pk=pk).update(**values)
        yield state
    finally:
        with connection.cursor() as cursor:
            columns = {
                column.name for column in connection.introspection.get_table_description(cursor, Channel._meta.db_table)
            }
        if "slug" in columns:
            columns_only = migrations.Migration("previous_release_form_columns_removed", "messaging")
            columns_only.operations = template.Migration.operations[1:]
            with connection.schema_editor() as editor:
                columns_only.apply(state.clone(), editor)
        MigrationRecorder(connection).record_unapplied("messaging", _NAME)


def _form_of(channel: Any) -> Any:
    """The one form whose responses are posted on ``channel``."""

    with system_context(reason="webform upgrade tests read"):
        (form,) = [
            form for form in Form._base_manager.order_by("pk") if form.target_config.get("channel") == channel.sqid
        ]
    return form


@pytest.mark.django_db(transaction=True)
def test_the_template_moves_the_previous_releases_webform_channels_once_and_does_not_reverse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A published form, a draft, an address without questions, an unused channel, a stray slug and a second run."""

    rows = _channels(
        request="webform", draft="webform", blank="webform", unused="webform", mailbox="manual",
    )
    stable_id = "webform:request-form:before-1"
    with system_context(reason="webform upgrade tests setup"):
        (earlier,) = Message.objects.ingest(
            [ParsedMessage(
                external_id=stable_id, platform="other",
                sender=ParsedHandle(platform="other", value=stable_id, external_id=stable_id, display_name="Anonymous"),
                thread=ParsedThread(external_id="webform:request-form", title="Request form"),
                body=ParsedPart(type="text/plain", role="body", text="## Subject\n\nAn earlier request"),
            )],
            channel=rows["request"], quote_edges=False,
        )
    executor = MigrationExecutor(connection)
    migration = template.Migration(_NAME, "messaging")
    held = {
        str(rows["request"].pk): {"slug": "request-form", "is_published": True, "form_schema": _SCHEMA},
        str(rows["draft"].pk): {
            "slug": "draft-form", "max_field_bytes": 1_024,
            "form_schema": {"type": "object", "properties": {"note": {"type": "string"}}},
        },
        str(rows["blank"].pk): {"slug": "blank-form"},
        str(rows["mailbox"].pk): {"slug": "stray"},
    }
    with _previous_release(**held) as before:
        with connection.schema_editor() as editor:  # A run cut short before its column changes.
            template.move_webform_channels(before.apps, editor)
        executor.apply_migration(before.clone(), migration)

        with connection.cursor() as cursor:
            kept = {
                column.name for column in connection.introspection.get_table_description(cursor, Channel._meta.db_table)
            }
        assert kept.isdisjoint(template.FORM_COLUMNS)
        with pytest.raises(IrreversibleError):
            executor.unapply_migration(before.clone(), migration)

    with system_context(reason="webform upgrade tests read"):
        assert Form._base_manager.count() == 3
        request, draft, blank = (_form_of(rows[key]) for key in ("request", "draft", "blank"))
        versions = {form.pk: list(FormVersion._base_manager.filter(form=form)) for form in (request, draft, blank)}
        addresses = dict(FormAlias._base_manager.values_list("slug", "form_id"))
        serving = list(FormAlias.objects.serving().values_list("slug", flat=True))
        for row in (request, draft, blank, *versions[request.pk], *versions[draft.pk]):
            row.full_clean()

    (published,) = versions[request.pk]
    (unpublished,) = versions[draft.pk]
    language = settings.LANGUAGE_CODE
    assert (request.name, request.status, request.published_version_id) == (
        "Request form", FormStatus.PUBLISHED, published.pk,
    )
    assert isinstance(request.target(), MessageTarget)
    assert request.target_config == {"channel": rows["request"].sqid, "slug": "request-form"}
    assert (published.number, published.definition, published.languages, published.default_language) == (
        1, _DEFINITION, [language], language,
    )
    assert (published.personal_questions, published.contact_question) == (
        ["subject", "email", "details", "budget", "urgent", "topic"], "email",
    )
    assert published.published_at is not None
    assert (draft.status, draft.published_version_id, unpublished.published_at, unpublished.personal_questions) == (
        FormStatus.DRAFT, None, None, None,
    )
    assert (blank.status, versions[blank.pk]) == (FormStatus.DRAFT, [])
    assert addresses == {"request-form": request.pk, "draft-form": draft.pk, "blank-form": blank.pk}
    assert serving == ["request-form"]
    logged = [record.getMessage() for record in caplog.records]
    assert any(rows["mailbox"].sqid in line and "never served a form" in line for line in logged)
    assert any(rows["draft"].sqid in line and "an answer to 1024" in line for line in logged)
    assert not any(rows["request"].sqid in line for line in logged)

    # The moved form takes a submission at its address's form, and its message continues the conversation.
    row, _made = Response.objects.receive(
        request, version=1, submission_id="after-1", language=language,
        answers={"subject": "A broken lamp", "email": "ada@example.test", "budget": 120.5, "topic": "delivery"},
    )
    message = row.deliver()
    assert (message.external_id, message.channel_id, message.thread_id) == (
        "webform:request-form:after-1", rows["request"].pk, earlier.thread_id,
    )
    assert message.webform_submission().unverified_submitter_email == "ada@example.test"


@pytest.mark.django_db(transaction=True)
def test_the_template_refuses_every_channel_it_cannot_move_before_any_column_goes() -> None:
    """A whole number, a form without an address and an address a form already has; the movable one waits too."""

    rows = _channels(sized="webform", unaddressed="webform", taken="webform", movable="webform")
    asks = {"type": "object", "properties": {"note": {"type": "string"}}}
    with system_context(reason="webform upgrade tests setup"):
        FormAlias.objects.create(form=Form.objects.create(name="Already here"), slug="taken-form")
    executor = MigrationExecutor(connection)
    held = {
        str(rows["sized"].pk): {
            "slug": "sized-form", "is_published": True,
            "form_schema": {"type": "object", "properties": {"size": {"type": "integer"}}},
        },
        str(rows["unaddressed"].pk): {"form_schema": asks},
        str(rows["taken"].pk): {"slug": "taken-form", "form_schema": asks},
        str(rows["movable"].pk): {"slug": "movable-form", "form_schema": asks},
    }
    with _previous_release(**held) as before:
        with pytest.raises(template.FormNotMovable) as refused:
            executor.apply_migration(before.clone(), template.Migration(_NAME, "messaging"))

        legacy = before.apps.get_model("messaging", "Channel")._base_manager
        assert dict(legacy.exclude(slug="").order_by().values_list("slug", "is_published")) == {
            "sized-form": True, "taken-form": False, "movable-form": False,
        }

    refusal = str(refused.value)
    assert f"channel {rows['sized'].sqid} (Sized form): field 'size' is 'integer'" in refusal
    assert f"channel {rows['unaddressed'].sqid} (Unaddressed form): it holds a form and no slug" in refusal
    assert f"channel {rows['taken'].sqid} (Taken form): the address forms/taken-form already belongs" in refusal
    assert rows["movable"].sqid not in refusal
    with system_context(reason="webform upgrade tests read"):
        assert list(Form._base_manager.values_list("name", flat=True)) == ["Already here"]


@pytest.mark.parametrize(("schema", "refusal"), [
    pytest.param({"type": "array"}, "not an object schema", id="not-an-object"),
    pytest.param({"type": "object", "properties": {}}, "asks no question", id="no-question"),
    pytest.param({"type": "object", "title": "Request", "properties": {"a": {"type": "string"}}}, "declares title",
                 id="a-root-key-a-form-does-not-say"),
    pytest.param({"type": "object", "properties": {"a": {"type": "integer"}}}, "'a' is 'integer'", id="a-whole-number"),
    pytest.param({"type": "object", "properties": {"a": {"type": "string", "readOnly": True}}}, "declares readOnly",
                 id="a-read-only-field"),
    pytest.param({"type": "object", "properties": {"a": {"type": "string", "pattern": "^x"}}}, "declares pattern",
                 id="a-pattern"),
    pytest.param({"type": "object", "properties": {"a": {"type": "string", "widget": "markdown"}}},
                 "shown as 'markdown'", id="a-widget-without-a-question"),
    pytest.param({"type": "object", "properties": {"a": {"type": "boolean", "enum": [True]}}}, "with choices",
                 id="choices-on-a-field-that-is-no-text"),
    pytest.param({"type": "object", "properties": {"a": {"type": "string", "enum": ["Very good"]}}},
                 "'Very good' does not match", id="a-choice-that-is-no-code"),
    pytest.param({"type": "object", "properties": {"a": {"type": "string", "maxLength": 5000}}},
                 "greater than the maximum of 2000", id="a-text-longer-than-a-text-answer"),
    pytest.param({"type": "object", "properties": {"a": {"type": "string", "minimum": 1}}}, "only a number or a date",
                 id="a-bound-on-a-text"),
    pytest.param(
        {"type": "object", "properties": {
            "a": {"type": "string", "widget": "email"}, "b": {"type": "string", "widget": "email"},
        }},
        "more than one email", id="two-email-fields",
    ),
    pytest.param({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["b"]}, "requires b",
                 id="a-required-field-never-declared"),
    pytest.param({"type": "object", "properties": {"a": {"type": "string"}}, "propertyOrder": ["a", "a"]},
                 "propertyOrder", id="an-order-that-repeats-a-field"),
    pytest.param({"type": "object", "properties": {"a": {"type": "string"}}, "success": {"title": 3}},
                 "success copy is not text", id="success-copy-that-is-no-text"),
])
def test_a_stored_schema_a_definition_cannot_say_is_refused_with_its_reason(schema: Any, refusal: str) -> None:
    with pytest.raises(template.FormNotMovable, match=refusal):
        template.definition_from_form_spec(schema, title="Request form", language="en")
