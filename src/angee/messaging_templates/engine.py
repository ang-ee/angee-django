"""The restricted Django template engine message templates render on.

:class:`MessageTemplateEngine` is a standalone :class:`django.template.Engine` that
loads none of Django's default builtins and exactly one library, this module's
:data:`register`: Django's own ``if``, ``for``, ``with``, ``comment``, ``block``,
``include``, ``extends`` and ``templatetag`` compile functions and fourteen
filters (:data:`ALLOWED_TAGS`, :data:`ALLOWED_FILTERS`). Every other tag and
filter, ``load`` included, fails at compile. ``include`` and ``extends`` take one
quoted name, and ``include`` always ends with ``only``.

Django sets no limit on a render, so a :class:`RenderBudget` set in
:data:`RENDER_BUDGET` bounds one pass: every loop body, every include and every
filter call charges it, and once it is spent every later charge raises
:class:`RenderBudgetExceeded`, even where an ``if`` operator swallowed the first.
A render with no budget set fails.

:class:`MessageTemplateContext` keeps the globals every row reads (``brand``,
``lang``, ``dir``) through the ``only`` of an include, which otherwise starts an
empty context; :func:`brand_values` builds the ``brand`` global from a brand kit.
:class:`MessageTemplateLoader` serves the engine the forms of the rows one render
reached, by template name. This module imports nothing else of the addon.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from operator import attrgetter
from typing import Any, ClassVar, cast
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.template import (
    Context,
    Engine,
    Library,
    Origin,
    TemplateDoesNotExist,
    TemplateSyntaxError,
    defaultfilters,
    defaulttags,
    loader_tags,
)
from django.template.base import FilterExpression, Node, NodeList, Parser, Token
from django.template.loader_tags import IncludeNode
from django.template.loaders.base import Loader
from django.utils.safestring import SafeData, SafeString
from django.views.decorators.debug import sensitive_variables
from markdown_it import MarkdownIt

from angee.base.origins import PublicOriginUnavailable, configured_public_origin
from angee.brandkit.models import BrandKit, MailRole, MailScheme

ALLOWED_TAGS = ("if", "for", "with", "comment", "block", "include", "extends", "templatetag")
"""The tags a message template may use; ``templatetag`` writes a literal ``{{``, ``{%`` or ``{#``."""

ALLOWED_FILTERS = (
    "default", "upper", "lower", "capfirst", "title", "truncatechars", "truncatewords", "linebreaksbr", "length",
    "date", "floatformat", "pluralize", "yesno", "url",
)
"""The filters a message template may use: Django's own thirteen and :func:`url`."""

MARKUP_FILTERS = frozenset({"linebreaksbr"})
"""The filters whose output is markup: ``linebreaksbr`` escapes what it reads and writes ``<br>``. A markup filter is
the last filter of a printed value (the walker's row rules), and no other filter's result is ever marked safe
(:func:`_charged`). An output is escaped text, a literal (which holds no ``<``, ``>`` or ``"``), or one markup
filter's whole output."""

URL_INPUT_LIMIT = 2_048
"""The longest value :func:`url` reads; a longer one gives no link."""

LINK_SCHEMES = ("https", "http", "mailto", "tel")
"""The schemes of a link :func:`url` gives. A mail has no base, so the link is absolute: ``https`` and ``http``
with a host (:data:`LINK_HOST`), or a ``mailto:`` or ``tel:`` link."""

LINK_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9](?::[0-9]{1,5})?", re.I)
"""The authority of an ``https`` or ``http`` link: a domain name of two labels or more, the last a name, and a port.
No userinfo (``user:pw@good.example@evil.example`` goes to the last host), no address, nothing empty. The
deployment's own public origin is linkable besides, whatever its host (:func:`_own_origin_authority`)."""

FILTER_CHARGE = 100
"""What a filter call charges a pass's ``filter_input``, besides the length of a string a reading filter reads."""

INPUT_READING_FILTERS = frozenset({
    "upper", "lower", "capfirst", "title", "truncatechars", "truncatewords", "linebreaksbr", "floatformat",
    "pluralize", "url",
})
"""The filters whose work grows with their input: they copy, scan or parse the string they read, so each call also
charges its length. ``default``, ``length`` and ``yesno`` hand on or test their input, and ``date`` reads a date, so
they charge :data:`FILTER_CHARGE` alone."""

LINE_BUDGET_SHARE = 10
"""The pass rendering a subject and a preheader spends this fraction of a body pass's budget: two lines of at
most 255 characters of source each, against a body's 50,000."""

RENDER_BUDGET: ContextVar[RenderBudget] = ContextVar("RENDER_BUDGET")
"""The budget of the pass rendering now, set by :meth:`RenderBudget.spend`."""

BUDGET_EXCEEDED_MESSAGE = (
    "The message template does more work than one render may: over %(iterations)s loop or include "
    "renders, %(output)s characters of loop output or %(filter_input)s characters of filter input."
)


class RefusalSource(StrEnum):
    """Whose fault a send's refusal item is, named for what a sender does with it (``params[REFUSAL_SOURCE]``).

    Set at each raise site by what that site knows. A sender reads a refusal's
    sources as ``{item.params[REFUSAL_SOURCE] for item in error.error_list}``.
    """

    DEPLOYMENT = "deployment"
    """The deployment's configuration: fix it; stop the run."""
    TEMPLATE = "template"
    """This template: fix it; it is out in this language, the others still go."""
    VALUES = "values"
    """This mail's values as the renderer saw them: skip the item. Whether they are one item's for every reader or one
    reader's own is the caller's to know; the renderer cannot tell."""
    UNKNOWN = "unknown"
    """An unexpected failure: nothing the renderer can name is at fault."""


REFUSAL_SOURCE = "source"
"""The ``params`` key of a send's refusal item that holds its :class:`RefusalSource`."""


def refusal_sources(error: ValidationError) -> frozenset[RefusalSource]:
    """The sources of a send's refusal items: empty for a refusal that is not this addon's, such as messaging's keyed
    ones (a recipient's, the system channel's), whose items carry none. Safe on a keyed or a listed error alike."""

    keyed = getattr(error, "error_dict", None)
    items = error.error_list if keyed is None else [item for listed in keyed.values() for item in listed]
    return frozenset(RefusalSource(item.params[REFUSAL_SOURCE]) for item in items
                     if isinstance(item.params, Mapping) and REFUSAL_SOURCE in item.params)


def refusal_item(
    message: str, code: str, source: RefusalSource, *, params: Mapping[str, Any] | None = None,
    error: type[ValidationError] = ValidationError,
) -> ValidationError:
    """One refusal item of a send: ``message`` coded ``code``, its ``source`` in ``params``; the one way a send's
    refusals are made. Neither the message nor ``params`` holds a value the mail was given: a path's name, a limit, a
    count, a length or a type, never the value itself."""

    # A message given no params of its own is literal text, which may hold a ``%`` (a row's ``{% include %}``);
    # Django formats every message that has params, so such a message is escaped for it.
    literal = message if params else message.replace("%", "%%")
    return error(literal, code=code, params={**(params or {}), REFUSAL_SOURCE: source})


class RenderBudgetExceeded(ValidationError):
    """A pass spent its :class:`RenderBudget`; the render it belongs to fails."""


RENDER_ITERATION_LIMIT = 500
"""The loop and include renders one render pass may do (:class:`RenderBudget`): a sender bounds the items a mail lists
by it."""

RENDER_OUTPUT_LIMIT = 200_000
"""The characters one render pass's loops may write (:class:`RenderBudget`)."""

RENDER_FILTER_INPUT_LIMIT = 500_000
"""What one render pass's filters may read (:class:`RenderBudget`)."""

VALUES_JSON_LIMIT = 50_000
"""The longest the values a render reads may be as JSON, in characters (``values_too_large`` past it)."""

VALUE_STRING_LIMIT = 10_000
"""The longest one string value may be (``invalid_values`` past it)."""

VALUE_ITEMS_LIMIT = 500
"""The most list items the values may hold in all (``invalid_values`` past it)."""


class RenderBudget:
    """The work one render pass may do: loop and include renders, loop output, filter input.

    Sticky: once a charge goes past any limit, that charge and every later one
    raise :class:`RenderBudgetExceeded`, and :meth:`check` does too, since Django's
    ``if`` operators swallow an exception a filter raises.
    """

    def __init__(
        self, *, iterations: int = RENDER_ITERATION_LIMIT, output: int = RENDER_OUTPUT_LIMIT,
        filter_input: int = RENDER_FILTER_INPUT_LIMIT,
    ) -> None:
        """Start a pass with these limits."""

        self.limits = {"iterations": iterations, "output": output, "filter_input": filter_input}
        self.left = dict(self.limits)
        self.exceeded = False

    @classmethod
    def for_lines(cls) -> RenderBudget:
        """Return the budget of the pass rendering a subject and a preheader: :data:`LINE_BUDGET_SHARE` of a body's."""

        return cls(**{name: limit // LINE_BUDGET_SHARE for name, limit in cls().limits.items()})

    @classmethod
    def current(cls) -> RenderBudget:
        """Return the budget of the pass rendering now; outside :meth:`spend` there is none."""

        try:
            return RENDER_BUDGET.get()
        except LookupError as error:
            raise LookupError("A message template renders only inside RenderBudget.spend().") from error

    def charge(self, *, iterations: int = 0, output: int = 0, filter_input: int = 0) -> None:
        """Spend work, raising :class:`RenderBudgetExceeded` once any limit is passed, and ever after."""

        self.check()
        self.left["iterations"] -= iterations
        self.left["output"] -= output
        self.left["filter_input"] -= filter_input
        if min(self.left.values()) < 0:
            self.exceeded = True
            self.check()

    def check(self) -> None:
        """Raise :class:`RenderBudgetExceeded` if the budget was ever passed."""

        if self.exceeded:
            raise refusal_item(
                BUDGET_EXCEEDED_MESSAGE, RENDER_BUDGET_EXCEEDED, RefusalSource.VALUES, params=self.limits,
                error=RenderBudgetExceeded,
            )

    @contextmanager
    def spend(self) -> Iterator[RenderBudget]:
        """Make this the budget of the pass rendered inside the block, and check it again at its end."""

        token = RENDER_BUDGET.set(self)
        try:
            yield self
            self.check()
        finally:
            RENDER_BUDGET.reset(token)


type Path = tuple[str, ...]

BRAND_FIELDS: Mapping[Path, Callable[[BrandKit], object]] = {
    ("name",): attrgetter("name"),
    ("font", "heading"): attrgetter("font_heading"),
    ("font", "body"): attrgetter("font_body"),
    ("footer", "organization"): attrgetter("footer_organization"),
    ("footer", "address"): attrgetter("footer_address"),
}
"""What ``brand`` holds besides the mail colours, each path with the brand kit column it reads."""

MAIL_COLOURS = tuple((str(scheme), str(role)) for scheme in MailScheme for role in MailRole)
"""The mail colours ``brand`` holds, ``<scheme>.<role>``, from :meth:`~angee.brandkit.models.BrandKit.mail_palette`."""

BRAND_PATHS = frozenset(("brand", *path) for path in (*BRAND_FIELDS, *MAIL_COLOURS))
"""Every value ``brand`` holds (:func:`brand_values`), as a path."""


def brand_values(kit: BrandKit) -> dict[str, Any]:
    """Return what ``brand`` holds in a render, built from a brand kit as plain strings.

    :data:`BRAND_FIELDS` and :data:`MAIL_COLOURS` are its one declaration. The mail
    colours come from :meth:`~angee.brandkit.models.BrandKit.mail_palette`, which
    raises ``ValidationError`` for a kit stored past its validation.
    """

    palette = kit.mail_palette()
    values: dict[str, Any] = {}
    readings = [(path, read(kit)) for path, read in BRAND_FIELDS.items()]
    readings.extend(((scheme, role), palette[scheme][role]) for scheme, role in MAIL_COLOURS)
    for path, value in readings:
        node = values
        for segment in path[:-1]:
            node = node.setdefault(segment, {})
        node[path[-1]] = str(value)
    return values


register = Library()
"""The one library a :class:`MessageTemplateEngine` loads."""


class BudgetedLoopBody(Node):
    """A ``for`` loop's body: each iteration charges one iteration, then the output it wrote."""

    child_nodelists = ("nodelist",)

    def __init__(self, nodelist: NodeList) -> None:
        """Wrap the loop body Django compiled."""

        self.nodelist = nodelist

    def render(self, context: Context) -> str:
        """Render one iteration inside the pass's budget."""

        budget = RenderBudget.current()
        budget.charge(iterations=1)
        output = self.nodelist.render(context)
        budget.charge(output=len(output))
        return output


class BudgetedIncludeNode(IncludeNode):
    """Django's include, charging one iteration each time it renders."""

    def render(self, context: Context) -> SafeString:
        """Render the included row inside the pass's budget."""

        RenderBudget.current().charge(iterations=1)
        return super().render(context)


def _one_quoted_name(name: FilterExpression, tag: str) -> None:
    """Refuse a row name that is not one quoted literal without filters."""

    if name.is_var or name.filters:
        raise TemplateSyntaxError(f"{tag} takes one quoted name, not a value or a filter.")


@register.tag("for")
def do_for(parser: Parser, token: Token) -> Node:
    """Django's ``for``, whose body is a :class:`BudgetedLoopBody`."""

    node = defaulttags.do_for(parser, token)
    node.nodelist_loop = NodeList([BudgetedLoopBody(cast(NodeList, node.nodelist_loop))])
    return node


@register.tag("include")
def do_include(parser: Parser, token: Token) -> Node:
    """Django's ``include`` of one quoted name, always ``only``, as a :class:`BudgetedIncludeNode`."""

    node = loader_tags.do_include(parser, token)
    _one_quoted_name(node.template, "include")
    if not node.isolated_context:
        raise TemplateSyntaxError("include ends with only: an included row reads what it is passed.")
    return BudgetedIncludeNode(node.template, extra_context=node.extra_context, isolated_context=True)


@register.tag("extends")
def do_extends(parser: Parser, token: Token) -> Node:
    """Django's ``extends`` of one quoted name."""

    node = loader_tags.do_extends(parser, token)
    _one_quoted_name(cast(FilterExpression, node.parent_name), "extends")
    return node


register.tag("if", defaulttags.do_if)
register.tag("with", defaulttags.do_with)
register.tag("comment", defaulttags.comment)
register.tag("block", loader_tags.do_block)
register.tag("templatetag", defaulttags.templatetag)


def _charged(name: str, function: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap the filter ``name`` so each call charges the pass's budget, and only a markup filter returns markup.

    Django's ``needs_autoescape`` and ``expects_localtime`` and its signature stay its own. ``is_safe`` is kept for
    :data:`MARKUP_FILTERS` alone, and any other filter's result is a plain string, so the HTML engine escapes it
    even where Django's filter hands on a safe input, marks its own result safe (``floatformat``) or returns its
    literal argument (``default``).
    """

    reads_input = name in INPUT_READING_FILTERS
    writes_markup = name in MARKUP_FILTERS

    @functools.wraps(function)
    def charged(value: Any, *args: Any, **kwargs: Any) -> Any:
        length = len(value) if reads_input and isinstance(value, str) else 0
        RenderBudget.current().charge(filter_input=FILTER_CHARGE + length)
        result = function(value, *args, **kwargs)
        return result if writes_markup or not isinstance(result, SafeData) else str.__str__(result)

    charged.__dict__["is_safe"] = writes_markup
    return charged


_LINKS = MarkdownIt("commonmark")
"""The parser whose public ``normalizeLink`` writes a link as Markdown links are written: percent-encoded, its host
in punycode."""


VALUE_NOT_LINKABLE = "value_not_linkable"
"""The code of a value a render prints as a link and :func:`url` would empty (:func:`clean_link`)."""

VALUE_NOT_PRINTED = "value_not_printed"
"""The code of a value a sender requires (``must_print``) that a body does not print, or this mail does not show."""

INVALID_VALUES = "invalid_values"
"""The code of the values a render refuses to read: not JSON, a shape or a value its declarations do not admit, a
required value missing, a number a float does not hold, a moment the date rules refuse."""

NOT_A_TEMPLATE = "not_a_template"
"""The code of a send of a layout, block or part, which only a template can be (``send_refusal``)."""

NOT_EFFECTIVE = "not_effective"
"""The code of a send of a row that is not what renders for its kind, key and language (``send_refusal``)."""

VALUES_TOO_LARGE = "values_too_large"
"""The code of values over the JSON size a render reads."""

MESSAGE_TEMPLATE_REFUSED = "message_template_refused"
"""The code of a row the walk refuses to render."""

RENDER_BUDGET_EXCEEDED = "render_budget"
"""The code of a render pass that spent its ``RenderBudget`` (:class:`RenderBudgetExceeded`)."""

SUBJECT_TOO_LONG = "subject_too_long"
"""The code of a rendered subject longer than a mail carries."""

NO_BRAND_KIT = "no_brand_kit"
"""The code of a render with no kit named and no default kit the reader reads."""

RENDER_FAILED = "render_failed"
"""The code of any other failure of a render, logged with its traceback first."""

SEND_CODES = frozenset({
    NOT_A_TEMPLATE, NOT_EFFECTIVE, INVALID_VALUES, VALUES_TOO_LARGE, MESSAGE_TEMPLATE_REFUSED, VALUE_NOT_LINKABLE,
    VALUE_NOT_PRINTED, RENDER_BUDGET_EXCEEDED, SUBJECT_TOO_LONG, NO_BRAND_KIT, RENDER_FAILED,
})
"""Every code ``send_as_system`` can return from this addon, for a sender to compare its handling against: a code
added later fails that comparison rather than falling silently into a default. What compose refuses is messaging's."""


def _link(value: Any) -> tuple[str, str | None]:
    """The link :func:`url` gives for ``value`` and, when it gives none for a value that is not blank, why."""

    text = str(value)
    if not text.strip():
        return "", None
    if len(text) > URL_INPUT_LIMIT:
        return "", f"it is over {URL_INPUT_LIMIT:,} characters"
    link = _LINKS.normalizeLink(text.strip())
    scheme, colon, rest = link.partition(":")
    scheme = scheme.lower()
    if not colon or scheme not in LINK_SCHEMES:
        schemes = f"{', '.join(LINK_SCHEMES[:-1])} or {LINK_SCHEMES[-1]}"
        return "", f"a mail has no base, so a link is an absolute {schemes} link"
    if scheme in ("https", "http"):
        authority = re.split(r"[/?#]", rest[2:], maxsplit=1)[0] if rest.startswith("//") else ""
        if not authority:
            return "", "it names no host"
        if "@" in authority:
            return "", "it carries user info before its host"
        if not LINK_HOST.fullmatch(authority):
            own = _own_origin_authority(scheme, authority)
            if own is None:
                return "", ("its host is neither a domain name of two labels or more, the last a name, with an "
                            "optional port, nor this deployment's public origin")
            link = f"{link.partition(':')[0]}://{own}{rest[2 + len(authority):]}"
    return link, None


def _own_origin_authority(scheme: str, authority: str) -> str | None:
    """The authority of this deployment's public origin, as its owner gives it
    (:func:`~angee.base.origins.configured_public_origin`), when ``scheme://authority`` (as markdown-it writes a
    link) is that origin; else ``None``. So a deployment whose origin is ``http://localhost:8000`` in ``DEBUG``, an
    address or another single-label host links its own pages, an IPv6 address in its brackets, which markdown-it
    would percent-encode, and no other host of that kind."""

    try:
        origin = configured_public_origin()
    except PublicOriginUnavailable:
        return None
    if f"{scheme}://{authority}".lower() != _LINKS.normalizeLink(origin).lower():
        return None
    return urlsplit(origin).netloc


def url(value: Any) -> str:
    """Return ``value`` as a link a message may carry, or ``""``.

    markdown-it's own normalisation of the value without its surrounding white
    space, kept only when it is an absolute link of :data:`LINK_SCHEMES`; any
    other scheme, a protocol- or path-relative link, a fragment, a host
    :data:`LINK_HOST` refuses unless it is the deployment's own public origin,
    and input over :data:`URL_INPUT_LIMIT` characters give ``""``. A render never lets a value
    it was given be emptied here unseen: it asks :func:`clean_link` of each value
    it prints through this filter first.
    """

    return _link(value)[0]


@sensitive_variables()
def clean_link(value: Any, *, path: str = "link") -> str:
    """Return the link a mail prints for ``value`` through :func:`url`, or refuse it as a render refuses it.

    A blank value (nothing but white space) gives ``""`` and no refusal, as a
    render leaves an optional link empty. Any other value :func:`url` would empty
    is refused with a ``ValidationError`` of one item, code
    :data:`VALUE_NOT_LINKABLE`, naming ``path`` and why: for a value as a render
    prepares it (:meth:`~angee.messaging_templates.walker.TemplateWalk.prepare`
    drops its control characters first), the link and the words a render gives
    that value at that path. Every local of this frame and of the rule's
    below it is Django's ``sensitive_variables``, so a report of a refusal that
    propagates does not show the link. It reads no row, so a caller that mails
    many recipients asks it once before the first send.
    """

    link, problem = _link(value)
    if problem is not None:
        raise ValidationError([refusal_item(
            f"{path} is not a link this mail can carry: {problem}.", VALUE_NOT_LINKABLE, RefusalSource.VALUES,
        )])
    return link


for _name in ALLOWED_FILTERS[:-1]:
    # No flags: Library.filter would set them on Django's own function, which every engine shares.
    register.filter(_name, _charged(_name, defaultfilters.register.filters[_name]))
register.filter("url", _charged("url", url), is_safe=False)


def filter_names(expression: FilterExpression) -> list[str]:
    """The names of ``expression``'s filters, in order, as ``Library.filter`` recorded each on its callable."""

    return [str(function._filter_name) for function, _ in expression.filters]


class MessageTemplateContext(Context):
    """A render's context, whose ``only`` scopes keep the globals every row reads.

    Built only by :meth:`MessageTemplateEngine.context`. It declares no class
    attribute: Django's lookup reaches a context's instance attribute only when
    the context's class declares the same name with a falsy value
    (``Variable._resolve_lookup``), so a template never reads ``mail_globals``.
    """

    def __init__(self, values: Mapping[str, Any], *, mail_globals: Mapping[str, Any], autoescape: bool) -> None:
        """Start the context from the globals, then the values."""

        self.mail_globals = dict(mail_globals)
        super().__init__({**self.mail_globals, **values}, autoescape=autoescape)

    def new(self, values: dict[str, Any] | Context | None = None) -> Context:
        """Start an ``only`` scope from the globals and ``values``; Django's include calls it."""

        scope = dict(self.mail_globals)
        scope.update(cast(Mapping[str, Any], values.flatten() if isinstance(values, Context) else values or {}))
        return super().new(scope)


class MessageTemplateEngine(Engine):
    """A standalone engine whose one library is :data:`register`, with no context processor."""

    default_builtins: ClassVar[list[str]] = []
    """None of Django's builtins: ``Engine.__init__`` prepends this class attribute to ``builtins``."""

    def __init__(
        self,
        *,
        autoescape: bool,
        loaders: list[Any] | None = None,
        mail_globals: Mapping[str, Any] | None = None,
    ) -> None:
        """Build an engine that escapes values (``autoescape``) or writes them as they are, for plain text."""

        super().__init__(loaders=loaders or [], builtins=[__name__], libraries={}, autoescape=autoescape)
        self.mail_globals = dict(mail_globals or {})

    def context(self, values: Mapping[str, Any], *, extra: Mapping[str, Any] | None = None) -> MessageTemplateContext:
        """Return the context a render of ``values`` reads; ``extra`` reaches the top level only."""

        return MessageTemplateContext(
            {**values, **(extra or {})}, mail_globals=self.mail_globals, autoescape=self.autoescape,
        )


class MessageTemplateLoader(Loader):
    """Serve one form of the rows a render reached, by template name, on ``locmem.Loader``'s contract.

    Names are a row's template name (``layouts/base``, ``blocks/button``, a
    template's bare key); none holds ``#``, which Django reads as a partial.
    """

    def __init__(self, engine: Engine, forms: Mapping[str, str]) -> None:
        """Serve ``forms``, each row's form by its template name."""

        super().__init__(engine)
        self.forms = forms

    def get_contents(self, origin: Origin) -> str:
        """Return the form of the row ``origin`` names."""

        form = self.forms.get(origin.name)
        if form is None:
            raise TemplateDoesNotExist(origin)
        return form

    def get_template_sources(self, template_name: str) -> Iterator[Origin]:
        """Name the one origin a row has."""

        yield Origin(name=template_name, template_name=template_name, loader=self)
