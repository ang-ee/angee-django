"""The webform channel template, applied through Django's executor to the previous release's rows.

The previous release's rows carry the channel's form columns and its slug
constraint: the test adds both to the channel table under a migration state
derived from the installed models, so the template's callable runs on historical
models, never live ones. What reads a project's own history (the bridge's
database check and ``check_webform_channels``) is given one: a temporary
migrations package for messaging, as a project's ``runtime/messaging/migrations``
is. Every name and address is invented.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.apps import apps
from django.conf import settings
from django.core import checks
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.validators import MinValueValidator
from django.db import connection, migrations, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.operations.base import Operation
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ModelState, ProjectState
from django.db.models.signals import post_migrate, pre_migrate
from django.test import override_settings
from django_compose.migrations import DropGuardAutodetector
from rebac import system_context

from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart, ParsedThread
from angee.messaging.testing.models import Channel, Message
from angee.messaging.webform_columns import check_form_columns_leave_with_the_move
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
        "subject": {
            "type": "string", "label": "Subject", "title": "Never shown", "maxLength": 120, "placeholder": "One line",
        },
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
    legacy.operations = form_column_operations()
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


def form_column_operations() -> list[Operation]:
    """The operations that give a channel the previous release's form columns and its slug constraint."""

    return [
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


def _declared(*concrete: type[models.Model]) -> list[Operation]:
    """The operations that declare installed models in a project's history, as its initial migration does."""

    states = [ModelState.from_model(model) for model in concrete]
    return [
        migrations.CreateModel(
            name=state.name, fields=list(state.fields.items()), options=state.options, bases=state.bases,
            managers=state.managers,
        )
        for state in states
    ]


def previous_release_history() -> list[Operation]:
    """The first migration of a project's messaging history: the channel as the previous release declared it."""

    return [*_declared(Channel), *form_column_operations()]


def webforms_history() -> list[Operation]:
    """The first migration of a project's webforms history: the models the move writes."""

    return _declared(Form, FormVersion, FormAlias)


_INITIAL = "0001_previous_release"
_HISTORY = """
from django.db import migrations

from tests.test_webforms_messaging_upgrade import {operations}


class Migration(migrations.Migration):
    initial = True
    operations = {operations}()
"""
_REMOVAL = """
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("messaging", "{parent}")]
    operations = [
        migrations.RemoveConstraint(model_name="channel", name="uq_messaging_channel_webform_slug"),
        migrations.RemoveField(model_name="channel", name="form_schema"),
        migrations.RemoveField(model_name="channel", name="form_schema_version"),
        migrations.RemoveField(model_name="channel", name="is_published"),
        migrations.RemoveField(model_name="channel", name="max_body_bytes"),
        migrations.RemoveField(model_name="channel", name="max_field_bytes"),
        migrations.RemoveField(model_name="channel", name="slug"),
    ]
"""
_LATER = """
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("messaging", "{parent}")]
    operations = [migrations.AlterModelOptions(name="channel", options={{"verbose_name": "channel"}})]
"""


class _ProjectHistories:
    """The run's migration modules, with a project's own histories laid over them.

    ``--nomigrations`` replaces the setting with an object that answers only
    ``in`` and ``[]``, which is all Django's loader asks of it; this answers the
    same two, so the histories are read whichever way the run was started.
    """

    def __init__(self, configured: Any, histories: dict[str, str]) -> None:
        self.configured, self.histories = configured, histories

    def __contains__(self, label: object) -> bool:
        return label in self.histories or label in self.configured

    def __getitem__(self, label: str) -> str | None:
        return self.histories[label] if label in self.histories else self.configured[label]


@pytest.fixture
def messaging_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Any) -> Iterator[Any]:
    """Give messaging and web forms a project's migration history, and return what adds a pending migration to it.

    Both histories start applied: messaging at the previous release, web forms
    at its initial migration. ``pending(name)`` adds the bridge's template to
    messaging's under ``name``, pinned as a project pins it;
    ``pending(name, removal=True)`` adds a removal of the form columns as
    ``makemigrations`` writes one, and ``pending(name, later=True)`` a migration
    of a later release that has nothing to do with them. Each follows the
    migration added before it.
    """

    package = f"project_history_{tmp_path.name.replace('-', '_')}"
    folder = tmp_path / package / "messaging"
    for label, name, operations in (
        ("messaging", _INITIAL, "previous_release_history"), ("webforms", "0001_initial", "webforms_history"),
    ):
        (tmp_path / package / label).mkdir(parents=True)
        (tmp_path / package / label / "__init__.py").write_text("")
        (tmp_path / package / label / f"{name}.py").write_text(_HISTORY.format(operations=operations))
    (tmp_path / package / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    settings.MIGRATION_MODULES = _ProjectHistories(
        settings.MIGRATION_MODULES, {"messaging": f"{package}.messaging", "webforms": f"{package}.webforms"},
    )
    recorder = MigrationRecorder(connection)
    recorder.record_applied("messaging", _INITIAL)
    recorder.record_applied("webforms", "0001_initial")
    names = [_INITIAL]

    def pending(name: str, *, removal: bool = False, later: bool = False, pinned: bool = True) -> None:
        if removal:
            source = _REMOVAL.format(parent=names[-1])
        elif later:
            source = _LATER.format(parent=names[-1])
        else:
            source = Path(template.__file__).read_text()
            if pinned:
                source = source.replace('("messaging", "0000_messaging_leaf")', f'("messaging", "{names[-1]}")')
                source = source.replace('("webforms", "0000_webforms_leaf")', '("webforms", "0001_initial")')
        (folder / f"{name}.py").write_text(source)
        names.append(name)
        importlib.invalidate_caches()

    try:
        yield pending
    finally:
        recorder.migration_qs.filter(app__in=("messaging", "webforms")).delete()


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
            "form_schema": {"type": "object", "properties": {
                "note": {"type": "string"}, "reply_to": {"type": "string", "widget": "email"},
            }},
        },
        str(rows["blank"].pk): {"slug": "blank-form"},
        str(rows["mailbox"].pk): {"slug": "stray"},
    }
    with _previous_release(**held) as before:
        plan = template.planned_moves(before.apps, "default")  # The judgment by itself writes nothing.
        assert (len(plan.moves), plan.refusals, Form._base_manager.count()) == (3, [], 0)
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
    # An unpublished version names its contact question and states no personal question yet, which holds together.
    assert (draft.status, draft.published_version_id, unpublished.published_at) == (FormStatus.DRAFT, None, None)
    assert (unpublished.personal_questions, unpublished.contact_question) == (None, "reply_to")
    assert (blank.status, versions[blank.pk]) == (FormStatus.DRAFT, [])
    assert addresses == {"request-form": request.pk, "draft-form": draft.pk, "blank-form": blank.pk}
    assert serving == ["request-form"]
    # What the move does not carry over is said with its channel's public id, never dropped silently.
    logged = [record.getMessage() for record in caplog.records]
    for sqid, left_out in (
        (rows["mailbox"].sqid, "never served a form"),
        (rows["draft"].sqid, "an answer to 1024"),
        (rows["request"].sqid, "field 'subject' has a title beside its label"),
        (rows["request"].sqid, "field 'topic' has 1 disabled option(s)"),
        (rows["request"].sqid, "already has a form that posts on it"),  # The second run, of the migration itself.
    ):
        assert any(sqid in line and left_out in line for line in logged), left_out
    assert not any(rows["unused"].sqid in line or rows["blank"].sqid in line for line in logged[:len(plan.reports)])

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
        assert len(template.planned_moves(before.apps, "default").refusals) == 3
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
def test_a_stored_schema_the_move_cannot_turn_into_questions_is_refused_with_its_reason(
    schema: Any, refusal: str,
) -> None:
    with pytest.raises(template.FormNotMovable, match=refusal):
        template.definition_from_form_spec(schema, title="Request form")


