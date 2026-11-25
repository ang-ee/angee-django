"""Rendering message templates: markup, declared values, the forms a row gives, and the renderer.

:class:`MessageTemplateFormat` and :func:`validate_variables` live here, in a
module that imports no model of this addon and reads no database, so the model,
the walker and any other reader of a row's declared values import one owner
without a cycle. A host's first migration records ``validate_variables`` by this
module's path (the format's choices it writes as literals).

A row's markup decides its two **forms**, through :attr:`MessageTemplateFormat.handler`:
the HTML form and the plain-text form the engine compiles (:class:`RowForms`). An
HTML row's forms are its ``source`` and ``text_source``. A Markdown row goes
through the **Markdown pass** first (markdown-it, raw HTML off, link reference
definitions off), which keeps every Django template span whole and renders it
verbatim; its plain-text form is a text rendering of the same tokens, never
scraped from the HTML. :func:`check_html` holds an HTML form to what a mail may
carry.

:func:`render_message` renders a walked row (:class:`~angee.messaging_templates.walker.TemplateWalk`)
on two :class:`~angee.messaging_templates.engine.MessageTemplateEngine` engines,
one escaping values for the HTML body and one writing them as they are for the
subject, preheader and plain-text body, each pass in its own
:class:`~angee.messaging_templates.engine.RenderBudget` under the row's language.
"""

from __future__ import annotations

import quopri
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from html.parser import HTMLParser
from itertools import islice
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, cast

from django.core.exceptions import ValidationError
from django.db import models
from django.template import Origin, TemplateDoesNotExist, TemplateSyntaxError
from django.template.base import DebugLexer, FilterExpression, Token, TokenType, VariableNode, tag_re
from django.template.loaders.base import Loader
from django.utils import translation
from django.utils.html import escape
from markdown_it import MarkdownIt
from markdown_it.helpers import parseLinkLabel
from markdown_it.renderer import RendererHTML
from markdown_it.rules_core import StateCore
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token as MarkdownToken
from markdown_it.utils import EnvType, OptionsDict

from angee.brandkit.models import MailRole, MailScheme
from angee.messaging_templates.engine import FILTER_NAMES, MessageTemplateEngine, RenderBudget, url

if TYPE_CHECKING:
    from angee.messaging_templates.walker import TemplateWalk


class MessageTemplateFormat(models.TextChoices):
    """The markup a message template's ``source`` is written in; its forms are :attr:`handler`'s."""

    MARKDOWN = "markdown", "Markdown"
    HTML = "html", "HTML"

    @property
    def handler(self) -> MessageTemplateFormatHandler:
        """Return the behaviour owned by this markup."""

        return _FORMAT_HANDLERS[self]


RESERVED_VARIABLE_ROOTS = frozenset({"brand", "lang", "dir", "subject", "preheader", "forloop", "block"})
"""Names no declared value starts with: the mail around a row supplies them (the brand, the language and its
writing direction, the subject and the preheader), and Django's template engine names its loops and blocks so."""

MAX_VARIABLE_SEGMENTS = 8
"""The most names a declared path joins, so following a declared path stays a short walk."""

_VARIABLE_PATH = re.compile(r"[a-z][a-z0-9_]*(?:\.(?:[a-z][a-z0-9_]*|\*))*")


def validate_variables(variables: Any, *, max_paths: int = 50) -> None:
    """Refuse ``variables`` that are not an object of declared paths to their descriptions.

    Each key is a dotted path of at most :data:`MAX_VARIABLE_SEGMENTS` lower-case
    names (``order.total``) whose later segments may be ``*``, every item of a list
    (``tags.*`` is a list of scalars); each value is a string describing it. There
    are at most ``max_paths`` keys: a row's own declaration takes the default, and a
    caller judging several rows' declarations together passes its own limit. No
    path is a proper prefix of another (``order`` and ``order.total`` are not both
    declared), no path reads one value both as a list and as an object
    (``items.*`` beside ``items.pop``), and no path starts with a name in
    :data:`RESERVED_VARIABLE_ROOTS`. The first offending key is named. The check
    takes time linear in the number and length of the keys, since a console insert
    runs it before it checks who may insert.
    """

    if not isinstance(variables, dict):
        raise ValidationError("Declare variables as an object of paths to descriptions.", code="invalid_variables")
    if len(variables) > max_paths:
        extra = next(islice(variables, max_paths, None))
        raise ValidationError(
            "Declare at most %(limit)s variables; %(path)s is one too many.",
            code="too_many_variables", params={"limit": max_paths, "path": extra},
        )
    for path, description in variables.items():
        if isinstance(path, str) and path.count(".") >= MAX_VARIABLE_SEGMENTS:
            raise ValidationError(
                "%(path)s joins more than %(limit)s names.",
                code="variable_too_deep", params={"path": path, "limit": MAX_VARIABLE_SEGMENTS},
            )
        if not isinstance(path, str) or not _VARIABLE_PATH.fullmatch(path):
            raise ValidationError(
                "%(path)s is not a dotted path of lower-case names, each later one a name or *.",
                code="invalid_variable_path", params={"path": path},
            )
        if not isinstance(description, str):
            raise ValidationError(
                "Describe %(path)s in words.", code="invalid_variable_description", params={"path": path},
            )
        if path.split(".", 1)[0] in RESERVED_VARIABLE_ROOTS:
            raise ValidationError(
                "%(path)s starts with a reserved name.", code="reserved_variable", params={"path": path},
            )
    # Each path's parents, at most seven of them, say what the path reads them as:
    # a parent that is itself declared is a proper prefix, and a parent read both
    # through ``*`` and through a name is both a list and an object.
    read_as: dict[str, tuple[bool, str]] = {}
    for path in variables:
        segments = path.split(".")
        for depth in range(1, len(segments)):
            parent = ".".join(segments[:depth])
            if parent in variables:
                raise ValidationError(
                    "%(path)s is declared and so is %(following)s inside it: declare only the inner paths.",
                    code="variable_prefix", params={"path": parent, "following": path},
                )
            as_list = segments[depth] == "*"
            first_as_list, first = read_as.setdefault(parent, (as_list, path))
            if first_as_list != as_list:
                raise ValidationError(
                    "%(path)s and %(other)s read %(parent)s both as a list and as an object.",
                    code="variable_list_and_object", params={"path": first, "other": path, "parent": parent},
                )


