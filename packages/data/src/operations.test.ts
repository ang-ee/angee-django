import { print } from "graphql";
import { describe, expect, test } from "vitest";
import type { DataResourceMetadata } from "@angee/sdk";

import {
  aggregateRequest,
  deletePreviewRequest,
  extractAggregate,
  extractDeletePreview,
  extractFacets,
  extractGroupBy,
  extractRevisions,
  facetsRequest,
  groupByRequest,
  listRequest,
  groupDimension,
  revisionSnapshot,
  revisionsRequest,
} from "./operations";

describe("Hasura custom operations", () => {
  test("builds an aggregate request over the native Hasura aggregate root", () => {
    const request = aggregateRequest(resource(), {
      where: { status: { _eq: "ACTIVE" } },
      measures: [{ op: "sum", field: "word_count" }],
    });

    expect(request.dataProviderName).toBe("console");
    expect(request.meta.gqlVariables).toEqual({
      where: { status: { _eq: "ACTIVE" } },
    });
    expect(printDocument(request.meta.gqlQuery)).toBe(
      "query notes_aggregate($where: notes_bool_exp) { " +
        "notes_aggregate(where: $where) { aggregate { count sum { word_count } } } }",
    );
  });

  test("builds a list request over the native Hasura list and aggregate roots", () => {
    const request = listRequest(resource(), {
      fields: ["title", "owner.display_name"],
      where: { metadata: { _contains: { kind: "note" } } },
      orderBy: { updated_at: "desc" },
    });

    expect(request.dataProviderName).toBe("console");
    expect(request.root).toBe("notes");
    expect(request.meta.gqlVariables).toEqual({
      where: { metadata: { _contains: { kind: "note" } } },
      order_by: { updated_at: "desc" },
    });
    expect(printDocument(request.meta.gqlQuery)).toBe(
      "query notes_list($limit: Int, $offset: Int, $where: notes_bool_exp, $order_by: [notes_order_by!]) { " +
        "notes(limit: $limit, offset: $offset, where: $where, order_by: $order_by) { " +
        "id title owner { id display_name } } " +
        "notes_aggregate(where: $where) { aggregate { count } } }",
    );
  });

  test("builds a typed-key grouped request using group_by, where, limit, and offset", () => {
    const request = groupByRequest(resource(), {
      dimensions: [groupDimension("STATUS", "status")],
      where: { is_starred: { _eq: true } },
      page: 2,
      pageSize: 20,
      measures: [{ op: "avg", input: "word_count" }],
    });

    expect(request.meta.gqlVariables).toEqual({
      group_by: [{ field: "STATUS" }],
      where: { is_starred: { _eq: true } },
      limit: 20,
      offset: 20,
    });
    expect(printDocument(request.meta.gqlQuery)).toBe(
      "query notes_groups($group_by: [NoteTypeGroupBySpec!]!, $where: notes_bool_exp, $limit: Int, $offset: Int) { " +
        "notes_groups(group_by: $group_by, where: $where, limit: $limit, offset: $offset) { " +
        "key { status } aggregate { count avg { word_count } } } }",
    );
  });

  test("builds aliased facet requests over the same grouped root", () => {
    const request = facetsRequest(resource(), {
      facets: [
        {
          id: "status",
          dimensions: [groupDimension("STATUS", "status")],
          where: { status: { _neq: "ARCHIVED" } },
          pageSize: 10,
        },
        {
          id: "month",
          dimensions: [
            groupDimension("UPDATED_AT", "updated_at_month", {
              granularity: "MONTH",
              rangeKey: "updated_at_month_range",
            }),
          ],
        },
      ],
    });

    expect(request.meta.gqlVariables).toEqual({
      group_by0: [{ field: "STATUS" }],
      where0: { status: { _neq: "ARCHIVED" } },
      limit0: 10,
      offset0: 0,
      group_by1: [{ field: "UPDATED_AT", granularity: "MONTH" }],
    });
    expect(printDocument(request.meta.gqlQuery)).toBe(
      "query notes_groups_facets($group_by0: [NoteTypeGroupBySpec!]!, $where0: notes_bool_exp, $limit0: Int, $offset0: Int, $group_by1: [NoteTypeGroupBySpec!]!) { " +
        "facet0: notes_groups( group_by: $group_by0 where: $where0 limit: $limit0 offset: $offset0 ) { " +
        "key { status } aggregate { count } } " +
        "facet1: notes_groups(group_by: $group_by1) { key { updated_at_month updated_at_month_range { from to } } aggregate { count } } }",
    );
  });

  test("builds an authored delete-preview mutation from resource metadata", () => {
    const request = deletePreviewRequest(resource(), {
      id: "note_123",
      confirm: true,
    });

    expect(request.dataProviderName).toBe("console");
    expect(request.root).toBe("delete_note");
    expect(request.meta.gqlVariables).toEqual({
      id: "note_123",
      confirm: true,
    });
    expect(printDocument(request.meta.gqlMutation)).toBe(
      "mutation delete_note($id: ID!, $confirm: Boolean) { " +
        "delete_note(id: $id, confirm: $confirm) { " +
        "totalDeletedCount hasBlockers deleted { label count } updated { label count } blocked { label count } " +
        "root { label objectLabel objectId children { label objectLabel objectId children { label objectLabel objectId } } } } }",
    );
  });

  test("builds an authored revisions query from resource metadata", () => {
    const request = revisionsRequest(resource(), "note_123");

    expect(request.dataProviderName).toBe("console");
    expect(request.root).toBe("noteRevisions");
    expect(request.meta.gqlVariables).toEqual({ id: "note_123" });
    expect(printDocument(request.meta.gqlQuery)).toBe(
      "query noteRevisions($id: ID!) { " +
        "noteRevisions(id: $id) { id createdAt comment body } }",
    );
  });

  test("extracts aggregate measures from Hasura aggregate responses", () => {
    expect(
      extractAggregate(
        {
          notes_aggregate: {
            aggregate: {
              count: 3,
              sum: { word_count: 42 },
            },
          },
        },
        "notes_aggregate",
      ),
    ).toEqual({
      key: null,
      count: 3,
      sum: { word_count: 42 },
    });
  });

  test("extracts authored delete preview payloads by root name", () => {
    expect(
      extractDeletePreview(
        {
          delete_note: {
            totalDeletedCount: 2,
            hasBlockers: false,
            deleted: [{ label: "notes", count: 1 }],
            updated: [],
            blocked: [],
            root: {
              label: "note",
              objectLabel: "Draft",
              objectId: "note_123",
              children: [
                {
                  label: "comments",
                  objectLabel: "1 comment",
                  objectId: null,
                  children: [],
                },
              ],
            },
          },
        },
        "delete_note",
      ),
    ).toEqual({
      totalDeletedCount: 2,
      hasBlockers: false,
      deleted: [{ label: "notes", count: 1 }],
      updated: [],
      blocked: [],
      root: {
        label: "note",
        objectLabel: "Draft",
        objectId: "note_123",
        children: [
          {
            label: "comments",
            objectLabel: "1 comment",
            objectId: null,
            children: [],
          },
        ],
      },
    });
  });

  test("extracts revisions and snapshots changed fields", () => {
    const [revision] = extractRevisions(
      {
        noteRevisions: [
          {
            id: "rev_2",
            createdAt: "2026-01-02T00:00:00Z",
            comment: "updated",
            body: "Second",
          },
        ],
      },
      "noteRevisions",
    );

    expect(revision).toEqual({
      id: "rev_2",
      createdAt: "2026-01-02T00:00:00Z",
      comment: "updated",
      body: "Second",
    });
    expect(revision ? revisionSnapshot(revision) : null).toBe("Second");
  });

  test("extracts grouped buckets from typed-key Hasura group responses", () => {
    expect(
      extractGroupBy(
        {
          notes_groups: [
            {
              key: { status: "ACTIVE" },
              aggregate: { count: 2, avg: { word_count: 10 } },
            },
            {
              key: { status: "DRAFT" },
              aggregate: { count: 1, avg: { word_count: 5 } },
            },
          ],
        },
        "notes_groups",
      ),
    ).toEqual({
      count: 3,
      buckets: [
        {
          key: { status: "ACTIVE" },
          count: 2,
          avg: { word_count: 10 },
        },
        {
          key: { status: "DRAFT" },
          count: 1,
          avg: { word_count: 5 },
        },
      ],
    });
  });

  test("extracts facet options from aliased grouped responses", () => {
    expect(
      extractFacets(
        {
          facet0: [
            {
              key: { status: "ACTIVE" },
              aggregate: { count: 7 },
            },
          ],
        },
        [{ id: "status", dimensions: [groupDimension("STATUS", "status")] }],
      ),
    ).toEqual({
      status: {
        count: 7,
        options: [
          {
            value: "ACTIVE",
            label: "ACTIVE",
            count: 7,
            key: { status: "ACTIVE" },
          },
        ],
      },
    });
  });
});