def test_what_a_schema_says_that_a_form_does_not_is_named_and_not_dropped_silently() -> None:
    """A title the form never showed and an option nobody could choose are left out, and said."""

    schema = {"type": "object", "properties": {
        "mood": {"type": "string", "label": "Mood", "title": "Hidden", "options": [
            {"value": "good", "label": "Good"}, {"value": "gone", "label": "Gone", "disabled": True},
        ]},
    }}

    definition, left_out = template.definition_from_form_spec(schema, title="Request form")

    assert definition["elements"] == [
        {"type": "dropdown", "name": "mood", "title": "Mood", "choices": [{"value": "good", "text": "Good"}]},
    ]
    assert left_out == [
        "field 'mood' has 1 disabled option(s), which a form does not offer",
        "field 'mood' has a title beside its label, which the form never showed",
    ]


def test_a_field_shown_as_a_date_becomes_a_date_question_a_published_version_takes() -> None:
    """The previous FormSpec offered ``widget: date`` on a string; the form asks for a date the same way."""

    schema = {
        "type": "object", "required": ["due"],
        "properties": {"due": {"type": "string", "widget": "date", "label": "Due date"}},
    }

    definition, left_out = template.definition_from_form_spec(schema, title="Request form")
    content, _left_out = template.version_from_form_spec(schema, title="Request form", language="en", published=True)

    assert definition["elements"] == [
        {"type": "text", "inputType": "date", "name": "due", "title": "Due date", "isRequired": True},
    ]
    assert (left_out, content["definition"]) == ([], definition)


