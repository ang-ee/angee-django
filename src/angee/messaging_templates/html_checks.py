"""What an HTML form of a message template may hold, so that a value never becomes markup.

:func:`check_html` reads one HTML form (a row's HTML source, a Markdown row's
HTML form, the markup inside an Outlook comment) with its template spans read as
placeholders, and returns what a mail may not carry; the walker calls it on every
row it checks. Promise 1, a value never becomes markup, an attribute, a link
target or CSS, holds because every list a row or a value varies along is closed.
This module owns the HTML lines of that table (the spelling, the elements and
attributes, where a value sits, an author's links and the character set); the
filters and their order are the engine's and the walker's row rules, a value's
characters :meth:`~angee.messaging_templates.walker.TemplateWalk.prepare`'s and
the schemes :func:`~angee.messaging_templates.engine.url`'s. The addon README's
table of closed lists names the owner and the test of each line.

A form is admitted only when it is written in one strict spelling every HTML
reader tokenises the same way (:class:`SpellingRule`): the text equals the
canonical rebuild of what ``html.parser`` read, event by event, so nothing a
browser could read differently from what was judged gets through. It is a
refusal saying how to write the row, never a rewrite. Each event the parser
reports is located by :meth:`~html.parser.HTMLParser.getpos` and must begin
where the one before it ended, so nothing the parser consumes without reporting
it (``</>``) passes either. Two spellings every reader takes alike stay free:
the case of a tag or attribute name, and the run of spaces, tabs and newlines
between attributes.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Sequence
from enum import IntEnum
from html import escape as escape_html
from html.parser import HTMLParser
from typing import cast

from django.template import TemplateSyntaxError
from django.template.base import DebugLexer, FilterExpression, Token, TokenType, VariableNode

from angee.messaging_templates.engine import LINK_SCHEMES, MAIL_COLOURS, MessageTemplateEngine, filter_names, url

CONTROL_CHARACTERS = "\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
"""The C0 and C1 control characters but tab, line feed and carriage return, as the body of a character class. A
row's text holds none, nor a carriage return (rule 1, :data:`SOURCE_CONTROLS`); a value drops them
(:meth:`~angee.messaging_templates.walker.TemplateWalk.prepare`)."""

SOURCE_CONTROLS = re.compile(f"[{CONTROL_CHARACTERS}\r]")
"""What a row's text and its forms may not hold (rule 1): a NUL would forge the checks' placeholders, an escape could
switch a reader's character set, and a carriage return gives one text two content hashes."""

MAIL_CHARSET = "utf-8"
"""The one character set a mail's HTML declares, and the one it is sent in."""

STYLE_ELEMENT_OUTPUTS = frozenset(("brand", *path) for path in MAIL_COLOURS)
"""The outputs a ``<style>`` element may hold, without filters: the mail colours."""

STYLE_OUTPUTS = STYLE_ELEMENT_OUTPUTS | {("brand", "font", "heading"), ("brand", "font", "body"), ("dir",)}
"""The outputs a ``style`` attribute may hold, without filters: a mail colour, a font stack, the direction."""


class SpellingRule(IntEnum):
    """The strict spelling of an HTML form, one rule each, with the refusal that says how to write the row."""

    NO_CONTROL = 1
    LESS_THAN = 2
    START_TAG = 3
    SELF_CLOSING = 4
    END_TAG = 5
    TEMPLATE_CODE = 6
    ELEMENTS = 7
    ENDS_IN_TEXT = 8

    @property
    def message(self) -> str:
        """How a row is written to meet this rule."""

        return SPELLING[self]


