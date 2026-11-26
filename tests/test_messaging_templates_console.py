"""What the message templates console reads and creates: a row's permissions, its kind's message fields, the rows
naming it, the direction its preview says, and the deployment's own row, decided before any work and seeded by the
server."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import replace
from itertools import product
from typing import Any, cast

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context, to_subject_ref
from rebac.backends import backend
from rebac.models import PermissionAuditEvent
from rebac.schema.ast import PermNil
from rebac.testing import install_schema

from angee.base.models.identity import public_id_of
from angee.graphql.testing.documents import GraphQLDocumentRead, execute_schema, measure_graphql_document, result_data
from angee.iam.roles import subject_permitted
from angee.messaging_templates.models import (
    BASE_LAYOUT,
    SHIPPED_DEFAULT_MESSAGE,
    MessageTemplateKind,
    MessageTemplateQuerySet,
)
from angee.messaging_templates.schema import MessageTemplateWriteBackend
from angee.messaging_templates.testing.models import MessageTemplate
from angee.messaging_templates.walker import TemplateWalker, TextDirection
from angee.provenance.context import changeset_model
from tests.conftest import create_platform_admin, create_user
from tests.test_messaging_templates import _UPDATE, _row, _schema
from tests.test_messaging_templates_layout import library as library
from tests.test_messaging_templates_render import TIMED, _recording, _text_read

_LAYOUT: dict[str, Any] = {
    "source": "<div>{% block content %}{% endblock %}</div>",
    "text_source": "{% block content %}{% endblock %}",
}
"""A layout's two forms, each holding the one empty content block."""

_DUPLICATE_MESSAGE = "This deployment already has a message template with this kind, key and language."


def _template(key: str, source: str, language: str = "en", *, layout: str = "base") -> Any:
    return _row("template", key, language, layout=layout, source=source)


def _include(name: str) -> str:
    return f'{{% include "{name}" only %}}'


# ---------- what a reader may do, and what a kind carries ----------


_PERMISSIONS = "{ message_templates { id kind key is_baseline permissions carries_message_fields } }"


def test_a_row_reports_what_its_reader_may_do_with_it_and_whether_its_kind_carries_message_fields(db: None) -> None:
    """An administrator previews a shipped default and writes, previews and deletes an override; a member does none."""

    del db
    admin = create_platform_admin("messaging-templates-console-admin")
    member = create_user("messaging-templates-console-member")
    _row(baseline=True)
    _row()
    _template("tests.welcome", "Hello.")
    _row("block", "button")
    _row("part", "greeting")

    def read(user: Any) -> dict[tuple[str, str, bool], tuple[list[str], bool]]:
        rows = result_data(execute_schema(_schema(), _PERMISSIONS, user=user))["message_templates"]
        return {(row["kind"], row["key"], row["is_baseline"]): (row["permissions"], row["carries_message_fields"])
                for row in rows}

    every = ["delete", "preview", "write"]
    assert read(admin) == {
        ("LAYOUT", "base", True): (["preview"], False),
        ("LAYOUT", "base", False): (every, False),
        ("TEMPLATE", "tests.welcome", False): (every, True),
        ("BLOCK", "button", False): (every, False),
        ("PART", "greeting", False): (every, False),
    }
    assert all(permissions == [] for permissions, _ in read(member).values())


def _measured(schema: Any, document: str, variables: dict[str, Any], actor: Any) -> GraphQLDocumentRead:
    """The second read of ``document``, after a warm one, so one-time reads stay out of the count."""

    execute_schema(schema, document, variables, user=actor)
    return measure_graphql_document(schema, document, variables, actor=actor)


def test_permissions_cost_the_same_at_one_and_at_twenty_five_rows(db: None) -> None:
    del db
    admin = create_platform_admin("messaging-templates-console-lister")
    schema = _schema()
    _row(baseline=True)
    one = _measured(schema, _PERMISSIONS, {}, admin)
    for index in range(1, 25):
        _row(key=f"layout_{index}", baseline=index % 2 == 0)
    many = _measured(schema, _PERMISSIONS, {}, admin)
    assert (len(one.data["message_templates"]), len(many.data["message_templates"])) == (1, 25)
    assert {tuple(row["permissions"]) for row in many.data["message_templates"]} == {
        ("preview",), ("delete", "preview", "write"),
    }
    many.assert_ceiling(statements=one.statements, repeated=one.repeated)


def test_presence_asks_preview_because_create_is_not_decidable_for_the_type(db: None) -> None:
    """The tripwire for the console page's presence ref, ``#preview``.

    ``create`` subtracts the filtered constant ``baseline``, which a type-level
    check cannot decide, so nobody holds ``#create`` there and a menu requiring
    it would vanish for administrators too. If a change to the permission schema
    makes ``create`` decidable for the type, this fails, and the ref can move.
    """

    del db
    preview, create = (f"messaging_templates.MessageTemplate#{name}" for name in ("preview", "create"))
    admin = create_platform_admin("messaging-templates-console-presence")
    member = create_user("messaging-templates-console-absent")
    assert subject_permitted(to_subject_ref(admin), [preview, create]) == [preview]
    assert subject_permitted(to_subject_ref(member), [preview, create]) == []


# ---------- what the verbs admit ----------


_ADMISSIONS = "{ message_templates { id overrides_default can_override can_keep_override } }"
_OVERRIDE = "mutation($id: ID!) { override_message_template(template: $id) { ok } }"
_KEEP = "mutation($id: ID!) { keep_message_template_override(template: $id) { ok } }"


def _states() -> dict[str, Any]:
    """A row in each state the two verbs tell apart."""

    rows = {
        "shipped default": _row("layout", "plain", baseline=True, **_LAYOUT),
        "overridden default": _row("layout", "base", baseline=True, **_LAYOUT),
        "override": _row("layout", "base", **_LAYOUT),
        "override, default updated": _row("layout", "letter", **_LAYOUT),
        "own row": _row("layout", "own", **_LAYOUT),
    }
    released = _row("layout", "letter", baseline=True, **_LAYOUT)
    with system_context(reason="tests.messaging_templates.console.release"):
        released.source = "<div>Released.{% block content %}{% endblock %}</div>"
        released.save()
    return rows


def _admitted(user: Any, document: str, row: Any) -> bool:
    """Whether the verb admits ``user`` on ``row``: it runs, and what it wrote is rolled back."""

    with transaction.atomic():
        result = execute_schema(_schema(), document, {"id": public_id_of(row)}, user=user)
        transaction.set_rollback(True)
    if result.errors:
        assert [error.extensions["code"] for error in result.errors] == ["PERMISSION_DENIED"], result.errors
        return False
    (answer,) = result.data.values()
    return bool(answer["ok"])