@pytest.mark.parametrize(("schema", "refusal"), [
    pytest.param({"type": "object", "properties": {}}, "definition: .*should be non-empty", id="no-question"),
    pytest.param({"type": "object", "properties": {"a": {"type": "string", "enum": ["Very good"]}}},
                 "'Very good' does not match", id="a-choice-that-is-no-code"),
    pytest.param({"type": "object", "properties": {"a": {"type": "string", "maxLength": 5000}}},
                 "greater than the maximum of 2000", id="a-text-longer-than-a-text-answer"),
    pytest.param({"type": "object", "properties": {"a": {"type": "string", "minimum": 1}}}, "only a number or a date",
                 id="a-bound-on-a-text"),
    pytest.param({"type": "object", "properties": {"version": {"type": "string"}}},
                 "the name 'version' is the submission's own", id="a-field-named-as-a-submission-names-its-version"),
    pytest.param({"type": "object", "properties": {"answers": {"type": "string"}}},
                 "the name 'answers' is the submission's own", id="a-field-named-as-a-submission-names-its-answers"),
    pytest.param(
        {"type": "object", "properties": {"a": {"type": "string"}}, "success": {"body": "Thank you, {a}."}},
        "completedHtml: curly brackets are not allowed", id="success-text-with-a-curly-bracket",
    ),
])
@pytest.mark.parametrize("published", [True, False], ids=["published", "unpublished"])
def test_a_version_the_web_forms_rule_refuses_is_refused_in_that_rules_words(
    schema: Any, refusal: str, published: bool,
) -> None:
    """The move restates none of these rules: the refusal is the validators' own, under the field it is about."""

    with pytest.raises(template.FormNotMovable, match=refusal):
        template.version_from_form_spec(schema, title="Request form", language="en", published=published)


def test_the_version_a_schema_becomes_states_what_its_publication_needs_and_no_more() -> None:
    """A published form's version states every question personal; an unpublished one states nothing yet."""

    schema = {
        "type": "object", "properties": {"note": {"type": "string"}, "reply": {"type": "string", "widget": "email"}},
    }

    served, _left_out = template.version_from_form_spec(schema, title="Request form", language="en", published=True)
    drafted, _left_out = template.version_from_form_spec(schema, title="Request form", language="en", published=False)

    assert (served["personal_questions"], served["contact_question"]) == (["note", "reply"], "reply")
    assert (drafted["personal_questions"], drafted["contact_question"]) == (None, "reply")
    assert served["definition"] == drafted["definition"]
    assert (served["languages"], served["default_language"]) == (["en"], "en")


