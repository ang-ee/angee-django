"""The walker: the one owner of every refusal a message template meets, at save and at render.

:meth:`TemplateWalker.walk` reads a row and every row it reaches (its layout and
the blocks and parts it includes, at any depth) as Django compiles them, and
refuses what may not render: oversized or pathological sources before anything
parses them, compile errors, literals that could carry markup, costly or unsafe
filter use, template spans the Markdown pass does not keep whole, HTML a mail may
not carry, reads of values no row declares, lookups that could reach a method of
a ``dict``, ``list`` or ``str``, include cycles, and every bound in the walk's
caps. It runs from ``clean()`` and after a resource load (at save) and from
``render()`` and ``preview()`` before anything renders.

A walk that passes is a :class:`TemplateWalk`: the values the render reads (``D``,
the union of a template's and its layout's ``variables``), which of them are
required, which are dates, and the forms the renderer serves.
:meth:`TemplateWalk.prepare` turns a render's values into exactly that shape.

The walker reads compiled nodes by their attributes, which Django does not
document; ``docs/stack.md`` (the Django row) lists each one. It dispatches on the
exact node class (:data:`WALKED_NODES`) and raises ``TypeError`` on any other, so a
Django upgrade that changes the nodes fails loudly.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
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
)
from django.template.defaulttags import CommentNode, ForNode, IfNode, TemplateLiteral, TemplateTagNode, WithNode
from django.template.loader_tags import BlockNode, ExtendsNode
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.functional import Promise

from angee.messaging_templates.engine import FILTER_NAMES, BudgetedIncludeNode, BudgetedLoopBody, MessageTemplateEngine
from angee.messaging_templates.rendering import (
    BRAND_PATHS,
    RESERVED_VARIABLE_ROOTS,
    MessageTemplateFormat,
    RowForms,
    RowRefusal,
    check_html,
    compiled_expression,
    validate_variables,
)

WALKED_NODES = (
    TextNode, VariableNode, IfNode, ForNode, BudgetedLoopBody, WithNode, CommentNode, BudgetedIncludeNode,
    ExtendsNode, BlockNode, TemplateTagNode,
)
"""Every node class the allowlisted library compiles to; the walker raises on any other."""

SOURCE_LIMIT = 50_000
LINE_LIMIT = 1_000
OPENERS_PER_LINE = 30
FORM_OPENERS_PER_LINE = 60
WALK_SOURCE_LIMIT = 150_000
NESTING_LIMIT = 16
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
DECLARED_LIMIT = 100
VALUES_JSON_LIMIT = 50_000
VALUE_STRING_LIMIT = 10_000
VALUE_ITEMS_LIMIT = 500

FORLOOP_KEYS = frozenset({"counter", "counter0", "revcounter", "revcounter0", "first", "last", "length"})
FORLOOP_PATHS = frozenset({("forloop",), *(("forloop", key) for key in FORLOOP_KEYS)})
CONSTANTS = frozenset({"True", "False", "None"})
UNBINDABLE = RESERVED_VARIABLE_ROOTS | CONSTANTS
OPENERS = ("{{", "{%", "{#")
NESTED_TAGS = frozenset({"if", "for", "with", "block"})
NESTED_ENDS = frozenset({"endif", "endfor", "endwith", "endblock"})
CONTENT_BLOCK = "content"

type Path = tuple[str, ...]


class WalkedKind(Protocol):
    """What the walker asks a row's kind handler."""

    carries_message_fields: bool
    includable: bool
    reads_message_fields: bool
    holds_content: bool

    def template_name(self, key: str) -> str: ...

    def layout_name(self, row: Any) -> str | None: ...

    def wrap(self, row: Any, form: str) -> str: ...


class RowReader(Protocol):
    """Where a walk reads the rows it names: the model's scoped manager."""

    def named_rows(self, names: Iterable[str], *, language: str) -> Mapping[str, Any]: ...

    def resolves_name(self, name: str) -> bool: ...


def _dotted(path: Sequence[str]) -> str:
    return ".".join(path)


def _prefixes(paths: Iterable[Path]) -> frozenset[Path]:
    """Every declared path and every path a declared one continues."""

    return frozenset(path[:end] for path in paths for end in range(1, len(path) + 1))


BRAND_PREFIXES = _prefixes(BRAND_PATHS)


