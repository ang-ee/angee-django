"""What the message templates console reads: a row's permissions, its kind's message fields and the rows naming
it."""

from __future__ import annotations

import os
import time
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import system_context, to_subject_ref

from angee.base.models.identity import public_id_of
from angee.graphql.testing.documents import GraphQLDocumentRead, execute_schema, measure_graphql_document, result_data
from angee.iam.roles import subject_permitted
from angee.messaging_templates.testing.models import MessageTemplate
from angee.messaging_templates.walker import TemplateWalker
from tests.conftest import create_platform_admin, create_user
from tests.test_messaging_templates import _row, _schema
from tests.test_messaging_templates_render import TIMED, _recording

_LAYOUT = {
    "source": "<div>{% block content %}{% endblock %}</div>",
    "text_source": "{% block content %}{% endblock %}",
}
"""A layout's two forms, each holding the one empty content block."""


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
