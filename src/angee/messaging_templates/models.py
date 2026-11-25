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
A non-baseline row with no baseline is the deployment's own. ``content_hash``
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
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import IntegrityError, models, transaction
from django.db.models.expressions import BaseExpression
from rebac import actor_context, system_context

from angee.base.fields.state import StateField
from angee.base.models.base import AngeeDataModel
from angee.base.models.locks import ImmutableFieldsMixin
from angee.base.models.timestamps import update_fields_with_auto_now
from angee.base.querysets import ClosedUpdateQuerySet
from angee.base.serialization import canonical_json_sha256
from angee.brandkit.models import BrandKit
from angee.messaging_templates.rendering import MessageTemplateFormat, validate_variables
from angee.messaging_templates.resources import MessageTemplateResource
from angee.messaging_templates.walker import (
    PreviewFrame,
    RenderedMessage,
    TemplatePreview,
    TemplateWalker,
    TemplateWarning,
    TemplateWarningCode,
    in_layout,
)
from angee.parties.fields import LanguageCodeField, normalize_language_code
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager, AngeeHistoryUnscopedManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet, AngeeHistoryUnscopedQuerySet
from angee.resources.mixins import ResourceLoadMixin

if TYPE_CHECKING:
    from angee.parties.models import Person

FALLBACK_LANGUAGE = "en"
"""The language a key falls back to first when no requested language has a row: the one shipped rows are written in."""

CONTENT_HASH_FIELDS = ("kind", "format", "subject", "preheader", "source", "text_source", "layout", "variables")
"""The columns ``content_hash`` fingerprints: what a row is and renders, not what it is called."""

_KEY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\Z")
"""Anchored, since ``RegexValidator`` searches; ``\\Z`` refuses the trailing newline ``$`` admits."""

NOT_A_BASELINE_MESSAGE = "Only a shipped default can be overridden."
NO_BASELINE_MESSAGE = "This message template overrides no shipped default."
UNRESOLVED_DELETE_MESSAGE = (
    "Other message templates use this %(kind)s, which would no longer resolve without this row: "
    "give its key a row in the default language first."
)
ONE_AT_A_TIME_MESSAGE = "Message templates are written one at a time, so each save derives its content hash."
NO_BRAND_KIT_MESSAGE = "No brand kit is the default, and a message renders in a brand kit's name and colours."

LAYOUT_PREVIEW = PreviewFrame(
    subject="A message in this layout",
    preheader="The preheader shows after the subject in an inbox list.",
    html='<p style="margin:0 0 16px">A template writes its message here. The layout owns everything around it: '
         "the header, the footer, the width, the colours and the type.</p>",
    text="A template writes its message here. The layout owns everything around it: the header, the footer, the "
         "width, the colours and the type.",
)
"""The fixed body, subject and preheader a layout is previewed with."""


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
    conditional_comments: ClassVar[bool] = False
    """Whether a row of this kind may carry Outlook's conditional comments, static and checked as markup."""
    preview_frame: ClassVar[PreviewFrame | None] = None
    """The fixed body, subject and preheader a row of this kind is previewed around; ``None`` previews it alone."""

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

    def naming_rows(self, rows: models.QuerySet[Any], key: str) -> models.QuerySet[Any]:
        """Return the rows among ``rows``, in any language, that name ``key`` as a row of this kind."""

        del key
        return rows.none()


class LayoutHandler(MessageTemplateKindHandler):
    """A message template layout wraps templates, which name it in their ``layout``; it is previewed around a body."""

    template_prefix = "layouts/"
    reads_message_fields = True
    holds_content = True
    conditional_comments = True
    preview_frame = LAYOUT_PREVIEW

    def naming_rows(self, rows: models.QuerySet[Any], key: str) -> models.QuerySet[Any]:
        """Return the rows whose ``layout`` is ``key``: only templates fill the column."""

        return rows.filter(layout=key)


class TemplateHandler(MessageTemplateKindHandler):
    """A message template of the kind ``template`` is a mail: a subject, a preheader and the layout it sits in."""

    carries_message_fields = True
    reads_message_fields = True

    def layout_name(self, row: MessageTemplate) -> str | None:
        """Return the name of the layout the template's ``layout`` column names."""

        return MessageTemplateKind.LAYOUT.handler.template_name(row.layout)

    def wrap(self, row: MessageTemplate, form: str) -> str:
        """Return the form as the content of its layout: ``{% extends %}`` and ``{% block content %}`` around it."""

        return in_layout(str(self.layout_name(row)), form)


