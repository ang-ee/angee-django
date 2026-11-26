"""A message template's markup and declared values: the format, its forms, and the grammar of ``variables``.

:class:`MessageTemplateFormat` and :func:`validate_variables` live here, in a
module that imports nothing else of this addon and reads no database, so the
model, the walker and any other reader of a row's declared values import one
owner without a cycle. A host's first migration records ``validate_variables``
by this module's path (the format's choices it writes as literals), so this
module imports only Django and markdown-it.

A row's markup decides its two **forms**, through :attr:`MessageTemplateFormat.handler`:
the HTML form and the plain-text form the engine compiles (:class:`RowForms`). An
HTML row's forms are its ``source`` and ``text_source``. A Markdown row goes
through the **Markdown pass** first (markdown-it, raw HTML off, link reference
definitions off), which keeps every Django template span whole and renders it
verbatim; its plain-text form is a text rendering of the same tokens, never
scraped from the HTML. The walker (:mod:`angee.messaging_templates.walker`)
judges the forms and renders them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from itertools import islice
from typing import Any, ClassVar, NamedTuple, Protocol, cast

from django.core.exceptions import ValidationError
from django.db import models
from django.template.base import DebugLexer, TokenType, tag_re
from markdown_it import MarkdownIt
from markdown_it.helpers import parseLinkLabel
from markdown_it.renderer import RendererHTML
from markdown_it.rules_core import StateCore
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token as MarkdownToken
from markdown_it.utils import EnvType, OptionsDict


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

MAX_VARIABLE_PATH_LENGTH = 200
"""The most characters an author's declared path holds."""

MAX_VARIABLE_DESCRIPTION_LENGTH = 1_000
"""The most characters an author's description of a declared path holds: with :data:`MAX_VARIABLE_PATH_LENGTH`, a
row's own declaration holds at most about 60,400 characters as JSON, under the walk's ``WALK_VARIABLES_LIMIT``."""

_VARIABLE_PATH = re.compile(r"[a-z][a-z0-9_]*(?:\.(?:[a-z][a-z0-9_]*|\*))*")


def validate_variables(variables: Any, *, max_paths: int = 50, lengths: bool = True) -> None:
    """Refuse ``variables`` that are not an object of declared paths to their descriptions.

    Each key is a dotted path of at most :data:`MAX_VARIABLE_SEGMENTS` lower-case
    names (``order.total``) whose later segments may be ``*``, every item of a list
    (``tags.*`` is a list of scalars); each value is a string describing it. With
    ``lengths``, as an author's row is judged, a path holds at most
    :data:`MAX_VARIABLE_PATH_LENGTH` characters and a description at most
    :data:`MAX_VARIABLE_DESCRIPTION_LENGTH`; a walk judges a stored row's grammar
    without them, so a row stored longer still renders, its declaration counted by
    the walk's own cap. There
    are at most ``max_paths`` keys: a row's own declaration takes the default, and a
    caller judging several rows' declarations together passes its own limit. No
    path is a proper prefix of another (``order`` and ``order.total`` are not both
    declared), no path reads one value both as a list and as an object
    (``items.*`` beside ``items.pop``), and no path starts with a name in
    :data:`RESERVED_VARIABLE_ROOTS`. The first offending key is named. The check
    takes time linear in the number and length of the keys: every save through
    ``full_clean()`` runs it on what its author wrote, and every walk runs it on each
    row it reaches, rows stored past ``clean()`` among them.
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
        if lengths and isinstance(path, str) and len(path) > MAX_VARIABLE_PATH_LENGTH:
            raise ValidationError(
                "%(path)s… is over %(limit)s characters: name the value in fewer.",
                code="variable_path_too_long", params={"path": path[:40], "limit": MAX_VARIABLE_PATH_LENGTH},
            )
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
        if lengths and len(description) > MAX_VARIABLE_DESCRIPTION_LENGTH:
            raise ValidationError(
                "Describe %(path)s in at most %(limit)s characters.",
                code="variable_description_too_long", params={"path": path, "limit": MAX_VARIABLE_DESCRIPTION_LENGTH},
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


# ---------- the forms a row gives ----------


class RowForms(NamedTuple):
    """The two forms of a row the engine compiles: the HTML body's and the plain-text body's."""

    html: str
    text: str


class RowRefusal(ValueError):
    """A row's source is refused before its forms are compiled; the message names why."""


class FormedRow(Protocol):
    """What a markup reads of a row to give its forms."""

    source: str
    text_source: str