# ---------- what the mail around a row supplies ----------

BRAND_PATHS = frozenset(
    {("brand", "name"), ("brand", "font", "heading"), ("brand", "font", "body"),
     ("brand", "footer", "organization"), ("brand", "footer", "address")}
    | {("brand", str(scheme), str(role)) for scheme in MailScheme for role in MailRole},
)
"""Every value ``brand`` holds (:func:`brand_values`), as a path."""

STYLE_ELEMENT_OUTPUTS = frozenset(("brand", str(scheme), str(role)) for scheme in MailScheme for role in MailRole)
"""The outputs a ``<style>`` element may hold, without filters: the mail colours."""

STYLE_OUTPUTS = STYLE_ELEMENT_OUTPUTS | {("brand", "font", "heading"), ("brand", "font", "body"), ("dir",)}
"""The outputs a ``style`` attribute may hold, without filters: a mail colour, a font stack, the direction."""


def brand_values(kit: Any) -> dict[str, Any]:
    """Return what ``brand`` holds in a render, built from a brand kit as plain strings.

    The mail colours come from :meth:`~angee.brandkit.models.BrandKit.mail_palette`,
    which raises ``ValidationError`` for a kit stored past its validation.
    """

    palette = kit.mail_palette()
    return {
        "name": str(kit.name),
        "font": {"heading": str(kit.font_heading), "body": str(kit.font_body)},
        "footer": {"organization": str(kit.footer_organization), "address": str(kit.footer_address)},
        **{
            str(scheme): {str(role): str(palette[str(scheme)][str(role)]) for role in MailRole}
            for scheme in MailScheme
        },
    }


# ---------- the forms a row gives ----------


class RowForms(NamedTuple):
    """The two forms of a row the engine compiles: the HTML body's and the plain-text body's."""

    html: str
    text: str


class RowRefusal(ValueError):
    """A row's source is refused before its forms are compiled; the message names why."""


class MessageTemplateFormatHandler:
    """What one markup is: the forms a row's source gives and what it refuses before they compile."""

    def source_refusals(self, source: str) -> list[str]:
        """Return what this markup refuses in ``source`` before anything parses it."""

        del source
        return []

    def forms(self, row: Any) -> RowForms:
        """Return ``row``'s forms; raise :class:`RowRefusal` for a source this markup refuses."""

        raise NotImplementedError


class HtmlFormatHandler(MessageTemplateFormatHandler):
    """An HTML row is its own HTML form; its ``text_source`` is the plain-text form."""

    def forms(self, row: Any) -> RowForms:
        """Return ``source`` and ``text_source`` as they are."""

        return RowForms(html=str(row.source), text=str(row.text_source))


MARKDOWN_BRACKETS_PER_LINE = 30
"""The most ``[`` a line of Markdown holds: markdown-it scans each link label to the end of its line."""

_TEMPLATE_LINK = "\x00template-link:"
"""Marks a link whose destination is one output, in the token's ``href``."""

_PAIRED_TAGS = {"if": "endif", "for": "endfor", "with": "endwith", "comment": "endcomment"}
_MIDDLE_TAGS = frozenset({"elif", "else", "empty"})
_CLOSING_TAGS = frozenset(_PAIRED_TAGS.values())