def test_each_verb_is_projected_as_what_it_then_admits(db: None) -> None:
    """For an administrator and a member, on every row state, each projected admission is what its verb does."""

    del db
    admin = create_platform_admin("messaging-templates-console-verbs")
    member = create_user("messaging-templates-console-onlooker")
    rows = _states()
    expected = {  # overrides_default, Override, Keep override, for an administrator
        "shipped default": (False, True, False),
        "overridden default": (False, True, False),
        "override": (True, False, True),
        "override, default updated": (True, False, True),
        "own row": (False, False, False),
    }
    for user, administrator in ((admin, True), (member, False)):
        listed = {row["id"]: row for row in result_data(execute_schema(_schema(), _ADMISSIONS, user=user))[
            "message_templates"]}
        for state, row in rows.items():
            projected = listed[public_id_of(row)]
            fact, override, keep = expected[state]
            assert (projected["overrides_default"], projected["can_override"], projected["can_keep_override"]) == (
                fact, override and administrator, keep and administrator), (state, user)
            assert projected["can_override"] is _admitted(user, _OVERRIDE, row), (state, user)
            assert projected["can_keep_override"] is _admitted(user, _KEEP, row), (state, user)
    overrides = "{ message_templates(where: {overrides_default: {_eq: true}}) { key } }"
    assert sorted(row["key"] for row in result_data(execute_schema(_schema(), overrides, user=member))[
        "message_templates"]) == ["base", "letter"]


@contextmanager
def _narrowed(name: str) -> Iterator[None]:
    """A deployment whose message templates' permission ``name`` admits nobody, for the block's duration; a field gate
    the addon declares none of (``write__layout``) is added, as a deployment adds one."""

    def narrowing(permissions: tuple[Any, ...]) -> tuple[Any, ...]:
        if all(permission.name != name for permission in permissions):
            return (*permissions, replace(permissions[0], name=name, expression=PermNil(), raw_text="nil"))
        return tuple(
            replace(permission, expression=PermNil(), raw_text="nil") if permission.name == name else permission
            for permission in permissions
        )

    active = backend()
    original = active.schema()
    narrowed = deepcopy(original)
    narrowed.definitions = [
        replace(definition, permissions=narrowing(definition.permissions))
        if definition.resource_type == "messaging_templates/message_template" else definition
        for definition in narrowed.definitions
    ]
    install_schema(narrowed, backend=active)
    try:
        yield
    finally:
        install_schema(original, backend=active)


def test_a_deployment_that_narrows_create_but_keeps_preview_is_not_offered_override(db: None) -> None:
    """A deployment whose ``create`` admits nobody: the reader still previews, and Override is projected and refused."""

    del db
    admin = create_platform_admin("messaging-templates-console-narrowed")
    shipped = _row("layout", "plain", baseline=True, **_LAYOUT)
    document = "{ message_templates { permissions can_override } }"
    with _narrowed("create"):
        listed = result_data(execute_schema(_schema(), document, user=admin))
        assert listed["message_templates"] == [{"permissions": ["preview"], "can_override": False}]
        assert _admitted(admin, _OVERRIDE, shipped) is False


_KEEPABLE = "{ message_templates { id overrides_default can_keep_override } }"
_OVERRIDABLE = "{ message_templates { id can_override } }"


def test_the_projected_admissions_cost_what_their_docstrings_say(db: None) -> None:
    """``overrides_default`` and ``can_keep_override`` ride the list's statement; ``can_override`` decides ``create``
    for each row, a read for one record's page."""

    del db
    admin = create_platform_admin("messaging-templates-console-costed")
    schema = _schema()
    _row("layout", "layout_0", baseline=True, **_LAYOUT)
    keep_one, override_one = (_measured(schema, document, {}, admin) for document in (_KEEPABLE, _OVERRIDABLE))
    for index in range(1, 25):
        _row("layout", f"layout_{index}", baseline=index % 2 == 0, **_LAYOUT)
    keep_many, override_many = (_measured(schema, document, {}, admin) for document in (_KEEPABLE, _OVERRIDABLE))
    assert [len(read.data["message_templates"]) for read in (keep_one, override_one, keep_many, override_many)] == [
        1, 1, 25, 25]
    keep_one.assert_ceiling(statements=9, repeated=5)
    keep_many.assert_ceiling(statements=keep_one.statements, repeated=keep_one.repeated)
    override_one.assert_ceiling(statements=11, repeated=5)
    override_many.assert_ceiling(statements=107, repeated=101)  # four statements for each further row


# ---------- backlinks: the rows naming a row ----------


_BACKLINKS = "{ message_templates { kind key language is_baseline backlinks { kind key language } } }"


def test_backlinks_are_the_rows_in_any_language_that_name_a_kind_and_key(db: None) -> None:
    """A layout's templates, a block's includers whatever their kind, nothing for a template or an unused part."""

    del db
    admin = create_platform_admin("messaging-templates-console-linker")
    member = create_user("messaging-templates-console-unlinked")
    _row("layout", "base", baseline=True, **_LAYOUT)
    _row("layout", "base", "es", **_LAYOUT)
    _row("layout", "letter", source='<div>{% include "blocks/button" only %}{% block content %}{% endblock %}</div>',
         text_source='{% include "blocks/button" only %}{% block content %}{% endblock %}')
    button = _row("block", "button", source="<p>Open</p>", text_source="Open")
    unused = _row("part", "unused")
    _row("part", "mention", source="Styled as blocks/button.")  # holds the name and includes nothing
    _template("tests.welcome", _include("blocks/button"))
    _template("tests.welcome", "Hola.", "es")

    def read(user: Any) -> dict[tuple[str, str, str, bool], list[tuple[str, str, str]] | None]:
        rows = result_data(execute_schema(_schema(), _BACKLINKS, user=user))["message_templates"]
        return {
            (row["kind"], row["key"], row["language"], row["is_baseline"]): None if row["backlinks"] is None else [
                (named["kind"], named["key"], named["language"]) for named in row["backlinks"]
            ]
            for row in rows
        }

    welcome = [("TEMPLATE", "tests.welcome", "en"), ("TEMPLATE", "tests.welcome", "es")]
    assert read(admin) == {
        ("LAYOUT", "base", "en", True): welcome,
        ("LAYOUT", "base", "es", False): welcome,
        ("LAYOUT", "letter", "en", False): [],
        ("BLOCK", "button", "en", False): [("LAYOUT", "letter", "en"), ("TEMPLATE", "tests.welcome", "en")],
        ("PART", "mention", "en", False): [],
        ("PART", "unused", "en", False): [],
        ("TEMPLATE", "tests.welcome", "en", False): [],
        ("TEMPLATE", "tests.welcome", "es", False): [],
    }
    assert set(read(member).values()) == {None}
    with system_context(reason="tests.messaging_templates.console.delete"):
        spanish = MessageTemplate.objects.get(kind="layout", key="base", language="es")
        with CaptureQueriesContext(connection) as captured:
            assert spanish.delete_blocker() is None  # named by both templates, and the shipped row still resolves
        assert not any('"source"' in query["sql"] for query in captured.captured_queries)  # whether, not which
        assert MessageTemplate.objects.get(pk=button.pk).delete_blocker() == (
            "Other message templates use this block, which would no longer resolve without this row: "
            "give its key a row in the default language first."
        )
        assert MessageTemplate.objects.get(pk=unused.pk).delete_blocker() is None
    by_pk = "query($id: String!) { message_templates_by_pk(id: $id) { backlinks { key language } } }"
    alone = result_data(execute_schema(_schema(), by_pk, {"id": public_id_of(button)}, user=admin))
    assert alone["message_templates_by_pk"]["backlinks"] == [
        {"key": "letter", "language": "en"}, {"key": "tests.welcome", "language": "en"},
    ]


