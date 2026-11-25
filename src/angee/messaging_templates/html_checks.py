"""What an HTML form of a message template may hold, so that a value never becomes markup.

:func:`check_html` reads one HTML form (a row's HTML source, a Markdown row's
HTML form, the markup inside an Outlook comment) with its template spans read as
placeholders, and returns what a mail may not carry. Property 1's static side, a
value never becomes markup, an attribute, a link target or CSS, is this
module's alone; the walker calls it on every row it checks.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from html import escape as escape_html
from html.parser import HTMLParser
from typing import cast

from django.template import TemplateSyntaxError
from django.template.base import DebugLexer, FilterExpression, Token, TokenType, VariableNode

from angee.messaging_templates.engine import MAIL_COLOURS, MessageTemplateEngine, filter_names, refused_link, url

STYLE_ELEMENT_OUTPUTS = frozenset(("brand", *path) for path in MAIL_COLOURS)
"""The outputs a ``<style>`` element may hold, without filters: the mail colours."""

STYLE_OUTPUTS = STYLE_ELEMENT_OUTPUTS | {("brand", "font", "heading"), ("brand", "font", "body"), ("dir",)}
"""The outputs a ``style`` attribute may hold, without filters: a mail colour, a font stack, the direction."""


REFUSED_ELEMENTS = frozenset({
    "script", "iframe", "object", "embed", "form", "link", "base", "svg", "math",
    # Raw text whose markup one reader parses and another does not; noscript turns on scripting.
    "noscript", "xmp", "noembed", "noframes", "plaintext", "textarea",
})
REFUSED_ATTRIBUTES = frozenset(
    {"http-equiv", "srcset", "background", "poster", "action", "formaction", "xlink:href", "cite"},
)
LINK_ATTRIBUTES = frozenset({"href", "src"})
OUTPUT_ATTRIBUTES = frozenset({"alt", "title", "aria-label", "lang", "xml:lang", "dir"})
CONDITION_TAGS = frozenset({"if", "elif", "else", "endif"})

OFFICE_ELEMENTS = re.compile(r"xml|o:[a-z]+")
"""What Word reads inside Outlook's conditional comments: the ``xml`` island and Office (``o:``) settings."""

MSO_COMMENT = re.compile(r"\[if mso\]>(?P<markup>.*)<!\[endif\]", re.DOTALL)
"""The data of ``<!--[if mso]>…<![endif]-->``: markup classic Outlook alone renders."""
REVEALED_OPENS = "[if !mso]><!"
"""The data of ``<!--[if !mso]><!-->``, which opens markup every client but classic Outlook renders."""
REVEALED_CLOSES = "<![endif]"
"""The data of ``<!--<![endif]-->``, which closes it."""

_PLACEHOLDER = re.compile("\x00(\\d+)\x00")
_SPAN_OPENS_TAG = re.compile("<[/!?]?\x00")


def _written(value: str) -> str:
    """An attribute value as a start tag holding template code writes it: ``&``, ``<``, ``>`` and ``"`` escaped."""

    return escape_html(value, quote=False).replace('"', "&quot;")


