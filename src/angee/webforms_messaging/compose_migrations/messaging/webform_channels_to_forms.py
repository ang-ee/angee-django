"""Migration template: each webform channel becomes a form, then messaging's form columns go.

Copy this module into the project's ``runtime/messaging/migrations/`` under the
project's next migration name, replace the placeholder dependencies with the
project's own explicit leaves (never ``__latest__``), and keep its bytes. It
runs after the webforms migration that creates the form, version and alias
tables, while ``messaging.Channel`` still has its form columns. The operations
below remove them, which the drop guard refuses to do by autodetection while a
channel holds a form. A project whose channels hold none needs no template:
``makemigrations`` removes the empty columns.

Each channel with the ``webform`` backend and a slug becomes a ``Form`` named
after the channel. The form's ``message`` target posts its responses on that
channel under the same slug, so the channel's conversation and its message ids
continue. The stored FormSpec becomes the form's first ``FormVersion``, written
in one language, the project's ``LANGUAGE_CODE``. The slug becomes the form's
``FormAlias``, so the form answers at the address it had. A published channel
becomes a published form whose version states every question as personal,
because a FormSpec never said which are; an unpublished one becomes a draft
whose version states nothing yet. The schema's email field becomes the version's
contact question. A channel with a slug, the empty default schema and no
published flag becomes a draft form with its address and no version.

Nothing is guessed. A channel is refused, by name and before any column goes,
when its form cannot be said as a definition (:func:`definition_from_form_spec`
lists what can), when the web forms' own rule refuses the version it would
become (:func:`version_from_form_spec`), when its slug or name cannot be a
form's, when another form already has its address, or when it holds a schema or
a published flag without a slug. Every refusal is raised together. Change the
channel on the previous release, then apply the migration again.

Not carried over, and each reported in the log with its channel's public id: a
channel's ``max_body_bytes`` and ``max_field_bytes`` when it changed either (a
form's limits are its definition's and the deployment's ``ANGEE_WEBFORM_*``
bounds); a field's ``title`` beside its ``label``, which the form never showed;
a disabled option; the form columns of a channel with another backend, which
never served a form; and the form a channel holds when a form already posts on
that channel.

No ``Response`` is made: the submissions a channel received stay the messages
they are. Running it again makes nothing twice: a channel a form already posts
on is left as it is. The upgrade is **irreversible**: a definition is not
written back as a FormSpec.

``manage.py check_webform_channels`` runs the same judgment (:func:`planned_moves`)
before ``migrate`` and prints what would be refused, changing nothing. The
``Migration`` below says it is the move (``moves_webform_channels``), under
whatever name a project gives its copy: the command finds it by that, and so
does messaging when it refuses a migration that would remove the form columns
of channels that hold a form without being the move or following it. Which
channels hold a form is messaging's judgment, imported here
(``angee.messaging.webforms``), so the move and that refusal never differ.

Every row is read and written through historical models, as a data migration's
are, so no verb of a form runs here. The template applies the rule the verbs
apply instead: a published form's version is judged with the addon's
``validate_publishable`` before its published state is written, an unpublished
one with ``validate_version``, and each row is held to its fields' own rules.
Historical models keep ``Meta.ordering`` on ``sqid``, which migration state does
not carry, so every query here orders by the primary key.
"""

from __future__ import annotations

import logging
from html import escape
from typing import Any, NamedTuple

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connections, migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.utils import timezone

from angee.messaging.webforms import (
    FORM_COLUMNS,
    channel_public_id,
    channels_holding_forms,
    default_webform_schema,
    holds_form,
)
from angee.webforms.validators import validate_publishable, validate_version

EMPTY_SCHEMA = default_webform_schema()
DEFAULT_BYTE_LIMITS = (65_536, 4_096)
SCHEMA_KEYS = frozenset({"type", "properties", "required", "propertyOrder", "success"})
FIELD_KEYS = frozenset({
    "type", "label", "title", "description", "placeholder", "widget", "maxLength", "minimum", "maximum", "enum",
    "options",
})
TEXT_QUESTIONS: dict[str, dict[str, Any]] = {
    "": {"type": "text"},
    "text": {"type": "text"},
    "email": {"type": "text", "inputType": "email"},
    "textarea": {"type": "comment"},
}
CHOICE_WIDGETS = frozenset({"", "select", "selection", "combobox"})
NUMBER_WIDGETS = frozenset({"", "number"})
BOOLEAN_WIDGETS = frozenset({"", "boolean", "switch"})

