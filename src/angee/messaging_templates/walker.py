"""The walker: the one owner of every refusal a message template meets, at save and at render, and the renderer.

:meth:`TemplateWalker.walk` reads a row and every row it reaches (its layout and
the blocks and parts it includes, at any depth) as Django compiles them, and
refuses what may not render: oversized or pathological sources before anything
parses them, compile errors, literals that could carry markup, costly or unsafe
filter use, template spans the Markdown pass does not keep whole, HTML a mail may
not carry (:mod:`~angee.messaging_templates.html_checks`), reads of values no row
declares, lookups that could reach a method of a ``dict``, ``list`` or ``str``,
include cycles, and every bound in the walk's caps. It runs from ``clean()`` and after a resource load (at
save) and from ``render()`` and ``preview()`` before anything renders.

A walk that passes is a :class:`TemplateWalk`: the values the render reads (``D``,
the union of a template's and its layout's ``variables``), which of them are
required, which are dates, and the forms the renderer serves.
:meth:`TemplateWalk.prepare` turns a render's values into exactly that shape, and
:meth:`TemplateWalk.render` renders the walked rows on two
:class:`~angee.messaging_templates.engine.MessageTemplateEngine` engines, one
escaping values for the HTML body and one writing them as they are for the
subject, preheader and plain-text body, each pass in its own
:class:`~angee.messaging_templates.engine.RenderBudget` under the row's language.

The walker reads compiled nodes by their attributes, which Django does not
document; ``docs/stack.md`` (the Django row) lists each one. :data:`WALKED_NODES`
is its one table of node classes, what each evaluates and the node lists it
renders; both of its passes look a node up there and raise ``TypeError`` on any
other class, so a Django upgrade that changes the nodes fails loudly.
"""

from __future__ import annotations

import json
import logging
import math
import quopri
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Protocol, cast

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.template import TemplateSyntaxError
from django.template.base import (
    FilterExpression,
    Lexer,
    Node,
    NodeList,
    Template,
    TextNode,
    TokenType,
    VariableNode,
    tag_re,
)
from django.template.defaultfilters import date as date_filter
from django.template.defaulttags import CommentNode, ForNode, IfNode, TemplateLiteral, TemplateTagNode, WithNode
from django.template.loader_tags import BlockNode, ExtendsNode
from django.utils import translation
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.functional import Promise
from django.utils.html import escape
from django.utils.timezone import get_current_timezone, template_localtime  # type: ignore[attr-defined]  # unstubbed

from angee.brandkit.models import BrandKit
from angee.messaging_templates.engine import (
    BRAND_PATHS,
    MARKUP_FILTERS,
    BudgetedIncludeNode,
    BudgetedLoopBody,
    MessageTemplateEngine,
    RenderBudget,
    brand_values,
    filter_names,
    url,
)
from angee.messaging_templates.html_checks import (
    CONTROL_CHARACTERS,
    SOURCE_CONTROLS,
    SpellingRule,
    check_html,
    compiled_expression,
)
from angee.messaging_templates.rendering import (
    RESERVED_VARIABLE_ROOTS,
    MessageTemplateFormat,
    RowForms,
    RowRefusal,
    validate_variables,
)

SOURCE_LIMIT = 50_000
LINE_LIMIT = 1_000
OPENERS_PER_LINE = 30
FORM_OPENERS_PER_LINE = 60
WALK_SOURCE_LIMIT = 150_000
WALK_READ_LIMIT = 600_000
WALK_SPAN_READ_LIMIT = 4_000
"""What the checks of the rows a walk reaches read at most, summed as it gathers them: characters of their forms and
template spans, a comment's content included. A comment renders nothing, but the Markdown pass and the HTML checks
read all of it, and a Markdown pass multiplies its source; so the walk stops once its rows' forms pass either."""
NESTING_LIMIT = 16
CONDITION_OPERATOR_LIMIT = 10
"""The most operators (``and``, ``or``, ``not``, ``in``, comparisons) one ``if`` or ``elif`` condition holds. Django
parses and evaluates a condition recursively, an operator a level, and the walk counts each operator as a node."""
LITERAL_LIMIT = 200
FILTERS_PER_EXPRESSION = 4
FLOATFORMAT_PRECISION = 6
DATE_FORMAT_LIMIT = 20
DATE_FORMAT_NAMES = frozenset({
    "DATE_FORMAT", "DATETIME_FORMAT", "SHORT_DATE_FORMAT", "SHORT_DATETIME_FORMAT", "TIME_FORMAT",
    "MONTH_DAY_FORMAT", "YEAR_MONTH_FORMAT",
})
NODE_LIMIT = 1_000
OUTPUT_LIMIT = 300
LOOP_DEPTH_LIMIT = 2
INCLUDE_DEPTH_LIMIT = 3
TEXT_LIMIT = 200_000
FORM_SPAN_LIMIT = 2 * 1_000
"""The most template spans one form of a row holds, checked straight after the Markdown pass and before the HTML
checks read it: a walk admits at most :data:`NODE_LIMIT` nodes, and an opening tag is a node whose end tag is a
second span."""
DECLARED_LIMIT = 100
VALUES_JSON_LIMIT = 50_000
VALUE_STRING_LIMIT = 10_000
VALUE_ITEMS_LIMIT = 500
SUBJECT_LIMIT = 1_000
"""The longest rendered subject or preheader, after it is made one line."""

UNPRINTED = re.compile(f"[{CONTROL_CHARACTERS}\u202a-\u202e\u2066-\u2069]")
"""The one rule for what a value and a line drop: the C0 and C1 control characters but tab, line feed and carriage
return, and the bidirectional embeddings, overrides and isolates, so a value neither switches a reader's character
set nor reorders the author's text after it. The directional marks a language needs (LRM, RLM, ALM) stay.
:meth:`TemplateWalk.prepare` drops them from every string value; a subject or preheader from each of its words,
splitting the line on white space having dropped tab, line feed and return."""

SUBJECT_WARNING_LENGTH = 60
PREHEADER_WARNING_LENGTH = 100
SIZE_WARNING_BYTES = 104_448
"""Gmail clips a message's HTML past about 102 KB."""

FLOAT_FILTERS = frozenset({"floatformat", "pluralize"})
"""The filters that read their input as a float: a number no float holds raises in them, and nowhere else."""

FORLOOP_KEYS = frozenset({"counter", "counter0", "revcounter", "revcounter0", "first", "last", "length"})
FORLOOP_PATHS = frozenset({("forloop",), *(("forloop", key) for key in FORLOOP_KEYS)})
CONSTANTS = frozenset({"True", "False", "None"})
UNBINDABLE = RESERVED_VARIABLE_ROOTS | CONSTANTS
OPENERS = ("{{", "{%", "{#")
NESTED_TAGS = frozenset({"if", "for", "with", "block"})
NESTED_ENDS = frozenset({"endif", "endfor", "endwith", "endblock"})
CONTENT_BLOCK = "content"

type Path = tuple[str, ...]

logger = logging.getLogger(__name__)


@contextmanager
def refusals_only() -> Iterator[None]:
    """The one owner of "only ``ValidationError`` leaves" a walk, ``prepare()``, a render's passes or its warnings.

    Every public entry of a walk runs inside it, so ``MessageTemplate.render``,
    ``MessageTemplate.preview`` and a sender rendering a walk itself are covered
    whole. A ``ValidationError`` passes as it is. ``MemoryError`` and
    ``RecursionError`` pass unconverted: they say the process ran out of room, not
    that the row cannot render, and no admitted row and values reach either. Any
    other exception is logged with its traceback, then becomes
    ``ValidationError(code="render_failed")``.
    """

    try:
        yield
    except (ValidationError, MemoryError, RecursionError):
        raise
    except Exception as error:
        logger.exception("A message template failed to render.")
        raise ValidationError(
            "The message template did not render: %(error)s", code="render_failed",
            params={"error": f"{type(error).__name__}: {error}"},
        ) from error


def _refused(messages: Iterable[str], code: str) -> ValidationError:
    """One ``ValidationError`` whose every item carries ``code``: Django keeps no code on an error built from a list."""

    return ValidationError([ValidationError(message, code=code) for message in dict.fromkeys(messages)])


class RowPiece(StrEnum):
    """A piece of a row a pass renders: its two forms, and a template's subject and preheader."""

    HTML = "html"
    TEXT = "text"
    SUBJECT = "subject"
    PREHEADER = "preheader"


class WalkedKind(Protocol):
    """What the walker asks a row's kind handler."""

    @property
    def carries_message_fields(self) -> bool: ...

    @property
    def includable(self) -> bool: ...

    @property
    def reads_message_fields(self) -> bool: ...

    @property
    def holds_content(self) -> bool: ...

    @property
    def writes_document(self) -> bool: ...

    def template_name(self, key: str) -> str: ...

    def layout_name(self, row: Any) -> str | None: ...

    def wrap(self, row: Any, form: str) -> str: ...