@dataclass(frozen=True)
class _RowCheck:
    """One row's own checks (rules 1 to 12): its forms, its compiled pieces, what it includes."""

    name: str
    forms: RowForms | None = None
    pieces: Mapping[str, Template] = field(default_factory=dict)
    includes: frozenset[str] = frozenset()
    refusals: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemplateRead:
    """One read of a value: by which row, as which path there and in ``D``, and how."""

    row: str
    local: Path
    effective: Path | None
    use: str


@dataclass
class TemplateWalk:
    """A row as a render reaches it: the rows, their forms, the values ``D`` and what the render requires.

    ``rows`` and ``forms`` are keyed by template name; ``declared`` is ``D``;
    ``required`` the paths a render's values must hold, not null, in every list
    item present; ``date_paths`` the paths read through ``date``; ``counts`` the
    largest tree's nodes, outputs and text, and the deepest loops and includes.
    """

    root: Any
    root_name: str
    language: str
    rows: dict[str, Any] = field(default_factory=dict)
    forms: dict[str, RowForms] = field(default_factory=dict)
    declared: dict[str, str] = field(default_factory=dict)
    required: set[Path] = field(default_factory=set)
    date_paths: set[Path] = field(default_factory=set)
    reads: list[TemplateRead] = field(default_factory=list)
    includes: set[str] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=dict)
    refusals: list[str] = field(default_factory=list)

    @property
    def unused(self) -> list[str]:
        """The paths of the root row's own ``variables`` no read reaches."""

        reached = {read.effective for read in self.reads if read.effective is not None}
        unused = []
        for path in self.root.variables if isinstance(self.root.variables, dict) else ():
            segments = tuple(path.split("."))
            if not any(segments[:end] in reached for end in range(1, len(segments) + 1)):
                unused.append(path)
        return unused

    def prepare(self, values: Any) -> dict[str, Any]:
        """Return ``values`` as the render reads them, or raise ``ValidationError`` naming each path.

        1. ``values`` as JSON (``DjangoJSONEncoder``: dates and decimals become
           strings, a ``SafeString`` a plain string, any other object is refused),
           refused over 50,000 characters, with a string over 10,000, with over 500
           list items in all, or with a reserved root.
        2. Keys outside ``D`` dropped at every level and in every list item; a
           present inner segment is an object, a ``*`` parent a list, a leaf a
           string, number, boolean or null.
        3. Each :attr:`required` path present and not null, in every present item.
        4. Every absent or null declared segment filled: an object with ``{}``, a
           list with ``[]``, a leaf with ``""``; so every lookup lands on a key.
        5. At each :attr:`date_paths` path a non-empty string becomes a ``date``,
           or else an aware ``datetime``.
        """

        if not isinstance(values, Mapping):
            raise ValidationError("Pass the values as an object of names to values.", code="invalid_values")
        try:
            text = json.dumps(values, cls=DjangoJSONEncoder)
            data = json.loads(text)
        except (TypeError, ValueError, RecursionError) as error:
            raise ValidationError(f"The values are not JSON: {error}", code="invalid_values") from error
        if len(text) > VALUES_JSON_LIMIT:
            raise ValidationError(
                f"The values are {len(text)} characters of JSON, over {VALUES_JSON_LIMIT}.", code="values_too_large",
            )
        errors = _value_errors(data)
        if errors:
            raise ValidationError(errors, code="invalid_values")
        tree = _tree(self.declared)
        shaped = {key: _shaped(data[key], subtree, (key,), errors) for key, subtree in tree.items() if key in data}
        errors.extend(
            f"{_dotted(path)} is required and missing or null." for path in sorted(self.required)
            if _lacks(shaped, path)
        )
        if errors:
            raise ValidationError(errors, code="invalid_values")
        filled = {key: _filled(shaped.get(key), subtree) for key, subtree in tree.items()}
        for path in sorted(self.date_paths):
            _parse_dates(filled, path, path, errors)
        if errors:
            raise ValidationError(errors, code="invalid_values")
        return filled


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
        if isinstance(value, (str, int, float, bool)):
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


def _parse_dates(value: Any, path: Path, full: Path, errors: list[str]) -> None:
    """Turn each non-empty string at ``path`` into a ``date``, else an aware ``datetime``, in place."""

    head, rest = path[0], path[1:]
    children = list(enumerate(value)) if head == "*" and isinstance(value, list) else (
        [(head, value[head])] if isinstance(value, dict) and head in value else []
    )
    for key, child in children:
        if rest:
            _parse_dates(child, rest, full, errors)
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
        value[key] = parsed


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