function resource(): DataResourceMetadata {
  return {
    schemaName: "console",
    modelLabel: "notes.Note",
    appLabel: "notes",
    modelName: "Note",
    publicIdField: "id",
    roots: {
      list: "notes",
      detail: "notes_by_pk",
      aggregate: "notes_aggregate",
      groups: "notes_groups",
      create: "insert_notes_one",
      update: "update_notes_by_pk",
      delete: "delete_notes_by_pk",
      deletePreview: "delete_note",
      revisions: "noteRevisions",
      changes: "noteChanged",
    },
    typeNames: {
      filter: "notes_bool_exp",
      order: "notes_order_by",
      groupKey: "NoteTypeGroupKey",
      groupBySpec: "NoteTypeGroupBySpec",
      groupOrder: "NoteTypeGroupOrder",
      having: "NoteTypeHaving",
    },
    capabilities: ["list", "detail", "aggregate", "groups"],
    fields: [],
    filterFields: ["status", "is_starred"],
    orderFields: ["updated_at"],
    aggregateFields: ["id", "word_count"],
    aggregateMeasures: [
      { op: "sum", field: "word_count", input: "word_count" },
      { op: "avg", field: "word_count", input: "word_count" },
    ],
    defaultMeasures: [{ op: "count", field: null, input: null }],
    revisionFields: ["id", "createdAt", "comment", "body"],
    groupByFields: ["status", "updated_at"],
    groupDimensions: [
      {
        field: "status",
        input: "STATUS",
        key: "status",
        kind: "column",
        scalar: null,
      },
    ],
    relationAxes: [],
  };
}

function compact(document: string): string {
  return document.replace(/\s+/g, " ").trim();
}

function printDocument(document: unknown): string {
  expect(document).toBeDefined();
  return compact(print(document as Parameters<typeof print>[0]));
}
