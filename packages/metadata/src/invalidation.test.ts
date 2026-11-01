import { describe, expect, test, vi } from "vitest";
import type { SchemaFieldMetadata } from "./artifact";

const EMPTY_SCHEMA_FIELD_METADATA: SchemaFieldMetadata = {
  types: {},
  labels: {},
  resources: [],
};

const hookMocks = vi.hoisted(() => ({
  metadata: { types: {}, labels: {}, resources: [] } as SchemaFieldMetadata,
}));

vi.mock("react", () => ({
  useMemo: (calculate: () => unknown) => calculate(),
}));

vi.mock("./context", () => ({
  useSchemaFieldMetadata: () => hookMocks.metadata,
}));

import {
  refineInvalidationParams,
  resourceInvalidationTargets,
  useResourceInvalidates,
  useResourceMutationModels,
} from "./invalidation";
import {
  schemaFieldMetadataFromDataResources,
} from "./artifact";
import { testDataResource } from "./testing";

describe("resource invalidation targets", () => {
  test("maps model labels to refine resource invalidation targets", () => {
    const [target] = resourceInvalidationTargets(
      schemaFieldMetadataFromDataResources([testDataResource("notes.Note")]),
      ["notes.Note"],
    );

    expect(target).toEqual({
      resource: "console:notes.Note",
      dataProviderName: "console",
    });
    expect(refineInvalidationParams(target!)).toEqual({
      resource: "console:notes.Note",
      dataProviderName: "console",
      invalidates: ["resourceAll"],
    });
  });

  test("exact writes refresh collections and only the written detail identities", () => {
    const metadata = schemaFieldMetadataFromDataResources([testDataResource("notes.Note")]);
    const targets = resourceInvalidationTargets(metadata, ["notes.Note"], [
      { model: "notes.Note", id: "note-1" },
      { model: "notes.Note", id: "note-2" },
      { model: "notes.Note", id: "note-1" },
    ]);
    expect(targets.map(refineInvalidationParams)).toEqual(["note-1", "note-2"].map((id) => ({
      resource: "console:notes.Note", dataProviderName: "console", id,
      invalidates: ["list", "many", "detail"],
    })));
  });

  test("unknown related effects keep the resource fallback beside exact written records", () => {
    const metadata = schemaFieldMetadataFromDataResources([
      testDataResource("notes.Note"), testDataResource("catalog.Summary"),
    ]);
    const targets = resourceInvalidationTargets(metadata, ["notes.Note", "catalog.Summary"], [
      { model: "notes.Note", id: "note-1" },
    ]);
    expect(targets.map(refineInvalidationParams)).toEqual([
      { resource: "console:notes.Note", dataProviderName: "console", id: "note-1", invalidates: ["list", "many", "detail"] },
      { resource: "console:catalog.Summary", dataProviderName: "console", invalidates: ["resourceAll"] },
    ]);
    expect(resourceInvalidationTargets(metadata, ["notes.Note"], [
      { model: "notes.Note", id: "note-1" }, { model: "notes.Note" },
    ]).map(refineInvalidationParams)).toEqual([
      { resource: "console:notes.Note", dataProviderName: "console", invalidates: ["resourceAll"] },
    ]);
  });

  test("mutation model mapping retains authored-only fallbacks beside canonical resource aliases", () => {
    hookMocks.metadata = schemaFieldMetadataFromDataResources([testDataResource("notes.Note")]);
    expect(useResourceMutationModels(["Note", "notes.NoteBody", "notes.RecordBinding", "notes.Note"])).toEqual({
      models: ["notes.Note", "notes.NoteBody", "notes.RecordBinding"], resourceModels: ["notes.Note"],
    });
  });

  test("mixed record effects map only exposed resources without requiring authored-only resources", () => {
    hookMocks.metadata = schemaFieldMetadataFromDataResources([testDataResource("notes.Note")]);
    const records = [
      { model: "notes.Note", id: "note-1" },
      { model: "notes.NoteBody", id: "body-1" },
      { model: "notes.RecordBinding", id: "binding-1", relatedRecords: [{ model: "notes.Target", id: "target-1" }] },
    ];
    expect(resourceInvalidationTargets(hookMocks.metadata, ["notes.Note"], records)
      .map(refineInvalidationParams)).toEqual([
      { resource: "console:notes.Note", dataProviderName: "console", id: "note-1", invalidates: ["list", "many", "detail"] },
    ]);
    const resolve = useResourceInvalidates([], () => records);
    if (typeof resolve !== "function") throw new Error("Expected dynamic record mapping");
    expect(resolve(undefined, undefined)).toEqual([
      { resource: "console:notes.Note", dataProviderName: "console", id: "note-1", invalidates: ["list", "many", "detail"] },
    ]);
    expect(useResourceInvalidates([], records.filter((record) => record.model !== "notes.Note"))).toEqual([]);
    // Explicit native-resource declarations retain their strict contract.
    expect(() => resourceInvalidationTargets(hookMocks.metadata, ["notes.RecordBinding"], records))
      .toThrow(/Unknown model spelling/);
  });

  test("canonicalizes an authored mutation's model alias", () => {
    const [target] = resourceInvalidationTargets(
      schemaFieldMetadataFromDataResources([testDataResource("notes.Note")]),
      ["Note"],
    );

    expect(target).toEqual({
      resource: "console:notes.Note",
      dataProviderName: "console",
    });
  });

  test("fails fast when a mutation declares an unknown model invalidation target", () => {
    expect(() =>
      resourceInvalidationTargets(
        schemaFieldMetadataFromDataResources([testDataResource("notes.Note")]),
        ["storage.File"],
      ),
    ).toThrow(/Unknown model spelling "storage\.File"/);
  });

  test("fails fast when resource metadata is unavailable", () => {
    expect(() =>
        resourceInvalidationTargets(EMPTY_SCHEMA_FIELD_METADATA, ["notes.Note"]),
    ).toThrow(/schema metadata exposes no resources/);
  });

  test("provider-less render degrades to no invalidations with a development warning", () => {
    hookMocks.metadata = EMPTY_SCHEMA_FIELD_METADATA;
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const result = useResourceInvalidates(["notes.Note"]);

    expect(result).toEqual([]);
    expect(warn).toHaveBeenCalledWith(
      expect.stringMatching(/authored-operation model label.*exposes no resources/),
    );
    warn.mockRestore();
  });

  test("an unknown render-time invalidation spelling is omitted with a development warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    hookMocks.metadata = schemaFieldMetadataFromDataResources([
      testDataResource("notes.Note"),
    ]);

    const result = useResourceInvalidates(["missing.Note"]);

    expect(result).toEqual([]);
    expect(warn).toHaveBeenCalledWith(
      expect.stringMatching(/authored-operation model label.*missing\.Note/),
    );
    warn.mockRestore();
  });
});