def _floatformat_precision(argument: Any) -> int | None:
    """The precision ``floatformat`` reads from ``argument``, parsed as the filter parses it."""

    if isinstance(argument, str):
        if argument[-2:] in ("gu", "ug"):
            argument = argument[:-2] or -1
        elif argument[-1:] in ("g", "u"):
            argument = argument[:-1] or -1
    try:
        return int(argument)
    except (TypeError, ValueError, OverflowError):
        return None


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
        if type(node) is TextNode:
            if any(opener in node.s for opener in OPENERS):
                self.refuse("text holds {{, {% or {# that is not template code")
        elif type(node) is VariableNode:
            self.expression(node.filter_expression)
        elif type(node) is IfNode:
            for condition, nodelist in node.conditions_nodelists:
                if condition is not None:
                    self.condition(condition)
                self.nodes(nodelist)
        elif type(node) is ForNode:
            sequence = cast(FilterExpression, node.sequence)
            if len(node.loopvars) != 1:
                self.refuse("for takes one loop variable")
            if sequence.filters:
                self.refuse("for loops over a value, not a filtered one")
            self.expression(sequence)
            self.nodes(cast(NodeList, node.nodelist_loop))
            self.nodes(cast(NodeList, node.nodelist_empty))
        elif type(node) is BudgetedLoopBody or type(node) is BlockNode:
            self.nodes(node.nodelist)
        elif type(node) is WithNode:
            for expression in node.extra_context.values():
                self.expression(expression)
            self.nodes(node.nodelist)
        elif type(node) is BudgetedIncludeNode:
            self.includes.add(str(node.template.var))
            for expression in node.extra_context.values():
                self.expression(expression)
        elif type(node) is ExtendsNode:
            self.nodes(node.nodelist)
        elif type(node) is not CommentNode and type(node) is not TemplateTagNode:
            raise TypeError(f"The message template walker does not know {type(node).__qualname__}.")

    def literal(self, value: Any) -> None:
        if (reason := _literal_refusal(value)) is not None:
            self.refuse(reason)

    def expression(self, expression: FilterExpression) -> None:
        variable = expression.var
        if expression.is_var:
            if variable.translate:
                self.refuse("a _(…) literal: rows are per language, never translated")
            if variable.lookups is None:
                self.literal(variable.var)
        else:
            self.literal(variable)
        names = [FILTER_NAMES[function] for function, _ in expression.filters]
        if len(names) > FILTERS_PER_EXPRESSION:
            self.refuse(f"over {FILTERS_PER_EXPRESSION} filters in {expression.token}")
        for position, ((_, arguments), name) in enumerate(zip(expression.filters, names, strict=True)):
            for lookup, argument in arguments:
                if lookup and argument.lookups is not None:
                    self.refuse(f"{name} takes a literal argument, not {argument.var}")
                    continue
                value = argument.literal if lookup else argument
                self.literal(argument.var if lookup else argument)
                if name == "floatformat" and (precision := _floatformat_precision(value)) is not None and abs(
                    precision,
                ) > FLOATFORMAT_PRECISION:
                    self.refuse(f"floatformat precision {value} is beyond ±{FLOATFORMAT_PRECISION}")
                if name == "date" and str(value) not in DATE_FORMAT_NAMES and len(str(value)) > DATE_FORMAT_LIMIT:
                    self.refuse(f"a date format is a format name or at most {DATE_FORMAT_LIMIT} characters")
                if name == "linebreaksbr":
                    self.refuse("linebreaksbr takes no argument")
            if name == "date" and position:
                self.refuse(f"date is the first filter in {expression.token}")
            if name == "url" and (position != len(names) - 1 or set(names[:position]) - {"default"}):
                self.refuse(f"url is the last filter, after default alone, in {expression.token}")
            if name == "linebreaksbr" and self.text_form:
                self.refuse("linebreaksbr writes <br> into plain text")

    def condition(self, condition: Any) -> None:
        if type(condition) is TemplateLiteral:
            self.expression(cast(FilterExpression, condition.value))
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


