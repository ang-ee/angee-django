"""Message templates: the layouts, templates, blocks and parts outgoing mail is written from.

A :class:`MessageTemplate` is one row of one kind (:class:`MessageTemplateKind`)
for one key and one language. A template is the mail itself, with its subject,
preheader and the layout it sits in; a layout wraps templates; a block and a part
are pieces rows include. What differs by kind is its handler's
(:attr:`MessageTemplateKind.handler`): the constraints, :meth:`MessageTemplate.clean`
and :meth:`MessageTemplate.delete_blocker` read it, and nothing else branches on
the kind.

**Baselines and overrides.** An addon ships rows in its ``install`` resources as
baselines (``is_baseline``), which loads alone write. The deployment's override of
a baseline is the non-baseline row with the baseline's kind, key and language,
started by :meth:`MessageTemplateManager.override` and kept over a changed default
by :meth:`MessageTemplateManager.keep_override`; deleting it resets to the default.
A non-baseline row with no baseline is the deployment's own. ``content_hash``
fingerprints what renders, and an override's ``baseline_hash`` is the baseline's
fingerprint when the override started or was last kept, so "default updated" is a
comparison of the two (:meth:`MessageTemplateQuerySet.row_facts`).

**Languages.** :meth:`MessageTemplateQuerySet.effective_rows` picks, for each kind
and key, the row a recipient reads: the first of the requested languages (or the
person's) and then the default language that has a row, an override before its
baseline, and else the language of a baseline, English first.

**Access.** Every signed-in actor reads rows; platform administrators write,
delete and read samples of non-baseline rows, and nobody but a resource load
writes a baseline (``permissions.zed``).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from functools import reduce
from operator import and_
from typing import TYPE_CHECKING, Any, ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models.expressions import BaseExpression
from django.utils.translation import get_language_info

from angee.base.fields.state import StateField
from angee.base.models.base import AngeeDataModel
from angee.base.models.locks import ImmutableFieldsMixin
from angee.base.models.timestamps import update_fields_with_auto_now
from angee.base.serialization import canonical_json_sha256
from angee.messaging_templates.rendering import MessageTemplateFormat, validate_variables
from angee.messaging_templates.resources import MessageTemplateResource
from angee.parties.fields import normalize_language_codes
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
from angee.resources.mixins import ResourceLoadMixin

if TYPE_CHECKING:
    from angee.parties.models import Person

FALLBACK_LANGUAGE = "en"
"""The language a key falls back to first when no requested language has a row: the one shipped rows are written in."""

CONTENT_HASH_FIELDS = ("format", "subject", "preheader", "source", "text_source", "layout", "variables")
"""The columns ``content_hash`` fingerprints: what a row renders, not what it is called."""

ROW_FACTS = ("is_effective", "is_overridden", "default_updated")
"""The facts :meth:`MessageTemplateQuerySet.row_facts` derives for each row from its siblings."""

_KEY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\Z")
"""Anchored, since ``RegexValidator`` searches; ``\\Z`` refuses the trailing newline ``$`` admits."""

NOT_A_BASELINE_MESSAGE = "Only a shipped default can be overridden."
NO_BASELINE_MESSAGE = "This message template overrides no shipped default."
UNRESOLVED_DELETE_MESSAGE = (
    "Other message templates use this %(kind)s, which would no longer resolve without this row: "
    "give its key a row in the default language first."
)


class MessageTemplateKind(models.TextChoices):
    """What a message template is; each kind's behaviour is its :attr:`handler`."""

    LAYOUT = "layout", "Layout"
    TEMPLATE = "template", "Template"
    BLOCK = "block", "Block"
    PART = "part", "Part"

    @property
    def handler(self) -> KindHandler:
        """Return the behaviour owned by this kind."""

        return _KIND_HANDLERS[self]


class KindHandler:
    """What one kind of message template is: the columns it fills, its names, and the rows that name it."""

    carries_message_fields: ClassVar[bool] = False
    """Whether rows of this kind fill ``subject``, ``preheader`` and ``layout``; the others leave them empty."""
    template_prefix: ClassVar[str] = ""
    """What precedes a key in the template name the renderer serves the row under (``blocks/`` and a key)."""
    qualified_key: ClassVar[bool] = False
    """Whether a key is ``<owning addon label>.<name>``; otherwise it is a bare name."""
    required_format: ClassVar[MessageTemplateFormat | None] = None
    """The one format rows of this kind are written in, or ``None`` for either."""

    def naming_rows(self, rows: models.QuerySet[Any], key: str) -> models.QuerySet[Any]:
        """Return the rows among ``rows``, in any language, that name ``key`` as a row of this kind."""

        del key
        return rows.none()