class WalkedRow(Protocol):
    """What the walker and the renderer read of a row: its columns, its kind's handler and its fingerprint."""

    key: str
    language: str
    format: Any
    subject: str
    preheader: str
    source: str
    text_source: str
    variables: Any

    @property
    def handler(self) -> WalkedKind: ...

    def compute_content_hash(self) -> str: ...


class RowReader(Protocol):
    """Where a walk reads the rows it names: the model's scoped manager, or a queryset of it."""

    def named_rows(self, names: Iterable[str], *, language: str) -> Mapping[str, Any]: ...

    def resolving_names(self, names: Iterable[str]) -> frozenset[str]: ...


def _dotted(path: Sequence[str]) -> str:
    return ".".join(path)


def _prefixes(paths: Iterable[Path]) -> frozenset[Path]:
    """Every declared path and every path a declared one continues."""

    return frozenset(path[:end] for path in paths for end in range(1, len(path) + 1))


BRAND_PREFIXES = _prefixes(BRAND_PATHS)


# ---------- what a render gives ----------


@dataclass(frozen=True)
class PreviewFrame:
    """A fixed subject, preheader and body a row with no body of its own is previewed with."""

    subject: str
    preheader: str
    html: str
    text: str


class TextDirection(StrEnum):
    """The direction a mail's text reads in, as an HTML ``dir`` takes it."""

    RTL = "rtl"
    LTR = "ltr"


@dataclass
class RenderedMessage:
    """One rendered message: subject and preheader on one line each, the HTML and plain-text bodies."""

    subject: str
    preheader: str
    html: str
    plain_text: str
    language: str
    direction: TextDirection
    """The direction of the mail's own text, the one the HTML's ``dir`` says, for whoever shows the subject, the
    preheader or the plain text outside the HTML. Django's bidi answer for the row's language, left to right for a
    language Django does not know."""


class TemplateWarningCode(StrEnum):
    """What a preview warns about."""

    SUBJECT_LENGTH = "subject_length"
    PREHEADER = "preheader"
    UNUSED_VARIABLE = "unused_variable"
    STOP_LINK = "stop_link"
    PLAIN_TEXT = "plain_text"
    SIZE = "size"
    RENDER_ERROR = "render_error"


@dataclass
class TemplateWarning:
    """One thing a preview warns about, and what it found."""

    code: TemplateWarningCode
    detail: str
    field: str | None = None
    """The column a refusal names, as the row's edited columns are named; ``None`` for a warning, or a refusal of
    the row as a whole."""


@dataclass
class TemplatePreview:
    """A row rendered with its sample, or ``None`` when the walk or the render raised, and its warnings."""

    rendered: RenderedMessage | None
    warnings: list[TemplateWarning] = field(default_factory=list)


def in_layout(layout_name: str, form: str) -> str:
    """Return ``form`` as the content of the layout named ``layout_name``, as the loader serves a template."""

    return '{% extends "' + layout_name + '" %}{% block content %}' + form + "{% endblock %}"


# ---------- the node classes the walker reads ----------


@dataclass(frozen=True)
class WalkedNode:
    """What the walker reads on one node class: the expressions it evaluates and the node lists it renders."""

    expressions: Callable[[Any], tuple[FilterExpression, ...]]
    nodelists: Callable[[Any], tuple[NodeList, ...]]


def _nothing(node: Any) -> tuple[()]:
    del node
    return ()


WALKED_NODES: Mapping[type[Node], WalkedNode] = {
    TextNode: WalkedNode(_nothing, _nothing),
    VariableNode: WalkedNode(lambda node: (node.filter_expression,), _nothing),
    # An if's expressions sit in its conditions, which both passes read operand by operand.
    IfNode: WalkedNode(_nothing, lambda node: tuple(nodelist for _, nodelist in node.conditions_nodelists)),
    ForNode: WalkedNode(lambda node: (node.sequence,), lambda node: (node.nodelist_loop, node.nodelist_empty)),
    BudgetedLoopBody: WalkedNode(_nothing, lambda node: (node.nodelist,)),
    WithNode: WalkedNode(lambda node: tuple(node.extra_context.values()), lambda node: (node.nodelist,)),
    CommentNode: WalkedNode(_nothing, _nothing),
    BudgetedIncludeNode: WalkedNode(lambda node: tuple(node.extra_context.values()), _nothing),
    ExtendsNode: WalkedNode(_nothing, lambda node: (node.nodelist,)),
    BlockNode: WalkedNode(_nothing, lambda node: (node.nodelist,)),
    TemplateTagNode: WalkedNode(_nothing, _nothing),
}
"""Every node class the allowlisted library compiles to, what it evaluates and the node lists it renders."""


def walked(node: Node) -> WalkedNode:
    """Return what the walker reads on ``node``; another class is a defect to fix here, never a refusal."""

    reads = WALKED_NODES.get(type(node))
    if reads is None:
        raise TypeError(f"The message template walker does not know {type(node).__qualname__}.")
    return reads


# ---------- a walked row ----------


@dataclass(frozen=True)
class _RowCheck:
    """One row's own checks (rules 1 to 12): its forms, its compiled pieces, what it includes."""

    name: str
    forms: RowForms | None = None
    pieces: Mapping[RowPiece, Template] = field(default_factory=dict)
    includes: frozenset[str] = frozenset()
    refusals: tuple[str, ...] = ()
    sizes: Mapping[RowPiece, tuple[int, int, int]] = field(default_factory=dict)
    """What each body form's own nodes add to any tree reaching the row, at least: nodes, outputs, text
    (:func:`_own_size`)."""
    read: tuple[int, int] = (0, 0)
    """What the row's checks read: its forms' characters and template spans, comments included; nothing for a row
    its own form caps refuse, whose refusal ends the walk."""


def _own_size(nodelist: NodeList) -> tuple[int, int, int]:
    """The nodes (loop bodies aside), outputs and text a compiled piece adds to every tree that reaches it.

    A tree walks every node of every row it reaches at least once, both branches of an ``if`` and a loop body once,
    and counts conditions, ``with`` values and include arguments besides; so this is a floor of the tree's count,
    and the rows it includes add theirs.
    """

    nodes = nodelist.get_nodes_by_type(Node)
    return (
        sum(type(node) is not BudgetedLoopBody for node in nodes),
        sum(type(node) is VariableNode for node in nodes),
        sum(len(cast(TextNode, node).s) for node in nodes if type(node) is TextNode),
    )


def _over_caps(root_name: str, form: RowPiece, nodes: int, outputs: int, text: int) -> list[str]:
    """The tree caps a form's count passes: :data:`NODE_LIMIT`, :data:`OUTPUT_LIMIT`, :data:`TEXT_LIMIT`."""

    refusals = []
    if nodes > NODE_LIMIT:
        refusals.append(f"{root_name}: its {form} reaches over {NODE_LIMIT} nodes")
    if outputs > OUTPUT_LIMIT:
        refusals.append(f"{root_name}: its {form} reaches over {OUTPUT_LIMIT} outputs")
    if text > TEXT_LIMIT:
        refusals.append(f"{root_name}: its {form} writes over {TEXT_LIMIT} characters of text")
    return refusals