_MARKDOWN_STYLES: Mapping[str, Mapping[str, str]] = {
    "p": {"style": "margin:0 0 16px"},
    "h1": {"style": "margin:0 0 16px;font:600 24px/32px {{ brand.font.heading }}"},
    "h2": {"style": "margin:20px 0 6px;font:600 18px/26px {{ brand.font.heading }}"},
    "h3": {"style": "margin:16px 0 6px;font:600 16px/24px {{ brand.font.heading }}"},
    "h4": {"style": "margin:16px 0 6px;font:600 16px/24px {{ brand.font.heading }}"},
    "h5": {"style": "margin:16px 0 6px;font:600 16px/24px {{ brand.font.heading }}"},
    "h6": {"style": "margin:16px 0 6px;font:600 16px/24px {{ brand.font.heading }}"},
    "ul": {"style": "margin:0 0 16px;padding:0 20px"},
    "ol": {"style": "margin:0 0 16px;padding:0 20px"},
    "li": {"style": "margin:0 0 6px"},
    "blockquote": {
        "class": "brand-muted brand-border",
        "style": "margin:0 0 16px;padding:4px 14px;border-left:3px solid {{ brand.light.border }};"
                 "color:{{ brand.light.muted }}",
    },
    "a": {"class": "brand-link", "style": "color:{{ brand.light.link }};text-decoration:underline"},
    "hr": {"class": "brand-border", "style": "border:0;border-top:1px solid {{ brand.light.border }};margin:16px 0"},
}
"""Inline styles the HTML form of a Markdown row carries where an element departs from the content cell's
font and colour; a colour carries the role class the layout's dark rules set."""


def _template_span(state: StateInline, silent: bool) -> bool:
    """Take one Django template span whole, as Django's own lexer finds it (it never spans a line)."""

    if state.src[state.pos] != "{":
        return False
    match = tag_re.match(state.src, state.pos)
    if match is None or match.end() > state.posMax:
        return False
    if not silent:
        token = state.push("template_span", "", 0)
        token.content = match.group(0)
    state.pos = match.end()
    return True


def _template_link(state: StateInline, silent: bool) -> bool:
    """Take ``[label]({{ expression }})`` as a link whose ``href`` is ``{{ expression|url }}``.

    It fires only when ``](`` and an output follow on the rest of the line and
    markdown-it's own label parser confirms the label; one right after ``!``, an
    image whose source is an output, is recorded as a refusal.
    """

    source, start = state.src, state.pos
    if source[start] != "[":
        return False
    end_of_line = source.find("\n", start)
    end_of_line = state.posMax if end_of_line < 0 else min(end_of_line, state.posMax)
    if source.find("]({{", start, end_of_line) < 0:
        return False
    label_end = parseLinkLabel(state, start, True)
    if label_end < 0 or not source.startswith("({{", label_end + 1):
        return False
    match = tag_re.match(source, label_end + 2)
    if match is None or not match.group(0).startswith("{{") or source[match.end():match.end() + 1] != ")":
        return False
    if match.end() + 1 > state.posMax:
        return False
    if not silent:
        if start > 0 and source[start - 1] == "!":
            state.env.setdefault("refusals", []).append(
                f"an image's source is an output ({match.group(0)}): link to it, or write a static image",
            )
        token = state.push("link_open", "a", 1)
        token.attrs = {"href": _TEMPLATE_LINK + match.group(0)[2:-2].strip()}
        limit = state.posMax
        state.pos, state.posMax = start + 1, label_end
        state.md.inline.tokenize(state)
        state.posMax = limit
        state.push("link_close", "a", -1)
    state.pos = match.end() + 1
    return True


def _blank(token: MarkdownToken) -> bool:
    return token.type == "softbreak" or (token.type == "text" and not token.content.strip())


def _hide_tag_paragraphs(state: StateCore) -> None:
    """Write no ``<p>`` around a paragraph holding only ``{% %}`` and ``{# #}`` spans."""

    tokens = state.tokens
    for index in range(len(tokens) - 2):
        if tokens[index].type != "paragraph_open" or tokens[index + 1].type != "inline":
            continue
        kids = [kid for kid in tokens[index + 1].children or () if not _blank(kid)]
        if kids and all(kid.type == "template_span" and kid.content[:2] in ("{%", "{#") for kid in kids):
            tokens[index].hidden = tokens[index + 2].hidden = True


def _render_styled(
    renderer: RendererHTML, tokens: Sequence[MarkdownToken], index: int, options: OptionsDict, env: EnvType,
) -> str:
    """Render an opening tag, with its inline style when the render is the styled one."""

    token = tokens[index]
    added = _MARKDOWN_STYLES.get(token.tag) if env.get("styled") else None
    if not added:
        return renderer.renderToken(tokens, index, options, env)
    held = token.attrs
    token.attrs = {**held, **added}
    try:
        return renderer.renderToken(tokens, index, options, env)
    finally:
        token.attrs = held