@pytest.mark.django_db(transaction=True)
def test_the_drop_guard_refuses_an_autodetected_removal_of_form_columns_that_hold_a_form() -> None:
    """``makemigrations`` on a database whose channels hold forms: refused by column, with the remedy.

    The guard is the composer's (``DropGuardAutodetector``). It judges only what
    it autodetects, against the database the command runs on.
    """

    rows = _channels(held="webform", empty="webform")
    held = {str(rows["held"].pk): {"slug": "held-form", "is_published": True}}
    graph = MigrationGraph()  # A project that has authored its messaging history.
    graph.add_node(("messaging", "0001_initial"), migrations.Migration("0001_initial", "messaging"))
    with _previous_release(**held) as before:
        with pytest.raises(CommandError) as refused:
            DropGuardAutodetector(before, ProjectState.from_apps(apps)).changes(graph=graph)

        table = Channel._meta.db_table
        assert f"messaging.channel: {table}.slug holds data in 1 rows" in str(refused.value)
        assert f"messaging.channel: {table}.is_published holds data in 1 rows" in str(refused.value)
        assert "Declare a reviewed cutover migration before native makemigrations" in str(refused.value)

        # With no channel holding a form the same removal is autodetected and allowed: the columns are empty.
        before.apps.get_model("messaging", "Channel")._base_manager.update(slug="", is_published=False)
        (removal,) = DropGuardAutodetector(before, ProjectState.from_apps(apps)).changes(graph=graph)["messaging"]
        assert {operation.name for operation in removal.operations if hasattr(operation, "model_name")} >= {
            "slug", "is_published", "form_schema",
        }


@pytest.mark.django_db(transaction=True)
def test_the_check_command_asks_for_the_template_only_where_a_channel_holds_a_form(messaging_history: Any) -> None:
    """The columns are there and the project has no copy: what it is told depends on what the channels hold."""

    rows = _channels(open="webform", unused="webform", mailbox="manual")
    said = StringIO()
    with _previous_release(**{str(rows["mailbox"].pk): {"slug": "stray"}}) as before:
        call_command("check_webform_channels", stdout=said)

        before.apps.get_model("messaging", "Channel")._base_manager.filter(pk=rows["open"].pk).update(slug="open-form")
        with pytest.raises(CommandError) as asked:
            call_command("check_webform_channels")

    assert said.getvalue() == (
        "No webform channel holds a form: nothing to move. makemigrations messaging removes the empty columns.\n"
    )
    assert str(asked.value).startswith(
        "1 webform channel(s) hold a form, and this project's messaging history holds no copy of the webform "
        "channel template."
    )