@dataclass(frozen=True)
class _Binding:
    """What a name stands for in a row: a value (its paths here and in ``D``), or a name the mail supplies."""

    kind: str
    local: Path = ()
    effective: Path | None = None


@dataclass(frozen=True)
class _Frame:
    """One row as the walk stands in it: its scope, guards, loops and the includes above it."""

    name: str
    row: Any
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

    form: str
    content: tuple[_Frame, NodeList] | None = None
    nodes: int = 0
    outputs: int = 0
    text: int = 0


class TemplateWalker:
    """Walk message templates: each row's own checks, then the walk from a root through what it reaches.

    ``rows`` reads the rows a walk names (the model's scoped manager); ``saving``
    asks, as a save does, that every included key resolve for every recipient.
    A row's own checks are remembered by its content hash for the walker's life.
    """

    def __init__(self, rows: RowReader, *, saving: bool = False) -> None:
        """Walk rows read through ``rows``."""

        self.reader = rows
        self.saving = saving
        self.html_engine = MessageTemplateEngine(autoescape=True)
        self.text_engine = MessageTemplateEngine(autoescape=False)
        self.checked: dict[tuple[str, str], _RowCheck] = {}

    # -- one row's own checks --------------------------------------------------

    def check(self, row: Any) -> _RowCheck:
        """Return ``row``'s own checks (rules 1 to 12), remembered by its name and content hash."""

        name = row.handler.template_name(row.key)
        key = (name, row.compute_content_hash())
        if key not in self.checked:
            self.checked[key] = self._check(row, name)
        return self.checked[key]

    def includes(self, row: Any) -> frozenset[str]:
        """The template names ``row``'s compiled forms include; none when they do not compile."""

        return self.check(row).includes

    def _check(self, row: Any, name: str) -> _RowCheck:
        refusals: list[str] = []

        def refuse(reason: str) -> None:
            refusals.append(f"{name}: {reason}")

        try:
            validate_variables(row.variables)
        except ValidationError as error:
            refuse(" ".join(error.messages))
        try:
            markup = MessageTemplateFormat(row.format).handler
        except ValueError:
            refuse(f"{row.format!r} is not a markup")
            return _RowCheck(name, refusals=tuple(refusals))
        texts = {"source": row.source, "text_source": row.text_source, "subject": row.subject,
                 "preheader": row.preheader}
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
        for form_name, form in forms._asdict().items():
            if any(sum(line.count(opener) for opener in OPENERS) > FORM_OPENERS_PER_LINE for line in form.split("\n")):
                refuse(f"a line of its {form_name} form holds over {FORM_OPENERS_PER_LINE} {{{{, {{% or {{#")
        if refusals:
            return _RowCheck(name, refusals=tuple(refusals))
        handler = row.handler
        served = RowForms(html=handler.wrap(row, forms.html), text=handler.wrap(row, forms.text))
        pieces_to_compile: dict[str, tuple[str, str, MessageTemplateEngine]] = {
            "html": (forms.html, served.html, self.html_engine),
            "text": (forms.text, served.text, self.text_engine),
        }
        if handler.carries_message_fields:
            pieces_to_compile["subject"] = (row.subject, row.subject, self.text_engine)
            pieces_to_compile["preheader"] = (row.preheader, row.preheader, self.text_engine)
        pieces: dict[str, Template] = {}
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
            self._structure(template.nodelist, piece, handler, row, rules.refuse)
            includes |= rules.includes
            pieces[piece] = template
        if "html" in pieces:
            refusals.extend(f"{name}: {reason}" for reason in check_html(forms.html, compiled_expression(
                self.html_engine)))
        return _RowCheck(name, forms=served, pieces=pieces, includes=frozenset(includes), refusals=tuple(refusals))

    @staticmethod
    def _structure(nodelist: NodeList, piece: str, handler: WalkedKind, row: Any, refuse: Any) -> None:
        """Rule 8: ``extends`` and ``block`` only as the wrapper and each layout form's one empty content block."""

        if piece in ("subject", "preheader"):
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

    def walk(self, root: Any) -> TemplateWalk:
        """Walk ``root`` and every row it reaches in its language; raise ``ValidationError`` listing refusals."""

        handler = root.handler
        root_name = handler.template_name(root.key)
        walk = TemplateWalk(root=root, root_name=root_name, language=str(root.language), rows={root_name: root})
        checks = self._gather(walk)
        if not walk.refusals:
            self._declare(walk, checks)
        if not walk.refusals:
            self._walk_trees(walk, checks)
        if walk.refusals:
            raise ValidationError(list(dict.fromkeys(walk.refusals)), code="message_template_refused")
        return walk

    def _gather(self, walk: TemplateWalk) -> dict[str, _RowCheck]:
        """Rows reached, level by level, each counted before it is parsed and checked alone."""

        checks: dict[str, _RowCheck] = {}
        characters = 0
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
                if check.forms is not None:
                    walk.forms[name] = check.forms
                walk.includes |= check.includes
                layout = row.handler.layout_name(row)
                for target in sorted(check.includes | ({layout} if layout else set())):
                    if target not in walk.rows and target not in wanted:
                        wanted.append(target)
            if walk.refusals or not wanted:
                return checks
            found = self.reader.named_rows(wanted, language=walk.language)
            for target in wanted:
                row = found.get(target)
                if row is None:
                    walk.refusals.append(f"{walk.root_name}: no message template {target} in {walk.language}")
                elif target in walk.includes and not row.handler.includable:
                    walk.refusals.append(f"{walk.root_name}: only blocks and parts are included, not {target}")
                elif self.saving and target in walk.includes and not self.reader.resolves_name(target):
                    walk.refusals.append(
                        f"{walk.root_name}: {target} has no shipped default or row in the default language",
                    )
                else:
                    walk.rows[target] = row
            level = [target for target in wanted if target in walk.rows]
        return checks

    def _declare(self, walk: TemplateWalk, checks: Mapping[str, _RowCheck]) -> None:
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
        frame = self._frame(walk.root_name, root, top=True)
        pieces = checks[walk.root_name].pieces
        layout = root.handler.layout_name(root)
        walker = _TreeWalker(self, walk, checks)
        for form in ("html", "text"):
            tree = _Tree(form)
            if layout is not None:
                extends = cast(ExtendsNode, pieces[form].nodelist[0])
                tree.content = (frame, extends.blocks[CONTENT_BLOCK].nodelist)
                tree.nodes += 2
                walker.nodes(self._frame(layout, walk.rows[layout], top=True), checks[layout].pieces[form].nodelist,
                             tree)
            else:
                walker.nodes(frame, pieces[form].nodelist, tree)
            walker.close(tree)
        for piece in ("subject", "preheader"):
            if piece in pieces:
                tree = _Tree(piece)
                walker.nodes(frame, pieces[piece].nodelist, tree)
                walker.close(tree)

    @staticmethod
    def _frame(name: str, row: Any, *, top: bool, scope: Mapping[str, _Binding] | None = None, **state: Any) -> _Frame:
        variables = row.variables if isinstance(row.variables, dict) else {}
        leaves = frozenset(tuple(path.split(".")) for path in variables)
        return _Frame(
            name=name, row=row, declared=_prefixes(leaves), leaves=leaves, top=top, scope=scope or {}, **state,
        )