def _render_link_open(
    renderer: RendererHTML, tokens: Sequence[MarkdownToken], index: int, options: OptionsDict, env: EnvType,
) -> str:
    """Render a link; one whose destination is an output takes it through ``url``."""

    href = str(tokens[index].attrs.get("href", ""))
    if not href.startswith(_TEMPLATE_LINK):
        return _render_styled(renderer, tokens, index, options, env)
    added = "".join(
        f' {name}="{value}"' for name, value in (_MARKDOWN_STYLES["a"].items() if env.get("styled") else ())
    )
    return '<a href="{{ ' + href.removeprefix(_TEMPLATE_LINK) + '|url }}"' + added + ">"


def _markdown() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": False}).disable("reference")
    parser.inline.ruler.before("text", "template_span", _template_span)
    parser.inline.ruler.before("link", "template_link", _template_link)
    parser.core.ruler.push("hide_tag_paragraphs", _hide_tag_paragraphs)
    parser.add_render_rule("template_span", lambda renderer, tokens, index, options, env: tokens[index].content)
    for kind in ("paragraph_open", "heading_open", "bullet_list_open", "ordered_list_open", "list_item_open",
                 "blockquote_open", "hr"):
        parser.add_render_rule(kind, _render_styled)
    parser.add_render_rule("link_open", _render_link_open)
    return parser


def _spans(text: str) -> list[str]:
    """Every template span of ``text``, in order, as Django's lexer finds it."""

    return [
        text[slice(*cast(tuple[int, int], token.position))]
        for token in DebugLexer(text).tokenize() if token.token_type != TokenType.TEXT
    ]


def _same_span(source: str, form: str) -> bool:
    """Whether a form's span is the source's, the ``|url`` a link destination takes allowed for."""

    return form == source or (source.startswith("{{") and form == "{{ " + source[2:-2].strip() + "|url }}")