@dataclass
class TemplateWalk:
    """A row as a render reaches it: the rows, their forms, the values ``D`` and what the render requires.

    ``rows`` and ``forms`` are keyed by template name; ``declared`` is ``D``;
    ``required`` the paths a render's values must hold, not null, in every list
    item present; ``date_paths`` the paths read through ``date``, each with the
    formats it is printed in; ``float_paths`` the paths a filter reads as a float
    (:data:`FLOAT_FILTERS`); ``reads`` every path of ``D`` some row reads.
    """

    root: WalkedRow
    root_name: str
    language: str
    rows: dict[str, WalkedRow] = field(default_factory=dict)
    forms: dict[str, RowForms] = field(default_factory=dict)
    declared: dict[str, str] = field(default_factory=dict)
    required: set[Path] = field(default_factory=set)
    date_paths: dict[Path, set[str]] = field(default_factory=dict)
    float_paths: set[Path] = field(default_factory=set)
    reads: set[Path] = field(default_factory=set)
    includes: set[str] = field(default_factory=set)
    refusals: list[str] = field(default_factory=list)

    @property
    def unused(self) -> list[str]:
        """The paths of the root row's own ``variables`` no read reaches."""

        unused = []
        for path in self.root.variables if isinstance(self.root.variables, dict) else ():
            segments = tuple(path.split("."))
            if not any(segments[:end] in self.reads for end in range(1, len(segments) + 1)):
                unused.append(path)
        return unused

    @refusals_only()
    def prepare(self, values: Any) -> dict[str, Any]:
        """Return ``values`` as the render reads them, or raise ``ValidationError`` naming each path.

        1. ``values`` as JSON (``DjangoJSONEncoder``: dates and decimals become
           strings, a ``SafeString`` a plain string, any other object is refused),
           refused over 50,000 characters of JSON (characters, not escapes), with
           a string over 10,000, with over 500 list items in all, with a
           reserved root, or holding a character no mail can encode (a lone
           surrogate).
        2. Keys outside ``D`` dropped at every level and in every list item; a
           present inner segment is an object, a ``*`` parent a list, a leaf a
           string, number, boolean or null, a string without its control
           characters and bidirectional overrides and isolates
           (:data:`UNPRINTED`), so one holding nothing else is empty;
           at a :attr:`float_paths` path, a number one a float holds.
        3. Each :attr:`required` path present and not null, in every present item.
        4. Every absent or null declared segment filled: an object with ``{}``, a
           list with ``[]``, a leaf with ``""``; so every lookup lands on a key.
        5. At each :attr:`date_paths` path a non-empty string becomes a ``date``,
           or else an aware ``datetime``; refused, a moment the local time zone
           cannot hold, and one ``date`` cannot print in a format the row prints
           it in: a bare date printed with a time takes an aware ``datetime``.
           Named formats resolve in the active language, the row's in a render.

        Nothing here costs more than a constant per declared path per value: what
        a format prints is decided once per format, never per value
        (:class:`_DatePrinter`).
        """

        if not isinstance(values, Mapping):
            raise ValidationError("Pass the values as an object of names to values.", code="invalid_values")
        try:
            text = json.dumps(values, cls=DjangoJSONEncoder, ensure_ascii=False)
            data = json.loads(text)
        except (TypeError, ValueError, RecursionError) as error:
            raise ValidationError(f"The values are not JSON: {error}", code="invalid_values") from error
        if len(text) > VALUES_JSON_LIMIT:
            raise ValidationError(
                f"The values are {len(text)} characters of JSON, over {VALUES_JSON_LIMIT}.", code="values_too_large",
            )
        try:
            text.encode()
        except UnicodeEncodeError as error:
            raise ValidationError(
                "The values hold a character no mail can encode, a lone surrogate.", code="invalid_values",
            ) from error
        errors = _value_errors(data)
        if errors:
            raise _refused(errors, "invalid_values")
        tree = _tree(self.declared)
        shaped = {key: _shaped(data[key], subtree, (key,), errors) for key, subtree in tree.items() if key in data}
        errors.extend(
            f"{_dotted(path)} is required and missing or null." for path in sorted(self.required)
            if _lacks(shaped, path)
        )
        if errors:
            raise _refused(errors, "invalid_values")
        filled = {key: _filled(shaped.get(key), subtree) for key, subtree in tree.items()}
        for path in sorted(self.float_paths):
            errors.extend(f"{_dotted(path)} is not a number a float holds." for number in _at(filled, path)
                          if isinstance(number, (int, float)) and not isinstance(number, bool) and not _finite(number))
        if errors:
            raise _refused(errors, "invalid_values")
        printer = _DatePrinter(self.date_paths)
        for path in sorted(self.date_paths):
            _parse_dates(filled, path, path, printer, errors)
        if errors:
            raise _refused(errors, "invalid_values")
        return filled

    @refusals_only()
    def render(self, values: Mapping[str, Any], *, kit: BrandKit, frame: PreviewFrame | None = None) -> RenderedMessage:
        """Render the walked row with ``values``, the mail colours from ``kit``.

        Passes, each in ``translation.override`` of the row's language and its own
        :class:`~angee.messaging_templates.engine.RenderBudget`: (1) :meth:`prepare`
        and the globals ``brand``, ``lang`` and ``dir``; (2) the subject and
        preheader on the text engine, each made one line, on a tenth of a body
        pass's budget (:meth:`~angee.messaging_templates.engine.RenderBudget.for_lines`);
        (3) the HTML body on the
        HTML engine, the subject and preheader at its top level; (4) the plain-text
        body on the text engine, from the rows' text forms. ``frame`` renders a
        layout around a fixed body, subject and preheader. Raises ``ValidationError``
        alone (:func:`refusals_only`).
        """

        language = self.language
        with translation.override(language):
            prepared = self.prepare(values)
            direction = TextDirection.RTL if translation.get_language_bidi() else TextDirection.LTR
            mail_globals = {"brand": brand_values(kit), "lang": language, "dir": direction.value}
            text_engine = self._engine(RowPiece.TEXT, mail_globals)
            html_engine = self._engine(RowPiece.HTML, mail_globals)
            with RenderBudget.for_lines().spend():
                subject = _one_line(
                    text_engine.from_string(frame.subject if frame else str(self.root.subject)).render(
                        text_engine.context(prepared)), "subject",
                )
                preheader = _one_line(
                    text_engine.from_string(frame.preheader if frame else str(self.root.preheader)).render(
                        text_engine.context(prepared)), "preheader",
                )
            html_root = (
                html_engine.from_string(in_layout(self.root_name, frame.html)) if frame
                else html_engine.get_template(self.root_name)
            )
            with RenderBudget().spend():
                html = html_root.render(
                    html_engine.context(prepared, extra={"subject": subject, "preheader": preheader}),
                )
            text_root = (
                text_engine.from_string(in_layout(self.root_name, frame.text)) if frame
                else text_engine.get_template(self.root_name)
            )
            with RenderBudget().spend():
                text = text_root.render(text_engine.context(prepared))
        return RenderedMessage(
            subject=subject, preheader=preheader, html=html, plain_text=_plain(text), language=language,
            direction=direction,
        )

    def _engine(self, form: RowPiece, mail_globals: Mapping[str, Any]) -> MessageTemplateEngine:
        """An engine serving ``form`` of every walked row, through a per-render ``cached.Loader``."""

        forms = {name: getattr(row_forms, form) for name, row_forms in self.forms.items()}
        loader = ("angee.messaging_templates.engine.MessageTemplateLoader", forms)
        return MessageTemplateEngine(
            autoescape=form == RowPiece.HTML,
            loaders=[("django.template.loaders.cached.Loader", [loader])],
            mail_globals=mail_globals,
        )

    @refusals_only()
    def preview_warnings(self, rendered: RenderedMessage, sample: Mapping[str, Any]) -> list[TemplateWarning]:
        """Return what a preview of ``rendered``, the row's ``sample`` rendered, warns about.

        The subject and preheader are judged on a row that carries them; every row is
        judged on its own unused ``variables``, the stop link it declares, its plain
        text and its size, raw or quoted-printable.
        """

        warnings: list[TemplateWarning] = []
        carries_message_fields = self.root.handler.carries_message_fields
        if carries_message_fields and len(rendered.subject) > SUBJECT_WARNING_LENGTH:
            warnings.append(TemplateWarning(
                TemplateWarningCode.SUBJECT_LENGTH, f"The subject is {len(rendered.subject)} characters long.",
            ))
        if carries_message_fields and not rendered.preheader:
            warnings.append(TemplateWarning(
                TemplateWarningCode.PREHEADER,
                "The preheader is missing: an inbox shows the start of the body after the subject instead.",
            ))
        elif carries_message_fields and len(rendered.preheader) > PREHEADER_WARNING_LENGTH:
            warnings.append(TemplateWarning(
                TemplateWarningCode.PREHEADER, f"The preheader is {len(rendered.preheader)} characters long.",
            ))
        warnings.extend(
            TemplateWarning(TemplateWarningCode.UNUSED_VARIABLE, f"Nothing reads {path}.") for path in self.unused
        )
        if isinstance(self.root.variables, dict) and "unsubscribe_url" in self.root.variables:
            stop = sample.get("unsubscribe_url")
            links = {stop, url(stop)} - {""} if isinstance(stop, str) else set()
            if not any(escape(link) in rendered.html for link in links) or not any(
                link in rendered.plain_text for link in links
            ):
                warnings.append(TemplateWarning(
                    TemplateWarningCode.STOP_LINK, "The HTML or plain-text body lacks the sample's unsubscribe_url.",
                ))
        if not rendered.plain_text.strip():
            warnings.append(TemplateWarning(TemplateWarningCode.PLAIN_TEXT, "The plain-text body is empty."))
        raw = rendered.html.encode()
        size = max(len(raw), len(quopri.encodestring(raw)))
        if size > SIZE_WARNING_BYTES:
            warnings.append(TemplateWarning(TemplateWarningCode.SIZE, f"The HTML is {size} bytes."))
        return warnings


ENCODED_WORD_OPENER = re.compile(r"=\?(?=[^?]*\?[bq]\?)", re.IGNORECASE)
"""Where a mail library reads an RFC 2047 encoded word as starting: ``=?``, a charset (empty, unknown or with its
``*lang`` too), ``?``, ``b`` or ``q`` in either case, and ``?``, terminated or not, at a word's start or inside one.
CPython's header machinery decodes one there when it takes a subject, so an ASCII spelling would bring back a line
break, a control character or an override the line rule drops."""


