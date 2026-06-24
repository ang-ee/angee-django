import { describe, expect, test } from "vitest";

import {
  refineResourceName,
  refineResourcesFromDataResources,
} from "./resources";
import type { DataResourceMetadata } from "@angee/sdk";

describe("refine resource metadata", () => {
  test("uses the Hasura list root as the refine resource name", () => {
    expect(refineResourceName(resource())).toBe("notes");
  });

  test("maps backend resource metadata to refine resources", () => {
    expect(
      refineResourcesFromDataResources([resource()], {
        pathsByModel: { "notes.Note": "/notes" },
      }),
    ).toEqual([
      {
        name: "notes",
        identifier: "console:notes.Note",
        list: "/notes",
        show: "/notes/:id",
        create: "/notes/new",
        edit: "/notes/:id",
        meta: {
          dataProviderName: "console",
          modelLabel: "notes.Note",
          schemaName: "console",
          resource: resource(),
        },
      },
    ]);
  });

  test("accepts existing short model route keys during the migration", () => {
    const [mapped] = refineResourcesFromDataResources([resource()], {
      pathsByModel: { Note: "/notes" },
    });

    expect(mapped?.list).toBe("/notes");
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
      revisions: "note_revisions",
      changes: "note_changed",
    },
    typeNames: {},
    capabilities: ["list", "detail", "create", "update", "delete"],
    filterFields: ["status"],
    orderFields: ["updated_at"],
    aggregateFields: ["id"],
    groupByFields: ["status"],
    relationAxes: [],
  };
}