class IncludedHandler(MessageTemplateKindHandler):
    """A kind rows include by name: rows name a key of it when their compiled forms include it."""

    includable = True

    def naming_rows(self, rows: models.QuerySet[Any], key: str) -> models.QuerySet[Any]:
        """Return the rows among ``rows`` whose compiled forms include this kind's row named ``key``.

        Found at delete time: the rows whose source or plain-text form holds the
        name, then each confirmed by compiling it. A row that does not compile
        names nothing.
        """

        name = self.template_name(key)
        walker = TemplateWalker()
        candidates = rows.filter(models.Q(source__contains=name) | models.Q(text_source__contains=name))
        return rows.filter(pk__in=[row.pk for row in candidates if name in walker.includes(row)])


class BlockHandler(IncludedHandler):
    """A message template block is a styled piece of a mail that rows include."""

    template_prefix = "blocks/"


class PartHandler(IncludedHandler):
    """A message template part is a piece of wording that rows include."""

    template_prefix = "parts/"


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
                languages, key=lambda code: (code != FALLBACK_LANGUAGE, code)
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
        resolved by :meth:`effective_rows` in one query; a name with no row is absent.
        """

        identities: dict[tuple[str, str], str] = {}
        for name in names:
            if (identity := MessageTemplateKind.for_template_name(name)) is not None:
                identities[identity] = name
        if not identities:
            return {}
        rows = self.effective_rows(
            {kind for kind, _ in identities}, keys={key for _, key in identities}, language=language,
        )
        return {identities[identity]: row for identity, row in rows.items() if identity in identities}

    def resolves_name(self, name: str) -> bool:
        """Whether every recipient finds the row a template name names (:meth:`resolves`)."""

        identity = MessageTemplateKind.for_template_name(name)
        return identity is not None and self.resolves(*identity)

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
    """Owns the writes that start from a shipped default: overriding it and keeping an override over it."""

    def override(self, baseline: TemplateT) -> TemplateT:
        """Return the deployment's override of ``baseline``, created from it when there is none.

        Requires ``create`` on the override before anything is locked (named
        system work always may). Then, in one transaction under ``baseline``'s row
        lock, refuses a row that is not a baseline and returns the existing
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
            override.require_access("create", actor)
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

        Requires ``write`` on ``override`` before anything is locked (named system
        work always may). Then, under its row lock, refuses a row that overrides no
        baseline and stamps its ``baseline_hash`` with the baseline's ``content_hash``.
        """

        actor, unscoped = override.effective_actor(strict=True)
        if not unscoped:
            override.require_access("write", actor)
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


class MessageTemplate(ImmutableFieldsMixin, ResourceLoadMixin, HistoryMixin, AngeeDataModel, concrete=True):
    """One message template: a layout, template, block or part, for one key and one language."""

    resource_class = MessageTemplateResource
    immutable_fields = ("kind", "key", "language", "is_baseline")
    edited_fields: ClassVar[tuple[str, ...]] = (
        "name", "description", "format", "subject", "preheader", "source", "text_source", "layout", "variables",
        "sample",
    )
    """The columns an administrator edits: what an override copies and the console updates."""
    sqid_prefix = "mtl_"

    kind = StateField(choices_enum=MessageTemplateKind)
    """What the row is (:class:`MessageTemplateKind`), written once."""
    key = models.CharField(
        max_length=120,
        validators=[RegexValidator(_KEY, "Use lower-case names of letters, digits and underscores, joined by dots.")],
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

    def _row_fact(self, name: str) -> bool:
        """Read one row fact: the annotation ``with_row_facts`` set, else one query for this fact alone."""

        if (annotated := self.__dict__.get(f"_{name}")) is not None:
            return bool(annotated)
        rows = type(self).system_queryset()
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

    def clean(self) -> None:
        """Refuse what the columns' own validators and the constraints cannot see.

        A template's layout that does not resolve (:meth:`MessageTemplateQuerySet.resolves`),
        judged through the acting reader's scope and only when the layout is new
        or changed, so a later change of the default language never refuses an
        edit of the rest of the row; ``variables`` or ``sample`` that is not an
        object. Then, when what the row renders is new or changed (its content
        hash differs from the stored row's), :meth:`validate_source`, through the
        same scope: an edit of only the name, the description or the sample walks
        nothing.
        """

        super().clean()
        errors: dict[str, str] = {}
        stored_layout, stored_hash = (None, None) if self._state.adding else (
            type(self).system_queryset().filter(pk=self.pk).values_list("layout", "content_hash").first()
            or (None, None)
        )
        if self.kind in MessageTemplateKind.values and self.handler.carries_message_fields and self.layout:
            if stored_layout != self.layout and not type(self).objects.resolves(
                MessageTemplateKind.LAYOUT, self.layout,
            ):
                errors["layout"] = "No layout with this key exists in the default language or as a shipped default."
        for field in ("variables", "sample"):
            if not isinstance(getattr(self, field), dict):
                errors[field] = "Give an object."
        if errors:
            raise ValidationError(errors)
        if stored_hash != self.compute_content_hash() and self._walkable():
            self.validate_source()

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

    def validate_source(self) -> None:
        """Walk this row as it would be saved, among its language's effective rows; raise ``ValidationError``.

        The walk (:meth:`~angee.messaging_templates.walker.TemplateWalker.walk`)
        refuses what may not render: this row's own source, subject, preheader
        and plain-text form, and what it reaches, its layout and every row it
        includes, each of which must resolve for every recipient. A layout, block or
        part saved walks what it reaches; templates including it are judged when
        they render.
        """

        TemplateWalker(saving=True).walk(self, type(self).objects)

    def render(self, values: Mapping[str, Any], *, kit: BrandKit | None = None) -> RenderedMessage:
        """Render this row with ``values``, in ``kit``'s name and colours (the default kit when ``None``).

        Walks the row first, so a row written past :meth:`clean` that would not
        render fails before anything renders; then
        :meth:`~angee.messaging_templates.walker.TemplateWalk.render`. Every required
        value is present and not null, dates are ``date``, aware ``datetime`` or
        ISO 8601, and money is a ``Decimal`` printed through ``floatformat``. Reads
        rows and the kit through the acting reader's scope: a sender renders as the
        system actor. Raises ``ValidationError``.
        """

        walk = TemplateWalker().walk(self, type(self).objects)
        return walk.render(values, kit=self._brand_kit(kit))

    def preview(self, *, kit: BrandKit | None = None) -> TemplatePreview:
        """Render this row with its ``sample`` and say what a sender should know about it.

        Requires ``preview`` on the row before anything else (named system work
        always may); ``PermissionDenied`` otherwise. A template renders whole, a
        layout around its handler's fixed body, subject and preheader, and a block
        or part alone, its sample as its arguments. ``rendered`` is ``None`` when
        the walk or the render raised, and the one warning is then ``render_error``.
        """

        self.require_access("preview")
        try:
            walk = TemplateWalker().walk(self, type(self).objects)
            rendered = walk.render(self.sample, kit=self._brand_kit(kit), frame=self.handler.preview_frame)
        except ValidationError as error:
            return TemplatePreview(
                rendered=None, warnings=[TemplateWarning(TemplateWarningCode.RENDER_ERROR, " ".join(error.messages))],
            )
        return TemplatePreview(rendered=rendered, warnings=walk.preview_warnings(rendered, self.sample))

    @staticmethod
    def _brand_kit(kit: BrandKit | None) -> BrandKit:
        """Return ``kit``, or the default kit the acting reader reads; refuse when there is none."""

        if kit is None:
            kit = apps.get_model("brandkit", "BrandKit").objects.default()
        if kit is None:
            raise ValidationError(NO_BRAND_KIT_MESSAGE, code="no_brand_kit")
        return cast(BrandKit, kit)

    @classmethod
    def after_resource_load(cls, instances: Iterable[Self], *, tier: str, source: str) -> None:
        """Walk every loaded row, unchanged ones included, as a save walks it (:meth:`validate_source`)."""

        loaded = tuple(instances)
        walker = TemplateWalker(saving=True)
        for instance in loaded:
            walker.walk(instance, cls.objects)
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
        if not self.handler.naming_rows(rows, self.key).exists():
            return None
        if rows.exclude(pk=self.pk).resolves(self.kind, self.key):
            return None
        return UNRESOLVED_DELETE_MESSAGE % {"kind": MessageTemplateKind(self.kind).label.lower()}

    def compute_content_hash(self) -> str:
        """Return the fingerprint of what this row is and renders now: ``sha256:`` and the canonical digest of
        :data:`CONTENT_HASH_FIELDS`, which :meth:`save` stores as ``content_hash``."""

        return "sha256:" + canonical_json_sha256({
            field.name: field.value_from_object(self)
            for field in self._meta.concrete_fields if field.name in CONTENT_HASH_FIELDS
        })

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the row, deriving ``content_hash`` and stamping a new override's ``baseline_hash``.

        A save that writes a column of :data:`CONTENT_HASH_FIELDS` writes the hash
        with it, ``update_fields`` included, since a resource reload saves only the
        columns its row states. A non-baseline row inserted with no
        ``baseline_hash`` where its baseline exists takes the baseline's
        ``content_hash``; one that holds a hash already, as a history revert
        restores it, keeps it.
        """

        update_fields = kwargs.get("update_fields")
        if update_fields is None or not set(CONTENT_HASH_FIELDS).isdisjoint(update_fields):
            self.content_hash = self.compute_content_hash()
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "content_hash"}
        if self._state.adding and not self.is_baseline and not self.baseline_hash:
            shipped = (
                type(self).system_queryset()
                .filter(is_baseline=True, kind=self.kind, key=self.key, language=self.language)
                .values_list("content_hash", flat=True)
                .first()
            )
            if shipped is not None:
                self.baseline_hash = shipped
        super().save(*args, **kwargs)
