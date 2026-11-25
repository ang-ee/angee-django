"""What the message templates console reads: a row's permissions and its kind's message fields."""

from __future__ import annotations

from typing import Any

from rebac import to_subject_ref

from angee.graphql.testing.documents import GraphQLDocumentRead, execute_schema, measure_graphql_document, result_data
from angee.iam.roles import subject_permitted
from tests.conftest import create_platform_admin, create_user
from tests.test_messaging_templates import _row, _schema


def _template(key: str, source: str, language: str = "en", *, layout: str = "base") -> Any:
    return _row("template", key, language, layout=layout, source=source)


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