class LayoutHandler(KindHandler):
    """A layout wraps templates, which name it in their ``layout``; it is HTML."""

    template_prefix = "layouts/"
    required_format = MessageTemplateFormat.HTML

    def naming_rows(self, rows: models.QuerySet[Any], key: str) -> models.QuerySet[Any]:
        """Return the rows whose ``layout`` is ``key``: only templates fill the column."""

        return rows.filter(layout=key)


class TemplateHandler(KindHandler):
    """A template is a mail: it has a subject, a preheader and the layout it sits in, and its key names its addon."""

    carries_message_fields = True
    qualified_key = True


class BlockHandler(KindHandler):
    """A block is a styled piece of a mail that rows include."""

    template_prefix = "blocks/"


class PartHandler(KindHandler):
    """A part is a piece of wording that rows include."""

    template_prefix = "parts/"


_KIND_HANDLERS: Mapping[MessageTemplateKind, KindHandler] = {
    MessageTemplateKind.LAYOUT: LayoutHandler(),
    MessageTemplateKind.TEMPLATE: TemplateHandler(),
    MessageTemplateKind.BLOCK: BlockHandler(),
    MessageTemplateKind.PART: PartHandler(),
}

_MESSAGE_KINDS = [str(kind) for kind in MessageTemplateKind if kind.handler.carries_message_fields]
_FIXED_FORMATS = [
    (str(kind), str(fixed)) for kind in MessageTemplateKind if (fixed := kind.handler.required_format) is not None
]


def _known_language(code: str) -> bool:
    """Whether ``code`` is spelled as parties stores a language and Django knows it (for its writing direction)."""

    try:
        normalized = normalize_language_codes([code])
        get_language_info(code)
    except (ValidationError, KeyError):
        return False
    return normalized == [code]


class MessageTemplateQuerySet[TemplateT: MessageTemplate](AngeeHistoryQuerySet[TemplateT]):
    """Read scopes over message templates: the language rule, whether a key resolves, and the row facts."""

    @staticmethod
    def default_language() -> str:
        """Return the deployment's default language: ``LANGUAGE_CODE``'s primary subtag, as parties spells it."""

        return normalize_language_codes([settings.LANGUAGE_CODE.split("-", 1)[0]])[0]

    def language_candidates(self, *, language: str | None = None, person: Person | None = None) -> list[str]:
        """Return the languages a lookup tries, in order, each once: ``language`` or ``person``'s, then the default.

        ``language``, when given, is normalized as parties stores languages and
        replaces ``person``'s; a code parties refuses raises ``ValidationError``.
        """

        preferred = normalize_language_codes([language]) if language else list(person.languages) if person else []
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
        absent. ``keys`` ``None`` reads every key of ``kinds``. Rows are read
        through this queryset's scope.
        """

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

    @staticmethod
    def row_facts(model: type[MessageTemplate]) -> dict[str, BaseExpression]:
        """Return :data:`ROW_FACTS` as boolean expressions over ``model``'s rows, each read from its siblings.

        ``is_effective``: a non-baseline row, or a baseline with no non-baseline row
        of its kind, key and language, so what renders for them. ``is_overridden``:
        a baseline with such a row. ``default_updated``: an override whose
        ``baseline_hash`` differs from its baseline's ``content_hash``. Siblings are
        read unscoped: every reader of a row reads them all.
        """

        siblings = model._base_manager.filter(
            kind=models.OuterRef("kind"), key=models.OuterRef("key"), language=models.OuterRef("language"),
        )
        overridden = models.Exists(siblings.filter(is_baseline=False))
        moved = models.Exists(siblings.filter(is_baseline=True).exclude(content_hash=models.OuterRef("baseline_hash")))
        return {
            "is_effective": models.ExpressionWrapper(
                models.Q(is_baseline=False) | ~overridden, output_field=models.BooleanField(),
            ),
            "is_overridden": models.ExpressionWrapper(
                models.Q(is_baseline=True) & overridden, output_field=models.BooleanField(),
            ),
            "default_updated": models.ExpressionWrapper(
                models.Q(is_baseline=False) & moved, output_field=models.BooleanField(),
            ),
        }

    def with_row_facts(self) -> MessageTemplateQuerySet[TemplateT]:
        """Annotate each of :data:`ROW_FACTS` as ``_<name>``, the annotation the model's properties read."""

        return self.annotate(**{f"_{name}": fact for name, fact in self.row_facts(self.model).items()})