logger = logging.getLogger(__name__)


class FormNotMovable(ValueError):
    """A channel's form cannot become a web form as it is stored."""


class Plan(NamedTuple):
    """What the move would do: the rows to write, the channels it refuses, and what it leaves out."""

    moves: list[tuple[Any, Any, Any]]
    """One unsaved ``(form, version or None, alias)`` per channel that becomes a form."""
    refusals: list[str]
    """One line per channel that cannot become a form, with its public id and the reason."""
    reports: list[str]
    """One line per fact that is not carried over, with its channel's public id."""


def definition_from_form_spec(schema: Any, *, title: str) -> tuple[dict[str, Any], list[str]]:
    """Return the definition that asks what a stored FormSpec asks, and what it leaves out.

    What carries over: a ``string`` field as a text answer (``widget`` ``email``
    or ``textarea`` makes it an email address or a long text), ``number`` and
    ``boolean`` fields, a ``string`` field's ``enum`` or ``options`` as a
    dropdown, ``label`` or else ``title``, ``description``, ``placeholder``,
    ``maxLength``, ``minimum`` and ``maximum``, the root ``required`` and
    ``propertyOrder``, and the ``success`` copy as the form's thank-you text.
    Every text is one plain string. A field without a label shows its name.

    Left out, and named in the second value: a ``title`` beside a ``label``
    (the label is what the form showed) and a disabled option (a form offers
    only what can be chosen).

    Anything else raises :class:`FormNotMovable`: an ``integer`` (a number
    question also admits fractions), a read-only, hidden or defaulted field, a
    pattern, a second email field. Whether the result is a definition a form may
    store is the web forms' rule to judge (:func:`version_from_form_spec`).
    """

    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        raise FormNotMovable("its schema is not an object schema")
    _only(schema, SCHEMA_KEYS, "its schema")
    properties, required = schema.get("properties", {}), schema.get("required", [])
    order = schema.get("propertyOrder", list(properties) if isinstance(properties, dict) else [])
    if not isinstance(properties, dict) or not isinstance(required, list) or not isinstance(order, list):
        raise FormNotMovable("its schema's properties, required or propertyOrder are not what a FormSpec holds")
    if len(order) != len(properties) or set(order) != set(properties):
        raise FormNotMovable("its propertyOrder does not name every field once")
    undeclared = sorted(str(name) for name in required if name not in properties)
    if undeclared:
        raise FormNotMovable(f"it requires {', '.join(undeclared)}, which it does not declare")
    left_out: list[str] = []
    elements = [_question(name, properties[name], required=name in required, left_out=left_out) for name in order]
    if sum(element.get("inputType") == "email" for element in elements) > 1:
        raise FormNotMovable("it declares more than one email field")
    definition: dict[str, Any] = {"title": title, "elements": elements}
    success = schema.get("success", {})
    if not isinstance(success, dict) or any(not isinstance(text, str) for text in success.values()):
        raise FormNotMovable("its success copy is not text")
    _only(success, frozenset({"title", "body"}), "its success copy")
    thanks = "".join(
        f"<{tag}>{escape(success[key], quote=False)}</{tag}>"
        for key, tag in (("title", "h3"), ("body", "p"))
        if success.get(key)
    )
    if thanks:
        definition["completedHtml"] = thanks
    return definition, left_out