def test_a_row_naming_a_listed_block_and_a_listed_part_is_checked_once(
    db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    del db
    admin = create_platform_admin("messaging-templates-console-both")
    _row("layout", "base", baseline=True, **_LAYOUT)
    _row("block", "button", source="<p>Open</p>", text_source="Open")
    _row("part", "thanks", source="Thank you.")
    _template("tests.both", _include("blocks/button") + "\n\n" + _include("parts/thanks"))
    checked: list[str] = []
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: checked.append(name))
    variables = {"where": {"key": {"_in": ["button", "thanks"]}}}
    rows = result_data(execute_schema(_schema(), _BLOCK_BACKLINKS, variables, user=admin))["message_templates"]
    assert {row["key"]: [named["key"] for named in row["backlinks"]] for row in rows} == {
        "button": ["tests.both"], "thanks": ["tests.both"],
    }
    assert checked == ["tests.both"]


_BLOCK_KEYS = [f"b{index:02}" for index in range(1, 26)]
_TALL = ("b01", "b02", "b03")
"""The blocks one template, ``tall``, includes together."""

_BLOCK_BACKLINKS = """
query($where: message_templates_bool_exp) {
  message_templates(where: $where) { id key backlinks { id key is_effective } }
}
"""
_BLOCKS = "query($where: message_templates_bool_exp) { message_templates(where: $where) { id key } }"


@pytest.fixture
def blocks(db: None) -> None:
    """25 blocks, each included by its own template, and ``tall``, including three of them."""

    del db
    _row("layout", "plain", **_LAYOUT)
    for key in _BLOCK_KEYS:
        _row("block", key, source=f"<p>Block {key}</p>", text_source=f"Block {key}")
        _template(f"t{key[1:]}", _include(f"blocks/{key}"), layout="plain")
    _template("tall", "\n\n".join(_include(f"blocks/{key}") for key in _TALL), layout="plain")


def _named(read: GraphQLDocumentRead) -> dict[str, list[str]]:
    rows = read.data["message_templates"]
    assert all(named["is_effective"] for row in rows for named in row["backlinks"])
    return {row["key"]: [named["key"] for named in row["backlinks"]] for row in rows}


def test_backlinks_cost_the_same_for_one_listed_block_and_for_twenty_five(
    blocks: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One read for the listed blocks' includers, each checked once by one walker: ``tall`` once for its three."""

    del blocks
    admin = create_platform_admin("messaging-templates-console-batcher")
    schema = _schema()
    checked: list[str] = []
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: checked.append(name))

    def read(keys: list[str]) -> GraphQLDocumentRead:
        variables = {"where": {"key": {"_in": keys}}}
        execute_schema(schema, _BLOCK_BACKLINKS, variables, user=admin)
        checked.clear()
        return measure_graphql_document(schema, _BLOCK_BACKLINKS, variables, actor=admin)

    one = read(_BLOCK_KEYS[:1])
    assert (_named(one), sorted(checked)) == ({"b01": ["t01", "tall"]}, ["t01", "tall"])
    one.assert_ceiling(statements=15, repeated=9)
    many = read(_BLOCK_KEYS)
    assert _named(many) == {key: [f"t{key[1:]}", *(["tall"] if key in _TALL else [])] for key in _BLOCK_KEYS}
    assert (len(checked), checked.count("tall")) == (26, 1)
    many.assert_ceiling(statements=one.statements, repeated=one.repeated)
    assert many.statements == one.statements


def test_a_member_reads_no_backlinks_and_causes_no_read_or_check(
    blocks: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    del blocks
    member = create_user("messaging-templates-console-lurker")
    schema = _schema()
    variables = {"where": {"key": {"_in": _BLOCK_KEYS}}}
    checked: list[str] = []
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: checked.append(name))

    def statements(document: str) -> list[str]:
        execute_schema(schema, document, variables, user=member)
        with CaptureQueriesContext(connection) as captured:
            rows = result_data(execute_schema(schema, document, variables, user=member))["message_templates"]
        assert len(rows) == 25 and all(row.get("backlinks", None) is None for row in rows)
        return [query["sql"] for query in captured.captured_queries]

    with_backlinks = statements(_BLOCK_BACKLINKS)
    assert len(with_backlinks) == len(statements(_BLOCKS))
    assert not any(" LIKE " in sql for sql in with_backlinks)
    assert checked == []


@TIMED
def test_backlinks_time(blocks: None) -> None:
    """An administrator's backlinks of 25 listed blocks, 26 includers checked, take well under a second of CPU."""

    del blocks
    admin = create_platform_admin("messaging-templates-console-timer")
    schema = _schema()
    variables = {"where": {"key": {"_in": _BLOCK_KEYS}}}
    execute_schema(schema, _BLOCK_BACKLINKS, variables, user=admin)
    started = time.process_time()
    rows = result_data(execute_schema(schema, _BLOCK_BACKLINKS, variables, user=admin))["message_templates"]
    spent = time.process_time() - started
    print(f"backlinks CPU 25 blocks {spent:.3f}s load {os.getloadavg()[0]:.0f}")  # noqa: T201 -- the report quotes it
    assert len(rows) == 25
    assert spent < 1.0


# ---------- what a preview says ----------


_DIRECTED = "query($id: ID!) { message_template_preview(template: $id) { rendered { subject html direction } } }"
_UNDIRECTED = "query($id: ID!) { message_template_preview(template: $id) { rendered { subject html } } }"


def test_a_preview_says_the_direction_of_the_mails_own_text(library: dict[str, Any]) -> None:
    """An Arabic mail whose subject starts with a Latin brand name reads right to left (``RTL`` on the wire), as its
    HTML's ``dir`` says, and an English one left to right (``LTR``), named in the console as its warning codes are.
    Reading the direction costs no statement and adds no gate."""

    del library
    admin = create_platform_admin("messaging-templates-console-director")
    member = create_user("messaging-templates-console-undirected")
    schema = _schema()
    sdl = schema.as_str()
    assert "direction: MessageTemplateTextDirection!" in sdl and "enum TextDirection" not in sdl
    arabic = _row("template", "tests.welcome", "ar", subject="Acme: مرحبا", source="مرحبا بك.")
    english = _row("template", "tests.welcome", subject="Acme: welcome", source="Welcome.")
    for row, wire, direction in ((arabic, "RTL", TextDirection.RTL), (english, "LTR", TextDirection.LTR)):
        variables = {"id": public_id_of(row)}
        undirected = _measured(schema, _UNDIRECTED, variables, admin)
        directed = _measured(schema, _DIRECTED, variables, admin)
        rendered = directed.data["message_template_preview"]["rendered"]
        assert (rendered["direction"], rendered["subject"]) == (wire, row.subject)
        assert f'<html lang="{row.language}" dir="{direction.value}"' in rendered["html"]
        assert (directed.statements, directed.repeated) == (undirected.statements, undirected.repeated)
        for user in (member, AnonymousUser()):
            answers = [execute_schema(schema, document, variables, user=user) for document in (_DIRECTED, _UNDIRECTED)]
            refusals = [[(error.extensions["code"], error.message) for error in answer.errors] for answer in answers]
            assert refusals[0] == refusals[1] and [code for code, _ in refusals[0]] == ["PERMISSION_DENIED"], refusals


