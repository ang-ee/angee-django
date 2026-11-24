"""``ANGEE_REBAC_READ_THROUGH_PARENT``: a referred record's label shown with the row that refers to it.

With the switch (and ``REBAC_DISPLAY_FIELDS``, which it turns on), a to-one relation
field whose every reader in the document reads its target's display fields alone
(``Meta.rebac_display_fields``) asks REBAC for display rows: the target's key and
label, read with no permission statement, whether or not the reader may read it.
These tests exercise that contract on test-owned models; the composed models'
declarations and their audience are ``tests.native_read_through_parent``'s.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, cast

import pytest
import strawberry
import strawberry_django
from django.db import connection, models
from django.test.utils import CaptureQueriesContext, override_settings
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    display,
    system_context,
    to_object_ref,
    write_relationships,
)
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.managers import RebacQuerySet
from rebac.schema import parse_zed
from rebac.testing import install_schema
from strawberry import auto

from angee.base.models.base import AngeeDataModel
from angee.base.testing.queries import statement_startswith
from angee.graphql.checks import check_display_fields
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.relations import (
    actor_scoped_public_id,
    actor_scoped_to_many,
    actor_scoped_to_one,
    display_audience,
)
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import execute_schema, result_data
from tests.conftest import SchemaAddon
from tests.tables import model_tables

# The switch's effects on display rows, as base autoconfig derives them.
ON = {"ANGEE_REBAC_READ_THROUGH_PARENT": True, "REBAC_DISPLAY_FIELDS": True}

POLICY = """
definition auth/user {}
definition tests/label_tag {
    relation reader: auth/user
    relation writer: auth/user
    permission read = reader + writer
    permission write = writer
    permission read__cost = writer
}
definition tests/label_card {
    relation reader: auth/user
    relation auditor: auth/user
    permission browse = reader
    permission read = reader + auditor
}
definition tests/label_note {
    relation reader: auth/user
    permission read = reader
}
"""


class LabelTag(AngeeDataModel):
    """A referred record whose ``code`` is its label; ``secret`` and the gated ``cost`` are not."""

    sqid_prefix = "ltg_"
    code = models.CharField(max_length=32)
    secret = models.CharField(max_length=32, default="")
    cost = models.IntegerField(default=0)

    class Meta(AngeeDataModel.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "tests/label_tag"
        rebac_display_fields = ("code",)


class LabelCard(AngeeDataModel):
    """A row that refers to a tag."""

    sqid_prefix = "lcd_"
    name = models.CharField(max_length=32)
    tag = models.ForeignKey(LabelTag, null=True, on_delete=models.SET_NULL, related_name="cards")

    class Meta(AngeeDataModel.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "tests/label_card"
        ordering = ("name",)


class LabelNote(LabelCard):
    """A card of another kind (multi-table inheritance): its ``tag`` is its parent card's."""

    remark = models.CharField(max_length=32, default="")

    class Meta:
        app_label = "scopedemo"
        rebac_resource_type = "tests/label_note"


# The rows a resolver saw through ``tag``, for the tests that look at them.
SEEN: list[Any] = []


@strawberry_django.type(LabelTag)
class LabelTagType(AngeeNode):
    code: auto
    secret: auto
    cost: int | None
    cards: list[LabelCardType] = actor_scoped_to_many("cards")


@strawberry_django.type(LabelCard)
class LabelCardType(AngeeNode):
    name: auto
    tag: LabelTagType | None = actor_scoped_to_one("tag")
    tag_ref: strawberry.ID | None = actor_scoped_public_id("tag")

    @strawberry_django.field(only=["name"])
    def shout(self) -> str:
        """Declares what it reads, and reads nothing through ``tag``."""

        return str(cast(Any, self).name).upper()

    @strawberry_django.field(only=["tag_id"])
    def tag_secret(self) -> str | None:
        """Declares that it reads ``tag`` through the row."""

        tag = cast(Any, self).tag
        return tag.secret if tag is not None else None

    @strawberry_django.field(only=["tag__secret"])
    def tag_secret_by_path(self) -> str | None:
        """Declares a path through ``tag``."""

        tag = cast(Any, self).tag
        return tag.secret if tag is not None else None

    @strawberry_django.field(only=["name"])
    def misdeclared(self) -> str | None:
        """Declares ``name`` alone and reads ``tag`` through the row all the same."""

        tag = cast(Any, self).tag
        SEEN.append(tag)
        return tag.secret if tag is not None else None

    @strawberry.field
    def caption(self) -> str:
        """Declares nothing."""

        return str(cast(Any, self).name)

    @strawberry_django.field
    def tag_secret_undeclared(self) -> str | None:
        """A strawberry-django resolver that declares nothing and reads ``tag`` through the row."""

        tag = cast(Any, self).tag
        return tag.secret if tag is not None else None


