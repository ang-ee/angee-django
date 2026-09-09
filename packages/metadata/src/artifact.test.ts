import { describe, expect, test } from "vitest";

import { defineAngeeSchemaMetadata, resourceOperationTarget } from "./artifact";
import { testDataResource } from "./testing";

describe("generated subtitle metadata", () => {
  test("accepts declared dotted selection paths", () => {
    const resource = testDataResource("knowledge.Page", {
      subtitle: {
        created: "created_at",
        updated: "updated_at",
        wordCount: "markdown.word_count",
      },
    });

    expect(defineAngeeSchemaMetadata({ angee: { resources: [resource] } }))
      .toEqual({ angee: { resources: [resource] } });
  });

  test("rejects malformed subtitle selection paths", () => {
    const resource = {
      ...testDataResource("knowledge.Page"),
      subtitle: { wordCount: "markdown..word_count" },
    };

    expect(() =>
      defineAngeeSchemaMetadata({ angee: { resources: [resource] } }),
    ).toThrow(
      "schema metadata.angee.resources[0].subtitle.wordCount must be a dotted selection path.",
    );
  });

  test("rejects non-string subtitle facts", () => {
    const resource = {
      ...testDataResource("knowledge.Page"),
      subtitle: { created: 1 },
    };

    expect(() =>
      defineAngeeSchemaMetadata({ angee: { resources: [resource] } }),
    ).toThrow("schema metadata.angee.resources[0].subtitle.created must be a string.");
  });

  test("rejects subtitle facts outside the renderer vocabulary", () => {
    const resource = {
      ...testDataResource("knowledge.Page"),
      subtitle: { summary: "markdown.excerpt" },
    };

    expect(() =>
      defineAngeeSchemaMetadata({ angee: { resources: [resource] } }),
    ).toThrow(
      "schema metadata.angee.resources[0].subtitle.summary is not a supported subtitle fact.",
    );
  });
});


describe("generated resource wire contract", () => {
  test("accepts a computed object field with no relation target", () => {
    const resource = testDataResource("workflows.StepArtifact", {
      fields: [{
        name: "target_reference", kind: "object", readable: true,
        aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
      }],
    });
    expect(defineAngeeSchemaMetadata({ angee: { resources: [resource] } }))
      .toEqual({ angee: { resources: [resource] } });
  });

  test("preserves extension keys and nullable emitted values", () => {
    const resource = testDataResource("notes.Note", {
      roots: { list: "notes", customRoot: "notes_custom" },
      fields: [{
        name: "status", kind: "enum", values: [{ value: "OPEN", description: null }],
        readable: true, aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false,
        relationModelLabel: null, widget: null,
      }],
      futureResourceFact: { enabled: true },
    });
    const wire = { vendor: { retained: true }, angee: { resources: [resource], future: "kept" } };
    expect(defineAngeeSchemaMetadata(wire)).toEqual(wire);
  });

  test.each([
    { query: { identity: { field: 42 } } },
    { aggregateMeasures: [{ op: 42 }] },
    { linesResource: { field: "lines", modelLabel: "notes.Line", fields: [{ name: "body", kind: "scalar", readable: "yes" }] } },
  ])("rejects malformed nested resource facts: %j", (patch) => {
    expect(() => defineAngeeSchemaMetadata({ angee: { resources: [{ ...testDataResource("notes.Note"), ...patch }] } }))
      .toThrow(/schema metadata\.angee\.resources\[0\]/);
  });

  test("keeps absent optional envelope sections absent", () => {
    expect(defineAngeeSchemaMetadata({})).toEqual({});
    expect(defineAngeeSchemaMetadata({ angee: {} })).toEqual({ angee: {} });
    expect(defineAngeeSchemaMetadata({ angee: null })).toEqual({ angee: null });
  });
});


test("resource operation targets retain the canonical live model identity", () => {
  const resource = testDataResource("messaging.Message", { roots: { groups: "messages_groups" } });
  expect(resourceOperationTarget(resource, "groups")).toEqual({
    dataProviderName: "console", root: "messages_groups", modelLabel: "messaging.Message",
  });
});