SPELLING: dict[SpellingRule, str] = {
    SpellingRule.NO_CONTROL: (
        "a row holds no control character but tab and newline, a carriage return written as a newline"
    ),
    SpellingRule.LESS_THAN: (
        "a < opens a start tag, an end tag, and in a layout its opening <!doctype html> or Outlook's comments; "
        "anywhere else write &lt;"
    ),
    SpellingRule.START_TAG: (
        "a start tag is written plainly: a name of letters and digits, each attribute once with a name of letters, "
        "digits, -, _, : or ., every value in double quotes with &amp;, &lt;, &gt; and &quot; for those "
        "characters, spaces, tabs or newlines between attributes and nothing else"
    ),
    SpellingRule.SELF_CLOSING: "only a void element (br, hr, img, meta…) closes itself with />",
    SpellingRule.END_TAG: "an end tag is </name> and nothing else",
    SpellingRule.TEMPLATE_CODE: (
        "template code sits in text or inside a double-quoted attribute value, never in a tag, an end tag, a "
        "declaration or a comment"
    ),
    SpellingRule.ELEMENTS: (
        "a mail is written with the document, table, text, link, list and image elements alone (MAIL_ELEMENTS), "
        "and in a layout's Outlook comment Office's settings"
    ),
    SpellingRule.ENDS_IN_TEXT: "a form ends in text, outside any tag, comment or <style> or <title> element",
}
"""The refusal of each :class:`SpellingRule`, stated once."""

MAIL_ELEMENTS = frozenset({
    "html", "head", "body", "meta", "title", "style",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr", "center",
    "span", "a", "b", "strong", "i", "em", "u", "s", "small", "sub", "sup", "code", "br", "font",
    "ul", "ol", "li",
    "img",
})
"""The elements a mail is written with (rule 7): the document, tables, block and inline text, links, lists and
images, as the shipped rows, the Markdown pass and hand-written mail markup use them. ``title`` and ``style`` are the
only ones that change how a reader reads what follows. Frames, forms and their controls, ``base``, ``link``,
scripts, embedded and foreign content are not among them."""

OFFICE_ELEMENTS = frozenset({"xml", "o:officedocumentsettings", "o:allowpng", "o:pixelsperinch"})
"""What Word reads inside Outlook's conditional comments, and only there: the ``xml`` island and its settings."""

VOID_ELEMENTS = frozenset({"br", "hr", "img", "meta"})
"""The elements of :data:`MAIL_ELEMENTS` that have no content, which alone close themselves with ``/>`` (rule 4)."""

MAIL_ATTRIBUTES = frozenset({
    "class", "id", "style", "title", "lang", "xml:lang", "dir", "role", "aria-label", "aria-hidden",
    "aria-roledescription",
    "width", "height", "align", "valign", "bgcolor", "background", "border", "cellpadding", "cellspacing", "colspan",
    "rowspan", "nowrap", "scope",
    "href", "target", "rel", "src", "alt", "cite",
    "xmlns", "xmlns:v", "xmlns:o", "name", "content", "charset", "type", "media",
    "color", "face", "size", "start",
})
"""The attributes a mail is written with; every other one, ``on*`` and ``http-equiv`` among them, is refused by
name."""

LINK_ATTRIBUTES = frozenset({"href", "src", "background", "cite"})
"""The attributes of :data:`MAIL_ATTRIBUTES` a reader follows as a link: one ``url`` output, or a static link."""

STATIC_ANCHOR = re.compile(r"(?:#|cid:)[A-Za-z0-9._~!$&'()*+,;=:@/?%-]*")
"""What a static link may be besides a link ``url`` leaves unchanged: a fragment of the mail, or a ``cid:`` image
the mail carries."""

STATIC_LINKS = f"an absolute {', '.join(LINK_SCHEMES[:-1])} or {LINK_SCHEMES[-1]} link, a #fragment or a cid: image"

OUTPUT_ATTRIBUTES = frozenset({"alt", "title", "aria-label", "lang", "xml:lang", "dir"})
"""The text attributes an output may write."""

CONDITION_TAGS = frozenset({"if", "elif", "else", "endif"})

MSO_COMMENT = re.compile(r"\[if mso\]>(?P<markup>.*)<!\[endif\]", re.DOTALL)
"""The data of ``<!--[if mso]>…<![endif]-->``: markup classic Outlook alone renders."""
REVEALED_OPENS = "[if !mso]><!"
"""The data of ``<!--[if !mso]><!-->``, which opens markup every client but classic Outlook renders."""
REVEALED_CLOSES = "<![endif]"
"""The data of ``<!--<![endif]-->``, which closes it."""
DOCTYPE = "doctype html"
"""The one declaration, a layout's opening ``<!doctype html>``."""

