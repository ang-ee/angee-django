import { describe, expect, test } from "vitest";

import {
  capabilityForRefineAction,
  createAngeeAccessControlProvider,
} from "./access-control";
import type { DataResourceMetadata } from "./metadata";
import type { AngeeRefineResource } from "./resources";

describe("Angee Refine access control", () => {
  test("maps Refine actions to backend resource capabilities", async () => {
    const provider = createAngeeAccessControlProvider([resource()]);

    await expect(provider.can({ resource: "notes", action: "list" }))
      .resolves.toEqual({ can: true });
    await expect(provider.can({ resource: "notes", action: "show" }))
      .resolves.toEqual({ can: true });
    await expect(provider.can({ resource: "notes", action: "edit" }))
      .resolves.toEqual({
        can: false,
        reason: 'Resource "notes.Note" does not expose update.',
      });
    await expect(provider.can({ resource: "notes", action: "delete" }))
      .resolves.toEqual({
        can: false,
        reason: 'Resource "notes.Note" does not expose delete.',
      });
  });

  test("resolves resources by refine identifier from params", async () => {
    const provider = createAngeeAccessControlProvider([resource()]);

    await expect(
      provider.can({
        action: "create",
        params: { resource: { name: "notes", identifier: "console:notes.Note" } },
      }),
    ).resolves.toEqual({ can: true });
  });

  test("allows menu-only refine resources", async () => {
    const provider = createAngeeAccessControlProvider([
      {
        name: "menu:integrate",
        identifier: "menu:integrate",
        list: "/integrate",
      },
    ]);

    await expect(provider.can({ resource: "menu:integrate", action: "list" }))
      .resolves.toEqual({ can: true });
  });

  test("normalizes standard refine actions", () => {
    expect(capabilityForRefineAction("show")).toBe("detail");
    expect(capabilityForRefineAction("edit")).toBe("update");
    expect(capabilityForRefineAction("deleteMany")).toBe("delete");
    expect(capabilityForRefineAction("publish")).toBe("publish");
  });
});

function resource(): AngeeRefineResource {
  const metadata: DataResourceMetadata = {
    schemaName: "console",
    modelLabel: "notes.Note",
    appLabel: "notes",
    modelName: "Note",
    publicIdField: "id",
    roots: {
      list: "notes",
      detail: "notes_by_pk",
      create: "insert_notes_one",
    },
    typeNames: {},
    capabilities: ["list", "detail", "create"],
    filterFields: [],
    orderFields: [],
    aggregateFields: [],
    groupByFields: [],
    relationAxes: [],
  };
  return {
    name: "notes",
    identifier: "console:notes.Note",
    meta: {
      dataProviderName: "console",
      modelLabel: "notes.Note",
      schemaName: "console",
      resource: metadata,
    },
  };
}