class _TreeWalker:
    """Rules 13 to 18: every read resolved through scope, alias and include argument, and every walk bound."""

    def __init__(self, owner: TemplateWalker, walk: TemplateWalk, checks: Mapping[str, _RowCheck]) -> None:
        self.owner = owner
        self.walk = walk
        self.checks = checks
        self.d_prefixes = _prefixes(tuple(path.split(".")) for path in walk.declared)
        self.d_leaves = frozenset(tuple(path.split(".")) for path in walk.declared)

    def refuse(self, frame: _Frame, reason: str) -> None:
        self.walk.refusals.append(f"{frame.name}: {reason}")

    def close(self, tree: _Tree) -> None:
        for count, value in (("nodes", tree.nodes), ("outputs", tree.outputs), ("text", tree.text)):
            self.walk.counts[count] = max(self.walk.counts.get(count, 0), value)
        if tree.nodes > NODE_LIMIT:
            self.walk.refusals.append(f"{self.walk.root_name}: its {tree.form} reaches over {NODE_LIMIT} nodes")
        if tree.outputs > OUTPUT_LIMIT:
            self.walk.refusals.append(f"{self.walk.root_name}: its {tree.form} reaches over {OUTPUT_LIMIT} outputs")
        if tree.text > TEXT_LIMIT:
            self.walk.refusals.append(
                f"{self.walk.root_name}: its {tree.form} writes over {TEXT_LIMIT} characters of text",
            )

    def nodes(self, frame: _Frame, nodelist: Iterable[Node], tree: _Tree) -> None:
        for node in nodelist:
            if tree.nodes > NODE_LIMIT:
                return  # over the bound: close() refuses, and the walk stops expanding includes
            self.node(frame, node, tree)

    def node(self, frame: _Frame, node: Node, tree: _Tree) -> None:
        if type(node) is BudgetedLoopBody:
            self.nodes(frame, node.nodelist, tree)
            return
        tree.nodes += 1
        if type(node) is TextNode:
            tree.text += len(node.s)
        elif type(node) is VariableNode:
            tree.outputs += 1
            self.expression(frame, node.filter_expression, "output")
        elif type(node) is IfNode:
            for condition, nodelist in node.conditions_nodelists:
                guards = frame.guards
                if condition is not None:
                    tree.nodes += self.condition(frame, condition)
                    guards = guards | self.guards(frame, condition)
                self.nodes(_replace(frame, guards=guards), nodelist, tree)
        elif type(node) is ForNode:
            self.loop(frame, node, tree)
        elif type(node) is WithNode:
            scope = dict(frame.scope)
            for name, expression in node.extra_context.items():
                tree.nodes += 1
                self.bindable(frame, name)
                scope[name] = self.bind(frame, expression)
            self.nodes(_replace(frame, scope=scope), node.nodelist, tree)
        elif type(node) is BudgetedIncludeNode:
            self.include(frame, node, tree)
        elif type(node) is BlockNode:
            if tree.content is not None and node.name == CONTENT_BLOCK:
                content_frame, content = tree.content
                self.nodes(content_frame, content, tree)
        elif type(node) is ExtendsNode:
            self.refuse(frame, "extends is the wrapper's alone")
        elif type(node) is not CommentNode and type(node) is not TemplateTagNode:
            raise TypeError(f"The message template walker does not know {type(node).__qualname__}.")

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
                binding = _Binding("brand", effective=("brand",))
            elif head in ("lang", "dir"):
                binding = _Binding("global", effective=(head,))
            elif head in CONSTANTS:
                binding = _Binding("constant", effective=(head,))
            elif head in ("subject", "preheader"):
                if not frame.row.handler.reads_message_fields:
                    self.refuse(frame, f"{head} is read in a template or a layout alone")
                    return None
                binding = _Binding("message", effective=(head,))
            elif head in ("forloop", "block"):
                self.refuse(frame, f"{_dotted(lookups)} is read outside a loop or is the engine's own")
                return None
            elif frame.top:
                binding = _Binding("value", local=(head,), effective=(head,))
            else:
                binding = _Binding("unpassed", local=(head,))
        local = (*binding.local, *rest) if binding.local else ()
        if local and local not in frame.declared:
            self.refuse(frame, f"{_dotted(local)} is not in its variables")
            return None
        if binding.kind in ("value", "unpassed"):
            effective = (*binding.effective, *rest) if binding.effective is not None else None
            if effective is not None and effective not in self.d_prefixes:
                self.refuse(frame, f"{_dotted(effective)} is not a value the render declares")
                return None
            return _Binding(binding.kind, local=local, effective=effective)
        if binding.kind in ("brand", "forloop"):
            path = (*(binding.effective or ()), *rest)
            if path not in (BRAND_PREFIXES if binding.kind == "brand" else FORLOOP_PATHS):
                self.refuse(frame, f"{_dotted(path)} is not a value the mail supplies")
                return None
            return _Binding(binding.kind, local=local, effective=path)
        if rest:
            self.refuse(frame, f"{_dotted(lookups)} looks up inside {head}, which holds one value")
            return None
        return binding

    def leaf(self, frame: _Frame, binding: _Binding) -> bool:
        """Whether ``binding`` reads one value, not an object or a list, here and in ``D``."""

        if binding.kind in ("value", "unpassed"):
            return binding.local in frame.leaves and (binding.effective is None or binding.effective in self.d_leaves)
        if binding.kind == "brand":
            return binding.effective in BRAND_PATHS
        if binding.kind == "forloop":
            return binding.effective in FORLOOP_PATHS and binding.effective != ("forloop",)
        return True

    def guarded(self, frame: _Frame, binding: _Binding) -> bool:
        return self.guard_key(binding) in frame.guards

    @staticmethod
    def guard_key(binding: _Binding) -> tuple[str, Path]:
        return ("e", binding.effective) if binding.effective is not None else ("l", binding.local)

    # -- expressions -----------------------------------------------------------

    def expression(self, frame: _Frame, expression: FilterExpression, use: str) -> _Binding | None:
        """Judge one expression read as ``use`` (rules 5, 11, 13 to 17); return what it binds to."""

        names = [FILTER_NAMES[function] for function, _ in expression.filters]
        variable = expression.var
        if not expression.is_var or variable.lookups is None:
            if use == "test" and names:
                self.refuse(frame, f"an if tests a literal through a filter ({expression.token})")
            if use == "sequence":
                self.refuse(frame, f"for loops over a list a row declares, not {expression.token}")
            return _Binding("computed" if names else "literal")
        binding = self.resolve(frame, tuple(variable.lookups))
        if binding is None:
            return None
        leaf = self.leaf(frame, binding)
        value = binding.kind in ("value", "unpassed")
        if names and names[0] != "length" and not leaf:
            self.refuse(frame, f"{expression.token} filters an object or a list; only length reads one")
        if use == "output" and not leaf and not (value and names[:1] == ["length"]):
            self.refuse(frame, f"{{{{ {expression.token} }}}} prints an object or a list")
        if use == "test" and not (
            (value and (names == ["length"] or leaf and not names))
            or (binding.kind in ("brand", "forloop", "global", "constant") and leaf and not names)
        ):
            self.refuse(frame, f"an if tests a declared value, its length or a name the mail supplies, not "
                               f"{expression.token}")
        if use == "sequence":
            if not value or (*binding.local, "*") not in frame.declared or (
                    binding.effective is not None and (*binding.effective, "*") not in self.d_prefixes):
                self.refuse(frame, f"for loops over a list a row declares, not {expression.token}")
                return None
        if value:
            self.walk.reads.append(TemplateRead(frame.name, binding.local, binding.effective, use))
            optional = names[:1] == ["default"] or self.guarded(frame, binding)
            if use in ("output", "argument") and not optional:
                if binding.kind == "unpassed":
                    self.refuse(frame, f"{expression.token} reads {binding.local[0]}, which it was not passed")
                elif binding.effective is not None:
                    self.walk.required.add(binding.effective)
            if names[:1] == ["date"] and binding.effective is not None:
                self.walk.date_paths.add(binding.effective)
        if names and use != "sequence":
            return _Binding("computed")
        return binding

    def bind(self, frame: _Frame, expression: FilterExpression) -> _Binding:
        """What a ``with`` value or an include argument binds its name to."""

        use = "argument" if expression.filters else "alias"
        binding = self.expression(frame, expression, use)
        return binding if binding is not None else _Binding("computed")

    def condition(self, frame: _Frame, condition: Any) -> int:
        """Judge each operand of an ``if``/``elif`` condition; return how many there are."""

        if type(condition) is TemplateLiteral:
            self.expression(frame, cast(FilterExpression, condition.value), "test")
            return 1
        return sum(self.condition(frame, operand) for operand in (condition.first, condition.second)
                   if operand is not None)

    def guards(self, frame: _Frame, condition: Any) -> frozenset[tuple[str, Path]]:
        """The paths an ``if`` branch guards: its condition, or each operand of an ``and`` of them."""

        if type(condition) is TemplateLiteral:
            expression = cast(FilterExpression, condition.value)
            if expression.is_var and expression.var.lookups is not None and not expression.filters:
                binding = self.resolve_quietly(frame, tuple(expression.var.lookups))
                if binding is not None and binding.kind in ("value", "unpassed"):
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
        sequence = self.expression(frame, cast(FilterExpression, node.sequence), "sequence")
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
        scope["forloop"] = _Binding("forloop", effective=("forloop",))
        self.nodes(_replace(frame, scope=scope, loops=loops), cast(NodeList, node.nodelist_loop), tree)
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
            self.owner._frame(target, row, top=False, scope=scope, loops=frame.loops, includes=stack),
            self.checks[target].pieces[tree.form].nodelist,
            tree,
        )


def _replace(frame: _Frame, **changes: Any) -> _Frame:
    return _Frame(**{**frame.__dict__, **changes})