@strawberry_django.type(LabelNote)
class LabelNoteType(AngeeNode):
    name: auto
    tag: LabelTagType | None = actor_scoped_to_one("tag")

    @strawberry.field
    def caption(self) -> str:
        """Declares nothing."""

        return str(cast(Any, self).name)


@strawberry_django.type(LabelCard, name="BrowsedCardType")
class BrowsedCardType(AngeeNode):
    name: auto
    tag: LabelTagType | None = actor_scoped_to_one("tag")


@strawberry_django.type(LabelCard, name="PlainLabelCardType")
class PlainLabelCardType(AngeeNode):
    name: auto
    tag: LabelTagType | None


TYPES = (LabelCardType, LabelTagType)


@pytest.fixture
def label_case(db: None):
    """Alice reads three cards, referring to a tag she reads, to one she does not and to none; Bob reads a fourth."""

    del db
    reset_backend()
    active = backend()
    assert isinstance(active, LocalBackend)
    with model_tables((LabelTag, LabelCard, LabelNote)):
        install_schema(parse_zed(POLICY), backend=active)
        try:
            alice = SubjectRef.of("auth/user", "alice")
            bob = SubjectRef.of("auth/user", "bob")
            cards = hasura_model_resource(
                LabelCardType, model=LabelCard, name="label_cards", filterable=["name", "tag"],
                sortable=["name", "tag"], aggregatable=[], insert=False, update=False, delete=False,
            )
            browsed = hasura_model_resource(
                BrowsedCardType, model=LabelCard, name="browsed_cards", filterable=["name"], sortable=["name"],
                aggregatable=[], insert=False, update=False, delete=False,
                get_queryset=lambda info: LabelCard.objects.with_action("browse"),
            )
            plain = hasura_model_resource(
                PlainLabelCardType, model=LabelCard, name="plain_cards", filterable=["name"], sortable=["name"],
                aggregatable=[], insert=False, update=False, delete=False,
            )
            notes = hasura_model_resource(
                LabelNoteType, model=LabelNote, name="label_notes", filterable=["name"], sortable=["name"],
                aggregatable=[], insert=False, update=False, delete=False,
            )
            tags = hasura_model_resource(
                LabelTagType, model=LabelTag, name="label_tags", filterable=["code"], sortable=["code"],
                aggregatable=[], insert=False, update=False, delete=False,
            )
            # One resource of a model per schema: the cards are read three ways in three schemas.
            owner = GraphQLSchemas([
                SchemaAddon({
                    "public": {
                        "query": (cards.query, tags.query, notes.query),
                        "types": (*TYPES, LabelNoteType, *cards.types, *tags.types, *notes.types),
                    },
                    "browse": {"query": (browsed.query,), "types": (BrowsedCardType, *TYPES, *browsed.types)},
                    "console": {"query": (plain.query,), "types": (PlainLabelCardType, *TYPES, *plain.types)},
                })
            ])
            with system_context(reason="tests.display_rows.seed"):
                one = LabelTag.objects.create(code="one", secret="s-one", cost=1)
                two = LabelTag.objects.create(code="two", secret="s-two", cost=2)
                first = LabelCard.objects.create(name="a", tag=one)
                second = LabelCard.objects.create(name="b", tag=two)
                third = LabelCard.objects.create(name="c")
                fourth = LabelCard.objects.create(name="d", tag=one)
                note = LabelNote.objects.create(name="n", tag=two)
            write_relationships([
                *(RelationshipTuple(to_object_ref(card), "reader", alice) for card in (first, second, third)),
                RelationshipTuple(to_object_ref(fourth), "reader", bob),
                RelationshipTuple(to_object_ref(one), "reader", alice),
                RelationshipTuple(to_object_ref(note), "reader", alice),
            ])
            SEEN.clear()
            yield SimpleNamespace(
                owner=owner, schema=owner.build("public"), browse=owner.build("browse"),
                alice=alice, one=one, two=two,
            )
        finally:
            SEEN.clear()
            reset_backend()