# ---------- what a preview of a patch says ----------


_PATCHED = """
query($id: ID!, $patch: message_templates_set_input) {
  message_template_preview(template: $id, patch: $patch) {
    rendered { subject plain_text } warnings { code detail field }
  }
}
"""
_EDIT = {"subject": "Edited {{ brand.name }}"}


def _patch_rows() -> dict[str, Any]:
    """A shipped default, the deployment's override of it, and a row of the deployment's own; a second layout."""

    _row("layout", "letter")
    return {
        "shipped default": _row("template", "tests.welcome", baseline=True),
        "override": _row("template", "tests.welcome"),
        "own row": _row("template", "tests.own"),
    }


_NARROWED = {
    "preview only": "write", "layout gated": "write__layout", "sample gated": "write__sample",
    "format gated": "write__format",
}
"""The administrators a deployment narrows: one whose ``write`` admits nobody, others one column's field gate."""


@pytest.mark.parametrize(("caller", "state", "patch", "answer"), [
    pytest.param("administrator", "override", None, "renders", id="administrator-override"),
    pytest.param("administrator", "override", _EDIT, "renders", id="administrator-override-patched"),
    pytest.param("administrator", "own row", None, "renders", id="administrator-own"),
    pytest.param("administrator", "own row", _EDIT, "renders", id="administrator-own-patched"),
    pytest.param("administrator", "own row", {}, "renders", id="administrator-own-empty-patch"),
    pytest.param("administrator", "shipped default", None, "renders", id="administrator-shipped"),
    pytest.param("administrator", "shipped default", _EDIT, "denied", id="administrator-shipped-patched"),
    pytest.param("administrator", "shipped default", {}, "denied", id="administrator-shipped-empty-patch"),
    pytest.param("preview only", "own row", None, "renders", id="preview-only-own"),
    pytest.param("preview only", "own row", _EDIT, "denied", id="preview-only-own-patched"),
    pytest.param("preview only", "own row", {}, "denied", id="preview-only-own-empty-patch"),
    pytest.param("layout gated", "own row", {"layout": "letter"}, "denied", id="layout-gated-own-layout"),
    pytest.param("layout gated", "own row", _EDIT, "renders", id="layout-gated-own-patched"),
    pytest.param("layout gated", "own row", {"layout": "base"}, "renders", id="layout-gated-own-current-layout"),
    pytest.param("layout gated", "own row", {**_EDIT, "layout": "letter"}, "denied", id="layout-gated-own-two-columns"),
    pytest.param("sample gated", "own row", {"sample": {}}, "renders", id="sample-gated-own-equal-object"),
    pytest.param("format gated", "own row", {"format": "MARKDOWN"}, "renders", id="format-gated-own-member-name"),
    pytest.param("member", "own row", None, "denied", id="member-own"),
    pytest.param("member", "own row", {"source": ("{{ x }}" * 7_143)[:50_000]}, "denied", id="member-own-patched"),
    pytest.param("signed out", "own row", None, "signed out", id="signed-out-own"),
    pytest.param("signed out", "own row", _EDIT, "signed out", id="signed-out-own-patched"),
])
def test_who_may_preview_a_patch(
    library: dict[str, Any], monkeypatch: pytest.MonkeyPatch, caller: str, state: str, patch: dict[str, Any] | None,
    answer: str,
) -> None:
    """``preview`` admits the read, and a patch, empty or not, also asks ``write`` and what the save's gates ask of the
    columns it changes: so a shipped default, which only a load writes, a role granted ``preview`` alone and a column a
    deployment's field gate closes render the saved row only, a column resent as it is stored asks no gate, and a
    signed-in caller's patch is admitted exactly where the update of it is. All are decided before any row is cleaned
    or walked."""

    del library
    rows = _patch_rows()
    user = (create_user("messaging-templates-console-unpatched") if caller == "member" else AnonymousUser()
            if caller == "signed out" else create_platform_admin("messaging-templates-console-patcher"))
    checked: list[str] = []
    cleaned: list[Any] = []
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: checked.append(name))
    _recording(monkeypatch, MessageTemplate, "full_clean", lambda row, *args, **kwargs: cleaned.append(row.pk))
    variables = {"id": public_id_of(rows[state])}
    with _narrowed(_NARROWED[caller]) if caller in _NARROWED else nullcontext():
        result = execute_schema(_schema(), _PATCHED, {**variables, "patch": patch}, user=user)
        work = (list(checked), list(cleaned))
        if patch is not None and caller != "signed out":  # a signed-out update finds no row; the preview says why
            with transaction.atomic():
                updated = execute_schema(_schema(), _UPDATE, {**variables, "set": patch}, user=user)
                transaction.set_rollback(True)
            assert [(error.extensions or {}).get("code") for error in updated.errors or ()] == (
                [] if answer == "renders" else ["PERMISSION_DENIED"]), updated.errors
    if answer == "renders":
        rendered = result_data(result)["message_template_preview"]["rendered"]
        assert rendered["subject"] == ("Edited Angee" if patch == _EDIT else "Hello"), rendered
        assert work[1] == ([] if patch is None else [rows[state].pk])
        return
    (error,) = result.errors or [None]
    assert error is not None and error.extensions["code"] == "PERMISSION_DENIED", result
    assert ("Authentication required." in error.message) is (answer == "signed out"), error.message
    assert work == ([], [])


_REFUSED_PATCHES = {
    "walk": {"source": "{% if %}"},
    "fields": {"variables": [], "sample": [1]},
    "name": {"name": "n" * 201},
    "constraint": {"format": "html"},
    "layout and constraint": {"format": "html", "layout": ""},
    "variable path": {"variables": {"a" * 201: "x"}},
    "variable description": {"variables": {"name": "d" * 1_001}},
    "include depth": {"source": '{% include "parts/deep1" only %}'},
}
"""Patches a save refuses: the walk, two columns that are not objects (one message for both), a column's own
validator, a check constraint, the layout with a constraint at once, the two author limits on what a row declares,
and a walk past the include depth."""

_REFUSED_FIELDS = {"variable path": {"variables"}, "variable description": {"variables"}, "include depth": {None}}
"""The column each limit's refusal names: the author limits ``variables``, the walk's depth the row as a whole."""