NOTE = "write a note as {# … #}, which no mail carries"
"""What an author writes where an HTML comment or a processing instruction was meant as a note."""

VECTOR_MARKUP = ("v:", "w:")
"""Office's vector and Word markup, which a mail does not carry: a button drawn with it would need a value inside a
comment, where no value goes."""

_ELEMENT_NAME = re.compile(r"[a-z][a-z0-9]*(?::[a-z][a-z0-9]*)?")
_ATTRIBUTE_NAME = re.compile(r"[a-z_:][a-z0-9_:.-]*")
_GAP = " \t\n"
"""The characters a gap between attributes is a run of: spaces, tabs and newlines."""
_CODE_IN_TAG = re.compile(r"<[/!?]?(?:[a-zA-Z][^\t\n\r\f />\x00]*)?\x00")
"""Where a ``<`` the parser read as text names a tag that template code continues: rule 6, not rule 2."""
_PLACEHOLDER = re.compile("\x00(\\d+)\x00")


def _written(value: str) -> str:
    """An attribute value as the strict spelling writes it: ``&``, ``<``, ``>`` and ``"`` escaped."""

    return escape_html(value, quote=False).replace('"', "&quot;")


def _canonical(written: str, tag: str, attrs: list[tuple[str, str | None]], *, closing: bool) -> bool:
    """Whether ``written`` is the canonical rebuild of the start tag ``html.parser`` read, the case of names and the
    gaps free: matched in one pass along what the parser read, with no pattern compiled per tag.

    A name is matched as written in ASCII alone, so a character that only lower-cases to an ASCII letter (the
    Kelvin sign) is no name; a value is matched as :func:`_written` writes it.
    """

    at = 1 + len(tag)
    if not (written.startswith("<") and written[1:at].isascii() and written[1:at].lower() == tag):
        return False
    for name, value in attrs:
        gap = at
        while written[at:at + 1] and written[at] in _GAP:
            at += 1
        end = at + len(name)
        if at == gap or not written[at:end].isascii() or written[at:end].lower() != name:
            return False
        at = end
        if value is not None:
            quoted = '="' + _written(value) + '"'
            if not written.startswith(quoted, at):
                return False
            at += len(quoted)
    rest = written[at:].lstrip(_GAP) if closing else written[at:]
    return rest == ("/>" if closing else ">")


