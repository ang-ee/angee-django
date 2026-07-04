import type { DocumentNode } from "graphql";
import { describe, expect, test } from "vitest";

import {
  actionRequest,
  aggregateRequest,
  deletePreviewRequest,
  extractActionOutcome,
  extractAggregate,
  extractDeletePreview,
  extractFacet,
  extractGroupBy,
  extractRevisions,
  extractSaveResult,
  groupByRequest,
  groupDimension,
  runActionResult,
  revisionSnapshot,
  revisionsRequest,
  saveRequest,
} from "./operations";

describe("Hasura custom operations", () => {
  test("builds an aggregate request with a generated document", () => {
    const document = { kind: "Document", definitions: [] } as unknown as DocumentNode;
    const request = aggregateRequest(
      target("notes_aggregate"),
      {
        where: { status: { _eq: "ACTIVE" } },
        measures: [{ op: "sum", field: "word_count" }],
      },
      { document },
    );

    expect(request.dataProviderName).toBe("console");
    expect(request.meta.gqlVariables).toEqual({
      where: { status: { _eq: "ACTIVE" } },
    });
    expect(request.meta.gqlQuery).toBe(document);
  });

  test("builds a typed-key grouped request using group_by, where, order_by, limit, and offset", () => {
    const document = { kind: "Document", definitions: [] } as unknown as DocumentNode;
    const request = groupByRequest(target("notes_groups"), {
      dimensions: [groupDimension("STATUS", "status")],
      where: { is_starred: { _eq: true } },
      orderBy: [{ field: "status", direction: "ASC", nulls: "LAST" }],
      page: 2,
      pageSize: 20,
      measures: [{ op: "avg", input: "word_count" }],
    }, { document });

    expect(request.meta.gqlVariables).toEqual({
      group_by: [{ field: "STATUS" }],
      where: { is_starred: { _eq: true } },
      order_by: [{ field: "status", direction: "ASC", nulls: "LAST" }],
      limit: 20,
      offset: 20,
    });
    expect(request.meta.gqlQuery).toBe(document);
  });

  test("builds an authored delete-preview request with a generated document", () => {
    const document = { kind: "Document", definitions: [] } as unknown as DocumentNode;
    const request = deletePreviewRequest(
      target("delete_note"),
      {
        id: "note_123",
        confirm: true,
      },
      { document },
    );

    expect(request.dataProviderName).toBe("console");
    expect(request.root).toBe("delete_note");
    expect(request.meta.gqlVariables).toEqual({
      id: "note_123",
      confirm: true,
    });
    expect(request.meta.gqlMutation).toBe(document);
  });

  test("builds an authored save request with pk, patch, and lines", () => {
    const document = { kind: "Document", definitions: [] } as unknown as DocumentNode;
    const request = saveRequest(
      target("sale_docs_save"),
      {
        pk: "doc_1",
        patch: { note: "confirmed" },
        lines: [
          { id: "ln_1", label: "Keep", quantity: 3, position: 0 },
          { label: "New", quantity: 7, position: 1 },
        ],
      },
      { document },
    );

    expect(request.dataProviderName).toBe("console");
    expect(request.root).toBe("sale_docs_save");
    expect(request.meta.gqlVariables).toEqual({
      pk: "doc_1",
      patch: { note: "confirmed" },
      lines: [
        { id: "ln_1", label: "Keep", quantity: 3, position: 0 },
        { label: "New", quantity: 7, position: 1 },
      ],
    });
    expect(request.meta.gqlMutation).toBe(document);
  });

  test("omits absent patch and lines from a save request", () => {
    const document = { kind: "Document", definitions: [] } as unknown as DocumentNode;
    const request = saveRequest(target("sale_docs_save"), { pk: "doc_1" }, { document });

    expect(request.meta.gqlVariables).toEqual({ pk: "doc_1" });
  });

  test("extracts the saved row from a save response", () => {
    const row = { id: "doc_1", title: "Order", lines: [{ id: "ln_1" }] };
    expect(extractSaveResult({ sale_docs_save: row }, "sale_docs_save")).toEqual(row);
    expect(extractSaveResult({}, "sale_docs_save")).toBeNull();
  });

  test("builds an authored revisions request with a generated document", () => {
    const document = { kind: "Document", definitions: [] } as unknown as DocumentNode;
    const request = revisionsRequest(target("note_revisions"), "note_123", { document });

    expect(request.dataProviderName).toBe("console");
    expect(request.root).toBe("note_revisions");
    expect(request.meta.gqlVariables).toEqual({ id: "note_123" });
    expect(request.meta.gqlQuery).toBe(document);
  });

  test("builds a single-id action mutation for refine custom mutation execution", () => {
    const document = { kind: "Document", definitions: [] } as unknown as DocumentNode;
    const request = actionRequest(
      "provision_agent",
      { id: "agent_123" },
      { dataProviderName: "console", document },
    );

    expect(request.dataProviderName).toBe("console");
    expect(request.root).toBe("provision_agent");
    expect(request.meta.gqlVariables).toEqual({ id: "agent_123" });
    expect(request.meta.gqlMutation).toBe(document);
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
            total_deleted_count: 2,
            has_blockers: false,
            deleted: [{ label: "notes", count: 1 }],
            updated: [],
            blocked: [],
            root: {
              label: "note",
              object_label: "Draft",
              object_id: "note_123",
              children: [
                {
                  label: "comments",
                  object_label: "1 comment",
                  object_id: null,
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

  test("extracts and normalizes action outcomes", () => {
    const success = extractActionOutcome(
      { provision_agent: { ok: true, message: "Provisioning started." } },
      "provision_agent",
    );
    expect(runActionResult(success)).toBe("Provisioning started.");
    expect(() =>
      runActionResult({ ok: false, message: "Provisioning failed." }),
    ).toThrow("Provisioning failed.");
  });

  test("extracts revisions and snapshots changed fields", () => {
    const [revision] = extractRevisions(
      {
        note_revisions: [
          {
            id: "rev_2",
            created_at: "2026-01-02T00:00:00Z",
            comment: "updated",
            body: "Second",
          },
        ],
      },
      "note_revisions",
    );

    expect(revision).toEqual({
      id: "rev_2",
      created_at: "2026-01-02T00:00:00Z",
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

  test("extracts facet options from a grouped response", () => {
    expect(
      extractFacet(
        {
          notes_groups: [
            {
              key: { status: "ACTIVE" },
              aggregate: { count: 7 },
            },
          ],
        },
        "notes_groups",
        { id: "status", dimensions: [groupDimension("STATUS", "status")] },
      ),
    ).toEqual(
      {
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
    );
  });
});

function target(root: string) {
  return {
    dataProviderName: "console",
    root,
  };
}
