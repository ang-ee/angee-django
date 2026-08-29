import { describe, expect, test, vi } from "vitest";
import type { SchemaFieldMetadata } from "./artifact";

const hookMocks = vi.hoisted(() => ({
  metadata: { types: {} } as SchemaFieldMetadata,
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
      resource: "notes",
      dataProviderName: "console",
    });
    expect(refineInvalidationParams(target!)).toEqual({
      resource: "notes",
      dataProviderName: "console",
      invalidates: ["list", "many", "detail"],
    });
  });

  test("canonicalizes an authored mutation's model alias", () => {
    const [target] = resourceInvalidationTargets(
      schemaFieldMetadataFromDataResources([testDataResource("notes.Note")]),
      ["Note"],
    );

    expect(target).toEqual({
      resource: "notes",
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
      resourceInvalidationTargets({ types: {} }, ["notes.Note"]),
    ).toThrow(/schema metadata exposes no resources/);
  });

  test("provider-less render degrades to no invalidations with a development warning", () => {
    hookMocks.metadata = { types: {} };
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