def _one_line(text: str, name: str) -> str:
    """``text`` as one line of a subject or a preheader: its words joined by one space, each without
    :data:`UNPRINTED`, and every :data:`ENCODED_WORD_OPENER` broken to ``= ?``, so a mail library stores exactly the
    string it is given; ``2+2=?`` stays as it is."""

    line = " ".join(word for word in (UNPRINTED.sub("", word) for word in text.split()) if word)
    line = ENCODED_WORD_OPENER.sub("= ?", line)
    if len(line) > SUBJECT_LIMIT:
        raise ValidationError(
            "The rendered %(name)s is over %(limit)s characters.",
            code="subject_too_long", params={"name": name, "limit": SUBJECT_LIMIT},
        )
    return line


def _plain(text: str) -> str:
    lines = [line.rstrip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def _value_errors(data: Any) -> list[str]:
    errors: list[str] = []
    items = 0
    pending: list[tuple[str, Any]] = [("", data)]
    while pending:
        path, value = pending.pop()
        if isinstance(value, str) and len(value) > VALUE_STRING_LIMIT:
            errors.append(f"{path or 'A value'} is over {VALUE_STRING_LIMIT} characters.")
        elif isinstance(value, list):
            items += len(value)
            pending.extend((f"{path}.*", item) for item in value)
        elif isinstance(value, dict):
            pending.extend((f"{path}.{key}" if path else str(key), item) for key, item in value.items())
    if items > VALUE_ITEMS_LIMIT:
        errors.append(f"The values hold {items} list items, over {VALUE_ITEMS_LIMIT}.")
    reserved = sorted(RESERVED_VARIABLE_ROOTS & set(data)) if isinstance(data, dict) else []
    errors.extend(f"{name} is a name the mail supplies, not a value." for name in reserved)
    return errors


def _tree(declared: Iterable[str]) -> dict[str, Any]:
    """``D`` as nested objects, segment to subtree; a leaf is ``{}``."""

    tree: dict[str, Any] = {}
    for path in declared:
        node = tree
        for segment in path.split("."):
            node = node.setdefault(segment, {})
    return tree


def _shaped(value: Any, tree: dict[str, Any], here: Path, errors: list[str]) -> Any:
    """Drop what ``D`` does not declare and refuse a value whose shape it contradicts."""

    if value is None:
        return None
    if not tree:
        if isinstance(value, str):
            return UNPRINTED.sub("", value)
        if isinstance(value, (int, float, bool)):
            return value
        errors.append(f"{_dotted(here)} is a string, a number, true, false or null.")
        return None
    if "*" in tree:
        if not isinstance(value, list):
            errors.append(f"{_dotted(here)} is a list.")
            return None
        return [_shaped(item, tree["*"], (*here, "*"), errors) for item in value]
    if not isinstance(value, dict):
        errors.append(f"{_dotted(here) or 'The values'} is an object.")
        return None
    return {key: _shaped(value[key], subtree, (*here, key), errors) for key, subtree in tree.items() if key in value}


def _finite(number: float) -> bool:
    """Whether ``number`` is a finite float, or an integer a float holds, as filters such as ``pluralize`` read it."""

    try:
        return math.isfinite(float(number))
    except OverflowError:
        return False


def _at(value: Any, path: Path) -> Iterator[Any]:
    """Every value ``path`` reaches in ``value``, a ``*`` reaching each item of a list."""

    if not path:
        yield value
        return
    head, rest = path[0], path[1:]
    if head == "*" and isinstance(value, list):
        for item in value:
            yield from _at(item, rest)
    elif isinstance(value, dict) and head in value:
        yield from _at(value[head], rest)


def _lacks(value: Any, path: Path) -> bool:
    """Whether ``path`` is missing or null in ``value``; a ``*`` binds only the items present."""

    head, rest = path[0], path[1:]
    if head == "*":
        if not isinstance(value, list):
            return False
        return any(item is None or (bool(rest) and _lacks(item, rest)) for item in value)
    child = value.get(head) if isinstance(value, dict) else None
    if child is None:
        return not (rest and rest[0] == "*")
    return bool(rest) and _lacks(child, rest)


def _filled(value: Any, tree: dict[str, Any]) -> Any:
    """Fill every absent or null declared segment: an object with ``{}``, a list with ``[]``, a leaf with ``""``."""

    if not tree:
        return "" if value is None else value
    if "*" in tree:
        return [_filled(item, tree["*"]) for item in value or ()]
    present = value if isinstance(value, dict) else {}
    return {key: _filled(present.get(key), subtree) for key, subtree in tree.items()}


def _parse_dates(value: Any, path: Path, full: Path, printer: _DatePrinter, errors: list[str]) -> None:
    """Turn each non-empty string at ``path`` into a ``date``, else an aware ``datetime``, in place.

    Refuse, naming the path, a string that is neither, and a moment the render
    could not print in a format the row prints it in (:class:`_DatePrinter`).
    """

    head, rest = path[0], path[1:]
    children = list(enumerate(value)) if head == "*" and isinstance(value, list) else (
        [(head, value[head])] if isinstance(value, dict) and head in value else []
    )
    for key, child in children:
        if rest:
            _parse_dates(child, rest, full, printer, errors)
            continue
        if child == "":
            continue
        parsed: date | datetime | None = None
        if isinstance(child, str):
            # parse_date first: parse_datetime reads a bare date as a naive midnight.
            try:
                parsed = parse_date(child)
                if parsed is None and (moment := parse_datetime(child)) is not None and moment.utcoffset() is not None:
                    parsed = moment
            except ValueError:
                parsed = None
        if parsed is None:
            errors.append(f"{_dotted(full)} is not a date, or a date and time with its offset: {str(child)[:40]!r}.")
            continue
        if (refusal := printer.refusal(full, parsed)) is not None:
            errors.append(f"{_dotted(full)} {refusal}")
            continue
        value[key] = parsed


class _DatePrinter:
    """Whether the render can print a moment at a date path, at a constant cost per value.

    What a format can print depends on the format, the language, the kind of
    moment (a ``date`` or an aware ``datetime``) and the end of the calendar it
    sits at (year 1, year 9999, or between), never on the moment itself: so it
    is asked of Django's own ``date`` filter once per format, kind and end, on a
    probe, and the verdict for a path's formats is remembered. Per value there
    remains the conversion to local time (``template_localtime``, which the
    engine applies before ``date`` reads a datetime) and a lookup.
    """

    def __init__(self, date_paths: Mapping[Path, set[str]]) -> None:
        self.formats = {path: sorted(formats) for path, formats in date_paths.items()}
        self.zone = get_current_timezone()
        self.verdicts: dict[tuple[Path, str, int], str | None] = {}
        self.probed: dict[tuple[str, str, int], str | None] = {}

    def refusal(self, path: Path, moment: date | datetime) -> str | None:
        """Why ``moment`` cannot be printed at ``path``, or ``None``."""

        if isinstance(moment, datetime):
            try:
                moment = template_localtime(moment)
            except (OverflowError, ValueError):
                return "is a moment the local time zone cannot hold."
        kind = "datetime" if isinstance(moment, datetime) else "date"
        end = moment.year if moment.year in (1, 9999) else 0
        key = (path, kind, end)
        if key not in self.verdicts:
            self.verdicts[key] = next(
                (refusal for name in self.formats[path] if (refusal := self.probe(name, kind, end)) is not None), None,
            )
        return self.verdicts[key]

    def probe(self, name: str, kind: str, end: int) -> str | None:
        """Ask Django's ``date`` filter to print a probe of ``kind`` at ``end`` in the format ``name``."""

        key = (name, kind, end)
        if key not in self.probed:
            year, month, day = {0: (2000, 1, 1), 1: (1, 1, 1), 9999: (9999, 12, 31)}[end]
            probe: date | datetime = (
                datetime(year, month, day, 12, tzinfo=self.zone) if kind == "datetime" else date(year, month, day)
            )
            try:
                date_filter(probe, name)
                self.probed[key] = None
            except TypeError:
                self.probed[key] = f"is printed with a time ({name}): pass a date and time with its offset."
            except (OverflowError, ValueError):
                self.probed[key] = f"cannot be printed as {name}" + (f" in year {end}." if end else ".")
        return self.probed[key]


# ---------- one row's own checks ----------


def _literal_refusal(value: Any) -> str | None:
    """Why a literal may not stand in a message template, or ``None``."""

    if isinstance(value, Promise):
        return "a _(…) literal: rows are per language, never translated"
    text = str(value)
    if any(character in text for character in '<>"'):
        return f"the literal {text[:40]!r} holds <, > or \""
    if len(text) > LITERAL_LIMIT:
        return f"a literal over {LITERAL_LIMIT} characters"
    return None


WHOLE_NUMBER_FILTERS = frozenset({"floatformat", "truncatechars", "truncatewords"})
"""The filters whose argument is a whole number, which Django reads with ``int()``."""

TEXT_ARGUMENT_FILTERS = frozenset({"yesno", "pluralize"})
"""The filters whose argument Django splits as text, so a number there raises."""


def _whole_number(name: str, argument: Any) -> int | None:
    """The whole number filter ``name`` reads from ``argument``, as Django parses it, or ``None`` when it cannot.

    ``floatformat`` first strips a ``g``, ``u``, ``gu`` or ``ug`` suffix; an
    empty argument, text or a number beyond what ``int()`` takes (``1e400`` is
    infinite) raises in Django's filter, so the walker refuses it.
    """

    if name == "floatformat" and isinstance(argument, str) and argument:
        if argument[-2:] in ("gu", "ug"):
            argument = argument[:-2] or -1
        elif argument[-1:] in ("g", "u"):
            argument = argument[:-1] or -1
    try:
        return int(argument)
    except (TypeError, ValueError, OverflowError):
        return None


def _operators(condition: Any) -> int:
    """How many operators a compiled ``if`` condition holds, counted without recursion."""

    count, pending = 0, [condition]
    while pending:
        operator = pending.pop()
        if type(operator) is not TemplateLiteral:
            count += 1
            pending.extend(operand for operand in (operator.first, operator.second) if operand is not None)
    return count


def _nesting(text: str) -> int:
    """The deepest tag nesting in ``text``, counted on Django's tokens before anything compiles."""

    depth = deepest = 0
    commented = False
    for token in Lexer(text).tokenize():
        if token.token_type != TokenType.BLOCK:
            continue
        command = (token.contents.split() or [""])[0]
        if commented:
            commented = command != "endcomment"
        elif command == "comment":
            commented = True
        elif command in NESTED_TAGS:
            depth += 1
            deepest = max(deepest, depth)
        elif command in NESTED_ENDS:
            depth = max(depth - 1, 0)
    return deepest


class _RowRules:
    """Rules 4 to 11 that one compiled piece of a row meets alone, and the rows it includes."""

    def __init__(self, refusals: list[str], prefix: str, *, text_form: bool) -> None:
        self.refusals = refusals
        self.prefix = prefix
        self.text_form = text_form
        self.includes: set[str] = set()

    def refuse(self, reason: str) -> None:
        self.refusals.append(f"{self.prefix}: {reason}")

    def nodes(self, nodelist: Iterable[Node]) -> None:
        for node in nodelist:
            self.node(node)

    def node(self, node: Node) -> None:
        reads = walked(node)
        if type(node) is TextNode:
            if any(opener in node.s for opener in OPENERS):
                self.refuse("text holds {{, {% or {# that is not template code")
        elif type(node) is IfNode:
            for condition, _ in node.conditions_nodelists:
                if condition is None:
                    continue
                if _operators(condition) > CONDITION_OPERATOR_LIMIT:
                    self.refuse(f"an if condition holds over {CONDITION_OPERATOR_LIMIT} operators")
                else:
                    self.condition(condition)
        elif type(node) is ForNode:
            if len(node.loopvars) != 1:
                self.refuse("for takes one loop variable")
            if cast(FilterExpression, node.sequence).filters:
                self.refuse("for loops over a value, not a filtered one")
        elif type(node) is BudgetedIncludeNode:
            self.includes.add(str(node.template.var))
        for expression in reads.expressions(node):
            self.expression(expression, printed=type(node) is VariableNode)
        for nodelist in reads.nodelists(node):
            self.nodes(nodelist)

    def literal(self, value: Any) -> None:
        if (reason := _literal_refusal(value)) is not None:
            self.refuse(reason)

    def expression(self, expression: FilterExpression, *, printed: bool) -> None:
        """Judge one expression's literals and filters; ``printed``: an output's, not a test's or an argument's."""

        variable = expression.var
        if expression.is_var:
            if variable.translate:
                self.refuse("a _(…) literal: rows are per language, never translated")
            if variable.lookups is None:
                self.literal(variable.var)
        else:
            self.literal(variable)
        names = filter_names(expression)
        if len(names) > FILTERS_PER_EXPRESSION:
            self.refuse(f"over {FILTERS_PER_EXPRESSION} filters in {expression.token}")
        for position, ((_, arguments), name) in enumerate(zip(expression.filters, names, strict=True)):
            for lookup, argument in arguments:
                if lookup and argument.lookups is not None:
                    self.refuse(f"{name} takes a literal argument, not {argument.var}")
                    continue
                value = argument.literal if lookup else argument
                self.literal(argument.var if lookup else argument)
                if name in WHOLE_NUMBER_FILTERS:
                    number = _whole_number(name, value)
                    if number is None:
                        self.refuse(f"{name} takes a whole number, not {value!r}")
                    elif name == "floatformat" and abs(number) > FLOATFORMAT_PRECISION:
                        self.refuse(f"floatformat precision {value} is beyond ±{FLOATFORMAT_PRECISION}")
                if name in TEXT_ARGUMENT_FILTERS and not isinstance(value, str):
                    self.refuse(f"{name} takes a quoted argument, not {value!r}")
                if name == "date" and str(value) not in DATE_FORMAT_NAMES and len(str(value)) > DATE_FORMAT_LIMIT:
                    self.refuse(f"a date format is a format name or at most {DATE_FORMAT_LIMIT} characters")
                if name == "linebreaksbr":
                    self.refuse("linebreaksbr takes no argument")
            if name == "date" and position:
                self.refuse(f"date is the first filter in {expression.token}")
            if name == "url" and (position != len(names) - 1 or set(names[:position]) - {"default"}):
                self.refuse(f"url is the last filter, after default alone, in {expression.token}")
            if name in MARKUP_FILTERS and self.text_form:
                self.refuse(f"{name} writes <br> into plain text")
            elif name in MARKUP_FILTERS and (not printed or position != len(names) - 1):
                self.refuse(f"{name} writes markup, so it is the last filter of a printed value, in {expression.token}")

    def condition(self, condition: Any) -> None:
        if type(condition) is TemplateLiteral:
            self.expression(cast(FilterExpression, condition.value), printed=False)
            return
        if condition.id in ("in", "not in"):
            right = condition.second
            value = cast(FilterExpression, right.value) if type(right) is TemplateLiteral else None
            literal = value is not None and (not value.is_var or value.var.lookups is None) and not value.filters
            if not literal:
                self.refuse(f"{condition.id} takes a literal on its right")
        for operand in (condition.first, condition.second):
            if operand is not None:
                self.condition(operand)


# ---------- the walk ----------


class BindingKind(StrEnum):
    """What a name in a row stands for."""

    VALUE = "value"
    """A declared value: the root's own, or an argument an include passed."""
    UNPASSED = "unpassed"
    """An included row's argument its include did not pass."""
    BRAND = "brand"
    GLOBAL = "global"
    """``lang`` or ``dir``."""
    CONSTANT = "constant"
    """``True``, ``False`` or ``None``."""
    MESSAGE = "message"
    """``subject`` or ``preheader``."""
    FORLOOP = "forloop"
    COMPUTED = "computed"
    """A filtered expression's result."""
    LITERAL = "literal"


class ReadUse(StrEnum):
    """How an expression's value is used where it is read."""

    OUTPUT = "output"
    ARGUMENT = "argument"
    """A ``with`` value or an include argument that runs a filter."""
    ALIAS = "alias"
    """A ``with`` value or an include argument that names a value as it is."""
    TEST = "test"
    SEQUENCE = "sequence"


_VALUES = frozenset({BindingKind.VALUE, BindingKind.UNPASSED})


@dataclass(frozen=True)
class _Binding:
    """What a name stands for in a row: a value (its paths here and in ``D``), or a name the mail supplies."""

    kind: BindingKind
    local: Path = ()
    effective: Path | None = None


@dataclass(frozen=True)
class _Frame:
    """One row as the walk stands in it: its scope, guards, loops and the includes above it."""

    name: str
    row: WalkedRow
    form: RowPiece
    declared: frozenset[Path]
    leaves: frozenset[Path]
    top: bool
    scope: Mapping[str, _Binding]
    guards: frozenset[tuple[str, Path]] = frozenset()
    loops: int = 0
    includes: tuple[str, ...] = ()


@dataclass
class _Tree:
    """One piece a pass renders, from the root through the layout and every include site."""

    form: RowPiece
    content: tuple[_Frame, NodeList] | None = None
    nodes: int = 0
    outputs: int = 0
    text: int = 0


class TemplateWalker:
    """Walk message templates: each row's own checks, then the walk from a root through what it reaches.

    ``saving`` asks, as a save does, that every included key resolve for every
    recipient. A row's own checks are remembered by its name and content hash for
    the walker's life; they read no other row, so :meth:`check` and
    :meth:`includes` need no reader.
    """

    def __init__(self, *, saving: bool = False) -> None:
        """Start a walker; ``saving`` judges includes as a save does."""

        self.saving = saving
        self.html_engine = MessageTemplateEngine(autoescape=True)
        self.text_engine = MessageTemplateEngine(autoescape=False)
        self.checked: dict[tuple[str, str], _RowCheck] = {}
        self.expression = compiled_expression(self.html_engine)

    # -- one row's own checks --------------------------------------------------

    def check(self, row: WalkedRow) -> _RowCheck:
        """Return ``row``'s own checks (rules 1 to 12), remembered by its name and content hash."""

        name = row.handler.template_name(row.key)
        key = (name, row.compute_content_hash())
        if key not in self.checked:
            self.checked[key] = self._check(row, name)
        return self.checked[key]

    def includes(self, row: WalkedRow) -> frozenset[str]:
        """The template names ``row``'s compiled forms include; none when they do not compile."""

        return self.check(row).includes

    def _check(self, row: WalkedRow, name: str) -> _RowCheck:
        refusals: list[str] = []

        def refuse(reason: str) -> None:
            refusals.append(f"{name}: {reason}")

        texts = {"source": row.source, "text_source": row.text_source, "subject": row.subject,
                 "preheader": row.preheader}
        if holding := [column for column, text in texts.items() if SOURCE_CONTROLS.search(text)]:
            # Before anything reads the text: a NUL would forge the HTML checks' placeholders.
            rule = SpellingRule.NO_CONTROL
            refuse(f"{', '.join(holding)}: {rule.message} (rule {rule.value})")
            return _RowCheck(name, refusals=tuple(refusals))
        try:
            validate_variables(row.variables)
        except ValidationError as error:
            refuse(" ".join(error.messages))
        try:
            markup = MessageTemplateFormat(row.format).handler
        except ValueError:
            refuse(f"{row.format!r} is not a markup")
            return _RowCheck(name, refusals=tuple(refusals))
        for column, text in texts.items():
            if len(text) > SOURCE_LIMIT:
                refuse(f"{column} is over {SOURCE_LIMIT} characters")
            elif (line := next((line for line in text.split("\n") if len(line) > LINE_LIMIT or sum(
                    line.count(opener) for opener in OPENERS) > OPENERS_PER_LINE), None)) is not None:
                refuse(f"a line of {column} is over {LINE_LIMIT} characters or holds over {OPENERS_PER_LINE} "
                       f"{{{{, {{% or {{# ({line[:40]!r})")
        refusals.extend(f"{name}: {reason}" for reason in markup.source_refusals(row.source))
        if refusals:
            return _RowCheck(name, refusals=tuple(refusals))
        try:
            forms = markup.forms(row)
        except RowRefusal as refusal:
            refuse(str(refusal))
            return _RowCheck(name, refusals=tuple(refusals))
        read = [0, 0]
        for form_name, form in forms._asdict().items():
            # Before anything else reads the form: a Markdown pass multiplies its source many times over.
            spans = tag_re.findall(form)
            read[0] += len(form)
            read[1] += len(spans)
            if len(spans) > FORM_SPAN_LIMIT:
                refuse(f"its {form_name} form holds over {FORM_SPAN_LIMIT} template spans")
            elif len(form) - sum(map(len, spans)) > TEXT_LIMIT:
                refuse(f"its {form_name} form writes over {TEXT_LIMIT} characters of text")
            elif any(sum(line.count(opener) for opener in OPENERS) > FORM_OPENERS_PER_LINE
                     for line in form.split("\n")):
                refuse(f"a line of its {form_name} form holds over {FORM_OPENERS_PER_LINE} {{{{, {{% or {{#")
        if refusals:
            return _RowCheck(name, refusals=tuple(refusals))
        handler = row.handler
        served = RowForms(html=handler.wrap(row, forms.html), text=handler.wrap(row, forms.text))
        pieces_to_compile: dict[RowPiece, tuple[str, str, MessageTemplateEngine]] = {
            RowPiece.HTML: (forms.html, served.html, self.html_engine),
            RowPiece.TEXT: (forms.text, served.text, self.text_engine),
        }
        if handler.carries_message_fields:
            pieces_to_compile[RowPiece.SUBJECT] = (row.subject, row.subject, self.text_engine)
            pieces_to_compile[RowPiece.PREHEADER] = (row.preheader, row.preheader, self.text_engine)
        pieces: dict[RowPiece, Template] = {}
        includes: set[str] = set()
        for piece, (authored, compiled, engine) in pieces_to_compile.items():
            if _nesting(authored) > NESTING_LIMIT:
                refuse(f"its {piece} nests tags over {NESTING_LIMIT} deep")
                continue
            try:
                template = engine.from_string(compiled)
            except TemplateSyntaxError as error:
                refuse(f"its {piece} does not compile: {error}")
                continue
            except RecursionError:
                refuse(f"its {piece} nests too deep to compile")
                continue
            rules = _RowRules(refusals, f"{name} ({piece})", text_form=engine is self.text_engine)
            rules.nodes(template.nodelist)
            self._structure(template.nodelist, piece, row, rules.refuse)
            includes |= rules.includes
            pieces[piece] = template
        if RowPiece.HTML in pieces:
            refusals.extend(f"{name}: {reason}" for reason in check_html(
                forms.html, self.expression, document=handler.writes_document,
            ))
        sizes = {piece: _own_size(pieces[piece].nodelist) for piece in (RowPiece.HTML, RowPiece.TEXT)
                 if piece in pieces}
        return _RowCheck(name, forms=served, pieces=pieces, includes=frozenset(includes), refusals=tuple(refusals),
                         sizes=sizes, read=(read[0], read[1]))

    @staticmethod
    def _structure(nodelist: NodeList, piece: RowPiece, row: WalkedRow, refuse: Callable[[str], None]) -> None:
        """Rule 8: ``extends`` and ``block`` only as the wrapper and each layout form's one empty content block."""

        handler = row.handler
        if piece in (RowPiece.SUBJECT, RowPiece.PREHEADER):
            if any(nodelist.get_nodes_by_type(kind) for kind in (BudgetedIncludeNode, ExtendsNode, BlockNode)):
                refuse("include, extends and block are not used in a subject or preheader")
            return
        if handler.layout_name(row) is not None:
            extends = nodelist[0] if len(nodelist) == 1 and type(nodelist[0]) is ExtendsNode else None
            inner = extends.nodelist if extends is not None else NodeList()
            content = inner[0] if len(inner) == 1 and type(inner[0]) is BlockNode else None
            if content is None or content.name != CONTENT_BLOCK or any(
                content.nodelist.get_nodes_by_type(kind) for kind in (ExtendsNode, BlockNode)
            ):
                refuse("extends and block are the layout's: a template writes neither")
            return
        blocks = nodelist.get_nodes_by_type(BlockNode)
        if nodelist.get_nodes_by_type(ExtendsNode):
            refuse("extends is not used in a layout, block or part")
        if handler.holds_content:
            top = [node for node in nodelist if type(node) is BlockNode]
            if len(blocks) != 1 or len(top) != 1 or top[0].name != CONTENT_BLOCK or top[0].nodelist:
                refuse("a layout holds one empty top-level {% block content %}{% endblock %} and no other block")
        elif blocks:
            refuse("block is the layout's alone")

    # -- the walk --------------------------------------------------------------

    @refusals_only()
    def walk(self, root: WalkedRow, rows: RowReader) -> TemplateWalk:
        """Walk ``root`` and every row it reaches in its language, read through ``rows``.

        Raise ``ValidationError`` listing every refusal, each item coded
        ``message_template_refused``.
        """

        walk = self.judge(root, rows)
        if walk.refusals:
            raise _refused(walk.refusals, "message_template_refused")
        return walk

    @refusals_only()
    def judge(self, root: WalkedRow, rows: RowReader) -> TemplateWalk:
        """Walk ``root`` as :meth:`walk` does, and return the walk with its refusals and the rows it reached."""

        root_name = root.handler.template_name(root.key)
        walk = TemplateWalk(root=root, root_name=root_name, language=str(root.language), rows={root_name: root})
        checks = self._gather(walk, rows)
        if not walk.refusals:
            self._declare(walk)
        if not walk.refusals:
            self._walk_trees(walk, checks)
        return walk

    def _gather(self, walk: TemplateWalk, reader: RowReader) -> dict[str, _RowCheck]:
        """Rows reached, level by level, each counted before it is parsed and checked alone.

        Each checked row's own size (:attr:`_RowCheck.sizes`) adds to the walk's totals, and the walk stops as soon as
        a total passes a tree cap: every row reached is in the tree at least once, so no admitted walk reaches it.
        What each row's checks read (:attr:`_RowCheck.read`) adds up too, against :data:`WALK_READ_LIMIT`.
        """

        checks: dict[str, _RowCheck] = {}
        totals: dict[RowPiece, tuple[int, int, int]] = {}
        characters = read = spans = 0
        level = [walk.root_name]
        while level:
            wanted: list[str] = []
            for name in level:
                row = walk.rows[name]
                characters += len(row.source) + len(row.text_source)
                if characters > WALK_SOURCE_LIMIT:
                    walk.refusals.append(
                        f"{walk.root_name}: the rows it reaches hold over {WALK_SOURCE_LIMIT} characters of source",
                    )
                    return checks
                check = checks[name] = self.check(row)
                walk.refusals.extend(check.refusals)
                read, spans = read + check.read[0], spans + check.read[1]
                if read > WALK_READ_LIMIT or spans > WALK_SPAN_READ_LIMIT:
                    walk.refusals.append(
                        f"{walk.root_name}: the forms of the rows it reaches hold over {WALK_READ_LIMIT} characters "
                        f"or {WALK_SPAN_READ_LIMIT} template spans for its checks to read, comments included",
                    )
                    return checks
                for piece, size in check.sizes.items():
                    total = totals[piece] = cast(tuple[int, int, int], tuple(
                        map(sum, zip(totals.get(piece, (0, 0, 0)), size, strict=True))))
                    if over := _over_caps(walk.root_name, piece, *total):
                        walk.refusals.extend(over)
                        return checks
                if check.forms is not None:
                    walk.forms[name] = check.forms
                walk.includes |= check.includes
                layout = row.handler.layout_name(row)
                for target in sorted(check.includes | ({layout} if layout else set())):
                    if target not in walk.rows and target not in wanted:
                        wanted.append(target)
            if walk.refusals or not wanted:
                return checks
            found = reader.named_rows(wanted, language=walk.language)
            # One query a level for what a save asks of every included key, never one a target.
            resolving = reader.resolving_names(
                [target for target in wanted if target in walk.includes]) if self.saving else frozenset()
            for target in wanted:
                reached = found.get(target)
                if reached is None:
                    walk.refusals.append(f"{walk.root_name}: no message template {target} in {walk.language}")
                elif target in walk.includes and not reached.handler.includable:
                    walk.refusals.append(f"{walk.root_name}: only blocks and parts are included, not {target}")
                elif self.saving and target in walk.includes and target not in resolving:
                    walk.refusals.append(
                        f"{walk.root_name}: {target} has no shipped default or row in the default language",
                    )
                else:
                    walk.rows[target] = reached
            level = [target for target in wanted if target in walk.rows]
        return checks

    @staticmethod
    def _declare(walk: TemplateWalk) -> None:
        """``D``: the root's ``variables``, and a template's layout's, judged as one set."""

        layout = walk.root.handler.layout_name(walk.root)
        declared = dict(walk.rows[layout].variables) if layout else {}
        declared.update(walk.root.variables)
        try:
            validate_variables(declared, max_paths=DECLARED_LIMIT)
        except ValidationError as error:
            walk.refusals.append(f"{walk.root_name}: with its layout, {' '.join(error.messages)}")
        walk.declared = declared

    def _walk_trees(self, walk: TemplateWalk, checks: Mapping[str, _RowCheck]) -> None:
        root = walk.root
        pieces = checks[walk.root_name].pieces
        layout = root.handler.layout_name(root)
        walker = _TreeWalker(walk, checks)
        for form in (RowPiece.HTML, RowPiece.TEXT):
            tree = _Tree(form)
            frame = _frame(walk.root_name, root, form, top=True)
            if layout is not None:
                extends = cast(ExtendsNode, pieces[form].nodelist[0])
                tree.content = (frame, extends.blocks[CONTENT_BLOCK].nodelist)
                tree.nodes += 2
                walker.nodes(_frame(layout, walk.rows[layout], form, top=True), checks[layout].pieces[form].nodelist,
                             tree)
            else:
                walker.nodes(frame, pieces[form].nodelist, tree)
            walker.close(tree)
        for piece in (RowPiece.SUBJECT, RowPiece.PREHEADER):
            if piece in pieces:
                tree = _Tree(piece)
                walker.nodes(_frame(walk.root_name, root, piece, top=True), pieces[piece].nodelist, tree)
                walker.close(tree)


def _date_format(expression: FilterExpression) -> str:
    """The format ``expression``'s first filter, ``date``, prints in: its literal argument, else ``DATE_FORMAT``."""

    arguments = expression.filters[0][1]
    if not arguments:
        return "DATE_FORMAT"
    lookup, argument = arguments[0]
    return str(argument.literal if lookup else argument)


def _frame(name: str, row: WalkedRow, form: RowPiece, *, top: bool, scope: Mapping[str, _Binding] | None = None,
           loops: int = 0, includes: tuple[str, ...] = ()) -> _Frame:
    variables = row.variables if isinstance(row.variables, dict) else {}
    leaves = frozenset(tuple(path.split(".")) for path in variables)
    return _Frame(
        name=name, row=row, form=form, declared=_prefixes(leaves), leaves=leaves, top=top, scope=scope or {},
        loops=loops, includes=includes,
    )


class _TreeWalker:
    """Rules 13 to 18: every read resolved through scope, alias and include argument, and every walk bound."""

    def __init__(self, walk: TemplateWalk, checks: Mapping[str, _RowCheck]) -> None:
        self.walk = walk
        self.checks = checks
        self.d_prefixes = _prefixes(tuple(path.split(".")) for path in walk.declared)
        self.d_leaves = frozenset(tuple(path.split(".")) for path in walk.declared)

    def refuse(self, frame: _Frame, reason: str) -> None:
        self.walk.refusals.append(f"{frame.name}: {reason}")

    def close(self, tree: _Tree) -> None:
        self.walk.refusals.extend(_over_caps(self.walk.root_name, tree.form, tree.nodes, tree.outputs, tree.text))

    def nodes(self, frame: _Frame, nodelist: Iterable[Node], tree: _Tree) -> None:
        for node in nodelist:
            if tree.nodes > NODE_LIMIT:
                return  # over the bound: close() refuses, and the walk stops expanding includes
            self.node(frame, node, tree)

    def node(self, frame: _Frame, node: Node, tree: _Tree) -> None:
        reads = walked(node)
        if type(node) is not BudgetedLoopBody:
            tree.nodes += 1
        if type(node) is TextNode:
            tree.text += len(node.s)
        elif type(node) is VariableNode:
            tree.outputs += 1
            self.expression(frame, node.filter_expression, ReadUse.OUTPUT)
        elif type(node) is IfNode:
            for condition, nodelist in node.conditions_nodelists:
                guards = frame.guards
                if condition is not None:
                    tree.nodes += self.condition(frame, condition)
                    guards = guards | self.guards(frame, condition)
                self.nodes(replace(frame, guards=guards), nodelist, tree)
        elif type(node) is ForNode:
            self.loop(frame, node, tree)
        elif type(node) is WithNode:
            scope = dict(frame.scope)
            for name, expression in node.extra_context.items():
                tree.nodes += 1
                self.bindable(frame, name)
                scope[name] = self.bind(frame, expression)
            self.nodes(replace(frame, scope=scope), node.nodelist, tree)
        elif type(node) is BudgetedIncludeNode:
            self.include(frame, node, tree)
        elif type(node) is BlockNode:
            if tree.content is not None and node.name == CONTENT_BLOCK:
                content_frame, content = tree.content
                self.nodes(content_frame, content, tree)
        elif type(node) is ExtendsNode:
            self.refuse(frame, "extends is the wrapper's alone")
        else:
            for nodelist in reads.nodelists(node):
                self.nodes(frame, nodelist, tree)

    # -- names and paths -------------------------------------------------------

    def bindable(self, frame: _Frame, name: str) -> None:
        if name in UNBINDABLE:
            self.refuse(frame, f"{name} is a name the mail or the engine supplies; it is not bound")

    def resolve(self, frame: _Frame, lookups: Path) -> _Binding | None:
        """What ``lookups`` reads in ``frame``, or ``None`` after refusing it."""

        head, rest = lookups[0], lookups[1:]
        binding = frame.scope.get(head)
        if binding is None:
            if head == "brand":
                binding = _Binding(BindingKind.BRAND, effective=("brand",))
            elif head in ("lang", "dir"):
                binding = _Binding(BindingKind.GLOBAL, effective=(head,))
            elif head in CONSTANTS:
                binding = _Binding(BindingKind.CONSTANT, effective=(head,))
            elif head in ("subject", "preheader"):
                if not frame.row.handler.reads_message_fields:
                    self.refuse(frame, f"{head} is read in a template or a layout alone")
                    return None
                if frame.form != RowPiece.HTML:
                    self.refuse(frame, f"{head} is read in the HTML form alone: the plain-text body, the subject and "
                                       "the preheader never hold it")
                    return None
                binding = _Binding(BindingKind.MESSAGE, effective=(head,))
            elif head in ("forloop", "block"):
                self.refuse(frame, f"{_dotted(lookups)} is read outside a loop or is the engine's own")
                return None
            elif frame.top:
                binding = _Binding(BindingKind.VALUE, local=(head,), effective=(head,))
            else:
                binding = _Binding(BindingKind.UNPASSED, local=(head,))
        local = (*binding.local, *rest) if binding.local else ()
        if local and local not in frame.declared:
            self.refuse(frame, f"{_dotted(local)} is not in its variables")
            return None
        if binding.kind in _VALUES:
            effective = (*binding.effective, *rest) if binding.effective is not None else None
            if effective is not None and effective not in self.d_prefixes:
                self.refuse(frame, f"{_dotted(effective)} is not a value the render declares")
                return None
            return _Binding(binding.kind, local=local, effective=effective)
        if binding.kind in (BindingKind.BRAND, BindingKind.FORLOOP):
            path = (*(binding.effective or ()), *rest)
            if path not in (BRAND_PREFIXES if binding.kind == BindingKind.BRAND else FORLOOP_PATHS):
                self.refuse(frame, f"{_dotted(path)} is not a value the mail supplies")
                return None
            return _Binding(binding.kind, local=local, effective=path)
        if rest:
            self.refuse(frame, f"{_dotted(lookups)} looks up inside {head}, which holds one value")
            return None
        return binding

    def leaf(self, frame: _Frame, binding: _Binding) -> bool:
        """Whether ``binding`` reads one value, not an object or a list, here and in ``D``."""

        if binding.kind in _VALUES:
            return binding.local in frame.leaves and (binding.effective is None or binding.effective in self.d_leaves)
        if binding.kind == BindingKind.BRAND:
            return binding.effective in BRAND_PATHS
        if binding.kind == BindingKind.FORLOOP:
            return binding.effective in FORLOOP_PATHS and binding.effective != ("forloop",)
        return True

    def guarded(self, frame: _Frame, binding: _Binding) -> bool:
        return self.guard_key(binding) in frame.guards

    @staticmethod
    def guard_key(binding: _Binding) -> tuple[str, Path]:
        return ("e", binding.effective) if binding.effective is not None else ("l", binding.local)

    # -- expressions -----------------------------------------------------------

    def expression(self, frame: _Frame, expression: FilterExpression, use: ReadUse) -> _Binding | None:
        """Judge one expression read as ``use`` (rules 5, 11, 13 to 17); return what it binds to."""

        names = filter_names(expression)
        variable = expression.var
        if not expression.is_var or variable.lookups is None:
            if names[:1] == ["date"]:
                self.refuse(frame, f"date reads a declared value, not {expression.token}")
            if use == ReadUse.TEST and names:
                self.refuse(frame, f"an if tests a literal through a filter ({expression.token})")
            if use == ReadUse.SEQUENCE:
                self.refuse(frame, f"for loops over a list a row declares, not {expression.token}")
            return _Binding(BindingKind.COMPUTED if names else BindingKind.LITERAL)
        binding = self.resolve(frame, tuple(variable.lookups))
        if binding is None:
            return None
        leaf = self.leaf(frame, binding)
        value = binding.kind in _VALUES
        if names[:1] == ["date"] and not value:
            self.refuse(frame, f"date reads a declared value, not {expression.token}")
        if names and names[0] != "length" and not leaf:
            self.refuse(frame, f"{expression.token} filters an object or a list; only length reads one")
        if use == ReadUse.OUTPUT and not leaf and not (value and names[:1] == ["length"]):
            self.refuse(frame, f"{{{{ {expression.token} }}}} prints an object or a list")
        if use == ReadUse.TEST and not (
            (value and (names == ["length"] or leaf and not names))
            or (binding.kind in (BindingKind.BRAND, BindingKind.FORLOOP, BindingKind.GLOBAL, BindingKind.CONSTANT)
                and leaf and not names)
        ):
            self.refuse(frame, f"an if tests a declared value, its length or a name the mail supplies, not "
                               f"{expression.token}")
        if use == ReadUse.SEQUENCE:
            if not value or (*binding.local, "*") not in frame.declared or (
                    binding.effective is not None and (*binding.effective, "*") not in self.d_prefixes):
                self.refuse(frame, f"for loops over a list a row declares, not {expression.token}")
                return None
        if value:
            if binding.effective is not None:
                self.walk.reads.add(binding.effective)
            optional = names[:1] == ["default"] or self.guarded(frame, binding)
            if use in (ReadUse.OUTPUT, ReadUse.ARGUMENT) and not optional:
                if binding.kind == BindingKind.UNPASSED:
                    self.refuse(frame, f"{expression.token} reads {binding.local[0]}, which it was not passed")
                elif binding.effective is not None:
                    self.walk.required.add(binding.effective)
            if FLOAT_FILTERS.intersection(names) and binding.effective is not None:
                self.walk.float_paths.add(binding.effective)
            if names[:1] == ["date"] and binding.effective is not None:
                self.walk.date_paths.setdefault(binding.effective, set()).add(_date_format(expression))
        if names and use != ReadUse.SEQUENCE:
            return _Binding(BindingKind.COMPUTED)
        return binding

    def bind(self, frame: _Frame, expression: FilterExpression) -> _Binding:
        """What a ``with`` value or an include argument binds its name to."""

        use = ReadUse.ARGUMENT if expression.filters else ReadUse.ALIAS
        binding = self.expression(frame, expression, use)
        return binding if binding is not None else _Binding(BindingKind.COMPUTED)

    def condition(self, frame: _Frame, condition: Any) -> int:
        """Judge each operand of an ``if``/``elif`` condition; return how many operands and operators it holds, each a
        node."""

        if type(condition) is TemplateLiteral:
            self.expression(frame, cast(FilterExpression, condition.value), ReadUse.TEST)
            return 1
        return 1 + sum(self.condition(frame, operand) for operand in (condition.first, condition.second)
                       if operand is not None)

    def guards(self, frame: _Frame, condition: Any) -> frozenset[tuple[str, Path]]:
        """The paths an ``if`` branch guards: its condition, or each operand of an ``and`` of them."""

        if type(condition) is TemplateLiteral:
            expression = cast(FilterExpression, condition.value)
            if expression.is_var and expression.var.lookups is not None and not expression.filters:
                binding = self.resolve_quietly(frame, tuple(expression.var.lookups))
                if binding is not None and binding.kind in _VALUES:
                    return frozenset({self.guard_key(binding)})
            return frozenset()
        if condition.id == "and":
            return self.guards(frame, condition.first) | self.guards(frame, condition.second)
        return frozenset()

    def resolve_quietly(self, frame: _Frame, lookups: Path) -> _Binding | None:
        held = len(self.walk.refusals)
        binding = self.resolve(frame, lookups)
        del self.walk.refusals[held:]
        return binding

    # -- loops and includes ----------------------------------------------------

    def loop(self, frame: _Frame, node: ForNode, tree: _Tree) -> None:
        sequence = self.expression(frame, cast(FilterExpression, node.sequence), ReadUse.SEQUENCE)
        loops = frame.loops + 1
        if loops > LOOP_DEPTH_LIMIT:
            self.refuse(frame, f"loops nest over {LOOP_DEPTH_LIMIT} deep, through includes too")
        scope = dict(frame.scope)
        for name in node.loopvars:
            self.bindable(frame, name)
            if sequence is not None:
                scope[name] = _Binding(
                    sequence.kind, local=(*sequence.local, "*"),
                    effective=(*sequence.effective, "*") if sequence.effective is not None else None,
                )
        scope["forloop"] = _Binding(BindingKind.FORLOOP, effective=("forloop",))
        self.nodes(replace(frame, scope=scope, loops=loops), cast(NodeList, node.nodelist_loop), tree)
        self.nodes(frame, cast(NodeList, node.nodelist_empty), tree)

    def include(self, frame: _Frame, node: BudgetedIncludeNode, tree: _Tree) -> None:
        target = str(node.template.var)
        stack = (*frame.includes, frame.name)
        if target in stack:
            self.refuse(frame, f"includes itself: {' -> '.join((*stack, target))}")
            return
        if len(stack) > INCLUDE_DEPTH_LIMIT:
            self.refuse(frame, f"includes nest over {INCLUDE_DEPTH_LIMIT} deep")
            return
        row = self.walk.rows[target]
        arguments = row.variables if isinstance(row.variables, dict) else {}
        declared_roots = {path.split(".", 1)[0] for path in arguments}
        scope: dict[str, _Binding] = {}
        for name, expression in node.extra_context.items():
            tree.nodes += 1
            self.bindable(frame, name)
            if name not in declared_roots:
                self.refuse(frame, f"passes {name} to {target}, which does not declare it")
            binding = self.bind(frame, expression)
            scope[name] = _Binding(binding.kind, local=(name,), effective=binding.effective)
        self.nodes(
            _frame(target, row, tree.form, top=False, scope=scope, loops=frame.loops, includes=stack),
            self.checks[target].pieces[tree.form].nodelist,
            tree,
        )