class _HtmlChecker(HTMLParser):
    """Read an HTML form whose template spans are placeholders, and record what a mail may not carry.

    ``text`` is what it is fed. ``conditional_comments`` admits Outlook's
    conditional comments (a layout's); ``office`` reads the markup inside one.
    """

    def __init__(
        self, spans: Sequence[Token], expression: Callable[[str], FilterExpression | None], text: str, *,
        conditional_comments: bool = False, office: bool = False,
    ) -> None:
        super().__init__(convert_charrefs=False)
        self.spans = spans
        self.expression = expression
        self.text = text
        self.line_starts = [0, *(index + 1 for index, character in enumerate(text) if character == "\n")]
        self.conditional_comments = conditional_comments
        self.office = office
        self.revealed = False
        self.refusals: list[str] = []

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
        return (tuple(lookups) if lookups else None), filter_names(expression)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.check_tag(tag, attrs, endings=(">",))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.check_tag(tag, attrs, endings=("/>", " />"))

    def check_tag(self, tag: str, attrs: list[tuple[str, str | None]], *, endings: tuple[str, ...]) -> None:
        if "\x00" in tag:
            return self.refuse("template code in a tag name")
        if tag in REFUSED_ELEMENTS:
            self.refuse(f"<{tag}> is not allowed in a mail")
        if (":" in tag or tag == "xml") and not (self.office and OFFICE_ELEMENTS.fullmatch(tag)):
            self.refuse(f"<{tag}> is not allowed in a mail: xml and Office settings sit in Outlook's comments")
        if any("\x00" in name or "\x00" in (value or "") for name, value in attrs):
            self.check_spelling(tag, attrs, endings)
        for name, raw_value in attrs:
            value = raw_value or ""
            if "\x00" in name:
                self.refuse(f"template code in an attribute name of <{tag}>")
                continue
            if name in REFUSED_ATTRIBUTES or name.startswith("on"):
                self.refuse(f"the {name} attribute is not allowed in a mail")
                continue
            ids = self.span_ids(value)
            self.check_conditions(name, ids)
            if name in LINK_ATTRIBUTES:
                self.check_link(tag, name, value, ids)
                continue
            static = _PLACEHOLDER.split(value)[::2]
            if any(refused_link(text) for text in (*static, "".join(static))):
                self.refuse(f"the {name} of <{tag}> reaches a scheme a mail may not link to")
            if name == "style":
                self.check_spans(f"the style attribute of <{tag}>", ids, STYLE_OUTPUTS, conditions=True)
            else:
                self.check_spans(
                    f"the {name} attribute of <{tag}>", ids, None if name in OUTPUT_ATTRIBUTES else frozenset(),
                    conditions=True,
                )

    def check_spelling(self, tag: str, attrs: list[tuple[str, str | None]], endings: tuple[str, ...]) -> None:
        """Admit a start tag holding template code only as it is written plainly, so every reader reads it alike.

        Its text equals the canonical rebuild of what ``html.parser`` read: the
        lower-case name, then each attribute once, its value double-quoted with
        ``&``, ``<``, ``>`` and ``"`` escaped, as web forms judge stored markup.
        """

        names = [name for name, _ in attrs]
        rebuilt = "<" + tag + "".join(
            f" {name}" if value is None else f' {name}="{_written(value)}"' for name, value in attrs
        )
        if len(set(names)) != len(names) or (self.get_starttag_text() or "") not in {
            rebuilt + ending for ending in endings
        }:
            self.refuse(
                f"<{tag}> holds template code, so it is written plainly: lower-case names, each attribute once, "
                "each value double-quoted, with &amp;, &lt;, &gt; and &quot; for those characters",
            )

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
        if self.cdata_elem == "style":
            self.check_spans("a <style> element", self.span_ids(data), STYLE_ELEMENT_OUTPUTS, conditions=False)
        elif self.cdata_elem == "title":
            self.check_spans("a <title> element", self.span_ids(data), None, conditions=True)

    def handle_comment(self, data: str) -> None:
        """Admit, in a layout, Outlook's conditional comments alone: static, balanced, their markup checked."""

        if not self.conditional_comments:
            return self.refuse("an HTML comment is not allowed in a mail; a layout alone holds Outlook's")
        line, column = self.getpos()
        if not self.text.startswith(f"<!--{data}-->", self.line_starts[line - 1] + column):
            return self.refuse("an HTML comment is written <!--…-->")
        if "\x00" in data:
            return self.refuse("template code in an HTML comment: Outlook's comments are static")
        if data in (REVEALED_OPENS, REVEALED_CLOSES):
            if self.revealed is (data == REVEALED_OPENS):
                return self.refuse("<!--[if !mso]><!--> and <!--<![endif]--> come in pairs, one pair at a time")
            self.revealed = data == REVEALED_OPENS
            return None
        if (comment := MSO_COMMENT.fullmatch(data)) is None:
            return self.refuse(
                "an HTML comment in a layout is <!--[if mso]>…<![endif]--> or the <!--[if !mso]><!-->…"
                "<!--<![endif]--> pair",
            )
        if self.revealed:
            return self.refuse("an Outlook comment sits inside <!--[if !mso]><!-->…<!--<![endif]-->")
        self.refusals.extend(
            f"in an Outlook comment: {refusal}"
            for refusal in _checked(_HtmlChecker([], self.expression, comment["markup"], office=True))
        )
        return None

    def handle_decl(self, decl: str) -> None:
        if "\x00" in decl or not decl.lower().startswith("doctype"):
            self.refuse("a declaration other than the doctype is not allowed in a mail")

    def unknown_decl(self, data: str) -> None:
        self.refuse("a marked section is not allowed in a mail")

    def handle_pi(self, data: str) -> None:
        self.refuse("a processing instruction is not allowed in a mail")