class _HtmlChecker(HTMLParser):
    """Read an HTML form whose template spans are placeholders, and record what a mail may not carry.

    ``text`` is what it is fed. ``document`` admits what a layout alone writes:
    its opening doctype and Outlook's conditional comments; ``office`` reads the
    markup inside one of those.
    """

    def __init__(
        self, spans: Sequence[Token], expression: Callable[[str], FilterExpression | None], text: str, *,
        document: bool = False, office: bool = False,
    ) -> None:
        super().__init__(convert_charrefs=False)
        self.spans = spans
        self.expression = expression
        self.text = text
        self.line_starts = [0, *(index + 1 for index, character in enumerate(text) if character == "\n")]
        self.document = document
        self.office = office
        self.revealed = False
        self.following: int | None = 0
        self.closing = False
        self.refusals: list[str] = []

    def refuse(self, reason: str) -> None:
        self.refusals.append(reason)

    def broken(self, rule: SpellingRule, what: str) -> None:
        """Refuse ``what`` for breaking ``rule`` of the strict spelling."""

        self.refuse(f"{what}: {rule.message} (rule {rule.value})")

    def event(self, length: int | None) -> int | None:
        """Locate the event the parser reports; it must begin where the one before ended. ``None``: not checked.

        ``length`` is the event's length in the text when its spelling is known, so
        the next event's start can be held to it; ``None`` after a refused one.
        """

        if self.closing:
            return None
        line, column = self.getpos()
        start = self.line_starts[line - 1] + column
        if self.following is not None and start > self.following:
            self.broken(SpellingRule.LESS_THAN, f"{self.text[self.following:start][:20]!r} reads as nothing")
        self.following = None if length is None else start + length
        return start

    def written(self, start: int, end: str = ">") -> str:
        """The author's own text from ``start`` through the next ``end``, quoted, cut after 60 characters."""

        stop = self.text.find(end, start)
        text = self.text[start:] if stop < 0 else self.text[start:stop + len(end)]
        return repr(text if len(text) <= 60 else text[:59] + "…")

    def misplaced(self, what: str, advice: str) -> None:
        """Refuse ``what``, a ``<`` that opens no tag a mail holds (rule 2), saying what to write instead."""

        self.refuse(f"{what}: {advice} (rule {SpellingRule.LESS_THAN.value})")

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

    # -- tags -------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.check_tag(tag, attrs, closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.check_tag(tag, attrs, closing=True)

    def check_tag(self, tag: str, attrs: list[tuple[str, str | None]], *, closing: bool) -> None:
        written = self.get_starttag_text() or ""
        if self.event(len(written)) is None:
            return
        if "\x00" in tag or any("\x00" in name for name, _ in attrs):
            return self.broken(SpellingRule.TEMPLATE_CODE, f"template code in the tag {written[:40]!r}")
        names = [name for name, _ in attrs]
        if "<" in written[1:]:
            self.broken(SpellingRule.LESS_THAN, f"a < in the tag {written[:60]!r}")
        elif (
            not _ELEMENT_NAME.fullmatch(tag) or not all(_ATTRIBUTE_NAME.fullmatch(name) for name in names)
            or len(set(names)) != len(names) or not _canonical(written, tag, attrs, closing=closing)
        ):
            self.broken(SpellingRule.START_TAG, f"the tag {written[:60]!r}")
        office = self.office and tag in OFFICE_ELEMENTS
        if closing and tag not in VOID_ELEMENTS and not office and not tag.startswith(VECTOR_MARKUP):
            self.broken(SpellingRule.SELF_CLOSING, f"<{tag}/>")
        if tag.startswith(VECTOR_MARKUP):
            self.refuse(
                f"<{tag}> is Office vector markup, which a mail does not carry: a button drawn with it would need a "
                f"value inside a comment, so write the button as a link (rule {SpellingRule.ELEMENTS.value})",
            )
        elif tag not in MAIL_ELEMENTS and not office:
            self.broken(SpellingRule.ELEMENTS, f"<{tag}>")
        for name, raw_value in attrs:
            value = raw_value or ""
            if name not in MAIL_ATTRIBUTES:
                self.refuse(f"the {name} attribute is not one a mail is written with (MAIL_ATTRIBUTES)")
                continue
            if name == "charset" and value.lower() != MAIL_CHARSET:
                self.refuse(f"the charset of <{tag}> is {MAIL_CHARSET}, the one a mail is sent in")
                continue
            ids = self.span_ids(value)
            self.check_conditions(name, ids)
            if name in LINK_ATTRIBUTES:
                self.check_link(tag, name, value, ids)
            elif name == "style":
                self.check_spans(
                    f"the style attribute of <{tag}>", ids, STYLE_OUTPUTS, conditions=True, comments=True,
                )
            else:
                self.check_spans(
                    f"the {name} attribute of <{tag}>", ids, None if name in OUTPUT_ATTRIBUTES else frozenset(),
                    conditions=True, comments=True,
                )
        return None

    def handle_endtag(self, tag: str) -> None:
        start = self.event(len(tag) + 3)
        if start is None:
            return
        if "\x00" in tag:
            self.following = None
            return self.broken(SpellingRule.TEMPLATE_CODE, f"template code in the end tag {self.text[start:][:30]!r}")
        written = self.text[start:start + len(tag) + 3]
        if not _ELEMENT_NAME.fullmatch(tag) or written.lower() != f"</{tag}>":
            self.following = None
            self.broken(SpellingRule.END_TAG, f"the end tag {self.text[start:][:30]!r}")
        return None

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
            sent = url(value)
            if sent == value or STATIC_ANCHOR.fullmatch(value):
                return
            self.refuse(
                f"the {name} of <{tag}> holds a space or a character a link percent-encodes: write {sent}" if sent
                else f"the {name} of <{tag}> is not a link a mail may carry: {STATIC_LINKS}",
            )
            return
        _path, filters = self.output_path(ids[0])
        if value != f"\x00{ids[0]}\x00" or not filters or filters[-1] != "url":
            self.refuse(f"the {name} of <{tag}> is one output whose last filter is url, or a static link")

    def check_spans(
        self, where: str, ids: list[int], outputs: frozenset[tuple[str, ...]] | None, *, conditions: bool,
        comments: bool,
    ) -> None:
        """Refuse a span ``where`` does not admit: outputs in ``outputs`` (``None``: any); ``if`` tags unless
        ``conditions``; ``{# #}`` comments unless ``comments``."""

        for index in ids:
            token = self.spans[index]
            if token.token_type == TokenType.COMMENT and comments:
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

    # -- text -------------------------------------------------------------------

    def handle_data(self, data: str) -> None:
        start = self.event(len(data))
        if start is None:
            return
        if "<" in data:
            at = start + data.index("<")
            opens_code = _CODE_IN_TAG.match(self.text, at) is not None
            self.broken(
                SpellingRule.TEMPLATE_CODE if opens_code else SpellingRule.LESS_THAN,
                f"{self.text[at:at + 20]!r}" + (f" in <{self.cdata_elem}>" if self.cdata_elem else ""),
            )
        if self.cdata_elem == "style":
            self.check_spans(
                "a <style> element", self.span_ids(data), STYLE_ELEMENT_OUTPUTS, conditions=False, comments=False,
            )
        elif self.cdata_elem == "title":
            # No if: a branch could leave out </title> and hold the rest of the mail in the title.
            self.check_spans("a <title> element", self.span_ids(data), None, conditions=False, comments=True)

    def handle_entityref(self, name: str) -> None:
        self.character_reference(1 + len(name))

    def handle_charref(self, name: str) -> None:
        self.character_reference(2 + len(name))

    def character_reference(self, length: int) -> None:
        """Hold a character reference's spelling: ``&name`` or ``&#…``, its ``;`` optional; it is text."""

        start = self.event(None)
        if start is not None:
            self.following = start + length + (self.text[start + length:start + length + 1] == ";")

    # -- what a layout alone writes ------------------------------------------

    def handle_comment(self, data: str) -> None:
        """Admit, in a layout, Outlook's conditional comments alone: static, balanced, their markup checked."""

        start = self.event(len(data) + 7)
        if start is None:
            return None
        if not self.text.startswith(f"<!--{data}-->", start):
            self.following = None
            return self.broken(SpellingRule.LESS_THAN, f"the comment {self.text[start:][:30]!r}")
        if "\x00" in data:
            return self.broken(SpellingRule.TEMPLATE_CODE, "template code in an HTML comment")
        if not self.document:
            return self.misplaced(
                f"the comment {self.written(start, '-->')}", f"{NOTE}; a layout alone holds Outlook's comments",
            )
        if data in (REVEALED_OPENS, REVEALED_CLOSES):
            if self.revealed is (data == REVEALED_OPENS):
                return self.refuse("<!--[if !mso]><!--> and <!--<![endif]--> come in pairs, one pair at a time")
            self.revealed = data == REVEALED_OPENS
            return None
        if (comment := MSO_COMMENT.fullmatch(data)) is None:
            return self.misplaced(
                f"the comment {self.written(start, '-->')}",
                f"{NOTE}; a layout holds Outlook's <!--[if mso]>…<![endif]--> and the "
                "<!--[if !mso]><!-->…<!--<![endif]--> pair",
            )
        if self.revealed:
            return self.refuse("an Outlook comment sits inside <!--[if !mso]><!-->…<!--<![endif]-->")
        self.refusals.extend(
            f"in an Outlook comment: {refusal}"
            for refusal in _checked(_HtmlChecker([], self.expression, comment["markup"], office=True))
        )
        return None

    def handle_decl(self, decl: str) -> None:
        start = self.event(len(decl) + 3)
        if start is None:
            return
        if "\x00" in decl:
            self.following = None
            self.broken(SpellingRule.TEMPLATE_CODE, "template code in a declaration")
        elif not (self.document and start == 0 and decl.lower() == DOCTYPE):
            self.following = None
            self.misplaced(
                f"the declaration {self.written(start)}",
                "a layout opens with <!doctype html>, the one declaration a mail holds",
            )

    def unknown_decl(self, data: str) -> None:
        if (start := self.event(None)) is not None:
            self.misplaced(
                f"the marked section {self.written(start)}", "a mail holds none; write its text with &lt; and &amp;",
            )

    def handle_pi(self, data: str) -> None:
        if (start := self.event(None)) is not None:
            self.misplaced(f"the processing instruction {self.written(start)}", f"a mail holds none; {NOTE}")


