"""Message templates: the layouts, templates, blocks and parts outgoing mail is written from.

A :class:`MessageTemplate` is one row of one kind (:class:`MessageTemplateKind`)
for one key and one language. A message template of the kind ``template`` is the
mail itself, with its subject, preheader and the message template layout it sits
in; a layout wraps templates; a message template block and a message template part
are pieces rows include. What differs by kind is its handler's
(:attr:`MessageTemplateKind.handler`) or a declarative constraint naming the kind.

**Rendering.** :meth:`MessageTemplate.render` and :meth:`MessageTemplate.preview`
walk the row (:class:`~angee.messaging_templates.walker.TemplateWalker`) and render
it on Django's template engine, restricted
(:mod:`angee.messaging_templates.engine`); a save and a resource load walk it too
(:meth:`MessageTemplate.validate_source`), so a row that would not render is
refused when it is written.

**Baselines and overrides.** An addon ships rows in its ``install`` resources as
baselines (``is_baseline``), which loads alone write. The deployment's override of
a baseline is the non-baseline row with the baseline's kind, key and language,
started by :meth:`MessageTemplateManager.override` and kept over a changed default
by :meth:`MessageTemplateManager.keep_override`; deleting it resets to the default.
A non-baseline row with no baseline is the deployment's own, started by
:meth:`MessageTemplateManager.create_own`. ``content_hash``
fingerprints what renders, and an override's ``baseline_hash`` is the baseline's
fingerprint when the override started or was last kept, so "has an updated default"
is a comparison of the two (:attr:`MessageTemplateQuerySet.row_fact_expressions`).
Rows are written one at a time, through ``save()``, which derives the hash: the
querysets refuse ``update``, ``bulk_update`` and ``bulk_create``.

**Languages.** :meth:`MessageTemplateQuerySet.effective_rows` picks, for each kind
and key, the row a recipient reads: the first of the requested languages (or the
person's) and then the default language that has a row, an override before its
baseline, and else the language of a baseline, English first. A language is
spelled as parties stores one (:class:`~angee.parties.fields.LanguageCodeField`).

**Access.** Every signed-in actor reads rows; platform administrators create,
write and delete rows that are not baselines, read every row's sample and preview
every row, and nobody but a resource load writes a baseline (``permissions.zed``).

**Sending.** :meth:`MessageTemplateManager.send_as_system` renders an effective
template as named system work and records it on messaging's system channel, which
delivers it: system mail, which a person's button may cause when the caller's own
permission decided the send, the causing actor recorded in the caller's changeset.
:meth:`MessageTemplateManager.send_test` is an administrator's test send of a
row with its sample to their own address: decided by the row's ``preview``, then
sent the same way, as named system work, with the administrator recorded as who
caused it.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from copy import copy, deepcopy
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import RegexValidator
from django.db import IntegrityError, models, transaction
from django.db.models.expressions import BaseExpression
from django.db.models.functions import Cast, Length
from django.views.decorators.debug import sensitive_variables
from rebac import PermissionDenied, actor_context, current_actor, system_context, to_object_ref, to_subject_ref

from angee.base.fields.state import StateField
from angee.base.models.base import AngeeDataModel
from angee.base.models.locks import ImmutableFieldsMixin
from angee.base.models.timestamps import update_fields_with_auto_now
from angee.base.permissions import require_save_allowed
from angee.base.prefetch import InstancePrefetch
from angee.base.querysets import ClosedUpdateQuerySet
from angee.base.serialization import canonical_json_sha256
from angee.brandkit.models import BrandKit
from angee.graphql.capabilities import held_permissions
from angee.messaging_templates.engine import (
    NO_BRAND_KIT,
    NOT_A_TEMPLATE,
    NOT_EFFECTIVE,
    RefusalSource,
    clean_link,
    refusal_item,
)
from angee.messaging_templates.rendering import MessageTemplateFormat, validate_variables
from angee.messaging_templates.resources import MessageTemplateResource
from angee.messaging_templates.walker import (
    PreviewFrame,
    RenderedMessage,
    TemplatePreview,
    TemplateWalk,
    TemplateWalker,
    TemplateWarning,
    TemplateWarningCode,
    in_layout,
)
from angee.parties.fields import LanguageCodeField, normalize_language_code
from angee.provenance.context import changeset, changeset_model, require_changeset
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager, AngeeHistoryUnscopedManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet, AngeeHistoryUnscopedQuerySet
from angee.resources.mixins import ResourceLoadMixin

if TYPE_CHECKING:
    from angee.iam.models import User
    from angee.messaging.models import Message
    from angee.parties.models import Person

logger = logging.getLogger(__name__)

FALLBACK_LANGUAGE = "en"
"""The language a key falls back to first when no requested language has a row: the one shipped rows are written in."""


def _fallback_order(language: str) -> tuple[bool, str]:
    """The order a key's languages are taken in when no requested one has a row: :data:`FALLBACK_LANGUAGE`, then by
    code."""

    return language != FALLBACK_LANGUAGE, language


CONTENT_HASH_FIELDS = ("kind", "format", "subject", "preheader", "source", "text_source", "layout", "variables")
"""The columns ``content_hash`` fingerprints: what a row is and renders, not what it is called."""

TEXT_COLUMNS = ("subject", "preheader", "source", "text_source")
"""The columns a row's text is written in, whose line endings :meth:`MessageTemplate.clean` makes newlines."""

_KEY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\Z")
"""Anchored, since ``RegexValidator`` searches; ``\\Z`` refuses the trailing newline ``$`` admits."""

validate_key = RegexValidator(_KEY, "Use lower-case names of letters, digits and underscores, joined by dots.")
"""A row's ``key``, and a template's ``layout``, which names a layout's key."""

STORED_COLUMNS = (
    "kind", "key", "language", "name", "description", "format", "subject", "preheader", "source", "text_source",
    "layout", "variables", "sample",
)
"""The columns :meth:`MessageTemplate.clean_fields` holds to what JSON writes and UTF-8 encodes, before anything else
reads them."""


STORED_DEPTH_LIMIT = 64
"""The deepest a value in a column nests: a row's own values nest a few levels, and Django's text conversion and a
render recurse over what nests deeper."""


