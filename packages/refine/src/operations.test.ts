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
  groupByRequest,
  groupDimension,
  runActionResult,
  revisionSnapshot,
  revisionsRequest,
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

  test("builds an authored revisions request with a generated document", () => {
    const document = { kind: "Document", definitions: [] } as unknown as DocumentNode;
    const request = revisionsRequest(target("noteRevisions"), "note_123", { document });

    expect(request.dataProviderName).toBe("console");
    expect(request.root).toBe("noteRevisions");
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