def check_html(
    form: str, expression: Callable[[str], FilterExpression | None], *, document: bool = False,
) -> list[str]:
    """Return what an HTML form holds that a mail may not, its template spans read as placeholders.

    Refused: what breaks a :class:`SpellingRule`, an element outside
    :data:`MAIL_ELEMENTS` (rule 7) but Office's settings inside Outlook's
    comments; an attribute outside :data:`MAIL_ATTRIBUTES`; an ``if`` an
    attribute value does not hold whole; in an attribute of
    :data:`LINK_ATTRIBUTES` anything but one output whose last filter is ``url``,
    or a static link :func:`~angee.messaging_templates.engine.url` leaves as
    ``html.parser`` decodes it or a :data:`STATIC_ANCHOR`; in ``style`` an output but :data:`STYLE_OUTPUTS`;
    in a ``<style>`` element a span but :data:`STYLE_ELEMENT_OUTPUTS`; in a
    ``<title>`` element or any other attribute an output outside
    :data:`OUTPUT_ATTRIBUTES` (``<title>``: any) or a tag other than an ``if``
    family one. ``document`` (a layout) admits the opening ``<!doctype html>`` and
    Outlook's ``<!--[if mso]>…<![endif]-->`` and
    ``<!--[if !mso]><!-->…<!--<![endif]-->``, balanced, static, the markup of the
    first held to this same contract with :data:`OFFICE_ELEMENTS` admitted
    there alone. ``expression`` compiles one output span to its
    ``FilterExpression``.
    """

    if SOURCE_CONTROLS.search(form):
        return [f"the form: {SpellingRule.NO_CONTROL.message} (rule {SpellingRule.NO_CONTROL.value})"]
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
    checker = _HtmlChecker(spans, expression, "".join(parts), document=document)
    refusals = _checked(checker)
    if checker.revealed:
        refusals.append("<!--[if !mso]><!--> is never closed with <!--<![endif]-->")
    return list(dict.fromkeys(refusals))