def _unstorable(value: Any) -> str | None:
    """Why no column can store, hash or render ``value``, or ``None``: a value nested over
    :data:`STORED_DEPTH_LIMIT`, a NUL in any string, key or text (PostgreSQL refuses it in text and JSON alike), a
    lone surrogate, or what JSON cannot write."""

    pending: list[tuple[Any, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > STORED_DEPTH_LIMIT:
            return f"Nest the value at most {STORED_DEPTH_LIMIT} levels deep."
        if isinstance(item, str) and "\x00" in item:
            return "Remove the NUL character: the database stores none."
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in (*item.keys(), *item.values()))
        elif isinstance(item, list | tuple):
            pending.extend((child, depth + 1) for child in item)
    try:
        json.dumps(value, allow_nan=False, ensure_ascii=False).encode()
    except UnicodeEncodeError:
        return "Remove the lone surrogate: no database or mail can encode it."
    except (TypeError, ValueError):
        return "Write text, true, false, null and finite numbers of at most 4,300 digits: JSON holds nothing else."
    return None

NOT_A_BASELINE_MESSAGE = "Only a shipped default can be overridden."
NO_BASELINE_MESSAGE = "This message template overrides no shipped default."
UNRESOLVED_DELETE_MESSAGE = (
    "Other message templates use this %(kind)s, which would no longer resolve without this row: "
    "give its key a row in the default language first."
)
ONE_AT_A_TIME_MESSAGE = "Message templates are written one at a time, so each save derives its content hash."
NO_BRAND_KIT_MESSAGE = "No brand kit is the default, and a message renders in a brand kit's name and colours."
SHIPPED_DEFAULT_MESSAGE = "A shipped default has this kind, key and language: override it instead."
NOT_A_TEMPLATE_MESSAGE = "Only a template is sent: a layout, block or part reaches a mail inside a template."
NOT_EFFECTIVE_MESSAGE = (
    "This row is not what renders for its kind, key and language (a shipped default its override replaces, or a row "
    "not saved): send the row that renders."
)
NO_TEST_ADDRESS_MESSAGE = "Your account has no email address to send the test to."

TEST_SUBJECT_PREFIX = "[Test] "
"""What a test send's subject starts with (:meth:`MessageTemplateManager.send_test`), so the administrator who asked
tells it from the mail its recipients get."""

LAYOUT_PREVIEW = PreviewFrame(
    subject="A message in this layout",
    preheader="The preheader shows after the subject in an inbox list.",
    html='<p style="margin:0 0 16px">A template writes its message here. The layout owns everything around it: '
         "the header, the footer, the width, the colours and the type.</p>",
    text="A template writes its message here. The layout owns everything around it: the header, the footer, the "
         "width, the colours and the type.",
)
"""The fixed body, subject and preheader a layout is previewed with."""

BASE_LAYOUT = "base"
"""The layout this addon ships (``resources/install/030_messaging_templates.messagetemplate.yaml``);
a new template starts in it."""


class MessageTemplateKind(models.TextChoices):
    """What a message template is; each kind's behaviour is its :attr:`handler`."""

    LAYOUT = "layout", "Layout"
    TEMPLATE = "template", "Template"
    BLOCK = "block", "Block"
    PART = "part", "Part"

    @property
    def handler(self) -> MessageTemplateKindHandler:
        """Return the behaviour owned by this kind."""

        return _KIND_HANDLERS[self]

    @classmethod
    def for_template_name(cls, name: str) -> tuple[str, str] | None:
        """Return the kind and key a template name (``blocks/button``) names, or ``None`` for no row's name."""

        kind = max(
            (kind for kind in cls if name.startswith(kind.handler.template_prefix)),
            key=lambda kind: len(kind.handler.template_prefix),
        )
        key = name.removeprefix(kind.handler.template_prefix)
        return (str(kind), key) if _KEY.fullmatch(key) else None


class MessageTemplateKindHandler:
    """What one kind of message template is: the columns it fills, its name, how it renders and the rows naming it."""

    carries_message_fields: ClassVar[bool] = False
    """Whether rows of this kind fill ``subject``, ``preheader`` and ``layout``; the others leave them empty."""
    template_prefix: ClassVar[str] = ""
    """Put before a key, it names a row of this kind among message templates (``blocks/button``);
    a template is named by its bare key."""
    includable: ClassVar[bool] = False
    """Whether rows include a row of this kind by name, ``{% include "<name>" with … only %}``."""
    reads_message_fields: ClassVar[bool] = False
    """Whether a row of this kind reads ``subject`` and ``preheader``, which the mail around it supplies."""
    holds_content: ClassVar[bool] = False
    """Whether each form of a row of this kind holds the one empty ``{% block content %}`` a template fills."""
    writes_document: ClassVar[bool] = False
    """Whether a row of this kind writes the mail's document: its opening doctype and Outlook's conditional comments."""
    preview_frame: ClassVar[PreviewFrame | None] = None
    """The fixed body, subject and preheader a row of this kind is previewed around; ``None`` previews it alone."""
    defaults: ClassVar[Mapping[str, Any]] = {}
    """The columns a deployment's own row of this kind starts with when no row resolves its key
    (:meth:`MessageTemplateManager.create_own`); the identity and the name are the caller's."""

    def template_name(self, key: str) -> str:
        """Return the name the engine knows a row of this kind and ``key`` by."""

        return self.template_prefix + key

    def layout_name(self, row: MessageTemplate) -> str | None:
        """Return the name of the layout ``row``'s forms sit in, or ``None`` when they sit in none."""

        del row
        return None

    def wrap(self, row: MessageTemplate, form: str) -> str:
        """Return one of ``row``'s forms as the engine compiles it."""

        del row
        return form

    def naming_rows(
        self, rows: models.QuerySet[Any], keys: Iterable[str], *, walker: TemplateWalker,
    ) -> dict[str, list[Any]]:
        """Return the rows among ``rows``, in any language, naming each of ``keys`` as a row of this kind, by key.

        One read answers every key; a key nothing names is absent. ``walker``
        checks what a row includes and remembers each row it checked, so a caller
        asking several kinds with one walker checks a row once.
        """

        del rows, keys, walker
        return {}

    def is_named(self, rows: models.QuerySet[Any], key: str) -> bool:
        """Whether some row among ``rows``, in any language, names ``key`` as a row of this kind."""

        return bool(self.naming_rows(rows, [key], walker=TemplateWalker()))


class LayoutHandler(MessageTemplateKindHandler):
    """A message template layout wraps templates, which name it in their ``layout``; it is previewed around a body."""

    template_prefix = "layouts/"
    reads_message_fields = True
    holds_content = True
    writes_document = True
    preview_frame = LAYOUT_PREVIEW
    defaults = {
        "format": MessageTemplateFormat.HTML,
        "source": (
            "<!doctype html>\n"
            '<html lang="{{ lang }}" dir="{{ dir }}">\n'
            '<head>\n<meta charset="utf-8">\n<title>{{ subject }}</title>\n</head>\n'
            '<body style="margin:0;padding:24px 12px;background:{{ brand.light.canvas }}">\n'
            '<div style="max-width:600px;margin:0 auto;font:16px/24px {{ brand.font.body }};'
            'color:{{ brand.light.text }}">{% block content %}{% endblock %}</div>\n'
            "</body>\n</html>"
        ),
        "text_source": "{% block content %}{% endblock %}",
        "variables": {},
        "sample": {},
    }

    def naming_rows(
        self, rows: models.QuerySet[Any], keys: Iterable[str], *, walker: TemplateWalker,
    ) -> dict[str, list[Any]]:
        """Return, for each of ``keys``, the rows whose ``layout`` is it, in one read; nothing is checked."""

        del walker
        named: dict[str, list[Any]] = {}
        for row in self._naming(rows, keys).annotate(_backlink_layout=models.F("layout")):
            named.setdefault(row._backlink_layout, []).append(row)
        return named

    def is_named(self, rows: models.QuerySet[Any], key: str) -> bool:
        """Whether a template among ``rows`` sits in the layout ``key``: one ``EXISTS``, no row loaded."""

        return self._naming(rows, [key]).exists()

    @staticmethod
    def _naming(rows: models.QuerySet[Any], keys: Iterable[str]) -> models.QuerySet[Any]:
        """The rows naming one of ``keys`` as their layout: only templates fill the column."""

        return rows.filter(layout__in=[*keys])


class TemplateHandler(MessageTemplateKindHandler):
    """A message template of the kind ``template`` is a mail: a subject, a preheader and the layout it sits in."""

    carries_message_fields = True
    reads_message_fields = True
    defaults = {
        "format": MessageTemplateFormat.MARKDOWN,
        "subject": "A message from {{ brand.name }}",
        "preheader": "",
        "layout": BASE_LAYOUT,
        "source": "Write this message here.",
        "text_source": "",
        "variables": {},
        "sample": {},
    }

    def layout_name(self, row: MessageTemplate) -> str | None:
        """Return the name of the layout the template's ``layout`` column names."""

        return MessageTemplateKind.LAYOUT.handler.template_name(row.layout)

    def wrap(self, row: MessageTemplate, form: str) -> str:
        """Return the form as the content of its layout: ``{% extends %}`` and ``{% block content %}`` around it."""

        return in_layout(str(self.layout_name(row)), form)


class IncludedHandler(MessageTemplateKindHandler):
    """A kind rows include by name: rows name a key of it when their compiled forms include it."""

    includable = True

    def naming_rows(
        self, rows: models.QuerySet[Any], keys: Iterable[str], *, walker: TemplateWalker,
    ) -> dict[str, list[Any]]:
        """Return, for each of ``keys``, the rows among ``rows`` whose compiled forms include this kind's row of it.

        One read of the rows whose source or plain-text form holds one of the
        names, every column loaded whatever ``rows`` defers (``sample`` too, which
        the reader's field gate redacts after the fetch); then ``walker`` checks
        each of them (:meth:`TemplateWalker.includes`). A row that does not
        compile names nothing.
        """

        names = {self.template_name(key): key for key in keys}
        if not names:
            return {}
        holding = models.Q()
        for name in names:
            holding |= models.Q(source__contains=name) | models.Q(text_source__contains=name)
        named: dict[str, list[Any]] = {}
        for row in rows.defer(None).filter(holding):
            for name in names.keys() & walker.includes(row):
                named.setdefault(names[name], []).append(row)
        return named


class BlockHandler(IncludedHandler):
    """A message template block is a styled piece of a mail that rows include."""

    template_prefix = "blocks/"
    defaults = {
        "format": MessageTemplateFormat.HTML,
        "source": '<p style="margin:0 0 16px">Write this block here.</p>',
        "text_source": "Write this block here.",
        "variables": {},
        "sample": {},
    }


class PartHandler(IncludedHandler):
    """A message template part is a piece of wording that rows include."""

    template_prefix = "parts/"
    defaults = {
        "format": MessageTemplateFormat.MARKDOWN,
        "source": "Write this part here.",
        "text_source": "",
        "variables": {},
        "sample": {},
    }


_KIND_HANDLERS: Mapping[MessageTemplateKind, MessageTemplateKindHandler] = {
    MessageTemplateKind.LAYOUT: LayoutHandler(),
    MessageTemplateKind.TEMPLATE: TemplateHandler(),
    MessageTemplateKind.BLOCK: BlockHandler(),
    MessageTemplateKind.PART: PartHandler(),
}

_MESSAGE_KINDS = [str(kind) for kind in MessageTemplateKind if kind.handler.carries_message_fields]


def _siblings(rows: models.QuerySet[Any]) -> models.QuerySet[Any]:
    """The rows of the outer row's kind, key and language, read unscoped: every reader of a row reads them all."""

    return rows.model._base_manager.filter(
        kind=models.OuterRef("kind"), key=models.OuterRef("key"), language=models.OuterRef("language"),
    )


def _is_effective(rows: models.QuerySet[Any]) -> BaseExpression:
    """A non-baseline row, or a baseline with no non-baseline row of its kind, key and language: what renders."""

    return models.ExpressionWrapper(
        models.Q(is_baseline=False) | ~models.Exists(_siblings(rows).filter(is_baseline=False)),
        output_field=models.BooleanField(),
    )


def _is_overridden(rows: models.QuerySet[Any]) -> BaseExpression:
    """A baseline with a non-baseline row of its kind, key and language."""

    return models.ExpressionWrapper(
        models.Q(is_baseline=True) & models.Exists(_siblings(rows).filter(is_baseline=False)),
        output_field=models.BooleanField(),
    )


def _has_updated_default(rows: models.QuerySet[Any]) -> BaseExpression:
    """An override whose ``baseline_hash`` differs from its baseline's ``content_hash``."""

    moved = _siblings(rows).filter(is_baseline=True).exclude(content_hash=models.OuterRef("baseline_hash"))
    return models.ExpressionWrapper(
        models.Q(is_baseline=False) & models.Exists(moved), output_field=models.BooleanField(),
    )


def _overrides_default(rows: models.QuerySet[Any]) -> BaseExpression:
    """A non-baseline row with a baseline of its kind, key and language: the deployment's override of it."""

    return models.ExpressionWrapper(
        models.Q(is_baseline=False) & models.Exists(_siblings(rows).filter(is_baseline=True)),
        output_field=models.BooleanField(),
    )


class _OneAtATimeQuerySet[TemplateT: MessageTemplate](ClosedUpdateQuerySet[TemplateT]):
    """Refuse every collection write: ``save()`` derives the content hash and guards the write-once columns."""

    def immutable_error(self, operation: str) -> ValidationError:
        """Refuse ``operation`` with the one message every closed write path gives."""

        del operation
        return ValidationError(ONE_AT_A_TIME_MESSAGE, code="one_at_a_time")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[TemplateT]:
        """Reject a batched insert, which would skip ``save()``."""

        raise self.immutable_error("bulk_create")


class MessageTemplateQuerySet[TemplateT: MessageTemplate](
    _OneAtATimeQuerySet[TemplateT], AngeeHistoryQuerySet[TemplateT],
):
    """Read scopes over message templates: the language rule, whether a key resolves, and the row facts."""

    row_fact_expressions: ClassVar[Mapping[str, Callable[[models.QuerySet[Any]], BaseExpression]]] = {
        "is_effective": _is_effective,
        "is_overridden": _is_overridden,
        "has_updated_default": _has_updated_default,
        "overrides_default": _overrides_default,
    }
    """Each row fact's builder, taking a queryset of the model; the model's filter expressions are this mapping."""

    @staticmethod
    def default_language() -> str:
        """Return the deployment's default language: ``LANGUAGE_CODE``'s primary subtag, as parties spells it."""

        return normalize_language_code(settings.LANGUAGE_CODE.split("-", 1)[0])

    def language_candidates(self, *, language: str | None = None, person: Person | None = None) -> list[str]:
        """Return the languages a lookup tries, in order, each once: ``language`` or ``person``'s, then the default.

        Callers pass a language as parties stores one, a primary subtag such as
        ``en`` or ``zh`` (normalized by :func:`~angee.parties.fields.normalize_language_code`;
        ``en-us`` is refused with ``ValidationError``). ``language`` replaces
        ``person``'s languages.
        """

        preferred = [normalize_language_code(language)] if language else list(person.languages) if person else []
        return list(dict.fromkeys([*preferred, self.default_language()]))

    def effective_rows(
        self,
        kinds: Iterable[str],
        *,
        keys: Iterable[str] | None = None,
        language: str | None = None,
        person: Person | None = None,
    ) -> dict[tuple[str, str], TemplateT]:
        """Return the row a recipient reads for each kind and key that has one, keyed ``(kind, key)``, in one query.

        Per key, the first of :meth:`language_candidates` that has a row wins, its
        override before its baseline; when none has one, the language of a baseline
        of that kind and key wins, :data:`FALLBACK_LANGUAGE` first and then by
        code, again its override before its baseline. A key with neither is
        absent. ``kinds`` and ``keys`` are iterables of names (a bare string is
        refused, not iterated); ``keys`` ``None`` reads every key of ``kinds``.
        Rows are read through this queryset's scope, so a caller with no actor
        reads through ``MessageTemplate.system_queryset()``.
        """

        if isinstance(kinds, str) or isinstance(keys, str):
            raise TypeError("Pass kinds and keys as iterables of names, not one name.")
        candidates = self.language_candidates(language=language, person=person)
        shipped = self.model._base_manager.filter(
            is_baseline=True,
            kind=models.OuterRef("kind"),
            key=models.OuterRef("key"),
            language=models.OuterRef("language"),
        )
        rows = self.filter(models.Q(language__in=candidates) | models.Exists(shipped), kind__in=[*kinds])
        if keys is not None:
            rows = rows.filter(key__in=[*keys])
        found: dict[tuple[str, str], dict[str, dict[bool, TemplateT]]] = {}
        for row in rows.order_by("kind", "key", "language", "is_baseline"):
            found.setdefault((str(row.kind), row.key), {}).setdefault(row.language, {})[row.is_baseline] = row
        effective: dict[tuple[str, str], TemplateT] = {}
        for identity, languages in found.items():
            chosen = next((code for code in candidates if code in languages), None) or min(
                languages, key=_fallback_order,
            )
            pair = languages[chosen]
            effective[identity] = pair[False] if False in pair else pair[True]
        return effective

    def effective(
        self, kind: str, key: str, *, language: str | None = None, person: Person | None = None,
    ) -> TemplateT:
        """Return the row a recipient reads for ``kind`` and ``key`` (:meth:`effective_rows`).

        Raise ``DoesNotExist`` naming the key and the languages tried when it has none.
        """

        rows = self.effective_rows([kind], keys=[key], language=language, person=person)
        if (row := rows.get((str(kind), key))) is None:
            candidates = ", ".join(self.language_candidates(language=language, person=person))
            raise self.model.DoesNotExist(f"No message template {kind} {key!r} in {candidates} or a shipped language.")
        return row

    def resolves(self, kind: str, key: str) -> bool:
        """Whether every recipient finds ``kind`` and ``key``: a baseline in any language or a row in the default."""

        return self.filter(
            models.Q(is_baseline=True) | models.Q(language=self.default_language()), kind=kind, key=key,
        ).exists()

    def named_rows(self, names: Iterable[str], *, language: str) -> dict[str, TemplateT]:
        """Return the row a recipient in ``language`` reads for each template name (``blocks/button``) that has one.

        Names are read through :meth:`MessageTemplateKind.for_template_name` and
        resolved by :meth:`effective_rows` in one query. It reads each row's
        ``text_length`` in place of its ``source`` and ``text_source``: the characters
        the two hold, by the database's ``Length``, which counts characters as
        ``len()`` does on PostgreSQL and SQLite; and its ``variables_length`` in place
        of its ``variables``: the characters of the database's text of the JSON, which
        ``jsonb`` writes in its own key order and SQLite holds as Django wrote it, each
        non-ASCII character escaped. :meth:`with_text` reads the three, so a
        walk counts a level before it loads it, every candidate row among them.
        ``description`` and ``sample``, which a walk never reads, stay unread too. A
        name with no row is absent.
        """

        identities: dict[tuple[str, str], str] = {}
        for name in names:
            if (identity := MessageTemplateKind.for_template_name(name)) is not None:
                identities[identity] = name
        if not identities:
            return {}
        sized = cast(Self, self.defer("source", "text_source", "variables", "description", "sample").annotate(
            text_length=Length("source") + Length("text_source"),
            variables_length=Length(Cast("variables", output_field=models.TextField())),
        ))
        rows = sized.effective_rows(
            {kind for kind, _ in identities}, keys={key for _, key in identities}, language=language,
        )
        return {identities[identity]: row for identity, row in rows.items() if identity in identities}

    def with_text(self, rows: Mapping[str, TemplateT]) -> dict[str, TemplateT]:
        """Return ``rows`` (from :meth:`named_rows`) with their ``source``, ``text_source`` and ``variables`` read.

        One query reads them; a row no longer read through this queryset is absent.
        """

        texts = {
            pk: (source, text, variables) for pk, source, text, variables in
            self.filter(pk__in=[row.pk for row in rows.values()]).order_by().values_list(
                "pk", "source", "text_source", "variables",
            )
        }
        read: dict[str, TemplateT] = {}
        for name, row in rows.items():
            if (text := texts.get(row.pk)) is not None:
                row.source, row.text_source, row.variables = text
                read[name] = row
        return read

    def resolving_names(self, names: Iterable[str]) -> frozenset[str]:
        """The template names among ``names`` every recipient finds (:meth:`resolves`), judged in one query."""

        identities: dict[tuple[str, str], str] = {}
        for name in names:
            if (identity := MessageTemplateKind.for_template_name(name)) is not None:
                identities[identity] = name
        if not identities:
            return frozenset()
        found = self.filter(
            models.Q(is_baseline=True) | models.Q(language=self.default_language()),
            kind__in={kind for kind, _ in identities}, key__in={key for _, key in identities},
        ).values_list("kind", "key")
        return frozenset(identities[identity] for identity in found if identity in identities)

    def with_row_facts(self) -> MessageTemplateQuerySet[TemplateT]:
        """Annotate each row fact as ``_<name>``, the annotation the model's properties read."""

        return self.annotate(**{f"_{name}": build(self) for name, build in self.row_fact_expressions.items()})


class MessageTemplateBaseQuerySet[TemplateT: MessageTemplate](
    _OneAtATimeQuerySet[TemplateT], AngeeHistoryUnscopedQuerySet[TemplateT],
):
    """Unscoped rows for Django's own relation reads, closed to collection writes like the default manager."""


_MessageTemplateBaseManager = AngeeHistoryUnscopedManager.from_queryset(MessageTemplateBaseQuerySet)

_MessageTemplateManagerBase = AngeeHistoryManager.from_queryset(MessageTemplateQuerySet)


class MessageTemplateManager[TemplateT: MessageTemplate](_MessageTemplateManagerBase[TemplateT]):
    """Owns the writes that start a row or an override (the deployment's own row, overriding a shipped default and
    keeping an override over it) and the sends of a template (:meth:`send_as_system`, :meth:`send_test`)."""

    clean_link = staticmethod(clean_link)
    """``clean_link(value, *, path="link") -> str``: the link a mail prints for ``value``, as a render prepares it, or
    the render's refusal of it (:func:`~angee.messaging_templates.engine.clean_link`). A caller of
    :meth:`send_as_system` that sends many mails with one link asks it once, before the first."""

    @sensitive_variables()
    def send_as_system(
        self, row: TemplateT, *, reason: str, to: str, values: Mapping[str, Any], kit: BrandKit | None = None,
        must_print: Iterable[str] = (),
    ) -> Message:
        """Render ``row`` with ``values`` as named system work and record it on the system channel, queued.

        System mail from a template: named system work, which a person's button
        may cause when the caller's own permission decided the send, the causing
        actor recorded in the caller's changeset; and the one path every template
        mail takes (:meth:`send_test` shares it). **Call it only
        from system work whose own gate decided the send**: a person's request
        reaches it only through such a gate, never from a resolver, an MCP tool
        or a view that hands it a person's choice of row, address or values
        unchecked. It checks no permission and decides nothing about the values,
        the links in them or who may be mailed. The modules that call it are
        listed by the test of this boundary, so a new caller is a reviewed change.

        The caller picks ``row`` for its recipient inside its own named work, so
        the lookup and the send are audited under one reason::

            with system_context(reason=reason):
                row = MessageTemplate.objects.effective("template", key, person=person)
            MessageTemplate.objects.send_as_system(row, reason=reason, to=address, values=values)

        The verb takes a row: a key with no row raises ``DoesNotExist`` from the
        caller's own lookup, and a caller that offers the send to a person turns
        it into its own coded refusal. ``row`` renders as the instance holds it, so edits not yet
        saved are sent, and a row never saved is refused ``not_effective``. ``to``
        is one entry as :meth:`~angee.messaging.managers.MessageManager.clean_recipient`
        reads it: pass a bare address, since a typed name fills the display name
        of a handle that has none. Nothing is idempotent: a caller that retries
        sends again, unless it claims a key of its own first, as :meth:`send_test`
        does.

        ``must_print`` names the values the mail is useless without, a credential
        or a call to action's target (``["link"]``, ``["person.first_name"]``): a
        caller that mails one passes its path, and the send is refused
        (``value_not_printed``) when a body cannot print it, or, for a path with
        no ``*``, when this mail's body does not show it, as
        :meth:`MessageTemplate.render` states for each kind of path. The default
        requires nothing. A link value the mail cannot carry is refused whatever
        ``must_print`` says (``value_not_linkable``); :attr:`clean_link` asks that
        rule of a value before a run.

        In order: a blank ``reason`` is a ``ValueError``, before anything is read;
        :meth:`MessageTemplate.send_refusal`; the render
        (:meth:`MessageTemplate.render`) inside ``system_context(reason=reason)``,
        which reads every row and the default kit and whose entry writes one
        permission audit row at once, kept when the render then refuses
        (``value_not_linkable`` and ``value_not_printed`` among its refusals); then
        :meth:`~angee.messaging.managers.MessageManager.compose_as_system` under
        the same reason, outside that context, in the caller's own context and
        transaction, with the rendered subject, plain text and HTML, which
        refuses as its docstring says (an address the transport cannot carry
        among it). The render comes before every one of compose's refusals, so
        only a render refusal leaves the render's audit row. No message row is
        written before a refusal. The audit rows commit or roll back with the
        caller's transaction, and ``reason`` is recorded in them alone, never on
        the message. Each call records one message and hands it to delivery once,
        when the outermost transaction commits; it is returned unbound.

        What the caller's values become, on the system channel: a new thread
        titled with the rendered subject, kept in the thread's history; the
        message, whose preview is the first 280 characters of the text; its
        title, text and HTML parts, which point to content-addressed text
        fragments that carry a search vector and that nothing deletes, so they
        outlive the message. Who reads system mail, and how its handles are
        recorded, is messaging's (its README, "System mail"): a value that is a
        credential, such as a sign-in link, is readable by those readers until it
        expires, and deleting the message does not remove its fragments. So the
        send's frames hide every local, the values, the rendered text and the
        address among them, and the render's frames the values (down to
        ``TemplateWalk.render``, whose mark covers every frame it calls), as
        Django's ``sensitive_variables``: with ``DEBUG`` off, when Django's filter
        works, an exception's report never shows them.
        """

        # compose_as_system refuses a blank reason too, but only after the render, which this refusal precedes.
        if not reason.strip():
            raise ValueError("System mail requires a named reason.")
        if (refusal := row.send_refusal()) is not None:
            raise refusal
        return self._render_and_record(row, reason=reason, to=to, values=values, kit=kit, must_print=must_print)

    @sensitive_variables()
    def send_test(
        self, row: TemplateT, *, actor: User, kit: BrandKit | None = None, idempotency_key: str | None = None,
    ) -> Message:
        """Send ``row`` with its ``sample`` to ``actor``'s own account address, on the system channel: a test send.

        An administrator presses for it, and the system writes the mail: the
        pressing is decided by the row's ``preview`` alone, as a preview is, and
        the mail is sent as named system work on :meth:`send_as_system`'s one
        path, under the reason ``messaging_templates.message_template.send_test``,
        with ``actor`` recorded as who caused it (the message's ``created_by``,
        and the actor of the changeset its rows join). In order:
        :meth:`MessageTemplate.send_test_refusal` under ``actor``
        (``PermissionDenied`` before anything else is read, then the row's side,
        :meth:`MessageTemplate.send_refusal`); an account with no email address
        (``no_test_address``); with ``idempotency_key``, a claim of it for
        ``actor``, the request being the row (its id, its ``content_hash`` and its
        ``sample``) and the kit's id, through provenance's keyed creation
        (``claim_created``), so a retry of the same request
        returns the message it first sent, neither rendered nor sent again,
        unless that message was trashed since, which messaging's replay rule
        refuses (``MessageManager.replay_claimed``: ``discarded`` under
        ``idempotency_key``), and the same key for another request raises
        ``CreationKeyConflict``. A key names one press: mint one per press and
        reuse it only to retry that press. So an edit of the row's text or sample
        between two calls makes the second another request (a conflict, never a
        stale replay), while an edit of a row it includes or of the kit does not;
        after an address change a replay names the mail sent to the old address;
        an omitted kit and the default kit named are two requests; and a first
        message deleted outright leaves the key ``unavailable``. Then
        the render, inside the system context, and ``compose_as_system``, the
        subject after :data:`TEST_SUBJECT_PREFIX`, refusing as they do. A
        refused test writes no message row. A keyed one runs in a transaction of
        its own, the claim's, so its refusal rolls back the claim and everything
        after it, the render's audit row among them, and the key stays free; an
        unkeyed one opens none, and its render's audit row stays with the
        caller's transaction, as any send's does.

        The render reads what ``actor``'s own preview of the row reads: every
        signed-in reader reads every message template and brand kit, the render
        never reads an included row's sample, and the values are the sample of
        ``row`` as the caller loaded it. Two tests in
        ``tests/test_messaging_templates_send.py`` hold that condition, over the
        composed schema and over the rows:
        ``test_every_signed_in_reader_reads_every_template_and_kit_in_the_composed_schema``
        and ``test_a_signed_in_user_with_no_grant_reads_every_template_row_and_every_kit``.
        It stores what :meth:`send_as_system` stores, for the system channel's readers (messaging's README, "System
        mail"); through every verb those are administrators, who read samples
        and account addresses already. ``actor``'s existing handle for the
        address receives it, as compose finds one.
        """

        with actor_context(actor):
            if (refusal := row.send_test_refusal()) is not None:
                raise refusal
            if not actor.email:
                raise ValidationError(NO_TEST_ADDRESS_MESSAGE, code="no_test_address")

            def send() -> Message:
                return self._render_and_record(
                    row, reason="messaging_templates.message_template.send_test", to=actor.email, values=row.sample,
                    kit=kit, subject_prefix=TEST_SUBJECT_PREFIX,
                )

            if idempotency_key is None:
                return send()
            messages = apps.get_model("messaging", "Message")
            with changeset(fresh=True), transaction.atomic():
                sent = changeset_model().objects.claim_created(
                    f"messaging_templates.message_template.send_test:{to_subject_ref(actor)}",
                    idempotency_key,
                    values={
                        "template": row.sqid, "content": row.content_hash, "sample": row.sample,
                        "brand_kit": None if kit is None else kit.sqid,
                    },
                    model=messages,
                    unavailable=ValidationError({"idempotency_key": ValidationError(
                        "The claimed test send is unavailable.", code="unavailable",
                    )}),
                )
                if sent is not None:
                    return cast("Message", messages.objects.replay_claimed(sent))
                message = send()
                require_changeset().record_created(message)
                return message

    def _render_and_record(
        self, row: TemplateT, *, reason: str, to: str, values: Mapping[str, Any], kit: BrandKit | None,
        subject_prefix: str = "", must_print: Iterable[str] = (),
    ) -> Message:
        """Render ``row`` as named system work and record it on the system channel: the one path of both sends.

        The render runs inside ``system_context(reason=reason)``; ``compose_as_system`` runs after it, outside, in
        the caller's context. ``subject_prefix`` is the test send's marker, never a caller's argument.
        """

        with system_context(reason=reason):
            rendered = row.render(values, kit=kit, must_print=must_print)
        return cast("Message", apps.get_model("messaging", "Message").objects.compose_as_system(
            reason=reason, to=[to], subject=subject_prefix + rendered.subject, text=rendered.plain_text,
            html=rendered.html,
        ))

    def override(self, baseline: TemplateT) -> TemplateT:
        """Return the deployment's override of ``baseline``, created from it when there is none.

        Asks :meth:`MessageTemplate.override_refusal` first, before anything is
        locked, and raises what it answers: the acting reader may not create the
        override, or ``baseline`` is not a shipped default (named system work
        skips the permission). Then, in one transaction under ``baseline``'s row
        lock, refuses a row that is gone or not a baseline and returns the existing
        override, or saves a new one holding :meth:`MessageTemplate.override_values`,
        whose ``baseline_hash`` its save stamps. A writer that inserts the same
        row meanwhile, past the lock, is answered with that row. Calling it again
        returns the same row.
        """

        actor, unscoped = baseline.effective_actor(strict=True)
        identity = {"kind": baseline.kind, "key": baseline.key, "language": baseline.language}
        override = self.model(**identity)
        if unscoped:
            override.sudo(reason="messaging_templates.message_template.override")
        else:
            with actor_context(actor):
                refusal = baseline.override_refusal()
            if refusal is not None:
                raise refusal
            override.with_actor(actor)
        rows = self.model.system_queryset()
        with transaction.atomic():
            locked = self.model.system_queryset(lock=("self",)).filter(pk=baseline.pk).first()
            if locked is None or not locked.is_baseline:
                raise ValidationError(NOT_A_BASELINE_MESSAGE, code="not_a_baseline")
            existing = rows.filter(is_baseline=False, **identity).first()
            if existing is None:
                for name, value in locked.override_values().items():
                    setattr(override, name, value)
                try:
                    # Validation reads through the resolved actor's scope, whatever the ambient one is.
                    with (
                        system_context(reason="messaging_templates.message_template.override")
                        if unscoped else actor_context(actor)
                    ), transaction.atomic():
                        override.full_clean()
                        override.save()
                except (IntegrityError, ValidationError):
                    # A writer that does not take the baseline's lock inserted the row meanwhile.
                    if (existing := rows.filter(is_baseline=False, **identity).first()) is None:
                        raise
        if existing is None:
            if unscoped:
                override.unsudo()
            return override
        return existing if unscoped else existing.with_actor(actor)

    def keep_override(self, override: TemplateT) -> TemplateT:
        """Keep ``override``'s text over its baseline as it is now, which clears "has an updated default".

        Asks :meth:`MessageTemplate.keep_override_refusal` first, before anything
        is locked, and raises what it answers: the acting reader may not write
        ``override``, or it overrides no shipped default (named system work skips
        the permission). Then, under its row lock, refuses a row that overrides no
        baseline and stamps its ``baseline_hash`` with the baseline's ``content_hash``.
        """

        actor, unscoped = override.effective_actor(strict=True)
        if not unscoped:
            with actor_context(actor):
                refusal = override.keep_override_refusal()
            if refusal is not None:
                raise refusal
        with transaction.atomic():
            locked = self.model.system_queryset(lock=("self",)).filter(pk=override.pk).first()
            shipped = None if locked is None or locked.is_baseline else (
                self.model.system_queryset()
                .filter(is_baseline=True, kind=locked.kind, key=locked.key, language=locked.language)
                .first()
            )
            if locked is None or shipped is None:
                raise ValidationError(NO_BASELINE_MESSAGE, code="no_baseline")
            if unscoped:
                locked.sudo(reason="messaging_templates.message_template.keep_override")
            else:
                locked.with_actor(actor)
            locked.baseline_hash = shipped.content_hash
            locked.save(update_fields=update_fields_with_auto_now(locked, {"baseline_hash"}))
            if unscoped:
                locked.unsudo()
            return locked

    def create_own(self, *, kind: str, key: str, language: str, name: str) -> TemplateT:
        """Create the deployment's own row of ``kind``, ``key`` and ``language``, called ``name``; the rest is seeded.

        Requires ``create`` on the candidate row for the acting reader before
        anything is cleaned, read or written; named system work with no actor
        passes that check, as ``require_access`` lets it, and reads every row.
        Then, before anything is seeded or walked, refuses what the identity's
        own validators refuse, keyed by field; an identity a shipped default holds
        in this language (``shipped_default``: that row is overridden,
        :meth:`override`, never shadowed); and an identity the deployment already
        has, in the local unique constraint's words.

        The row is seeded from its key's content: what a recipient in its
        language reads for the kind and key today (:meth:`MessageTemplateQuerySet.effective`),
        else, when nothing resolves the key for that language, the key's row in
        another language (:data:`FALLBACK_LANGUAGE` first, then by code); in
        both cases that row's :meth:`MessageTemplate.override_values` but the
        name. Only a key with no row at all starts from its kind's
        :attr:`MessageTemplateKindHandler.defaults`, so creating a row puts no
        starter text live on a key that already has content.

        ``full_clean()`` then judges the row, and it is saved. A writer that
        inserts the same identity meanwhile is refused in the constraint's own
        words, and a load that ships the identity meanwhile with
        ``shipped_default``, the row rolled back; an ``IntegrityError`` the
        constraints do not explain propagates.
        """

        row = self.model(kind=kind, key=key, language=language, name=name, is_baseline=False)
        row.require_access("create", current_actor())
        identity = {"kind", "key", "language", "name", "is_baseline"}
        unjudged = [field.name for field in self.model._meta.fields if field.name not in identity]
        row.clean_fields(exclude=unjudged)
        shipped = self.model.system_queryset().filter(
            is_baseline=True, kind=row.kind, key=row.key, language=row.language,
        )
        if shipped.exists():
            raise ValidationError(SHIPPED_DEFAULT_MESSAGE, code="shipped_default")
        row.validate_constraints(exclude=unjudged)
        source: TemplateT | None
        try:
            source = self.effective(row.kind, row.key, language=row.language)
        except self.model.DoesNotExist:
            others = self.filter(kind=row.kind, key=row.key)
            source = min(others, key=lambda other: _fallback_order(other.language), default=None)
        values = deepcopy(dict(row.handler.defaults)) if source is None else source.override_values()
        values.pop("name", None)
        for field, value in values.items():
            setattr(row, field, value)
        try:
            with transaction.atomic():
                row.full_clean()
                row.save()
                if row.baseline_hash:
                    # save() met a shipped default of this identity, loaded since the check above: the row would
                    # hide it with nothing flagged, so it is rolled back.
                    raise ValidationError(SHIPPED_DEFAULT_MESSAGE, code="shipped_default")
        except IntegrityError:
            # A writer inserted the same identity after full_clean() judged it: refuse in its words.
            row.validate_constraints()
            raise
        return row


class _Backlinks(InstancePrefetch):
    """The rows naming each listed row's kind and key, read once per kind for the rows the reader may preview.

    The rows of one kind and key share one answer, ``((kind, key), rows)``,
    whatever their language: what names a key names each of its rows. A row
    naming several listed keys sits in each of their answers, and nothing is
    copied; one walker serves every kind, so a row naming a listed block and a
    listed part is checked once. A row the reader may not preview
    (:func:`~angee.graphql.capabilities.held_permissions`) gets ``None`` and
    causes no read, as does a row nothing names.
    """

    def related(
        self, instances: list[Any], queryset: models.QuerySet[Any],
    ) -> tuple[list[tuple[tuple[str, str], list[Any]]], Callable[[Any], object], Callable[[Any], object]]:
        identities = {
            row.pk: (str(row.kind), row.key) for row in instances if "preview" in held_permissions(row, ("preview",))
        }
        keys: dict[str, set[str]] = {}
        for kind, key in identities.values():
            keys.setdefault(kind, set()).add(key)
        walker = TemplateWalker()
        answers = [
            ((kind, key), rows)
            for kind in sorted(keys)
            for key, rows in MessageTemplateKind(kind).handler.naming_rows(
                queryset, sorted(keys[kind]), walker=walker,
            ).items()
        ]
        return answers, lambda answer: answer[0], lambda row: identities.get(row.pk)


def _lacking(row: MessageTemplate, permission: str) -> PermissionDenied | None:
    """``PermissionDenied`` in :meth:`~angee.base.models.base.AngeeModel.require_access`'s words when the acting reader
    does not hold ``permission`` on ``row`` (:func:`~angee.graphql.capabilities.held_permissions`, the list's own
    projection when it carries one); ``None`` when it does. The one denial the row's refusals answer with."""

    if permission in held_permissions(row, (permission,)):
        return None
    return PermissionDenied(f"Denied: the current actor lacks {permission!r} on {to_object_ref(row)}.")


def _refusals(error: ValidationError, code: TemplateWarningCode) -> list[TemplateWarning]:
    """``error`` as a preview's ``code`` warnings: one for each distinct refusal and the column it names, ``None`` for
    a refusal of the row as a whole (Django's ``NON_FIELD_ERRORS``)."""

    refusals: Iterable[tuple[str, list[str]]] = (
        error.message_dict.items() if hasattr(error, "error_dict") else [(NON_FIELD_ERRORS, error.messages)]
    )
    distinct = dict.fromkeys((name, message) for name, messages in refusals for message in messages)
    return [TemplateWarning(code, message, None if name == NON_FIELD_ERRORS else name) for name, message in distinct]


class MessageTemplate(ImmutableFieldsMixin, ResourceLoadMixin, HistoryMixin, AngeeDataModel, concrete=True):
    """One message template: a layout, template, block or part, for one key and one language."""

    resource_class = MessageTemplateResource
    immutable_fields = ("kind", "key", "language", "is_baseline")
    edited_fields: ClassVar[tuple[str, ...]] = (
        "name", "description", "format", "subject", "preheader", "source", "text_source", "layout", "variables",
        "sample",
    )
    """The columns an administrator edits: what an override copies and the console updates."""
    _clean_walk: TemplateWalk | None = None
    """The walk the last :meth:`clean` ran (:meth:`validate_source`), or ``None`` when it walked nothing; what
    :meth:`preview` renders a patch from, read straight after ``full_clean()``."""
    sqid_prefix = "mtl_"

    kind = StateField(choices_enum=MessageTemplateKind)
    """What the row is (:class:`MessageTemplateKind`), written once."""
    key = models.CharField(
        max_length=120,
        validators=[validate_key],
    )
    """The name rows and senders use; a shipped template's is ``<owning addon label>.<name>`` by convention."""
    language = LanguageCodeField()
    """The ISO 639 code the row is written in, spelled as parties stores ``Person.languages``."""
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    format = StateField(choices_enum=MessageTemplateFormat, db_index=False)
    """The markup ``source`` is written in (:class:`~angee.messaging_templates.rendering.MessageTemplateFormat`)."""
    subject = models.CharField(max_length=255, blank=True, default="")
    """A template's subject line; empty for the other kinds."""
    preheader = models.CharField(max_length=255, blank=True, default="")
    """A template's preview text, shown after the subject in an inbox; empty for the other kinds."""
    source = models.TextField()
    text_source = models.TextField(blank=True, default="")
    """The plain-text form of an HTML row, which every HTML row has; a Markdown row has none."""
    layout = models.CharField(max_length=120, blank=True, default="")
    """The key of the layout a template sits in, which must resolve (:meth:`clean`); empty for the other kinds."""
    variables = models.JSONField(default=dict, blank=True, validators=[validate_variables])
    """The values the row reads, as dotted paths to descriptions
    (:func:`~angee.messaging_templates.rendering.validate_variables`); a block's or part's are its arguments."""
    sample = models.JSONField(default=dict, blank=True)
    """Values to preview the row with; administrators alone read them."""
    is_baseline = models.BooleanField(default=False, editable=False)
    """A default an addon ships, written only by its resource load (:class:`MessageTemplateResource`)."""
    content_hash = models.CharField(max_length=71, editable=False)
    """``sha256:`` and the canonical JSON digest of :data:`CONTENT_HASH_FIELDS`, derived on every save."""
    baseline_hash = models.CharField(max_length=71, blank=True, default="", editable=False)
    """An override's copy of its baseline's ``content_hash``, stamped when it starts and when it is kept."""

    _angee_base = _MessageTemplateBaseManager()
    objects = MessageTemplateManager()

    hasura_filter_expressions: ClassVar[Mapping[str, Callable[[models.QuerySet[Any]], BaseExpression]]] = (
        MessageTemplateQuerySet.row_fact_expressions
    )

    _backlinks = _Backlinks()

    @classmethod
    def backlinks_prefetch(cls, queryset: models.QuerySet[Any], *, to_attr: str) -> models.Prefetch:
        """Batch, through the supplied scope, the rows naming each listed row's kind and key into ``to_attr``.

        Each listed row gets ``((kind, key), rows)``, or ``None`` when its reader
        may not preview it or nothing names its key (:class:`_Backlinks`). Select
        ``kind`` and ``key`` on the requesting rows and project ``preview`` on
        them; the supplied queryset owns the reader and the hints of the rows it
        returns, and the columns a check reads are loaded whatever it defers.
        """

        return models.Prefetch("_backlinks", queryset=queryset, to_attr=to_attr)

    class Meta:
        """Django model options for a message template."""

        abstract = True
        ordering = ("kind", "key", "language", "is_baseline", "sqid")
        rebac_resource_type = "messaging_templates/message_template"
        constraints = (
            models.UniqueConstraint(
                fields=("kind", "key", "language"),
                condition=models.Q(is_baseline=True),
                name="%(app_label)s_message_template_baseline_key",
                violation_error_message="A shipped default with this kind, key and language already exists.",
            ),
            models.UniqueConstraint(
                fields=("kind", "key", "language"),
                condition=models.Q(is_baseline=False),
                name="%(app_label)s_message_template_local_key",
                violation_error_message=(
                    "This deployment already has a message template with this kind, key and language."
                ),
            ),
            models.CheckConstraint(
                condition=(models.Q(kind__in=_MESSAGE_KINDS) & ~models.Q(layout=""))
                | (~models.Q(kind__in=_MESSAGE_KINDS) & models.Q(subject="", preheader="", layout="")),
                name="%(app_label)s_message_template_kind_fields",
                violation_error_message="A template names its layout; a layout, block or part has no subject, "
                "preheader or layout.",
            ),
            models.CheckConstraint(
                condition=~models.Q(kind=MessageTemplateKind.LAYOUT) | models.Q(format=MessageTemplateFormat.HTML),
                name="%(app_label)s_message_template_layout_html",
                violation_error_message="A message template layout is written in HTML.",
            ),
            models.CheckConstraint(
                condition=(models.Q(format=MessageTemplateFormat.HTML) & ~models.Q(text_source=""))
                | models.Q(format=MessageTemplateFormat.MARKDOWN, text_source=""),
                name="%(app_label)s_message_template_text_source",
                violation_error_message="An HTML message template has a plain-text form; a Markdown one has none.",
            ),
            models.CheckConstraint(
                condition=models.Q(is_baseline=False) | models.Q(baseline_hash=""),
                name="%(app_label)s_message_template_baseline_hash",
                violation_error_message="A shipped default overrides nothing.",
            ),
        )

    def __str__(self) -> str:
        """Return the template's name for Django displays."""

        return self.name

    @property
    def handler(self) -> MessageTemplateKindHandler:
        """Return the behaviour owned by this row's kind."""

        return MessageTemplateKind(self.kind).handler

    @property
    def is_effective(self) -> bool:
        """Whether this row is what renders for its kind, key and language, of all their rows."""

        return self._row_fact("is_effective")

    @property
    def is_overridden(self) -> bool:
        """Whether this baseline has an override."""

        return self._row_fact("is_overridden")

    @property
    def has_updated_default(self) -> bool:
        """Whether this override's baseline changed since the override started or was last kept."""

        return self._row_fact("has_updated_default")

    @property
    def overrides_default(self) -> bool:
        """Whether this row is the deployment's override of a shipped default; a deployment's own row is not."""

        return self._row_fact("overrides_default")

    def override_refusal(self) -> Exception | None:
        """What refuses the acting reader starting this row's override (:meth:`MessageTemplateManager.override`).

        ``PermissionDenied`` when the reader may not create the override, the
        row of this kind, key and language that is not a baseline; else
        ``not_a_baseline`` when this row is not a shipped default; ``None`` when
        nothing does. The verb asks it first, and the console projects
        ``can_override`` from it. The create decision is the candidate
        override's own, so it costs one permission decision per row asked.
        """

        candidate = type(self)(kind=self.kind, key=self.key, language=self.language)
        try:
            candidate.require_access("create", current_actor())
        except PermissionDenied as refused:
            return refused
        if not self.is_baseline:
            return ValidationError(NOT_A_BASELINE_MESSAGE, code="not_a_baseline")
        return None

    def keep_override_refusal(self) -> Exception | None:
        """What refuses the acting reader keeping this override (:meth:`MessageTemplateManager.keep_override`).

        ``PermissionDenied`` when the reader may not write this row
        (:func:`~angee.graphql.capabilities.held_permissions`, the list's own
        projection when it carries one); else ``no_baseline`` when the row
        overrides no shipped default (:attr:`overrides_default`); ``None`` when
        nothing does. The verb asks it first, and the console projects
        ``can_keep_override`` from it.
        """

        if (refused := _lacking(self, "write")) is not None:
            return refused
        if not self.overrides_default:
            return ValidationError(NO_BASELINE_MESSAGE, code="no_baseline")
        return None

    def send_test_refusal(self) -> Exception | None:
        """What refuses the acting reader's test send of this row (:meth:`MessageTemplateManager.send_test`).

        ``PermissionDenied`` unless the reader holds ``preview`` on the row
        (:func:`~angee.graphql.capabilities.held_permissions`, the list's own
        projection when it carries one), an administrator's tool; else
        :meth:`send_refusal`; ``None`` when nothing does. The verb asks
        it first, and the console projects ``can_send_test`` from it: the row's
        side alone, so a test send it admits may still be refused in band, for an
        account with no address, the system channel or its transport, or the
        sample's render.
        """

        if (refused := _lacking(self, "preview")) is not None:
            return refused
        return self.send_refusal()

    def send_refusal(self) -> ValidationError | None:
        """What refuses sending this row (:meth:`MessageTemplateManager.send_as_system`, and a test send after its
        permission): the row's side of a send.

        ``not_a_template`` unless its kind is a mail
        (:attr:`MessageTemplateKindHandler.carries_message_fields`), since a
        layout, block or part reaches a mail inside a template; else
        ``not_effective`` unless it is what renders for its kind, key and
        language (:attr:`is_effective`: an overridden shipped default is not, nor
        is a row never saved);
        ``None`` when nothing does. It decides no permission: whoever sends
        decides that. It reads ``kind`` and at most one structural statement, none
        when the row carries the list's ``_is_effective`` annotation.
        """

        if not self.handler.carries_message_fields:
            return refusal_item(NOT_A_TEMPLATE_MESSAGE, NOT_A_TEMPLATE, RefusalSource.TEMPLATE)
        if not self.is_effective:
            return refusal_item(NOT_EFFECTIVE_MESSAGE, NOT_EFFECTIVE, RefusalSource.TEMPLATE)
        return None

    def _row_fact(self, name: str) -> bool:
        """Read one row fact: the annotation ``with_row_facts`` set, else one structural query for this fact alone,
        through the base manager, which writes no permission audit row: it answers a fact, never a row."""

        if (annotated := self.__dict__.get(f"_{name}")) is not None:
            return bool(annotated)
        rows = type(self)._base_manager.all()
        fact = MessageTemplateQuerySet.row_fact_expressions[name](rows)
        return bool(rows.filter(pk=self.pk).annotate(fact=fact).values_list("fact", flat=True).first())

    def override_values(self) -> dict[str, Any]:
        """Return what an override of this baseline starts from; contributors extend through ``super()``.

        The :attr:`edited_fields`. The identity (kind, key, language) is
        :meth:`MessageTemplateManager.override`'s, and the flag and the hashes are
        :meth:`save`'s.
        """

        return {
            field.name: deepcopy(field.value_from_object(self))
            for field in self._meta.concrete_fields if field.name in self.edited_fields
        }

    def clean_fields(self, exclude: Collection[str] | None = None) -> None:
        """Refuse what no column can store or hash before the columns' own validators read it, then run them.

        A lone surrogate in any of :data:`STORED_COLUMNS`, what JSON cannot write
        (NaN, an infinity, a number over 4,300 digits) in ``variables`` or
        ``sample``, and ``None`` in a column that is not null, are a
        ``ValidationError`` here: the content hash, the database and a render each
        encode what they read. A template's ``layout`` is a key
        (:data:`validate_key`).
        """

        skipped = set(exclude or ())
        errors: dict[str, Any] = {}
        for column in sorted(set(STORED_COLUMNS) - skipped):
            value, field = getattr(self, column), cast("models.Field[Any, Any]", self._meta.get_field(column))
            # Django skips a blank column's own checks for None, which the database then refuses.
            message = field.error_messages["null"] if value is None and not field.null else _unstorable(value)
            if message is not None:
                errors[column] = [message]
        if self.layout and not {"layout"} & (skipped | set(errors)):
            try:
                validate_key(self.layout)
            except ValidationError as error:
                errors["layout"] = error.messages
        try:
            super().clean_fields(exclude=skipped | set(errors))
        except ValidationError as error:
            errors = error.update_error_dict(errors)
        if errors:
            raise ValidationError(errors)

    def clean(self) -> None:
        """Refuse what the columns' own validators and the constraints cannot see.

        Nothing here reads a row :meth:`clean_fields` refuses as unstorable.

        First the text columns' line endings become newlines, so text pasted with
        carriage returns has the content hash of the same text written with newlines.
        A template's layout that is empty or does not resolve (:meth:`MessageTemplateQuerySet.resolves`),
        judged through the acting reader's scope and only when the layout is new
        or changed, so a later change of the default language never refuses an
        edit of the rest of the row; ``variables`` or ``sample`` that is not an
        object. Then, when the columns a walk reads hold what their own validators
        admit (:meth:`_walkable`, judged before the row is hashed) and what the row
        renders is new or changed (its content hash differs from the stored
        row's), :meth:`validate_source`, through the
        same scope, kept as :attr:`_clean_walk`: an edit of only the name, the
        description or the sample walks nothing. The stored layout and hash are a
        structural read of the row's own columns (``_base_manager``).
        """

        self._clean_walk = None
        if not self._readable():
            return
        super().clean()
        self._write_newlines()
        errors: dict[str, str] = {}
        stored_layout, stored_hash = (None, None) if self._state.adding else (
            type(self)._base_manager.filter(pk=self.pk).values_list("layout", "content_hash").first()
            or (None, None)
        )
        if self.kind in MessageTemplateKind.values and self.handler.carries_message_fields:
            if not self.layout:
                errors["layout"] = (
                    "Name the layout this template sits in: a mail renders inside its layout, which gives it its "
                    "document, its brand and its stop link."
                )
            elif stored_layout != self.layout and not type(self).objects.resolves(
                MessageTemplateKind.LAYOUT, self.layout,
            ):
                errors["layout"] = "No layout with this key exists in the default language or as a shipped default."
        for field in ("variables", "sample"):
            if not isinstance(getattr(self, field), dict):
                errors[field] = "Give an object."
        if errors:
            raise ValidationError(errors)
        if self._walkable() and stored_hash != self.compute_content_hash():
            self._clean_walk = self.validate_source()

    def _readable(self) -> bool:
        """Whether every column holds what :meth:`clean_fields` admits: text in a text column, what JSON writes in
        the rest. A column's own validator that refuses a value leaves it on the row as it came, a ``dict`` in
        ``key`` or ``None`` in ``source``, so :meth:`clean` reads nothing of a row that is not."""

        return all(
            _unstorable(value := getattr(self, field.name)) is None
            and (field.null if value is None else (
                not isinstance(field, models.CharField | models.TextField) or isinstance(value, str)))
            for field in self._meta.concrete_fields if field.name in STORED_COLUMNS
        )

    def _walkable(self) -> bool:
        """Whether the columns a walk reads hold what their own validators admit; when not, those refuse the row.

        A column longer than its ``max_length`` is refused by its own validator, so
        an over-long subject or preheader is never lexed.
        """

        if self.kind not in MessageTemplateKind.values or self.format not in MessageTemplateFormat.values:
            return False
        if any(
            len(str(field.value_from_object(self))) > field.max_length
            for field in self._meta.concrete_fields if field.name in CONTENT_HASH_FIELDS and field.max_length
        ):
            return False
        try:
            validate_variables(self.variables)
        except ValidationError:
            return False
        return True

    def validate_source(self) -> TemplateWalk:
        """Walk this row as it would be saved, among its language's effective rows; return the walk, or raise
        ``ValidationError``.

        The walk (:meth:`~angee.messaging_templates.walker.TemplateWalker.walk`)
        refuses what may not render: this row's own source, subject, preheader
        and plain-text form, and what it reaches, its layout and every row it
        includes, each of which must resolve for every recipient. A baseline,
        which only a resource load writes, reaches only shipped rows, so a
        deployment's row never refuses a load. A layout, block or part saved walks
        what it reaches; templates including it are judged when they render.
        """

        rows = type(self).objects
        return TemplateWalker(saving=True).walk(self, rows.filter(is_baseline=True) if self.is_baseline else rows)

    @sensitive_variables("values")
    def render(
        self, values: Mapping[str, Any], *, kit: BrandKit | None = None, must_print: Iterable[str] = (),
    ) -> RenderedMessage:
        """Render this row with ``values``, in ``kit``'s name and colours (the default kit when ``None``).

        Walks the row first, so a row written past :meth:`clean` that would not
        render fails before anything renders; then
        :meth:`~angee.messaging_templates.walker.TemplateWalk.render`. Every required
        value is present and not null, dates are ``date``, aware ``datetime`` or
        ISO 8601, and money is a ``Decimal`` printed through ``floatformat``. Reads
        rows and the kit through the ambient scope: a user's request renders under
        that user, and named system work inside its own ``system_context``
        (:meth:`MessageTemplateManager.send_as_system`). Raises ``ValidationError``
        alone, each item with its code (``invalid_values``, ``values_too_large``,
        ``message_template_refused``, ``value_not_linkable``, ``value_not_printed``, ``render_budget``,
        ``subject_too_long``, ``no_brand_kit``, ``render_failed``) in ``error_list``
        (:func:`~angee.messaging_templates.walker.refusals_only`).

        ``must_print`` is an iterable of the declared paths the mail is sent for
        (a bare string is a ``TypeError``), held as
        :meth:`~angee.messaging_templates.walker.TemplateWalk.render` states: every
        path declared and printed by each body's text, a path the row no longer
        declares refused like one its text no longer prints, because an override
        may edit ``variables`` as well as the text; a path with no ``*`` also
        shown by each body of this mail, its value text that is not blank; a path
        through a list held to the text alone, so an empty list passes. Each
        refusal is ``value_not_printed``, one item per path and body. The preview
        warns of a declared value nothing reads, but it knows no caller's
        requirement, so it stays a warning; the requirement is the sender's to
        pass here.
        """

        walk = TemplateWalker().walk(self, type(self).objects)
        return walk.render(values, kit=self._brand_kit(kit), must_print=must_print)

    @sensitive_variables()
    def preview(self, *, kit: BrandKit | None = None, patch: Mapping[str, Any] | None = None) -> TemplatePreview:
        """Render this row with its ``sample``, or as ``patch`` would save it, and say what a sender should know.

        Requires ``preview`` on the row, and with a ``patch`` also ``write`` and
        what the save's gates ask of the columns it edits, before anything is
        copied or walked (named system work always may); ``PermissionDenied``
        otherwise. So a patch on a shipped default, which only a resource load
        writes, is refused, and an empty patch is a patch. ``patch`` holds native
        values of :attr:`edited_fields`, decoded as the console's update decodes
        its input; the row it makes (:meth:`_patched`) is validated with
        ``full_clean()``, refused for what the update's validation refuses, and
        never saved. One walk serves the preview: the one that ``clean()`` ran when the
        patch changed what renders (:attr:`_clean_walk`, never a shipped default's,
        which reaches shipped rows alone), else the walk of the row as it would
        render. A template renders whole, a layout around its handler's fixed
        body, subject and preheader, and a block or part alone, its sample as its
        arguments. ``rendered`` is ``None`` when the validation, the walk, the
        render or its warnings refused, and each distinct refusal is then one
        warning naming its column (``field``; ``None`` for one of the row as a
        whole): ``save_refused`` when saving the patch would be refused (the
        copy's validation), ``render_error`` when the row, as saved or as the
        patch would save it, does not render with its sample. Only a
        ``ValidationError`` leaves any of them
        (:func:`~angee.messaging_templates.walker.refusals_only`). Every local of
        this frame and of each it calls (:meth:`_patched` among them) is Django's
        ``sensitive_variables``: a report of a failure that propagates shows no
        sample or patch.
        """

        self.require_access("preview")
        if patch is not None:
            self.require_access("write")
        row = self if patch is None else self._patched(patch)
        walk = None
        if patch is not None:
            try:
                row.full_clean()
            except ValidationError as error:
                return TemplatePreview(rendered=None, warnings=_refusals(error, TemplateWarningCode.SAVE_REFUSED))
            walk = None if row.is_baseline else row._clean_walk
        try:
            if walk is None:
                walk = TemplateWalker().walk(row, type(row).objects)
            rendered = walk.render(row.sample, kit=self._brand_kit(kit), frame=row.handler.preview_frame)
            warnings = walk.preview_warnings(rendered, row.sample)
        except ValidationError as error:
            return TemplatePreview(rendered=None, warnings=_refusals(error, TemplateWarningCode.RENDER_ERROR))
        return TemplatePreview(rendered=rendered, warnings=warnings)

    def _patched(self, patch: Mapping[str, Any]) -> Self:
        """This row with ``patch``'s native values over its :attr:`edited_fields`, for :meth:`preview`; never saved.

        Refused before anything is copied: a key outside :attr:`edited_fields`
        (``ValueError``), and a column the patch changes that the save's own
        gates refuse the reader, a deployment's ``write__<field>`` among them
        (``PermissionDenied``, :func:`~angee.base.permissions.require_save_allowed`
        on this row). As the update's save, it asks only the columns whose value
        differs from the loaded row's, by Python equality on native values, so
        a column resent as it is stored, a JSON value as a new equal object or
        the format as the value its console member name decodes to, asks
        nothing. ``copy`` keeps the primary key and the saved
        state, so Django's unique checks leave this row out. It does not keep
        REBAC's state of this row (its pinned actor, its loaded values, its
        field-deny mode), which is why the gates are asked of this row: the copy
        validates in the reader's own scope, the one every message template is
        read in.
        """

        if unknown := sorted(set(patch) - set(self.edited_fields)):
            raise ValueError(f"A patch edits {', '.join(self.edited_fields)}, never {', '.join(unknown)}.")
        if changed := [name for name, value in patch.items() if value != getattr(self, name)]:
            require_save_allowed(self, update_fields=changed)
        row = copy(self)
        for name, value in patch.items():
            setattr(row, name, value)
        return row

    @staticmethod
    def _brand_kit(kit: BrandKit | None) -> BrandKit:
        """Return ``kit``, or the default kit the acting reader reads; refuse when there is none."""

        if kit is None:
            kit = apps.get_model("brandkit", "BrandKit").objects.default()
        if kit is None:
            raise refusal_item(NO_BRAND_KIT_MESSAGE, NO_BRAND_KIT, RefusalSource.DEPLOYMENT)
        return cast(BrandKit, kit)

    @classmethod
    def after_resource_load(cls, instances: Iterable[Self], *, tier: str, source: str) -> None:
        """Walk every loaded row, unchanged ones included; a shipped defect fails the load, a deployment's is reported.

        Each loaded baseline is walked among the shipped rows alone, as
        :meth:`validate_source` walks it, and a refusal fails the load. It is then
        walked among the rows that render; a refusal reached only through a
        deployment's row (an override or its own row that the shipped row includes
        or is laid out by) is logged as a warning naming both rows and the
        refusal, the load completes, and the shipped row refuses at render until
        the deployment fixes its row.
        """

        loaded = tuple(instances)
        walker = TemplateWalker(saving=True)
        rows = cls.objects
        for instance in loaded:
            walker.walk(instance, rows.filter(is_baseline=True))
            try:
                walk = walker.judge(instance, rows)
            except ValidationError as error:  # a failure nobody foresaw, logged where it happened
                deployment, refusals = "rows", error.messages
            else:
                deployment = ", ".join(f"{name} ({row.language})" for name, row in walk.rows.items()
                                       if not cast(MessageTemplate, row).is_baseline)
                refusals = walk.refusals
            if refusals:
                logger.warning(
                    "The shipped message template %s (%s) refuses through the deployment's %s, and refuses at "
                    "render until that row is fixed: %s",
                    instance.handler.template_name(instance.key), instance.language, deployment,
                    "; ".join(dict.fromkeys(refusals)),
                )
        super().after_resource_load(loaded, tier=tier, source=source)

    def delete_blocker(self, *, cascaded: bool = False) -> str | None:
        """Refuse deleting a row other rows name while its key would stop resolving without it.

        Rows name a key through :meth:`MessageTemplateKindHandler.naming_rows`; the
        key keeps resolving while it has a baseline or another row in the default
        language (:meth:`MessageTemplateQuerySet.resolves`). Enforced on instance,
        queryset and cascade deletes by
        :func:`angee.messaging_templates.signals.refuse_unresolved_delete`. Each row
        is judged with the others still there, so one queryset delete from code
        that removes every row keeping a key resolvable, a baseline among them,
        passes; the console deletes one row by its id and cannot reach it.
        """

        del cascaded
        rows = type(self).system_queryset()
        if not self.handler.is_named(rows, self.key):
            return None
        if rows.exclude(pk=self.pk).resolves(self.kind, self.key):
            return None
        return UNRESOLVED_DELETE_MESSAGE % {"kind": MessageTemplateKind(self.kind).label.lower()}

    def _write_newlines(self) -> None:
        """Write a carriage return in :data:`TEXT_COLUMNS` as a newline, so pasted text has one content hash."""

        for column in TEXT_COLUMNS:
            text = getattr(self, column)
            if isinstance(text, str) and "\r" in text:
                setattr(self, column, text.replace("\r\n", "\n").replace("\r", "\n"))

    def compute_content_hash(self, stored: Mapping[str, Any] | None = None) -> str:
        """Return the fingerprint of what this row is and renders now: ``sha256:`` and the canonical digest of
        :data:`CONTENT_HASH_FIELDS`, which :meth:`save` stores as ``content_hash``; ``stored`` gives the stored value of
        a column the instance's own value stands in for."""

        return "sha256:" + canonical_json_sha256({
            field.name: (stored or {}).get(field.name, field.value_from_object(self))
            for field in self._meta.concrete_fields if field.name in CONTENT_HASH_FIELDS
        })

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the row, deriving ``content_hash`` and stamping a new override's ``baseline_hash``.

        Every writer's text has its carriage returns written as newlines first, as
        :meth:`clean` does, since a writer may validate one instance and save
        another. A save that writes a column of :data:`CONTENT_HASH_FIELDS` writes
        the hash with it, ``update_fields`` included, since a resource reload saves
        only the columns its row states; the hash of such a partial save reads the
        stored values of the hashed columns it omits, locked, so it describes the
        stored row; the read and the write share one transaction, with no savepoint,
        so a failure marks the caller's transaction for rollback as Django's own save
        does. A non-baseline row inserted with no
        ``baseline_hash`` where its baseline exists takes the baseline's
        ``content_hash``; one that holds a hash already, as a history revert
        restores it, keeps it.
        """

        self._write_newlines()
        update_fields = kwargs.get("update_fields")
        with transaction.atomic(savepoint=False):
            self._derive_hashes(update_fields)
            if update_fields is not None and not set(CONTENT_HASH_FIELDS).isdisjoint(update_fields):
                kwargs["update_fields"] = {*update_fields, "content_hash"}
            super().save(*args, **kwargs)

    def _derive_hashes(self, update_fields: Iterable[str] | None) -> None:
        """Set ``content_hash`` when the save writes a hashed column, and a new override's ``baseline_hash``."""

        if update_fields is None or not set(CONTENT_HASH_FIELDS).isdisjoint(update_fields):
            omitted = set() if update_fields is None else set(CONTENT_HASH_FIELDS) - set(update_fields)
            stored = (
                type(self).system_queryset().select_for_update().filter(pk=self.pk).values(*omitted).first()
                if omitted and not self._state.adding else None
            )
            self.content_hash = self.compute_content_hash(stored)
        if self._state.adding and not self.is_baseline and not self.baseline_hash:
            shipped = (
                type(self).system_queryset()
                .filter(is_baseline=True, kind=self.kind, key=self.key, language=self.language)
                .values_list("content_hash", flat=True)
                .first()
            )
            if shipped is not None:
                self.baseline_hash = shipped
