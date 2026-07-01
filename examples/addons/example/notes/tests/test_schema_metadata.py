"""Tests for the notes example's emitted GraphQL metadata artifact."""

from __future__ import annotations

from django.test import SimpleTestCase

from angee.graphql.sdl import GraphQLSdl


class NotesSchemaMetadataTests(SimpleTestCase):
    """Snapshot the note resource facts the frontend consumes."""

    maxDiff = None

    def test_public_note_resource_metadata_snapshot(self) -> None:
        """The source schema renders a complete notes resource artifact."""

        metadata = GraphQLSdl.from_discovery().render_metadata()["public"]
        note = {
            item["modelLabel"]: item
            for item in metadata["angee"]["resources"]
        }["notes.Note"]
        fields = {field["name"]: field for field in note["fields"]}

        self.assertEqual(
            note["roots"],
            {
                "aggregate": "notes_aggregate",
                "changes": None,
                "create": "insert_notes_one",
                "delete": "delete_notes_by_pk",
                "deletePreview": "delete_note",
                "detail": "notes_by_pk",
                "groups": "notes_groups",
                "list": "notes",
                "revisions": "note_revisions",
                "update": "update_notes_by_pk",
            },
        )
        self.assertEqual(note["typeNames"]["filter"], "notes_bool_exp")
        self.assertEqual(note["typeNames"]["order"], "notes_order_by")
        self.assertEqual(note["typeNames"]["aggregate"], "notes_aggregate")
        self.assertEqual(note["typeNames"]["grouped"], "notes_group")
        self.assertEqual(note["typeNames"]["groupKey"], "NoteTypeGroupKey")
        self.assertEqual(note["typeNames"]["groupBySpec"], "NoteTypeGroupBySpec")
        self.assertEqual(note["typeNames"]["groupOrder"], "NoteTypeGroupOrder")
        self.assertEqual(note["typeNames"]["having"], "NoteTypeHaving")
        self.assertEqual(note["typeNames"]["createInput"], "notes_insert_input")
        self.assertEqual(note["typeNames"]["updateInput"], "notes_set_input")
        self.assertEqual(
            {
                dimension["field"]: (
                    dimension["input"],
                    dimension["key"],
                    dimension["kind"],
                    dimension["scalar"],
                )
                for dimension in note["groupDimensions"]
            },
            {
                "status": ("STATUS", "status", "column", None),
                "tags": ("TAGS", "tags", "column", "JSON"),
                "updated_at": ("UPDATED_AT", "updated_at", "column", "DateTime"),
            },
        )
        updated_at = {
            dimension["field"]: dimension
            for dimension in note["groupDimensions"]
        }["updated_at"]
        group_dimensions = {
            dimension["field"]: dimension
            for dimension in note["groupDimensions"]
        }
        self.assertEqual(
            group_dimensions["status"]["filter"],
            {
                "kind": "equality",
                "field": "status",
                "valueKey": "status",
                "rangeKey": None,
                "lookup": None,
                "nullLookup": "isNull",
                "valueTransform": None,
                "valueMap": [
                    {"from": "DRAFT", "to": "draft"},
                    {"from": "IN_REVIEW", "to": "in_review"},
                    {"from": "ACTIVE", "to": "active"},
                    {"from": "ARCHIVED", "to": "archived"},
                ],
            },
        )
        self.assertEqual(
            group_dimensions["tags"]["filter"],
            {
                "kind": "equality",
                "field": "tags",
                "valueKey": "tags",
                "rangeKey": None,
                "lookup": "exact",
                "nullLookup": "isNull",
                "valueTransform": "json",
                "valueMap": [],
            },
        )
        self.assertEqual(
            {
                extraction["name"]: extraction
                for extraction in updated_at["extractions"]
                if extraction["name"] in {"year", "month"}
            },
            {
                "year": {
                    "name": "year",
                    "input": "YEAR",
                    "key": "updated_at_year",
                    "rangeKey": "updated_at_year_range",
                    "filter": {
                        "kind": "range",
                        "field": "updated_at",
                        "valueKey": "updated_at_year",
                        "rangeKey": "updated_at_year_range",
                        "lookup": None,
                        "nullLookup": "isNull",
                        "valueTransform": None,
                        "valueMap": [],
                    },
                },
                "month": {
                    "name": "month",
                    "input": "MONTH",
                    "key": "updated_at_month",
                    "rangeKey": "updated_at_month_range",
                    "filter": {
                        "kind": "range",
                        "field": "updated_at",
                        "valueKey": "updated_at_month",
                        "rangeKey": "updated_at_month_range",
                        "lookup": None,
                        "nullLookup": "isNull",
                        "valueTransform": None,
                        "valueMap": [],
                    },
                },
            },
        )
        self.assertEqual(
            note["aggregateMeasures"],
            [
                {"op": "sum", "field": "word_count", "input": "word_count"},
                {"op": "avg", "field": "word_count", "input": "word_count"},
                {"op": "min", "field": "word_count", "input": "word_count"},
                {"op": "max", "field": "word_count", "input": "word_count"},
            ],
        )
        self.assertEqual(note["defaultMeasures"], [{"op": "count", "field": None, "input": None}])
        self.assertEqual(
            note["defaultSort"],
            [
                {"field": "updated_at", "direction": "DESC"},
                {"field": "title", "direction": "ASC"},
            ],
        )
        self.assertEqual(
            note["createFields"],
            ["title", "body", "status", "tags", "is_starred", "reminder_at"],
        )
        self.assertEqual(note["requiredCreateFields"], ["title"])
        self.assertEqual(
            note["updateFields"],
            ["title", "body", "status", "tags", "is_starred", "reminder_at"],
        )
        self.assertEqual(note["revisionFields"], ["created_at", "comment", "body"])
        self.assertEqual(
            {
                key: fields["title"][key]
                for key in (
                    "kind",
                    "scalar",
                    "readable",
                    "filterable",
                    "sortable",
                    "creatable",
                    "updatable",
                    "requiredOnCreate",
                )
            },
            {
                "kind": "scalar",
                "scalar": "String",
                "readable": True,
                "filterable": True,
                "sortable": True,
                "creatable": True,
                "updatable": True,
                "requiredOnCreate": True,
            },
        )
        self.assertFalse(fields["word_count"]["creatable"])
        self.assertFalse(fields["word_count"]["updatable"])