def _checked(checker: _HtmlChecker) -> list[str]:
    """Feed ``checker`` its text and return its refusals; the text ends in text (rule 8).

    ``html.parser`` holds back an unfinished tag at the end of its input and never
    closes a raw-text element, while the next row's text would continue it. What it
    holds back may be a character reference (``AT&T``), which is text.
    """

    checker.feed(checker.text)
    if checker.cdata_elem:
        checker.broken(SpellingRule.ENDS_IN_TEXT, f"it ends inside <{checker.cdata_elem}>")
    elif checker.rawdata == "<":
        checker.broken(SpellingRule.LESS_THAN, "it ends in <")
    elif checker.rawdata and not checker.rawdata.startswith("&"):
        checker.broken(SpellingRule.ENDS_IN_TEXT, f"it ends inside {checker.rawdata[:20]!r}")
    checker.closing = True
    checker.close()
    return checker.refusals


def compiled_expression(engine: MessageTemplateEngine) -> Callable[[str], FilterExpression | None]:
    """Return a function compiling one output span in ``engine`` to its ``FilterExpression``, each distinct span once
    for the function's life (a walker's)."""

    @functools.cache
    def expression(span: str) -> FilterExpression | None:
        try:
            nodelist = engine.from_string(span).nodelist
        except TemplateSyntaxError:
            return None
        node = nodelist[0] if len(nodelist) == 1 else None
        return node.filter_expression if type(node) is VariableNode else None

    return expression