LABELS = "query { label_cards(order_by: [{name: asc}]) { name tag { id code } } }"
AUDIT_TIME = re.compile(r"'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?'")


def _read(case: Any, document: str, *, schema: Any = None, **settings: Any) -> tuple[dict[str, Any], list[str]]:
    """The document's data and statements for Alice, after one warm execution fills the process's policy caches."""

    schema = schema or case.schema
    with override_settings(**settings):
        result_data(execute_schema(schema, document, {}, user=case.alice))
        SEEN.clear()
        with CaptureQueriesContext(connection) as captured:
            data = result_data(execute_schema(schema, document, {}, user=case.alice))
    # An audit row's time is the one value that differs between two reads of one document.
    return data, [AUDIT_TIME.sub("'<time>'", query["sql"]) for query in captured.captured_queries]


def _tag_reads(statements: list[str]) -> list[str]:
    """The statements that read tags, field-gate probes apart; a scoped one carries ``rebac_`` SQL."""

    return [
        sql
        for sql in statements
        if statement_startswith(sql, f'SELECT "{LabelTag._meta.db_table}".') and ' AS "pk", CASE WHEN ' not in sql
    ]


def _tags(data: dict[str, Any], root: str = "label_cards") -> list[Any]:
    return [card["tag"] for card in data[root]]


def test_a_label_alone_is_read_as_a_display_row_with_no_permission_statement(label_case: Any) -> None:
    off, off_sql = _read(label_case, LABELS)
    on, on_sql = _read(label_case, LABELS, **ON)
    one, two = (public_id(tag) for tag in (label_case.one, label_case.two))
    assert _tags(off) == [{"id": one, "code": "one"}, None, None]
    # The one difference: the label and key of the tag Alice may not read.
    assert _tags(on) == [{"id": one, "code": "one"}, {"id": two, "code": "two"}, None]
    assert len(on_sql) <= len(off_sql)
    [scoped] = _tag_reads(off_sql)
    [delivered] = _tag_reads(on_sql)
    assert "rebac_" in scoped
    assert "rebac_" not in delivered
    assert '"code"' in delivered and '"secret"' not in delivered and '"cost"' not in delivered


@pytest.mark.parametrize(
    "document",
    [
        # Another field of the referred record, gated or not.
        "query { label_cards(order_by: [{name: asc}]) { name tag { id code secret } } }",
        "query { label_cards(order_by: [{name: asc}]) { name tag { id code cost } } }",
        "query { label_cards(order_by: [{name: asc}]) { name tag { id display_name } } }",
        "query { label_cards(order_by: [{name: asc}]) { name tag { id code redacted_fields } } }",
        # The same relation selected wider under an alias, in a fragment or at another path.
        "query { label_cards(order_by: [{name: asc}]) { name a: tag { code } b: tag { code secret } } }",
        "query { label_cards(order_by: [{name: asc}]) { name tag { ...Wide } } } "
        "fragment Wide on LabelTagType { id code secret }",
        "query { label_cards(order_by: [{name: asc}]) { name tag { code } } "
        "label_tags(order_by: [{code: asc}]) { cards { name tag { id secret } } } }",
        # A nested relation of the referred record.
        "query { label_cards(order_by: [{name: asc}]) { name tag { code cards { name } } } }",
        # A filter or an ordering on the relation.
        "query { label_cards(where: {tag: {_is_null: false}}, order_by: [{name: asc}]) { name tag { code } } }",
        "query { label_cards(order_by: [{tag: asc}, {name: asc}]) { name tag { code } } }",
        # Another reader of the relation through the row: one that declares it, one that declares nothing.
        "query { label_cards(order_by: [{name: asc}]) { name tag { code } tag_secret } }",
        "query { label_cards(order_by: [{name: asc}]) { name tag { code } tag_secret_by_path } }",
        "query { label_cards(order_by: [{name: asc}]) { name tag { code } caption } }",
        "query { label_cards(order_by: [{name: asc}]) { name tag { code } tag_secret_undeclared } }",
        "query { label_cards(order_by: [{name: asc}]) { name tag { code } ...Caption } } "
        "fragment Caption on LabelCardType { caption }",
        "query { label_cards(order_by: [{name: asc}]) { name tag { code } display_name } }",
        # A child model's rows read their parent's relation: the same readers decide.
        "query { label_notes(order_by: [{name: asc}]) { name tag { code } caption } }",
    ],
)
def test_more_than_the_label_keeps_the_scoped_read(label_case: Any, document: str) -> None:
    off, off_sql = _read(label_case, document)
    on, on_sql = _read(label_case, document, **ON)
    assert on == off
    assert on_sql == off_sql