class MessageTemplateFormatHandler:
    """What one markup is: the forms a row's source gives and what it refuses before they compile."""

    def source_refusals(self, source: str) -> list[str]:
        """Return what this markup refuses in ``source`` before anything parses it."""

        del source
        return []

    def forms(self, row: FormedRow) -> RowForms:
        """Return ``row``'s forms; raise :class:`RowRefusal` for a source this markup refuses."""

        raise NotImplementedError


class HtmlFormatHandler(MessageTemplateFormatHandler):
    """An HTML row is its own HTML form; its ``text_source`` is the plain-text form."""

    def forms(self, row: FormedRow) -> RowForms:
        """Return ``source`` and ``text_source`` as they are."""

        return RowForms(html=str(row.source), text=str(row.text_source))


MARKDOWN_BRACKETS_PER_LINE = 30
"""The most ``[`` a line of Markdown holds: markdown-it scans each link label to the end of its line."""

MARKDOWN_LABEL_SCAN_LIMIT = 50_000
"""The most characters a row's link and image labels may make the Markdown pass scan: for each ``[`` (a ``![`` among
them), the characters after it in its paragraph, list item or heading, where markdown-it's label scan stops at the
latest, walking it token by token. A row's cost grows with that sum, not with its brackets or its length: one ``[``
before a paragraph dense with emphasis delimiters re-reads all of it. 50,000 is one such full paragraph."""

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
        """Refuse a line holding over :data:`MARKDOWN_BRACKETS_PER_LINE` ``[``, then a row whose label scans would
        cover over :data:`MARKDOWN_LABEL_SCAN_LIMIT` characters, found from markdown-it's block pass alone."""

        refusals = [
            f"a Markdown line holds over {MARKDOWN_BRACKETS_PER_LINE} [ (line {number})"
            for number, line in enumerate(source.split("\n"), 1) if line.count("[") > MARKDOWN_BRACKETS_PER_LINE
        ][:1]
        if refusals or "[" not in source:
            return refusals
        blocks: list[MarkdownToken] = []
        self.parser.block.parse(source, self.parser, {}, blocks)
        scanned = 0
        for token in blocks:
            content, index = token.content if token.type == "inline" else "", -1
            while (index := content.find("[", index + 1)) >= 0:
                scanned += len(content) - index - 1
        if scanned > MARKDOWN_LABEL_SCAN_LIMIT:
            return [f"the [ of this Markdown row open link or image labels the Markdown pass would scan across "
                    f"{scanned} characters, over {MARKDOWN_LABEL_SCAN_LIMIT}: put a link in a shorter paragraph, "
                    "or write a literal [ as &#91;"]
        return []

    def forms(self, row: FormedRow) -> RowForms:
        """Return the styled HTML render and the text render of one parse of ``row.source``."""

        source = str(row.source)
        env: dict[str, Any] = {}
        tokens = self.parser.parse(source, env)
        refusals: list[str] = list(env.get("refusals", ()))
        unstyled = self.parser.renderer.render(tokens, self.parser.options, {})
        html = self.parser.renderer.render(tokens, self.parser.options, {"styled": True})
        text = _MarkdownText().write(tokens)
        refusals.extend(self._span_refusals(source, text, unstyled))
        refusals.extend(self._token_refusals(tokens))
        refusals.extend(self._pair_refusals(tokens))
        if refusals:
            raise RowRefusal("; ".join(dict.fromkeys(refusals)))
        return RowForms(html=html, text=text)

    @staticmethod
    def _span_refusals(source: str, text: str, html: str) -> Iterator[str]:
        """Refuse template spans the forms do not keep whole.

        The text form keeps the source's spans unchanged and in order, a link's
        destination output taking ``|url``. The HTML form holds the same spans: a
        link writes its destination in its tag, before its label, so
        ``[{{ label }}]({{ link }})`` keeps both, in another order.
        """

        expected, written = _spans(source), _spans(text)
        if len(written) != len(expected) or not all(map(_same_span, expected, written)):
            changed = next(
                (f"{left} became {right}" for left, right in zip(expected, written, strict=False)
                 if not _same_span(left, right)),
                "a span was added or lost",
            )
            yield f"template code in the Markdown does not reach the template whole ({changed})"
        elif sorted(_spans(html)) != sorted(written):
            yield "template code in the Markdown does not reach the template whole (its HTML adds, loses or alters one)"

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