class _MarkdownText:
    """The plain-text form of a Markdown row, written from the same tokens as its HTML form."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.quotes = 0
        self.lists: list[list[int]] = []

    def write(self, tokens: Sequence[MarkdownToken]) -> str:
        """Return the plain text of ``tokens``: a link as ``label (url)``, items ``- ``, quotes ``> ``."""

        item_prefix = ""
        for token in tokens:
            match token.type:
                case "inline":
                    self._block(item_prefix + self._inline(token.children or ()))
                    item_prefix = ""
                case "paragraph_close" | "heading_close":
                    if not token.hidden:  # a tight list item's paragraph, or one holding only tags
                        self._blank_line()
                case "bullet_list_open":
                    self.lists.append([])
                case "ordered_list_open":
                    self.lists.append([int(token.attrs.get("start", 1) or 1)])
                case "bullet_list_close" | "ordered_list_close":
                    self.lists.pop()
                    self._blank_line()
                case "list_item_open":
                    counter = self.lists[-1] if self.lists else []
                    item_prefix = f"{counter[0]}. " if counter else "- "
                    if counter:
                        counter[0] += 1
                case "blockquote_open":
                    self.quotes += 1
                case "blockquote_close":
                    if self.lines and self.lines[-1] == ("> " * self.quotes).rstrip():
                        self.lines.pop()  # no blank quote line closes a quote
                    self.quotes -= 1
                    self._blank_line()
                case "fence" | "code_block":
                    self._block(item_prefix + token.content.rstrip("\n"))
                    item_prefix = ""
                    self._blank_line()
                case "hr":
                    self._block("---")
                    self._blank_line()
        return "\n".join(self.lines).strip("\n") + "\n"

    def _block(self, text: str) -> None:
        prefix = "> " * self.quotes
        self.lines.extend(prefix + line for line in text.split("\n"))

    def _blank_line(self) -> None:
        blank = ("> " * self.quotes).rstrip()
        if self.lines and self.lines[-1] != blank:
            self.lines.append(blank)

    def _inline(self, children: Iterable[MarkdownToken]) -> str:
        out: list[str] = []
        links: list[str] = []
        for child in children:
            match child.type:
                case "text" | "code_inline" | "template_span":
                    out.append(child.content)
                case "softbreak" | "hardbreak":
                    out.append("\n")
                case "image":
                    out.append(child.content)
                case "link_open":
                    href = str(child.attrs.get("href", ""))
                    links.append(
                        "{{ " + href.removeprefix(_TEMPLATE_LINK) + "|url }}" if href.startswith(_TEMPLATE_LINK)
                        else href,
                    )
                case "link_close":
                    out.append(f" ({links.pop()})")
        return "".join(out)


class MarkdownFormatHandler(MessageTemplateFormatHandler):
    """A Markdown row goes through the Markdown pass; its forms are two renders of one parse.

    Refused: a source line holding over :data:`MARKDOWN_BRACKETS_PER_LINE` ``[``;
    template spans the forms do not keep whole, unchanged and in order; a template
    span in any token but a span of its own (code, a fence and its info string, a
    link title); an image whose source is an output; a tag pair neither within one
    inline run nor both alone in their own paragraphs of one container; an
    ``include`` not alone in its paragraph. Raw HTML in the source is text.
    """

    parser: ClassVar[MarkdownIt] = _markdown()

    def source_refusals(self, source: str) -> list[str]:
        """Refuse a line holding over :data:`MARKDOWN_BRACKETS_PER_LINE` ``[``."""

        return [
            f"a Markdown line holds over {MARKDOWN_BRACKETS_PER_LINE} [ (line {number})"
            for number, line in enumerate(source.split("\n"), 1) if line.count("[") > MARKDOWN_BRACKETS_PER_LINE
        ][:1]

    def forms(self, row: Any) -> RowForms:
        """Return the styled HTML render and the text render of one parse of ``row.source``."""

        source = str(row.source)
        env: dict[str, Any] = {}
        tokens = self.parser.parse(source, env)
        refusals: list[str] = list(env.get("refusals", ()))
        unstyled = self.parser.renderer.render(tokens, self.parser.options, {})
        html = self.parser.renderer.render(tokens, self.parser.options, {"styled": True})
        text = _MarkdownText().write(tokens)
        refusals.extend(self._span_refusals(source, unstyled, text))
        refusals.extend(self._token_refusals(tokens))
        refusals.extend(self._pair_refusals(tokens))
        if refusals:
            raise RowRefusal("; ".join(dict.fromkeys(refusals)))
        return RowForms(html=html, text=text)

    @staticmethod
    def _span_refusals(source: str, *forms: str) -> Iterator[str]:
        expected = _spans(source)
        for form in forms:
            found = _spans(form)
            if len(found) != len(expected) or not all(map(_same_span, expected, found)):
                changed = next(
                    (f"{left} became {right}" for left, right in zip(expected, found, strict=False)
                     if not _same_span(left, right)),
                    "a span was added or lost",
                )
                yield f"template code in the Markdown does not reach the template whole ({changed})"
                return

    @staticmethod
    def _token_refusals(tokens: Sequence[MarkdownToken]) -> Iterator[str]:
        pending = list(tokens)
        while pending:
            token = pending.pop()
            pending.extend(token.children or ())
            if token.type in ("inline", "template_span"):
                continue
            href = str(token.attrs.get("href", ""))
            values = [token.content, token.info, *(value for value in token.attrs.values() if value != href)]
            if not href.startswith(_TEMPLATE_LINK):
                values.append(href)
            if any(tag_re.search(str(value)) for value in values):
                yield f"template code inside Markdown {token.type.replace('_', ' ')} is not template code"

    @staticmethod
    def _pair_refusals(tokens: Sequence[MarkdownToken]) -> Iterator[str]:
        containers: list[tuple[str, tuple[int, ...]]] = []
        opened: list[tuple[str, int, bool, tuple[tuple[str, tuple[int, ...]], ...]]] = []
        commented = False
        for index, token in enumerate(tokens):
            if token.nesting == 1 and token.type != "paragraph_open":
                containers.append((token.type, tuple(token.map or ())))
            elif token.nesting == -1 and token.type != "paragraph_close" and containers:
                containers.pop()
            if token.type != "inline":
                continue
            kids = [kid for kid in token.children or () if not _blank(kid)]
            alone = len(kids) == 1 and index > 0 and tokens[index - 1].type == "paragraph_open"
            where = tuple(containers)
            for kid in kids:
                if kid.type != "template_span" or not kid.content.startswith("{%"):
                    continue
                name = (kid.content[2:-2].split() or [""])[0]
                if commented and name != "endcomment":
                    continue
                if name == "include" and not alone:
                    yield f"{kid.content} is not alone in its paragraph"
                if name in _PAIRED_TAGS:
                    opened.append((name, index, alone, where))
                    commented = name == "comment"
                elif name in _MIDDLE_TAGS or name in _CLOSING_TAGS:
                    if not opened:
                        yield f"{kid.content} closes no tag"
                        continue
                    opener, at, opener_alone, opener_where = opened[-1]
                    if not (at == index or (opener_alone and alone and opener_where == where)):
                        yield f"{{% {opener} %}} … {kid.content} sit in different Markdown blocks"
                    if name in _CLOSING_TAGS:
                        opened.pop()
                        commented = False
                    else:
                        opened[-1] = (opener, index, alone, where)


_FORMAT_HANDLERS: Mapping[MessageTemplateFormat, MessageTemplateFormatHandler] = {
    MessageTemplateFormat.MARKDOWN: MarkdownFormatHandler(),
    MessageTemplateFormat.HTML: HtmlFormatHandler(),
}


# ---------- the HTML checks ----------

REFUSED_ELEMENTS = frozenset({"script", "iframe", "object", "embed", "form", "link", "base", "svg", "math"})
REFUSED_ATTRIBUTES = frozenset(
    {"http-equiv", "srcset", "background", "poster", "action", "formaction", "xlink:href", "cite"},
)
LINK_ATTRIBUTES = frozenset({"href", "src"})
OUTPUT_ATTRIBUTES = frozenset({"alt", "title", "aria-label", "lang", "xml:lang", "dir"})
CONDITION_TAGS = frozenset({"if", "elif", "else", "endif"})

_PLACEHOLDER = re.compile("\x00(\\d+)\x00")
_SPAN_OPENS_TAG = re.compile("<[/!?]?\x00")
_RAW_ATTRIBUTE = re.compile(r"""([^\s"'/>=]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s>]*))?""")


class _HtmlChecker(HTMLParser):
    """Read an HTML form whose template spans are placeholders, and record what a mail may not carry."""

    def __init__(self, spans: Sequence[Token], expression: Callable[[str], FilterExpression | None]) -> None:
        super().__init__(convert_charrefs=False)
        self.spans = spans
        self.expression = expression
        self.refusals: list[str] = []
        self.in_style = False

    def refuse(self, reason: str) -> None:
        self.refusals.append(reason)

    def span_ids(self, text: str) -> list[int]:
        return [int(index) for index in _PLACEHOLDER.findall(text)]

    def output_path(self, index: int) -> tuple[tuple[str, ...] | None, list[str]]:
        """The path and filter names of an output span, from its compiled expression."""

        token = self.spans[index]
        if token.token_type != TokenType.VAR:
            return None, []
        expression = self.expression("{{ " + token.contents + " }}")
        if expression is None:
            return None, []
        lookups = expression.var.lookups if expression.is_var else None
        return (tuple(lookups) if lookups else None), [FILTER_NAMES.get(f, "?") for f, _ in expression.filters]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.check_tag(tag, attrs)
        if tag == "style":
            self.in_style = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.check_tag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self.in_style = False

    def check_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if "\x00" in tag:
            return self.refuse("template code in a tag name")
        if tag in REFUSED_ELEMENTS:
            self.refuse(f"<{tag}> is not allowed in a mail")
        quoted = self.double_quoted(self.get_starttag_text() or "")
        for name, raw_value in attrs:
            value = raw_value or ""
            if "\x00" in name:
                self.refuse(f"template code in an attribute name of <{tag}>")
                continue
            if name in REFUSED_ATTRIBUTES or name.startswith("on"):
                self.refuse(f"the {name} attribute is not allowed in a mail")
                continue
            ids = self.span_ids(value)
            if ids and name not in quoted:
                self.refuse(f"template code in the {name} attribute of <{tag}>, which is not double-quoted")
            self.check_conditions(name, ids)
            if name in LINK_ATTRIBUTES:
                self.check_link(tag, name, value, ids)
            elif name == "style":
                self.check_spans(f"the style attribute of <{tag}>", ids, STYLE_OUTPUTS, conditions=True)
            else:
                self.check_spans(
                    f"the {name} attribute of <{tag}>", ids, None if name in OUTPUT_ATTRIBUTES else frozenset(),
                    conditions=True,
                )

    @staticmethod
    def double_quoted(start_tag: str) -> set[str]:
        """The attributes a start tag writes double-quoted."""

        body = re.sub(r"^<[^\s/>]*", "", start_tag)
        return {
            match.group(1).lower() for match in _RAW_ATTRIBUTE.finditer(body)
            if match.group(2) and match.group(2).startswith('"')
        }

    def check_conditions(self, name: str, ids: list[int]) -> None:
        """Refuse an ``if`` whose pair an attribute value does not hold whole."""

        depth = 0
        for index in ids:
            token = self.spans[index]
            command = (token.contents.split() or [""])[0] if token.token_type == TokenType.BLOCK else ""
            if command == "if":
                depth += 1
            elif command == "endif":
                depth -= 1
            elif command in ("elif", "else") and depth < 1:
                depth = -1
            if depth < 0:
                break
        if depth:
            self.refuse(f"the {name} attribute holds an if without its whole tag pair")

    def check_link(self, tag: str, name: str, value: str, ids: list[int]) -> None:
        if not ids:
            if url(value) != value:
                self.refuse(f"the {name} of <{tag}> is not a link a mail may carry")
            return
        _path, filters = self.output_path(ids[0])
        if value != f"\x00{ids[0]}\x00" or not filters or filters[-1] != "url":
            self.refuse(f"the {name} of <{tag}> is one output whose last filter is url, or a static link")

    def check_spans(
        self, where: str, ids: list[int], outputs: frozenset[tuple[str, ...]] | None, *, conditions: bool,
    ) -> None:
        """Refuse a span ``where`` does not admit: outputs in ``outputs`` (``None``: any), conditions, comments."""

        for index in ids:
            token = self.spans[index]
            if token.token_type == TokenType.COMMENT and conditions:
                continue
            if token.token_type == TokenType.BLOCK:
                command = (token.contents.split() or [""])[0]
                if conditions and command in CONDITION_TAGS:
                    continue
                self.refuse(f"{{% {command} %}} in {where}")
                continue
            if token.token_type == TokenType.VAR:
                path, filters = self.output_path(index)
                if outputs is None or (not filters and path in outputs):
                    continue
                self.refuse(f"{{{{ {token.contents} }}}} in {where}")
                continue
            self.refuse(f"template code in {where}")

    def handle_data(self, data: str) -> None:
        if self.in_style:
            self.check_spans("a <style> element", self.span_ids(data), STYLE_ELEMENT_OUTPUTS, conditions=False)

    def handle_comment(self, data: str) -> None:
        self.refuse("an HTML comment, conditional ones included, is not allowed in a mail")

    def handle_decl(self, decl: str) -> None:
        if "\x00" in decl or not decl.lower().startswith("doctype"):
            self.refuse("a declaration other than the doctype is not allowed in a mail")

    def unknown_decl(self, data: str) -> None:
        self.refuse("a marked section is not allowed in a mail")

    def handle_pi(self, data: str) -> None:
        self.refuse("a processing instruction is not allowed in a mail")


def check_html(form: str, expression: Callable[[str], FilterExpression | None]) -> list[str]:
    """Return what an HTML form holds that a mail may not, its template spans read as placeholders.

    Refused: the elements in :data:`REFUSED_ELEMENTS`; the attributes in
    :data:`REFUSED_ATTRIBUTES` and every ``on*``; an ``if`` an attribute value does
    not hold whole; template code in a tag name, an attribute name or an attribute
    that is not double-quoted, and any HTML comment; in ``href`` or ``src``
    anything but one output whose last filter is ``url``, or a static link
    :func:`~angee.messaging_templates.engine.url` leaves as ``html.parser`` decodes
    it; in ``style`` an output but :data:`STYLE_OUTPUTS`; in a ``<style>`` element
    a span but :data:`STYLE_ELEMENT_OUTPUTS`; in any other attribute an output
    outside :data:`OUTPUT_ATTRIBUTES` or a tag other than an ``if`` family one.
    ``expression`` compiles one output span to its ``FilterExpression``.
    """

    spans: list[Token] = []
    parts: list[str] = []
    last = 0
    for token in DebugLexer(form).tokenize():
        if token.token_type == TokenType.TEXT:
            continue
        start, end = cast(tuple[int, int], token.position)
        parts.extend((form[last:start], f"\x00{len(spans)}\x00"))
        spans.append(token)
        last = end
    parts.append(form[last:])
    text = "".join(parts)
    checker = _HtmlChecker(spans, expression)
    if _SPAN_OPENS_TAG.search(text):
        checker.refuse("template code where a tag starts")
    checker.feed(text)
    checker.close()
    return list(dict.fromkeys(checker.refusals))


def compiled_expression(engine: MessageTemplateEngine) -> Callable[[str], FilterExpression | None]:
    """Return a function compiling one output span in ``engine`` to its ``FilterExpression``."""

    def expression(span: str) -> FilterExpression | None:
        try:
            nodelist = engine.from_string(span).nodelist
        except TemplateSyntaxError:
            return None
        node = nodelist[0] if len(nodelist) == 1 else None
        return node.filter_expression if type(node) is VariableNode else None

    return expression


# ---------- the loader and the renderer ----------


class MessageTemplateLoader(Loader):
    """Serve one form of the rows a render reached, by template name, on ``locmem.Loader``'s contract.

    Names are a row's template name (``layouts/base``, ``blocks/button``, a
    template's bare key); none holds ``#``, which Django reads as a partial.
    """

    def __init__(self, engine: MessageTemplateEngine, forms: Mapping[str, RowForms], form: str) -> None:
        """Serve ``form`` (``"html"`` or ``"text"``) of each row in ``forms``."""

        super().__init__(engine)
        self.forms = forms
        self.form = form

    def get_contents(self, origin: Origin) -> str:
        """Return the form of the row ``origin`` names."""

        forms = self.forms.get(origin.name)
        if forms is None:
            raise TemplateDoesNotExist(origin)
        return str(getattr(forms, self.form))

    def get_template_sources(self, template_name: str) -> Iterator[Origin]:
        """Name the one origin a row has."""

        yield Origin(name=template_name, template_name=template_name, loader=self)


def in_layout(layout_name: str, form: str) -> str:
    """Return ``form`` as the content of the layout named ``layout_name``, as the loader serves a template."""

    return '{% extends "' + layout_name + '" %}{% block content %}' + form + "{% endblock %}"


@dataclass(frozen=True)
class PreviewFrame:
    """A fixed subject, preheader and body a row with no body of its own is previewed with."""

    subject: str
    preheader: str
    html: str
    text: str


@dataclass
class RenderedMessage:
    """One rendered message: subject and preheader on one line each, the HTML and plain-text bodies."""

    subject: str
    preheader: str
    html: str
    plain_text: str
    language: str


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


@dataclass
class TemplatePreview:
    """A row rendered with its sample, or ``None`` when the walk or the render raised, and its warnings."""

    rendered: RenderedMessage | None
    warnings: list[TemplateWarning] = field(default_factory=list)


SUBJECT_LIMIT = 1_000
"""The longest rendered subject or preheader, after it is made one line."""

SUBJECT_WARNING_LENGTH = 60
PREHEADER_WARNING_LENGTH = 100
SIZE_WARNING_BYTES = 104_448
"""Gmail clips a message's HTML past about 102 KB."""


def _one_line(text: str, name: str) -> str:
    line = " ".join(text.split())
    if len(line) > SUBJECT_LIMIT:
        raise ValidationError(
            "The rendered %(name)s is over %(limit)s characters.",
            code="subject_too_long", params={"name": name, "limit": SUBJECT_LIMIT},
        )
    return line


def _plain(text: str) -> str:
    lines = [line.rstrip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def _engine(walk: TemplateWalk, form: str, *, mail_globals: Mapping[str, Any]) -> MessageTemplateEngine:
    loader = (f"{__name__}.MessageTemplateLoader", walk.forms, form)
    return MessageTemplateEngine(
        autoescape=form == "html",
        loaders=[("django.template.loaders.cached.Loader", [loader])],
        mail_globals=mail_globals,
    )


def render_message(
    walk: TemplateWalk, values: Mapping[str, Any], *, kit: Any, frame: PreviewFrame | None = None,
) -> RenderedMessage:
    """Render the row ``walk`` reached with ``values``, the mail colours from ``kit``.

    Passes, each in ``translation.override`` of the row's language and its own
    :class:`~angee.messaging_templates.engine.RenderBudget`: (1)
    :meth:`~angee.messaging_templates.walker.TemplateWalk.prepare` and the globals
    ``brand``, ``lang`` and ``dir``; (2) the subject and preheader on the text
    engine, each made one line; (3) the HTML body on the HTML engine, the subject
    and preheader at its top level; (4) the plain-text body on the text engine,
    from the rows' text forms. ``frame`` renders a layout around a fixed body,
    subject and preheader. Raises ``ValidationError``.
    """

    language = walk.language
    with translation.override(language):
        prepared = walk.prepare(values)
        mail_globals = {
            "brand": brand_values(kit), "lang": language, "dir": "rtl" if translation.get_language_bidi() else "ltr",
        }
        text_engine = _engine(walk, "text", mail_globals=mail_globals)
        html_engine = _engine(walk, "html", mail_globals=mail_globals)
        root = walk.root
        with RenderBudget().spend():
            subject = _one_line(
                text_engine.from_string(frame.subject if frame else str(root.subject)).render(
                    text_engine.context(prepared)), "subject",
            )
            preheader = _one_line(
                text_engine.from_string(frame.preheader if frame else str(root.preheader)).render(
                    text_engine.context(prepared)), "preheader",
            )
        html_root = (
            html_engine.from_string(in_layout(walk.root_name, frame.html)) if frame
            else html_engine.get_template(walk.root_name)
        )
        with RenderBudget().spend():
            html = html_root.render(html_engine.context(prepared, extra={"subject": subject, "preheader": preheader}))
        text_root = (
            text_engine.from_string(in_layout(walk.root_name, frame.text)) if frame
            else text_engine.get_template(walk.root_name)
        )
        with RenderBudget().spend():
            text = text_root.render(text_engine.context(prepared))
    return RenderedMessage(subject=subject, preheader=preheader, html=html, plain_text=_plain(text), language=language)


def preview_warnings(
    walk: TemplateWalk, rendered: RenderedMessage, sample: Mapping[str, Any], *, carries_message_fields: bool,
) -> list[TemplateWarning]:
    """Return what a preview of ``rendered``, the row's ``sample`` rendered, warns about.

    The subject and preheader are judged on a row that carries them; every row is
    judged on its own unused ``variables``, the stop link it declares, its plain
    text and its size, raw or quoted-printable.
    """

    warnings: list[TemplateWarning] = []
    if carries_message_fields and len(rendered.subject) > SUBJECT_WARNING_LENGTH:
        warnings.append(TemplateWarning(
            TemplateWarningCode.SUBJECT_LENGTH, f"The subject is {len(rendered.subject)} characters long.",
        ))
    if carries_message_fields and not 0 < len(rendered.preheader) <= PREHEADER_WARNING_LENGTH:
        warnings.append(TemplateWarning(
            TemplateWarningCode.PREHEADER, f"The preheader is {len(rendered.preheader)} characters long.",
        ))
    warnings.extend(
        TemplateWarning(TemplateWarningCode.UNUSED_VARIABLE, f"Nothing reads {path}.") for path in walk.unused
    )
    if "unsubscribe_url" in walk.root.variables:
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