def test_a_child_model_shows_its_parents_relation_as_a_label(label_case: Any) -> None:
    document = "query { label_notes(order_by: [{name: asc}]) { name tag { id code } } }"
    off, _off_sql = _read(label_case, document)
    on, on_sql = _read(label_case, document, **ON)
    assert _tags(off, "label_notes") == [None]
    assert _tags(on, "label_notes") == [{"id": public_id(label_case.two), "code": "two"}]
    assert all("rebac_" not in sql for sql in _tag_reads(on_sql))


def test_a_resolver_that_declares_its_reads_lets_the_label_through(label_case: Any) -> None:
    document = "query { label_cards(order_by: [{name: asc}]) { name shout tag { id code } } }"
    on, on_sql = _read(label_case, document, **ON)
    assert [card["shout"] for card in on["label_cards"]] == ["A", "B", "C"]
    assert [tag and tag["code"] for tag in _tags(on)] == ["one", "two", None]
    assert all("rebac_" not in sql for sql in _tag_reads(on_sql))


def test_the_relation_holds_the_display_row_and_nothing_loads_the_record(label_case: Any) -> None:
    """A reader that reads the relation through its row and does not say so gets the display row.

    It sees the label and the key and nothing else: no statement loads the tag, so
    what it shows differs from the switch off (``s-one`` there), never more.
    """

    document = "query { label_cards(order_by: [{name: asc}]) { name tag { id code } misdeclared } }"
    off, off_sql = _read(label_case, document)
    assert [card["misdeclared"] for card in off["label_cards"]] == ["s-one", None, None]
    on, on_sql = _read(label_case, document, **ON)
    held = [row for row in SEEN if row is not None]
    assert [card["misdeclared"] for card in on["label_cards"]] == [None, None, None]
    [delivered] = _tag_reads(on_sql)
    assert "rebac_" not in delivered and '"secret"' not in delivered
    assert len(on_sql) == len(_read(label_case, LABELS, **ON)[1])
    assert [row.code for row in held] == ["one", "two"]
    for row in held:
        assert display.is_display_row(row)
        assert row.secret is None and row.cost is None
        assert row.actor() == label_case.alice
        # A display row is not the record: written back, it is refused as the record would be.
        row.code = "changed"
        with pytest.raises(PermissionDenied):
            row.save()
    with system_context(reason="tests.display_rows.check"):
        stored = sorted(LabelTag.objects.values_list("code", "secret", "cost"))
    assert stored == [("one", "s-one", 1), ("two", "s-two", 2)]


def test_rows_admitted_under_another_action_refer_only_where_it_is_proven_to_imply_read(label_case: Any) -> None:
    """REBAC decides what an admitted row refers to; the relation fields follow it.

    Cards read under ``browse``, which grants no more than ``read``, show their
    tags' labels; once ``browse`` grants what ``read`` does not, they refer to
    nothing and the tags are read by their permission statement. A stored override
    proves nothing either (the composed host's ``tests.native_read_through_parent``).
    """

    browsed = "query { browsed_cards(order_by: [{name: asc}]) { name tag { id code } } }"
    on, on_sql = _read(label_case, browsed, schema=label_case.browse, **ON)
    assert [tag and tag["code"] for tag in _tags(on, "browsed_cards")] == ["one", "two", None]
    assert all("rebac_" not in sql for sql in _tag_reads(on_sql))
    widened = "    relation peeker: auth/user\n    permission browse = reader + peeker\n"
    install_schema(parse_zed(POLICY.replace("    permission browse = reader\n", widened)), backend=backend())
    off, off_sql = _read(label_case, browsed, schema=label_case.browse)
    on, on_sql = _read(label_case, browsed, schema=label_case.browse, **ON)
    assert on == off
    assert _tags(on, "browsed_cards")[1] is None
    assert on_sql == off_sql
    # Rows admitted under their read action itself still refer.
    labels, labels_sql = _read(label_case, LABELS, **ON)
    assert _tags(labels)[1] == {"id": public_id(label_case.two), "code": "two"}
    assert all("rebac_" not in sql for sql in _tag_reads(labels_sql))