@pytest.mark.parametrize("name", list(_REFUSED_PATCHES))
def test_a_patch_is_refused_as_its_save_is(library: dict[str, Any], name: str) -> None:
    """The preview of a patch the update refuses renders nothing and says why in the update's words: one
    ``SAVE_REFUSED`` warning for each refusal of the row as a whole (``formErrors``) and for each of a column's
    (``validationErrors``), naming the column."""

    del library
    admin = create_platform_admin("messaging-templates-console-refused")
    for depth in range(1, 6):  # parts/deep1 includes deep2 and so on: deep4 sits past the include depth
        _row("part", f"deep{depth}", source=f'{{% include "parts/deep{depth + 1}" only %}}' if depth < 5 else "Deep.")
    row = _row("template", "tests.refused")
    patch = _REFUSED_PATCHES[name]
    with transaction.atomic():
        updated = execute_schema(_schema(), _UPDATE, {"id": public_id_of(row), "set": patch}, user=admin)
        transaction.set_rollback(True)
    (error,) = updated.errors or [None]
    assert error is not None and error.extensions["code"] == "VALIDATION", updated
    refused = {(None, message) for message in error.extensions["formErrors"]} | {
        (field, message) for field, messages in error.extensions["validationErrors"].items() for message in messages}
    preview = result_data(execute_schema(_schema(), _PATCHED, {"id": public_id_of(row), "patch": patch}, user=admin))
    warnings = preview["message_template_preview"]["warnings"]
    assert preview["message_template_preview"]["rendered"] is None
    assert {warning["code"] for warning in warnings} == {"SAVE_REFUSED"}
    assert len(warnings) == len(refused)
    assert {(warning["field"], warning["detail"]) for warning in warnings} == refused
    assert name not in _REFUSED_FIELDS or {warning["field"] for warning in warnings} == _REFUSED_FIELDS[name]
    assert {warning["detail"] for warning in warnings} == {*error.extensions["formErrors"], *(
        message for messages in error.extensions["validationErrors"].values() for message in messages)}


def test_an_edit_the_save_admits_that_its_sample_cannot_render_is_a_render_error(library: dict[str, Any]) -> None:
    """A sample that lacks a value the template requires saves, and does not render: the preview says so with
    ``RENDER_ERROR``, never ``SAVE_REFUSED``, so a page tells "Save would refuse these edits" from "these edits would
    save, but do not render with these sample values"."""

    del library
    admin = create_platform_admin("messaging-templates-console-unrendered")
    row = _row("template", "tests.unrendered", source="Hello {{ name }}.", variables={"name": "x"},
               sample={"name": "Ana"})
    patch: dict[str, Any] = {"sample": {}}
    with transaction.atomic():
        updated = execute_schema(_schema(), _UPDATE, {"id": public_id_of(row), "set": patch}, user=admin)
        transaction.set_rollback(True)
    assert not updated.errors, updated.errors
    preview = result_data(execute_schema(_schema(), _PATCHED, {"id": public_id_of(row), "patch": patch}, user=admin))
    assert preview["message_template_preview"] == {"rendered": None, "warnings": [
        {"code": "RENDER_ERROR", "detail": "name is required and missing or null.", "field": None}]}


def test_a_patch_is_decoded_as_the_update_decodes_its_input(library: dict[str, Any]) -> None:
    """The console posts a format by its member name, as the record reads it: the preview takes ``MARKDOWN`` as the
    update does. The resource's backend hands the model the stored value, and refuses a name no column owns."""

    del library
    backend = MessageTemplateWriteBackend(MessageTemplate)
    assert backend.patch_values({"format": "MARKDOWN", "name": "Decoded"}) == {"format": "markdown", "name": "Decoded"}
    with pytest.raises(ValueError, match="not tags"):
        backend.patch_values({"source": "Hello.", "tags": []})
    admin = create_platform_admin("messaging-templates-console-decoder")
    row = _row("template", "tests.decoded")
    patch = {"format": "MARKDOWN", "source": "Hello again."}
    preview = result_data(execute_schema(_schema(), _PATCHED, {"id": public_id_of(row), "patch": patch}, user=admin))
    assert "Hello again." in preview["message_template_preview"]["rendered"]["plain_text"], preview
    with transaction.atomic():
        updated = execute_schema(_schema(), _UPDATE, {"id": public_id_of(row), "set": patch}, user=admin)
        transaction.set_rollback(True)
    assert result_data(updated)["update_message_templates_by_pk"]["source"] == "Hello again."


def test_a_patched_preview_writes_nothing(library: dict[str, Any], django_capture_on_commit_callbacks: Any) -> None:
    """A patch renders as it would save, and nothing is written: no save, no content hash, no history, no
    permission audit row, whatever commits; the saved preview writes none either. The save's gates are asked, on the
    row, in a rehearsal that rolls back what it writes, under a deployment's field gate too, which then refuses the
    column it closes; the copy is validated in the reader's own scope, so no bypass is audited."""

    del library
    admin = create_platform_admin("messaging-templates-console-unwritten")
    _row("layout", "letter")
    row = _row("template", "tests.unwritten")
    schema = _schema()
    variables = {"id": public_id_of(row)}

    def stored() -> tuple[Any, ...]:
        """The row as stored, its history, the permission audit and the changesets, read with no bypass of its own."""

        saved = MessageTemplate._base_manager.get(pk=row.pk)
        history = cast(Any, MessageTemplate).history.filter(id=row.pk).count()
        return (saved.source, saved.content_hash, saved.updated_at, history, PermissionAuditEvent.objects.count(),
                changeset_model()._base_manager.count())

    execute_schema(schema, _PATCHED, {**variables, "patch": {"source": "Hello, edited."}}, user=admin)
    before = stored()
    for patch in (None, {"source": "Hello, edited."}):
        with django_capture_on_commit_callbacks(execute=True):
            preview = result_data(execute_schema(schema, _PATCHED, {**variables, "patch": patch}, user=admin))
        text = preview["message_template_preview"]["rendered"]["plain_text"]
        assert ("Hello, edited." in text) is (patch is not None) and ("Hello." in text) is (patch is None), text
        assert stored() == before, patch
    with _narrowed("write__layout"):
        for patch, refused in (({"source": "Hello, edited."}, False), ({"layout": "letter"}, True)):
            with django_capture_on_commit_callbacks(execute=True):
                answer = execute_schema(schema, _PATCHED, {**variables, "patch": patch}, user=admin)
            assert [(error.extensions or {}).get("code") for error in answer.errors or ()] == (
                ["PERMISSION_DENIED"] if refused else []), answer.errors
            assert stored() == before, patch


def test_a_preview_reads_a_fixed_number_of_statements_with_a_patch_or_without(library: dict[str, Any]) -> None:
    """The saved preview, and a patch that changes what renders, its columns' gates asked of the row, validated as its
    save would be and rendered from the walk that validation ran, each read their measured statements, none repeated
    beyond the measured count."""

    del library
    admin = create_platform_admin("messaging-templates-console-measurer")
    schema = _schema()
    row = _row("template", "tests.measured", source=_include("parts/signature"))
    variables = {"id": public_id_of(row)}
    saved = _measured(schema, _PATCHED, {**variables, "patch": None}, admin)
    patched = _measured(schema, _PATCHED, {**variables, "patch": {"source": _include("parts/signature") + "\n\nMore."}},
                        admin)
    assert saved.data["message_template_preview"]["rendered"] is not None
    assert "More." in patched.data["message_template_preview"]["rendered"]["plain_text"]
    saved.assert_ceiling(statements=39, repeated=28)
    patched.assert_ceiling(statements=164, repeated=78)  # 41 of them the save's gates, rehearsed on the row


# ---------- creating the deployment's own row ----------


_INSERT = """
mutation($object: message_templates_insert_input!, $idempotency: String) {
  insert_message_templates_one(object: $object, idempotency_key: $idempotency) {
    id kind key language name is_baseline
  }
}
"""
_PREVIEW = "query($id: ID!) { message_template_preview(template: $id) { rendered { html } warnings { code } } }"