def version_from_form_spec(
    schema: Any, *, title: str, language: str, published: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Return the content of the one-language version a stored FormSpec becomes, and what it leaves out.

    The content is judged by the web forms' own rule. A published form's
    version states every question personal, because a FormSpec never said which
    are, and must be ready to serve (``validate_publishable``). An unpublished
    one states nothing yet and must only hold together (``validate_version``).
    The schema's email field is the contact question. What the rule refuses
    raises :class:`FormNotMovable` in the rule's own words: a choice whose value
    is not a code, an element named as a submission names its own keys, markup a
    form does not store.
    """

    definition, left_out = definition_from_form_spec(schema, title=title)
    asked = definition["elements"]
    content: dict[str, Any] = {
        "definition": definition,
        "languages": [language],
        "default_language": language,
        "personal_questions": [question["name"] for question in asked] if published else None,
        "contact_question": next((question["name"] for question in asked if question.get("inputType") == "email"), ""),
    }
    try:
        (validate_publishable if published else validate_version)(**content)
    except ValidationError as error:
        raise FormNotMovable(_reasons(error)) from error
    return content, left_out


def _reasons(error: ValidationError) -> str:
    """Return a refusal as one line, each message after the field it is about."""

    if hasattr(error, "message_dict"):
        return "; ".join(f"{field}: {' '.join(messages)}" for field, messages in error.message_dict.items())
    return "; ".join(error.messages)


def _only(holder: dict[str, Any], sayable: frozenset[str], what: str) -> None:
    unsaid = sorted(str(key) for key in holder if key not in sayable)
    if unsaid:
        raise FormNotMovable(f"{what} declares {', '.join(unsaid)}, which a web form does not say")


def _question(name: str, field: Any, *, required: bool, left_out: list[str]) -> dict[str, Any]:
    """Return the question one FormSpec field becomes, adding to ``left_out`` what it does not carry."""

    if not isinstance(field, dict):
        raise FormNotMovable(f"field {name!r} is not an object")
    _only(field, FIELD_KEYS, f"field {name!r}")
    choices = _choices(name, field, left_out=left_out)
    kind, widget = field.get("type", "string" if choices is not None else None), field.get("widget", "")
    if not isinstance(widget, str):
        raise FormNotMovable(f"field {name!r} names no widget")
    if kind == "string" and choices is not None and widget in CHOICE_WIDGETS:
        question: dict[str, Any] = {"type": "dropdown", "name": name, "choices": choices}
    elif kind == "string" and choices is None and widget in TEXT_QUESTIONS:
        question = {**TEXT_QUESTIONS[widget], "name": name}
    elif kind == "number" and choices is None and widget in NUMBER_WIDGETS:
        question = {"type": "text", "name": name, "inputType": "number"}
    elif kind == "boolean" and choices is None and widget in BOOLEAN_WIDGETS:
        question = {"type": "boolean", "name": name}
    else:
        shown = f" shown as {widget!r}" if widget else ""
        offered = " with choices" if choices is not None else ""
        raise FormNotMovable(f"field {name!r} is {kind!r}{shown}{offered}, which no question answers the same way")
    if "label" in field and "title" in field:
        left_out.append(f"field {name!r} has a title beside its label, which the form never showed")
    said = {
        "title": field.get("label", field.get("title")),
        "description": field.get("description"),
        "placeholder": field.get("placeholder"),
        "maxLength": field.get("maxLength"),
        "min": field.get("minimum"),
        "max": field.get("maximum"),
    }
    question.update({key: value for key, value in said.items() if value is not None})
    if required:
        question["isRequired"] = True
    return question


def _choices(name: str, field: dict[str, Any], *, left_out: list[str]) -> list[dict[str, Any]] | None:
    """Return the choices a field offers, or ``None`` when it offers none."""

    options, values = field.get("options"), field.get("enum")
    if options is None and values is None:
        return None
    if options is not None and values is not None:
        raise FormNotMovable(f"field {name!r} declares both enum and options")
    if options is None:
        if not isinstance(values, list):
            raise FormNotMovable(f"field {name!r} has an enum that is not a list")
        return [{"value": value} for value in values]
    if not isinstance(options, list) or any(
        not isinstance(option, dict) or not {"value", "label"} <= set(option) <= {"value", "label", "disabled"}
        for option in options
    ):
        raise FormNotMovable(f"field {name!r} has an option that is not a value, a label and a disabled flag")
    disabled = sum(bool(option.get("disabled")) for option in options)
    if disabled:
        left_out.append(f"field {name!r} has {disabled} disabled option(s), which a form does not offer")
    return [{"value": option["value"], "text": option["label"]} for option in options if not option.get("disabled")]


def _checked(row: Any, *, exclude: tuple[str, ...] = ()) -> Any:
    """Hold a historical row to its fields' own rules, which a migration's writes skip."""

    try:
        row.clean_fields(exclude=exclude)
    except ValidationError as error:
        raise FormNotMovable(_reasons(error)) from error
    return row


def planned_moves(apps: StateApps, using: str) -> Plan:
    """Judge every channel that holds a form and return what the move would do, writing nothing.

    ``apps`` is the migration state in which ``messaging.Channel`` still has its
    form columns and the web forms' models exist. Forms and addresses a database
    already holds are read when their tables exist; before the web forms' own
    migration is applied there are none.
    """

    channels = apps.get_model("messaging", "Channel")._base_manager.using(using)
    forms = apps.get_model("webforms", "Form")._base_manager.using(using)
    versions = apps.get_model("webforms", "FormVersion")._base_manager.using(using)
    aliases = apps.get_model("webforms", "FormAlias")._base_manager.using(using)
    tables = set(connections[using].introspection.table_names())
    moved: set[Any] = set()
    taken: set[str] = set()
    if forms.model._meta.db_table in tables:
        moved = {
            config.get("channel")
            for config in forms.filter(target_class="message").order_by("pk").values_list("target_config", flat=True)
            if isinstance(config, dict)
        }
    if aliases.model._meta.db_table in tables:
        taken = set(aliases.order_by("pk").values_list("slug", flat=True))
    language = settings.LANGUAGE_CODE
    plan = Plan([], [], [])

    others = channels.exclude(backend_class="webform").order_by("pk").values("pk", "display_name", *FORM_COLUMNS)
    for channel in others:
        if holds_form(channel):
            plan.reports.append(
                f"Channel {channel_public_id(channel['pk'])} ({channel['display_name']}) is not a webform channel and "
                "never served a form: its form columns are dropped."
            )

    for channel in channels_holding_forms(apps, using):
        slug, schema, published = channel["slug"], channel["form_schema"], bool(channel["is_published"])
        channel_id = channel_public_id(channel["pk"])
        named = f"{channel_id} ({channel['display_name'] or slug})"
        if channel_id in moved:
            plan.reports.append(
                f"Channel {named} already has a form that posts on it: the form it holds is left behind."
            )
            continue
        try:
            if not slug:
                raise FormNotMovable("it holds a form and no slug, so the form has no address")
            if slug in taken:
                raise FormNotMovable(f"the address forms/{slug} already belongs to a form")
            form = _checked(forms.model(
                name=channel["display_name"] or f"Public form {slug}", target_class="message",
                target_config={"channel": channel_id, "slug": slug},
            ))
            address = _checked(aliases.model(slug=slug), exclude=("form",))
            version = None
            left_out: list[str] = []
            if published or schema != EMPTY_SCHEMA:
                content, left_out = version_from_form_spec(
                    schema, title=channel["display_name"] or slug, language=language, published=published,
                )
                version = _checked(
                    versions.model(number=1, published_at=timezone.now() if published else None, **content),
                    exclude=("form",),
                )
        except FormNotMovable as refusal:
            plan.refusals.append(f"channel {named}: {refusal}")
            continue
        taken.add(slug)
        plan.moves.append((form, version, address))
        plan.reports.extend(f"Channel {named}: {note}: left out." for note in left_out)
        if (channel["max_body_bytes"], channel["max_field_bytes"]) != DEFAULT_BYTE_LIMITS:
            plan.reports.append(
                f"Channel {named} limited a submission to {channel['max_body_bytes']} bytes and an answer to "
                f"{channel['max_field_bytes']}: neither is carried over."
            )
    return plan


def move_webform_channels(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Make the form, version and address of every webform channel that holds a form, or refuse them all."""

    using = schema_editor.connection.alias
    plan = planned_moves(apps, using)
    for report in plan.reports:
        logger.warning(report)
    if plan.refusals:
        raise FormNotMovable("These webform channels cannot become forms as they are:\n" + "\n".join(plan.refusals))
    for form, version, address in plan.moves:
        form.save(using=using)
        if version is not None:
            version.form_id = form.pk
            version.save(using=using)
            if version.published_at is not None:
                form.status, form.published_version_id = "published", version.pk
                form.save(using=using, update_fields=["status", "published_version"])
        address.form_id = form.pk
        address.save(using=using)


class Migration(migrations.Migration):
    # This migration is the move, under whatever name a project gives its copy.
    moves_webform_channels = True
    # Placeholders: replace with this project's explicit leaves, the messaging migration this
    # follows (its channel still has the form columns) and the webforms migration that creates
    # the form, version and alias tables.
    dependencies = [
        ("messaging", "0000_messaging_leaf"),
        ("webforms", "0000_webforms_leaf"),
    ]
    operations = [
        migrations.RunPython(move_webform_channels),
        migrations.RemoveConstraint("channel", "uq_messaging_channel_webform_slug"),
        *(migrations.RemoveField("channel", column) for column in FORM_COLUMNS),
    ]