def check_html(
    form: str, expression: Callable[[str], FilterExpression | None], *, conditional_comments: bool = False,
) -> list[str]:
    """Return what an HTML form holds that a mail may not, its template spans read as placeholders.

    Refused: the elements in :data:`REFUSED_ELEMENTS`; the attributes in
    :data:`REFUSED_ATTRIBUTES` and every ``on*``; an ``if`` an attribute value does
    not hold whole; template code in a tag name or an attribute name; any HTML
    comment but, with ``conditional_comments`` (a layout), Outlook's
    ``<!--[if mso]>…<![endif]-->`` and the ``<!--[if !mso]><!-->…<!--<![endif]-->``
    pair, balanced, with no template code inside, the markup of the first checked
    as any other and ``xml`` and Office (``o:``) settings admitted there alone;
    ``xml`` and namespaced elements anywhere else; a start tag holding template
    code that is not written plainly
    (:meth:`_HtmlChecker.check_spelling`); in ``href`` or ``src`` anything but one
    output whose last filter is ``url``, or a static link
    :func:`~angee.messaging_templates.engine.url` leaves as ``html.parser`` decodes
    it; in any other attribute a static value reaching a scheme ``url`` refuses;
    in ``style`` an output but :data:`STYLE_OUTPUTS`; in a ``<style>`` element a
    span but :data:`STYLE_ELEMENT_OUTPUTS`; in a ``<title>`` element or any other
    attribute an output outside :data:`OUTPUT_ATTRIBUTES` (``<title>``: any) or a
    tag other than an ``if`` family one. A form ends where it started, in text
    (:func:`_checked`). ``expression`` compiles one output span to its
    ``FilterExpression``.
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
    checker = _HtmlChecker(spans, expression, text, conditional_comments=conditional_comments)
    if _SPAN_OPENS_TAG.search(text):
        checker.refuse("template code where a tag starts")
    refusals = _checked(checker)
    if checker.revealed:
        refusals.append("<!--[if !mso]><!--> is never closed with <!--<![endif]-->")
    return list(dict.fromkeys(refusals))


def _checked(checker: _HtmlChecker) -> list[str]:
    """Feed ``checker`` its text and return its refusals; the text ends where it started, in text.

    ``html.parser`` drops an unfinished tag at the end of its input and never
    closes a raw-text element, while the next row's text would continue it.
    """

    checker.feed(checker.text)
    if checker.cdata_elem or checker.rawdata:
        where = f"<{checker.cdata_elem}>" if checker.cdata_elem else repr(checker.rawdata[:20])
        checker.refuse(f"it ends inside {where}: a form ends in text, outside any tag, comment or <style> or <title>")
    checker.close()
    return checker.refusals


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