def _insert(
    user: Any, kind: str, key: str, language: str = "en", name: str = "New", *, idempotency: str | None = None,
) -> Any:
    variables = {"object": {"kind": kind, "key": key, "language": language, "name": name}, "idempotency": idempotency}
    return execute_schema(_schema(), _INSERT, variables, user=user)


def _claimed(key: str) -> bool:
    """Whether provenance holds a creation claim under ``key``."""

    with system_context(reason="tests.messaging_templates.console.claims"):
        return changeset_model()._base_manager.filter(key=key).exists()


def _rows(kind: str, key: str) -> list[Any]:
    with system_context(reason="tests.messaging_templates.console.read"):
        return list(MessageTemplate.objects.filter(kind=kind, key=key).order_by("language", "is_baseline"))


@pytest.mark.parametrize("kind", [str(kind) for kind in MessageTemplateKind])
def test_each_kind_starts_on_a_new_key_as_its_defaults_and_previews(library: dict[str, Any], kind: str) -> None:
    """Of the four inputs only the identity and the name are the caller's; the rest is the kind's starting row."""

    del library
    admin = create_platform_admin(f"messaging-templates-console-{kind}-creator")
    key = "tests.fresh" if kind == "template" else "fresh"
    created = result_data(_insert(admin, kind, key, name="Fresh"))["insert_message_templates_one"]
    assert {**created, "id": None} == {
        "id": None, "kind": kind.upper(), "key": key, "language": "en", "name": "Fresh", "is_baseline": False,
    }
    (row,) = _rows(kind, key)
    defaults = MessageTemplateKind(kind).handler.defaults
    assert {name: getattr(row, name) for name in defaults} == defaults
    preview = result_data(execute_schema(_schema(), _PREVIEW, {"id": created["id"]}, user=admin))
    assert preview["message_template_preview"]["rendered"] is not None, preview
    if kind == "template":
        assert row.layout == "base"
        assert [warning["code"] for warning in preview["message_template_preview"]["warnings"]] == ["PREHEADER"]


def test_a_new_language_of_a_key_starts_as_what_that_language_reads_today(library: dict[str, Any]) -> None:
    """Shipped ``greeting`` in English and Spanish: a German row starts as the English one, so German mail stays."""

    admin = create_platform_admin("messaging-templates-console-translator")
    values = {"person": {"first_name": "Ana"}}
    with system_context(reason="tests.messaging_templates.console.render"):
        english = MessageTemplate.objects.effective("part", "greeting", language="de")
        before = english.render(values, kit=library["kit"])
    assert (english.language, english.is_baseline) == ("en", True)
    result_data(_insert(admin, "part", "greeting", "de", name="Begrüßung"))
    with system_context(reason="tests.messaging_templates.console.render"):
        german = MessageTemplate.objects.effective("part", "greeting", language="de")
        after = german.render(values, kit=library["kit"])
    assert (german.language, german.is_baseline) == ("de", False)
    assert german.override_values() == {**english.override_values(), "name": "Begrüßung"}
    assert (after.html, after.plain_text) == (before.html, before.plain_text)


def test_starter_text_lands_only_on_a_key_with_no_row(db: None) -> None:
    """A key held only in languages a new one does not fall back to starts as one of them: English, else by code."""

    del db
    admin = create_platform_admin("messaging-templates-console-borrower")
    french = _row("part", "abroad", "fr", source="Bonjour.")
    _row("part", "abroad", "it", source="Ciao.")
    result_data(_insert(admin, "part", "abroad", "de", name="Gruß"))
    (german,) = [row for row in _rows("part", "abroad") if row.language == "de"]
    assert german.override_values() == {**french.override_values(), "name": "Gruß"}
    with override_settings(LANGUAGE_CODE="pt-br"):
        english = _row("part", "away", "en", source="Hello.")  # not the default language here: nothing resolves
        _row("part", "away", "de", source="Hallo.")
        with actor_context(admin):
            dutch = MessageTemplate.objects.create_own(kind="part", key="away", language="nl", name="Hoi")
    assert dutch.override_values() == {**english.override_values(), "name": "Hoi"}


def test_a_new_template_sits_in_a_layout_whichever_row_seeds_it(library: dict[str, Any]) -> None:
    """A new template starts in a layout and passes ``full_clean()``, seeded from what its language reads today, from
    the key's row in another language, or from the kind's starting row; the console refuses emptying its layout."""

    del library
    admin = create_platform_admin("messaging-templates-console-placer")
    _template("tests.read", "Read today.")  # the default language's row: what a German recipient reads today
    _template("tests.abroad", "Bonjour.", "fr")  # nothing resolves for German, so the French row seeds it
    starter = MessageTemplateKind("template").handler.defaults["source"]
    for key, seeded in (("tests.read", "Read today."), ("tests.abroad", "Bonjour."), ("tests.fresh", starter)):
        created = result_data(_insert(admin, "template", key, "de", name="Neu"))["insert_message_templates_one"]
        (row,) = [row for row in _rows("template", key) if row.language == "de"]
        assert (row.source, row.layout) == (seeded, BASE_LAYOUT), key
        with actor_context(admin):
            row.full_clean()
    (error,) = execute_schema(_schema(), _UPDATE, {"id": created["id"], "set": {"layout": ""}}, user=admin).errors
    assert (error.extensions["code"], list(error.extensions["validationErrors"])) == ("VALIDATION", ["layout"])


