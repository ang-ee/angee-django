import { describe, expect, test } from "vitest";

import { testDataResource } from "./testing";
import {
  canonicalModelLabel,
  mergeModelLabelInventory,
} from "./canonical-model-label";

describe("canonicalModelLabel", () => {
  test("resolves qualified, bare Pascal, and lowercase aliases across schemas", () => {
    const resources = [
      testDataResource("notes.Note", { schemaName: "console" }),
      testDataResource("notes.Note", { schemaName: "public" }),
      testDataResource("integrate.Integration", { schemaName: "console" }),
    ];

    expect(canonicalModelLabel(resources, "notes.Note")).toBe("notes.Note");
    expect(canonicalModelLabel(resources, "Note")).toBe("notes.Note");
    expect(canonicalModelLabel(resources, "note")).toBe("notes.Note");
    expect(canonicalModelLabel(resources, "Integration")).toBe(
      "integrate.Integration",
    );
  });

  test("rejects a bare model name shared by different app labels", () => {
    const resources = [
      testDataResource("iam.Relationship", { schemaName: "console" }),
      testDataResource("parties.Relationship", { schemaName: "public" }),
    ];

    expect(() => canonicalModelLabel(resources, "Relationship")).toThrow(
      /ambiguous.*iam\.Relationship.*parties\.Relationship/,
    );
  });

  test("rejects an unknown spelling", () => {
    expect(() =>
      canonicalModelLabel([testDataResource("notes.Note")], "Missing"),
    ).toThrow(/Unknown model spelling "Missing"/);
  });

  test("rejects a lowercase alias collision", () => {
    const resources = [
      testDataResource("alpha.API", { schemaName: "console", modelName: "api" }),
      testDataResource("beta.Api", { schemaName: "public", modelName: "api" }),
    ];

    expect(() => canonicalModelLabel(resources, "api")).toThrow(
      /ambiguous.*alpha\.API.*beta\.Api/,
    );
  });

  test("reports an inventory with no exposed resources distinctly", () => {
    expect(() => canonicalModelLabel([], "notes.Note")).toThrow(
      /schema metadata exposes no resources/,
    );
  });

  test("merges normalized resource inventories across schemas", () => {
    const resources = mergeModelLabelInventory([
      { types: {}, resources: [testDataResource("notes.Note")] },
      { types: {}, resources: [testDataResource("parties.Party")] },
    ]);

    expect(resources.map((resource) => resource.modelLabel)).toEqual([
      "notes.Note",
      "parties.Party",
    ]);
  });
});