@pytest.mark.parametrize(
    "settings",
    [
        # The switch off: a project's own REBAC_DISPLAY_FIELDS asks nothing of the relation fields.
        {"REBAC_DISPLAY_FIELDS": True},
        # The switch on with display rows turned off: children alone.
        {"ANGEE_REBAC_READ_THROUGH_PARENT": True, "REBAC_DISPLAY_FIELDS": False},
        # Set at run time without REBAC_DISPLAY_FIELDS, the switch asks for nothing.
        {"ANGEE_REBAC_READ_THROUGH_PARENT": True},
    ],
)
def test_nothing_asks_for_display_rows_unless_both_settings_are_on(
    label_case: Any, monkeypatch: pytest.MonkeyPatch, settings: dict[str, Any],
) -> None:
    off, off_sql = _read(label_case, LABELS)

    def refused(self: Any) -> Any:
        raise AssertionError("for_display() was asked for")

    monkeypatch.setattr(RebacQuerySet, "for_display", refused)
    data, statements = _read(label_case, LABELS, **settings)
    assert data == off
    assert statements == off_sql
    with override_settings(**settings):
        assert check_display_fields() == []


def test_the_audience_lists_every_field_named_for_the_relation(label_case: Any) -> None:
    from angee.base.permissions import effective_rebac_definition, read_arms

    # Readers are the arms of the holder's read in its composed declaration, which test models have none of.
    assert read_arms(LabelCard, definition_for=effective_rebac_definition) == ("reader", "auditor")
    assert read_arms(LabelCard) == ()
    audience = {
        (entry.schema, entry.type_name, entry.field): (entry.holder, entry.kind, entry.readers)
        for entry in display_audience(LabelTag, label_case.owner)
    }
    holder = LabelCard._meta.label
    assert audience == {
        ("console", "PlainLabelCardType", "tag"): (holder, "plain", ()),
        ("browse", "BrowsedCardType", "tag"): (holder, "prefetch", ()),
        ("browse", "LabelCardType", "tag"): (holder, "prefetch", ()),
        ("browse", "LabelCardType", "tag_ref"): (holder, "other", ()),
        ("console", "LabelCardType", "tag"): (holder, "prefetch", ()),
        ("console", "LabelCardType", "tag_ref"): (holder, "other", ()),
        ("public", "LabelCardType", "tag"): (holder, "prefetch", ()),
        ("public", "LabelNoteType", "tag"): (LabelNote._meta.label, "prefetch", ()),
        ("public", "LabelCardType", "tag_ref"): (holder, "other", ()),
    }


def test_the_checks_refuse_a_gated_label_and_a_label_a_join_reaches(monkeypatch: pytest.MonkeyPatch) -> None:
    from django.apps import apps

    with override_settings(**ON):
        # The framework's own declarations pass.
        assert check_display_fields() == []
        extraction = apps.get_model("extraction", "Extraction")
        party = apps.get_model("parties", "Party")
        monkeypatch.setattr(extraction._meta, "rebac_display_fields", ("result",), raising=False)
        monkeypatch.setattr(party._meta, "rebac_display_fields", ("display_name",), raising=False)
        found = {(error.id, error.obj) for error in check_display_fields()}
        assert found == {("angee.graphql.E005", extraction), ("angee.graphql.E006", party)}
        [joined] = [error for error in check_display_fields() if error.id == "angee.graphql.E006"]
        assert "AddressType.party" in joined.msg
    # Off, nothing is checked.
    assert check_display_fields() == []


def public_id(row: models.Model) -> str:
    return cast(Any, row).sqid