_MessageTemplateManagerBase = AngeeHistoryManager.from_queryset(MessageTemplateQuerySet)


class MessageTemplateManager[TemplateT: MessageTemplate](_MessageTemplateManagerBase[TemplateT]):
    """Owns the writes that start from a shipped default: overriding it and keeping an override over it."""

    def override(self, baseline: TemplateT) -> TemplateT:
        """Return the deployment's override of ``baseline``, created from it when there is none.

        In one transaction under ``baseline``'s row lock: refuses a row that is not
        a baseline, requires ``create`` on the override (named system work always
        may), and returns the existing override or a new one holding
        :meth:`MessageTemplate.override_values`, whose ``baseline_hash`` its save
        stamps. Calling it again returns the same row.
        """

        actor, unscoped = baseline.effective_actor(strict=True)
        with transaction.atomic():
            locked = self.model.system_queryset(lock=("self",)).filter(pk=baseline.pk).first()
            if locked is None or not locked.is_baseline:
                raise ValidationError(NOT_A_BASELINE_MESSAGE, code="not_a_baseline")
            identity = {"kind": locked.kind, "key": locked.key, "language": locked.language}
            override = self.model(**identity, **locked.override_values())
            if unscoped:
                override.sudo(reason="messaging_templates.message_template.override")
            else:
                override.require_access("create", actor)
            existing = self.model.system_queryset().filter(is_baseline=False, **identity).first()
            if existing is not None:
                return existing if unscoped else existing.with_actor(actor)
            override.full_clean()
            override.save()
            if unscoped:
                override.unsudo()
            return override

    def keep_override(self, override: TemplateT) -> TemplateT:
        """Keep ``override``'s text over its baseline as it is now, which clears "default updated".

        Under ``override``'s row lock: refuses a row that overrides no baseline,
        requires ``write`` on it (named system work always may), and stamps its
        ``baseline_hash`` with the baseline's ``content_hash``.
        """

        actor, unscoped = override.effective_actor(strict=True)
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
                locked.require_access("write", actor)
            locked.baseline_hash = shipped.content_hash
            locked.save(update_fields=update_fields_with_auto_now(locked, {"baseline_hash"}))
            if unscoped:
                locked.unsudo()
            return locked