def _held_forms(rows: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Two channels that hold a form, one published; every other channel holds none."""

    return {
        str(rows["open"].pk): {"slug": "open-form", "is_published": True, "form_schema": _SCHEMA},
        str(rows["draft"].pk): {"slug": "draft-form"},
    }


@pytest.mark.django_db(transaction=True)
def test_a_pending_removal_of_form_columns_that_is_not_the_move_is_refused_while_channels_hold_forms(
    messaging_history: Any, django_assert_num_queries: Any,
) -> None:
    """A removal authored where no channel held a form, about to be applied where two do."""

    rows = _channels(open="webform", draft="webform", unused="webform", mailbox="manual")
    messaging_history("0002_remove_channel_form_columns", removal=True)
    with _previous_release(**_held_forms(rows)):
        (error,) = check_form_columns_leave_with_the_move(databases=["default"])
        registered = checks.run_checks(tags=[checks.Tags.database], databases=["default"])
        # A plain check hands a database check no database: it reads none, whatever the database holds.
        with django_assert_num_queries(0):
            assert check_form_columns_leave_with_the_move() == []

    assert error.id == "angee.E054"
    assert error.msg == (
        "messaging.0002_remove_channel_form_columns removes the form columns of 2 webform channel(s) that hold a "
        f"form ({rows['open'].sqid}, {rows['draft'].sqid}), and it is not the move that carries them: applying it "
        "loses those forms."
    )
    assert "open-form" not in error.msg, "a channel is named by its public id, never by what it holds"
    assert "webform_channels_to_forms.py" in error.hint and "check_webform_channels" in error.hint
    assert [message.msg for message in registered if message.id == "angee.E054"] == [error.msg]


@pytest.mark.django_db(transaction=True)
def test_the_error_names_a_few_channels_and_counts_the_rest(messaging_history: Any) -> None:
    rows = _channels(**{f"form_{index}": "webform" for index in range(7)})
    messaging_history("0002_remove_channel_form_columns", removal=True)
    with _previous_release(**{str(row.pk): {"slug": f"form-{row.pk}"} for row in rows.values()}):
        (error,) = check_form_columns_leave_with_the_move(databases=["default"])

    named = [row.sqid for row in rows.values() if row.sqid in error.msg]
    assert "7 webform channel(s) that hold a form" in error.msg
    assert (len(named), "and 2 more" in error.msg) == (5, True)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("history", [
    pytest.param([("0002_webform_channels_to_forms", False)], id="the-move-alone"),
    pytest.param(
        [("0002_webform_channels_to_forms", False), ("0003_remove_channel_form_columns", True)],
        id="a-removal-after-the-move",
    ),
])
def test_the_move_and_whatever_follows_it_may_remove_the_form_columns(
    messaging_history: Any, history: list[tuple[str, bool]],
) -> None:
    """The correct order: the forms are carried before anything removes their columns."""

    rows = _channels(open="webform", draft="webform")
    for name, removal in history:
        messaging_history(name, removal=removal)
    with _previous_release(**_held_forms(rows)):
        assert check_form_columns_leave_with_the_move(databases=["default"]) == []


@pytest.mark.django_db(transaction=True)
def test_a_pending_removal_is_let_through_where_it_loses_nothing(messaging_history: Any) -> None:
    """No channel holds a form, the columns are already gone, or the project's history cannot be loaded."""

    rows = _channels(unused="webform", mailbox="manual")
    messaging_history("0002_remove_channel_form_columns", removal=True)
    # The columns are gone: this database has nothing to lose.
    assert check_form_columns_leave_with_the_move(databases=["default"]) == []
    with _previous_release(**{str(rows["mailbox"].pk): {"slug": "stray"}}):
        # No webform channel holds a form: the autodetected removal is the right migration here.
        assert check_form_columns_leave_with_the_move(databases=["default"]) == []
    with _previous_release(**{str(rows["unused"].pk): {"slug": "held-form"}}):
        assert len(check_form_columns_leave_with_the_move(databases=["default"])) == 1
        # A copy of the template left unpinned names a migration that does not exist: migrate says so itself.
        messaging_history("0003_webform_channels_to_forms", pinned=False)
        assert check_form_columns_leave_with_the_move(databases=["default"]) == []


@pytest.mark.django_db(transaction=True)
def test_the_check_command_says_what_the_move_would_do_and_changes_nothing(messaging_history: Any) -> None:
    """Two channels that would become forms and one the move refuses: the counts, the exit and the database."""

    rows = _channels(open="webform", draft="webform", sized="webform", unused="webform")
    held = {
        **_held_forms(rows),
        str(rows["sized"].pk): {
            "slug": "sized-form", "form_schema": {"type": "object", "properties": {"size": {"type": "integer"}}},
        },
    }
    messaging_history("0002_webform_channels_to_forms")
    said = StringIO()
    with _previous_release(**held) as before:
        legacy = before.apps.get_model("messaging", "Channel")._base_manager
        stored = list(legacy.order_by("pk").values("pk", *template.FORM_COLUMNS))
        with pytest.raises(CommandError, match="The move would be refused"):
            call_command("check_webform_channels", stdout=said)

        assert list(legacy.order_by("pk").values("pk", *template.FORM_COLUMNS)) == stored
        assert (Form._base_manager.count(), FormAlias._base_manager.count()) == (0, 0)
        assert ("messaging", "0002_webform_channels_to_forms") not in MigrationRecorder(connection).applied_migrations()

        # With the refused channel's form cleared, the same command passes.
        legacy.filter(pk=rows["sized"].pk).update(slug="", form_schema=default_webform_schema())
        passed = StringIO()
        call_command("check_webform_channels", stdout=passed)

    output = said.getvalue()
    assert (
        "messaging.0002_webform_channels_to_forms: 2 channel(s) would become forms, 1 would be refused, "
        "2 fact(s) would be left out."
    ) in output
    assert f"channel {rows['sized'].sqid} (Sized form): field 'size' is 'integer'" in output
    assert "2 channel(s) would become forms, 0 would be refused" in passed.getvalue()


def _form_columns() -> set[str]:
    """The form columns the channel table has now."""

    with connection.cursor() as cursor:
        described = connection.introspection.get_table_description(cursor, Channel._meta.db_table)
    return {column.name for column in described} & set(template.FORM_COLUMNS)


def _migrate(*target: str, fake: bool = False) -> None:
    """Run ``migrate`` on messaging as ``provision`` runs it, with system checks skipped.

    The bare host's session fixture restores its baseline rows on
    ``post_migrate``, which presumes the flush a transactional test ends with. A
    run in the middle of a test has rows, so its ``post_migrate`` is not sent;
    everything before it is the command's own.
    """

    with patch.object(post_migrate, "send"):
        call_command("migrate", "messaging", *target, fake=fake, skip_checks=True, verbosity=0)


@pytest.mark.django_db(transaction=True)
def test_migrate_refuses_a_removal_of_form_columns_that_is_not_the_move_and_applies_nothing(
    messaging_history: Any,
) -> None:
    """Checks are skipped, as under ``provision``: what refuses is the receiver of the run's own signal."""

    rows = _channels(open="webform", draft="webform", unused="webform", mailbox="manual")
    messaging_history("0002_remove_channel_form_columns", removal=True)
    with _previous_release(**_held_forms(rows)) as before:
        legacy = before.apps.get_model("messaging", "Channel")._base_manager
        stored = list(legacy.order_by("pk").values("pk", *template.FORM_COLUMNS))
        # Django sends the signal once for every app; only messaging's turn judges, so a run is judged once.
        plan = MigrationExecutor(connection).migration_plan([("messaging", "0002_remove_channel_form_columns")])
        elsewhere = apps.get_app_config("webforms")
        pre_migrate.send(sender=elsewhere, app_config=elsewhere, using="default", plan=plan, apps=before.apps)
        with pytest.raises(CommandError) as refused:
            _migrate()

        assert list(legacy.order_by("pk").values("pk", *template.FORM_COLUMNS)) == stored
        assert _form_columns() == set(template.FORM_COLUMNS)
        assert Form._base_manager.count() == 0
        applied = MigrationRecorder(connection).applied_migrations()
        assert ("messaging", "0002_remove_channel_form_columns") not in applied

    what, remedy = str(refused.value).split("\n")
    assert what == (
        "messaging.0002_remove_channel_form_columns removes the form columns of 2 webform channel(s) that hold a "
        f"form ({rows['open'].sqid}, {rows['draft'].sqid}), and it is not the move that carries them: applying it "
        "loses those forms. Nothing was applied."
    )
    assert "webform_channels_to_forms.py" in remedy and "check_webform_channels" in remedy


@pytest.mark.django_db(transaction=True)
def test_migrate_applies_the_move_and_whatever_removal_loses_nothing(messaging_history: Any) -> None:
    """The move carries the forms and removes the columns; where no channel holds a form, a plain removal may."""

    rows = _channels(open="webform", draft="webform", unused="webform")
    messaging_history("0002_webform_channels_to_forms")
    with _previous_release(**_held_forms(rows)):
        _migrate()

        assert _form_columns() == set()
        assert ("messaging", "0002_webform_channels_to_forms") in MigrationRecorder(connection).applied_migrations()
        said = StringIO()
        call_command("check_webform_channels", stdout=said)
        assert said.getvalue().startswith("messaging.0002_webform_channels_to_forms is applied: the move is done")
    with system_context(reason="webform upgrade tests read"):
        assert {_form_of(rows[key]).status for key in ("open", "draft")} == {FormStatus.PUBLISHED, FormStatus.DRAFT}


@pytest.mark.django_db(transaction=True)
def test_migrate_applies_an_autodetected_removal_where_no_channel_holds_a_form(messaging_history: Any) -> None:
    rows = _channels(unused="webform", mailbox="manual")
    messaging_history("0002_remove_channel_form_columns", removal=True)
    with _previous_release(**{str(rows["mailbox"].pk): {"slug": "stray"}}):
        _migrate()

        assert _form_columns() == set()


@pytest.mark.django_db(transaction=True)
def test_a_backwards_plan_is_not_judged(messaging_history: Any) -> None:
    """Unapplying the removal brings columns back: it is no removal, whatever the channels hold."""

    rows = _channels(open="webform", draft="webform")
    messaging_history("0002_remove_channel_form_columns", removal=True)
    recorder = MigrationRecorder(connection)
    recorder.record_applied("messaging", "0002_remove_channel_form_columns")
    with _previous_release(**_held_forms(rows)):
        _migrate(_INITIAL, fake=True)

        assert ("messaging", "0002_remove_channel_form_columns") not in recorder.applied_migrations()
        assert _form_columns() == set(template.FORM_COLUMNS)


@pytest.mark.django_db(transaction=True)
def test_a_project_whose_channels_never_held_a_form_is_left_alone(messaging_history: Any) -> None:
    """Its empty columns went with a plain removal, long applied, and no copy of the template is anywhere.

    Nothing asks it for the template: the command has nothing to move, the
    database check names nothing, and a later migration applies.
    """

    _channels(mailbox="manual")
    messaging_history("0002_remove_channel_form_columns", removal=True)
    MigrationRecorder(connection).record_applied("messaging", "0002_remove_channel_form_columns")
    messaging_history("0003_a_later_release", later=True)
    said = StringIO()

    call_command("check_webform_channels", stdout=said)
    named = check_form_columns_leave_with_the_move(databases=["default"])
    _migrate()

    assert said.getvalue() == "The webform columns are already removed from messaging's channels: nothing to move.\n"
    assert named == []
    assert ("messaging", "0003_a_later_release") in MigrationRecorder(connection).applied_migrations()


@pytest.mark.django_db(transaction=True)
def test_a_faked_removal_is_refused_while_forms_are_at_stake_and_recorded_once_the_columns_are_gone(
    messaging_history: Any,
) -> None:
    """Faking the removal records it as applied: the move could then never run, so it is refused all the same."""

    rows = _channels(open="webform", draft="webform")
    messaging_history("0002_remove_channel_form_columns", removal=True)
    recorder = MigrationRecorder(connection)
    with _previous_release(**_held_forms(rows)), pytest.raises(CommandError, match="Nothing was applied"):
        _migrate(fake=True)
    refused = ("messaging", "0002_remove_channel_form_columns") in recorder.applied_migrations()

    _migrate(fake=True)  # The columns are gone: nothing is at stake.

    recorded = ("messaging", "0002_remove_channel_form_columns") in recorder.applied_migrations()
    assert (refused, recorded) == (False, True)


@pytest.mark.django_db(transaction=True)
def test_a_project_without_the_bridge_is_judged_the_same(messaging_history: Any) -> None:
    """Nothing registers the ``webform`` backend there, and the channels that store it still hold their forms.

    The refusal is messaging's own, so it reads a channel's backend as it is
    stored, never through a registry the project may not have filled.
    """

    rows = _channels(open="webform", draft="webform", unused="webform")
    registry = {key: path for key, path in settings.ANGEE_CHANNEL_BACKEND_CLASSES.items() if key != "webform"}
    messaging_history("0002_remove_channel_form_columns", removal=True)
    with override_settings(ANGEE_CHANNEL_BACKEND_CLASSES=registry):
        with _previous_release(**_held_forms(rows)) as before:
            (named,) = check_form_columns_leave_with_the_move(databases=["default"])
            with pytest.raises(CommandError) as refused:
                _migrate()

            assert _form_columns() == set(template.FORM_COLUMNS)
            # With no form held, the same removal is the right migration, and it applies.
            before.apps.get_model("messaging", "Channel")._base_manager.update(
                slug="", is_published=False, form_schema=default_webform_schema(),
            )
            _migrate()
            assert _form_columns() == set()

    assert named.id == "angee.E054"
    assert str(refused.value).startswith(
        "messaging.0002_remove_channel_form_columns removes the form columns of 2 webform channel(s) that hold a "
        f"form ({rows['open'].sqid}, {rows['draft'].sqid})"
    )