def _stored_past_save(row: Any, **columns: str) -> None:
    """Write ``columns`` into ``row``'s stored row in SQL, as rows stored before every save wrote newlines hold them."""

    quote = connection.ops.quote_name
    assignments = ", ".join(f"{quote(column)} = %s" for column in columns)
    table = quote(MessageTemplate._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {table} SET {assignments} WHERE id = %s", [*columns.values(), row.pk])


_RENDERED = """
query($id: ID!) { message_template_preview(template: $id) { rendered { subject preheader html plain_text } } }
"""


def test_a_row_seeded_from_text_stored_with_carriage_returns_stores_newlines(library: dict[str, Any]) -> None:
    """A key's row stored with carriage returns, before every save wrote newlines, seeds a new language: the new row
    stores newlines, has the content hash of the same text typed with newlines, and previews as that text does."""

    del library
    admin = create_platform_admin("messaging-templates-console-paster")
    pasted = {"subject": "Hi\r\nthere", "preheader": "pre\rheader", "source": "Hello,\r\nfriend."}
    typed: dict[str, Any] = {column: text.replace("\r\n", "\n").replace("\r", "\n") for column, text in pasted.items()}
    _stored_past_save(_template("tests.pasted", "Hello."), **pasted)
    twin = _row("template", "tests.typed", "de", **typed)
    created = result_data(_insert(admin, "template", "tests.pasted", "de", name="Neu"))["insert_message_templates_one"]
    (german,) = [row for row in _rows("template", "tests.pasted") if row.language == "de"]
    assert {column: getattr(german, column) for column in pasted} == typed
    assert german.content_hash == german.compute_content_hash() == twin.content_hash
    previews = [
        result_data(execute_schema(_schema(), _RENDERED, {"id": row_id}, user=admin))["message_template_preview"]
        for row_id in (created["id"], public_id_of(twin))
    ]
    assert previews[0]["rendered"] is not None and previews[0] == previews[1]
    assert previews[0]["rendered"]["subject"] == "Hi there"


def test_an_insert_seeded_from_a_row_past_the_include_depth_reads_no_further(
    db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new language of a key whose row includes a chain past the include depth is refused in the tree's words, after
    as many queries for a chain of five as for one of 900; a member is refused create before any of it is read."""

    del db
    admin = create_platform_admin("messaging-templates-console-chainer")
    member = create_user("messaging-templates-console-unchained")
    for index in range(900):
        _row("part", f"chain{index}", source="x" if index == 899 else _include(f"parts/chain{index + 1}"))
    queries = {}
    for length in (5, 900):
        _row("part", f"deep{length}", source=_include(f"parts/chain{900 - length}"))
        with CaptureQueriesContext(connection) as captured:
            (error,) = _insert(admin, "part", f"deep{length}", "de").errors
        assert (error.extensions["code"], error.extensions["formErrors"]) == (
            "VALIDATION", [f"parts/chain{902 - length}: includes nest over 3 deep"]), error.extensions
        queries[length] = len(captured)
    assert queries == DEEP_INSERT_QUERIES, queries
    checked: list[str] = []
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: checked.append(name))
    (error,) = _insert(member, "part", "deep900", "de").errors
    assert (error.extensions["code"], checked) == ("PERMISSION_DENIED", [])
    assert [row.language for row in _rows("part", "deep900")] == ["en"]


DEEP_INSERT_QUERIES = {5: 168, 900: 168}
"""An administrator's insert of a new language seeded from a row including a chain past the include depth: the same
for any chain, since the gather reads no level past the depth. 156 before a level's text was read after its sizes: that
read, through the reader's scope, adds four statements to each of the three levels."""


def test_an_insert_seeded_from_a_row_past_the_source_limit_reads_none_of_its_text(
    db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new language of a key whose row includes 600 parts of 48,999 characters is refused at the level's count,
    before any of the level's text is read; a member is refused create before any of it is read."""

    del db
    admin = create_platform_admin("messaging-templates-console-widener")
    member = create_user("messaging-templates-console-unwidened")
    for index in range(600):
        _row("part", f"wide{index}", source="\n".join(["a" * 999] * 49))
    wide = "\n".join("".join(_include(f"parts/wide{index}") for index in range(line, line + 25))
                     for line in range(0, 600, 25))
    _row("part", "wide", format="html", source=wide, text_source="x")
    read = _text_read(monkeypatch)
    with CaptureQueriesContext(connection) as captured:
        (error,) = _insert(admin, "part", "wide", "de").errors
    assert (error.extensions["code"], error.extensions["formErrors"], read) == (
        "VALIDATION", ["parts/wide: the rows it reaches hold over 150000 characters of source"], []), error.extensions
    assert len(captured) == WIDE_INSERT_QUERIES, len(captured)
    (error,) = _insert(member, "part", "wide", "de").errors
    assert (error.extensions["code"], read) == ("PERMISSION_DENIED", [])
    assert [row.language for row in _rows("part", "wide")] == ["en"]


WIDE_INSERT_QUERIES = 132
"""An administrator's insert of a new language seeded from a row whose level is past the source limit: the level is
counted from its sizes and refused, and no text read follows."""


def test_an_insert_seeded_from_a_row_past_the_variables_limit_reads_none_of_them(
    db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new language of a key whose row includes 400 parts stored past clean() with a 40,000-character description
    each is refused at the level's count, before any of the level's variables are read; a member is refused create
    before any of it is read."""

    del db
    admin = create_platform_admin("messaging-templates-console-overdeclarer")
    member = create_user("messaging-templates-console-undeclared")
    for index in range(400):
        _row("part", f"described{index}", source="x", variables={"a": "d" * 40_000})
    described = "\n".join("".join(_include(f"parts/described{index}") for index in range(line, line + 25))
                          for line in range(0, 400, 25))
    _row("part", "described", format="html", source=described, text_source="x")
    read = _text_read(monkeypatch)
    with CaptureQueriesContext(connection) as captured:
        (error,) = _insert(admin, "part", "described", "de").errors
    assert (error.extensions["code"], error.extensions["formErrors"], read) == (
        "VALIDATION", ["parts/described: the rows it reaches declare over 100000 characters of variables"], [],
    ), error.extensions
    assert len(captured) == DECLARED_INSERT_QUERIES, len(captured)
    (error,) = _insert(member, "part", "described", "de").errors
    assert (error.extensions["code"], read) == ("PERMISSION_DENIED", [])
    assert [row.language for row in _rows("part", "described")] == ["en"]


DECLARED_INSERT_QUERIES = 130
"""An administrator's insert of a new language seeded from a row whose level declares past the variables limit: the
level is counted from its sizes and refused, and none of its variables is read."""


def test_the_console_refuses_a_declared_path_or_description_over_its_length(db: None) -> None:
    """An administrator's update declaring a path over 200 characters or a description over 1,000 is refused under
    ``variables`` in the declaration's own words, and nothing is written."""

    del db
    admin = create_platform_admin("messaging-templates-console-declarer")
    part = _row("part", "declared", variables={"name": "The person's name."})
    for variables, message in (
        ({"a" * 201: "x"}, f"{'a' * 40}… is over 200 characters: name the value in fewer."),
        ({"name": "d" * 1_001}, "Describe name in at most 1000 characters."),
    ):
        written = {"id": public_id_of(part), "set": {"variables": variables}}
        (error,) = execute_schema(_schema(), _UPDATE, written, user=admin).errors
        assert (error.extensions["code"], error.extensions["validationErrors"]) == (
            "VALIDATION", {"variables": [message]}), error.extensions
    with system_context(reason="tests.messaging_templates.console.read"):
        assert MessageTemplate.objects.get(pk=part.pk).variables == {"name": "The person's name."}


def test_a_shipped_identity_is_overridden_never_created(library: dict[str, Any]) -> None:
    del library
    admin = create_platform_admin("messaging-templates-console-shadower")
    for idempotency in (None, "console-shadow"):
        (error,) = _insert(admin, "part", "greeting", "en", idempotency=idempotency).errors
        assert (error.extensions["code"], error.extensions["formErrors"]) == ("VALIDATION", [SHIPPED_DEFAULT_MESSAGE])
    assert not _claimed("console-shadow")
    with actor_context(admin), pytest.raises(ValidationError) as raised:
        MessageTemplate.objects.create_own(kind="part", key="greeting", language="EN", name="Greeting")
    assert raised.value.code == "shipped_default"
    assert [(row.language, row.is_baseline) for row in _rows("part", "greeting")] == [("en", True), ("es", True)]


def test_a_duplicate_is_refused_in_the_constraints_words_even_past_full_clean(
    db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second insert of one identity is refused before it is seeded, cleaned or walked; one a racing writer won
    after every check is refused at the save, in the same words."""

    del db
    admin = create_platform_admin("messaging-templates-console-duplicator")
    result_data(_insert(admin, "part", "fresh"))
    checked: list[str] = []
    cleaned: list[str] = []
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: checked.append(name))
    _recording(monkeypatch, MessageTemplate, "full_clean", lambda row: cleaned.append(row.key))
    for idempotency in (None, "console-again"):
        (error,) = _insert(admin, "part", "fresh", idempotency=idempotency).errors
        assert error.extensions["formErrors"] == [_DUPLICATE_MESSAGE]
    assert (checked, cleaned, _claimed("console-again")) == ([], [], False)
    judged: list[Any] = []
    validate_constraints = MessageTemplate.validate_constraints

    def won_by_a_racer(row: Any, exclude: Any = None) -> None:
        judged.append(exclude)
        if exclude is None:  # the save's handler: every earlier judgement saw no rival, the racer came after
            validate_constraints(row)

    monkeypatch.setattr(MessageTemplate, "validate_constraints", won_by_a_racer)
    with actor_context(admin), pytest.raises(ValidationError) as raised:
        MessageTemplate.objects.create_own(kind="part", key="fresh", language="en", name="Again")
    assert (raised.value.messages, judged[-1]) == ([_DUPLICATE_MESSAGE], None)
    assert len(_rows("part", "fresh")) == 1


def test_an_integrity_error_the_constraints_do_not_explain_propagates_and_creates_nothing(db: None) -> None:
    """The database refuses the row's insert itself, for a reason no constraint states: the error is not swallowed."""

    del db
    admin = create_platform_admin("messaging-templates-console-refused-by-the-database")
    insert = f'INSERT INTO "{MessageTemplate._meta.db_table}"'

    def refused_by_the_database(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        if sql.startswith(insert):
            raise IntegrityError("refused for a reason no constraint states")
        return execute(sql, params, many, context)

    with (
        connection.execute_wrapper(refused_by_the_database), actor_context(admin),
        pytest.raises(IntegrityError, match="no constraint states"),
    ):
        MessageTemplate.objects.create_own(kind="part", key="fresh", language="en", name="Fresh")
    assert _rows("part", "fresh") == []


def test_a_default_shipped_while_a_row_is_created_is_never_hidden(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A load ships the identity after the shipped-default check: the saved row would hide it unflagged, so the
    create is refused as that check refuses and the row is rolled back."""

    del db
    admin = create_platform_admin("messaging-templates-console-overtaken")
    effective_rows = MessageTemplateQuerySet.effective_rows
    loaded: list[Any] = []

    def a_load_ships_it_meanwhile(rows: Any, *args: Any, **kwargs: Any) -> Any:
        answer = effective_rows(rows, *args, **kwargs)
        if not loaded:
            loaded.append(_row("part", "racy", baseline=True, source="Shipped meanwhile."))
        return answer

    monkeypatch.setattr(MessageTemplateQuerySet, "effective_rows", a_load_ships_it_meanwhile)
    with actor_context(admin), pytest.raises(ValidationError) as raised:
        MessageTemplate.objects.create_own(kind="part", key="racy", language="en", name="Racy")
    assert raised.value.code == "shipped_default"
    assert [(row.language, row.is_baseline, row.source) for row in _rows("part", "racy")] == [
        ("en", True, "Shipped meanwhile."),
    ]


def test_a_member_or_a_signed_out_caller_is_refused_before_anything_is_judged(
    library: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create is decided first: a malformed identity and a shipped one get the same refusal, and nothing is lexed.

    With an idempotency key, a member's insert is claimed first and refused as without one, the claim rolled back;
    a signed-out caller's key is refused by provenance before anything else.
    """

    del library
    member = create_user("messaging-templates-console-intruder")
    checked: list[str] = []
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: checked.append(name))
    keys = []
    for label, user in (("member", member), ("signed-out", AnonymousUser())):
        for kind, key, language, name in (
            ("part", "fresh", "en", "Fresh"), ("part", "Not A Key", "en-us", ""), ("part", "greeting", "en", "Shadow"),
            ("template", "tests.fresh", "de", "Fresh"),
        ):
            for idempotency in (None, f"console-{label}-{kind}-{key}"):
                (error,) = _insert(user, kind, key, language, name, idempotency=idempotency).errors
                refusal = (error.extensions["code"], sorted(error.extensions.get("validationErrors", {})))
                keyed_signed_out = idempotency is not None and label == "signed-out"
                assert refusal == (("VALIDATION", ["idempotency_key"]) if keyed_signed_out else (
                    "PERMISSION_DENIED", [])), (label, key, idempotency)
                keys.append(idempotency)
    assert checked == []
    assert _rows("part", "fresh") == [] and _rows("template", "tests.fresh") == []
    assert not any(_claimed(key) for key in keys if key is not None)


def test_an_insert_of_what_no_column_can_store_is_refused_before_any_row_is_read(
    db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lone surrogate or a NUL in any column the insert takes: an administrator is refused under that column, never
    with an internal error, and a member and a signed-out caller are refused create first. No seat's insert reads a
    message template row, lexes anything or writes a row. A null or a value JSON cannot write never reaches the
    insert: its four inputs are strings the schema requires."""

    del db
    admin = create_platform_admin("messaging-templates-console-unstorable")
    member = create_user("messaging-templates-console-unstorable-member")
    checked: list[str] = []
    _recording(monkeypatch, TemplateWalker, "_check", lambda walker, row, name: checked.append(name))
    table = MessageTemplate._meta.db_table
    for column, unstorable in product(("kind", "key", "language", "name"), ("a\ud800", "a\x00")):
        identity = {"kind": "part", "key": "fresh", "language": "en", "name": "Fresh", column: unstorable}
        for user, refusal in (
            (admin, ("VALIDATION", [column])), (member, ("PERMISSION_DENIED", [])),
            (AnonymousUser(), ("PERMISSION_DENIED", [])),
        ):
            with CaptureQueriesContext(connection) as captured:
                (error,) = _insert(user, **identity).errors
            assert (error.extensions["code"], sorted(error.extensions.get("validationErrors", {}))) == refusal, (
                column, unstorable, user)
            assert not any(table in query["sql"] for query in captured.captured_queries), (column, unstorable, user)
    assert checked == []
    with system_context(reason="tests.messaging_templates.console.read"):
        assert not MessageTemplate.objects.exists()


def test_an_identity_its_validators_refuse_is_refused_under_its_fields(db: None) -> None:
    del db
    admin = create_platform_admin("messaging-templates-console-misspeller")
    for idempotency in (None, "console-misspelled"):
        (error,) = _insert(admin, "part", "Not A Key", "en-us", idempotency=idempotency).errors
        assert error.extensions["code"] == "VALIDATION"
        assert set(error.extensions["validationErrors"]) == {"key", "language"}
    assert _rows("part", "Not A Key") == [] and not _claimed("console-misspelled")


def test_a_replayed_insert_returns_the_row_it_created(db: None) -> None:
    del db
    admin = create_platform_admin("messaging-templates-console-replayer")
    first = result_data(_insert(admin, "part", "fresh", idempotency="console-create"))
    assert result_data(_insert(admin, "part", "fresh", idempotency="console-create")) == first
    assert len(_rows("part", "fresh")) == 1 and _claimed("console-create")