class MessageTemplate(ImmutableFieldsMixin, ResourceLoadMixin, HistoryMixin, AngeeDataModel, concrete=True):
    """One message template: a layout, template, block or part, for one key and one language."""

    resource_class = MessageTemplateResource
    immutable_fields = ("kind", "key", "language", "is_baseline")
    sqid_prefix = "mtl_"

    kind = StateField(choices_enum=MessageTemplateKind)
    """What the row is (:class:`MessageTemplateKind`), written once."""
    key = models.CharField(
        max_length=120,
        validators=[RegexValidator(_KEY, "Use lower-case names of letters, digits and underscores, joined by dots.")],
    )
    """The name rows and senders use: ``<owning addon label>.<name>`` for a template, else a bare name."""
    language = models.CharField(max_length=3)
    """The ISO 639 code the row is written in, spelled as parties stores ``Person.languages``."""
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    format = StateField(choices_enum=MessageTemplateFormat)
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

    objects = MessageTemplateManager()

    hasura_filter_expressions: ClassVar[Mapping[str, Callable[[models.QuerySet[Any]], BaseExpression]]] = {
        "is_effective": lambda rows: MessageTemplateQuerySet.row_facts(rows.model)["is_effective"],
        "is_overridden": lambda rows: MessageTemplateQuerySet.row_facts(rows.model)["is_overridden"],
        "default_updated": lambda rows: MessageTemplateQuerySet.row_facts(rows.model)["default_updated"],
    }

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
                condition=reduce(
                    and_, (~models.Q(kind=kind) | models.Q(format=fixed) for kind, fixed in _FIXED_FORMATS),
                ),
                name="%(app_label)s_message_template_layout_html",
                violation_error_message="A layout is written in HTML.",
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
    def handler(self) -> KindHandler:
        """Return the behaviour owned by this row's kind."""

        return MessageTemplateKind(self.kind).handler

    @property
    def is_effective(self) -> bool:
        """Whether this row renders for its kind, key and language (:meth:`MessageTemplateQuerySet.row_facts`)."""

        return self._row_fact("is_effective")

    @property
    def is_overridden(self) -> bool:
        """Whether this baseline has an override (:meth:`MessageTemplateQuerySet.row_facts`)."""

        return self._row_fact("is_overridden")

    @property
    def default_updated(self) -> bool:
        """Whether this override's baseline changed since it started or was last kept (:data:`ROW_FACTS`)."""

        return self._row_fact("default_updated")

    def _row_fact(self, name: str) -> bool:
        """Read one of :data:`ROW_FACTS`: the annotation ``with_row_facts`` set, else one query."""

        if (annotated := self.__dict__.get(f"_{name}")) is not None:
            return bool(annotated)
        fact = MessageTemplateQuerySet.row_facts(type(self))[name]
        rows = type(self).system_queryset().filter(pk=self.pk).annotate(fact=fact)
        return bool(rows.values_list("fact", flat=True).first())

    def override_values(self) -> dict[str, Any]:
        """Return what an override of this baseline starts from; contributors extend through ``super()``.

        Everything an administrator edits. The identity (kind, key, language) is
        :meth:`MessageTemplateManager.override`'s, and the flag and the hashes are
        :meth:`save`'s.
        """

        return {
            "name": self.name,
            "description": self.description,
            "format": self.format,
            "subject": self.subject,
            "preheader": self.preheader,
            "source": self.source,
            "text_source": self.text_source,
            "layout": self.layout,
            "variables": deepcopy(self.variables),
            "sample": deepcopy(self.sample),
        }

    def clean(self) -> None:
        """Refuse what the columns' own validators and the constraints cannot see.

        A key shaped against its kind (:attr:`KindHandler.qualified_key`); a language
        parties would store differently or Django does not know; a template's layout
        that does not resolve (:meth:`MessageTemplateQuerySet.resolves`); ``variables``
        or ``sample`` that is not an object. Template syntax, Markdown and HTML are
        judged when a row renders.
        """

        super().clean()
        errors: dict[str, str] = {}
        if self.kind in MessageTemplateKind.values:
            handler = self.handler
            if _KEY.match(self.key) and handler.qualified_key != ("." in self.key):
                errors["key"] = (
                    "A template's key is its addon's label and a name, joined by a dot."
                    if handler.qualified_key else "A layout's, block's or part's key is a bare name, with no dot."
                )
            if handler.carries_message_fields and self.layout and not type(self).system_queryset().resolves(
                MessageTemplateKind.LAYOUT, self.layout,
            ):
                errors["layout"] = "No layout with this key exists in the default language or as a shipped default."
        if self.language and not _known_language(self.language):
            errors["language"] = "Use a language code as parties stores it, such as en or ast, that Django knows."
        for field in ("variables", "sample"):
            if not isinstance(getattr(self, field), dict):
                errors[field] = "Give an object."
        if errors:
            raise ValidationError(errors)

    def delete_blocker(self, *, cascaded: bool = False) -> str | None:
        """Refuse deleting a row other rows name while its key would stop resolving without it.

        Rows name a key through :meth:`KindHandler.naming_rows`; the key keeps
        resolving while it has a baseline or another row in the default language
        (:meth:`MessageTemplateQuerySet.resolves`). Enforced on instance, queryset
        and cascade deletes by
        :func:`angee.messaging_templates.signals.refuse_unresolved_delete`.
        """

        del cascaded
        rows = type(self).system_queryset()
        if not self.handler.naming_rows(rows, self.key).exists():
            return None
        if rows.exclude(pk=self.pk).resolves(self.kind, self.key):
            return None
        return UNRESOLVED_DELETE_MESSAGE % {"kind": MessageTemplateKind(self.kind).label.lower()}

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the row, deriving ``content_hash`` and stamping a new override's ``baseline_hash``.

        A save that writes a column of :data:`CONTENT_HASH_FIELDS` writes the hash
        with it, ``update_fields`` included, since a resource reload saves only the
        columns its row states. A non-baseline row inserted where its baseline
        exists takes the baseline's ``content_hash`` as its ``baseline_hash``.
        """

        update_fields = kwargs.get("update_fields")
        if update_fields is None or not set(CONTENT_HASH_FIELDS).isdisjoint(update_fields):
            self.content_hash = "sha256:" + canonical_json_sha256(
                {name: str(self.format) if name == "format" else getattr(self, name) for name in CONTENT_HASH_FIELDS}
            )
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "content_hash"}
        if self._state.adding and not self.is_baseline:
            shipped = (
                type(self).system_queryset()
                .filter(is_baseline=True, kind=self.kind, key=self.key, language=self.language)
                .values_list("content_hash", flat=True)
                .first()
            )
            if shipped is not None:
                self.baseline_hash = shipped
        super().save(*args, **kwargs)
