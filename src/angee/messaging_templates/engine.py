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
empty context. :class:`MessageTemplateLoader` serves the engine the forms of the
rows one render reached, by template name. This module imports nothing else of
the addon.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, ClassVar, cast

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
from django.utils.safestring import SafeString
from markdown_it import MarkdownIt

ALLOWED_TAGS = ("if", "for", "with", "comment", "block", "include", "extends", "templatetag")
"""The tags a message template may use; ``templatetag`` writes a literal ``{{``, ``{%`` or ``{#``."""

ALLOWED_FILTERS = (
    "default", "upper", "lower", "capfirst", "title", "truncatechars", "truncatewords", "linebreaksbr", "length",
    "date", "floatformat", "pluralize", "yesno", "url",
)
"""The filters a message template may use: Django's own thirteen and :func:`url`."""

URL_INPUT_LIMIT = 2_048
"""The longest value :func:`url` reads; a longer one gives no link."""

FILTER_CHARGE = 100
"""What a filter call charges a pass's ``filter_input`` besides the length of a string it reads."""

RENDER_BUDGET: ContextVar[RenderBudget] = ContextVar("RENDER_BUDGET")
"""The budget of the pass rendering now, set by :meth:`RenderBudget.spend`."""

BUDGET_EXCEEDED_MESSAGE = (
    "The message template does more work than one render may: over %(iterations)s loop or include "
    "renders, %(output)s characters of loop output or %(filter_input)s characters of filter input."
)


class RenderBudgetExceeded(ValidationError):
    """A pass spent its :class:`RenderBudget`; the render it belongs to fails."""


class RenderBudget:
    """The work one render pass may do: loop and include renders, loop output, filter input.

    Sticky: once a charge goes past any limit, that charge and every later one
    raise :class:`RenderBudgetExceeded`, and :meth:`check` does too, since Django's
    ``if`` operators swallow an exception a filter raises.
    """

    def __init__(self, *, iterations: int = 500, output: int = 200_000, filter_input: int = 500_000) -> None:
        """Start a pass with these limits."""

        self.limits = {"iterations": iterations, "output": output, "filter_input": filter_input}
        self.left = dict(self.limits)
        self.exceeded = False

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
            raise RenderBudgetExceeded(BUDGET_EXCEEDED_MESSAGE, code="render_budget", params=self.limits)

    @contextmanager
    def spend(self) -> Iterator[RenderBudget]:
        """Make this the budget of the pass rendered inside the block, and check it again at its end."""

        token = RENDER_BUDGET.set(self)
        try:
            yield self
            self.check()
        finally:
            RENDER_BUDGET.reset(token)


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


def _charged(function: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a filter so each call charges the pass's budget; its flags and signature stay Django's."""

    @functools.wraps(function)
    def charged(value: Any, *args: Any, **kwargs: Any) -> Any:
        RenderBudget.current().charge(filter_input=FILTER_CHARGE + (len(value) if isinstance(value, str) else 0))
        return function(value, *args, **kwargs)

    return charged


_LINKS = MarkdownIt("commonmark")
"""The parser whose public ``normalizeLink`` and ``validateLink`` judge a link, as Markdown links are judged."""


def _link(text: str) -> str:
    """Return ``text`` normalised as a link, or ``""`` for a scheme markdown-it refuses or any ``data:`` URL."""

    link = _LINKS.normalizeLink(text.strip())
    return link if _LINKS.validateLink(link) and not link.lower().startswith("data:") else ""


def url(value: Any) -> str:
    """Return ``value`` as a link a message may carry, or ``""``.

    markdown-it's own normalisation, then its validation, which refuses
    ``javascript:``, ``vbscript:`` and ``file:``; every ``data:`` URL is refused
    too, and so is input over :data:`URL_INPUT_LIMIT` characters.
    """

    text = str(value)
    return "" if len(text) > URL_INPUT_LIMIT else _link(text)


for _name in ALLOWED_FILTERS[:-1]:
    # No flags: Library.filter would set them on Django's own function, which every engine shares.
    register.filter(_name, _charged(defaultfilters.register.filters[_name]))
register.filter("url", _charged(url), is_safe=False)


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
