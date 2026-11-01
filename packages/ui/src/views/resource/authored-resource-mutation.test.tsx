// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { useAuthoredResourceMutation } from "./authored-resource-mutation";

const dataMocks = vi.hoisted(() => ({
  mutate: vi.fn(async () => undefined),
  useAuthoredMutation: vi.fn(),
}));

vi.mock("@angee/refine", () => ({
  useAuthoredMutation: dataMocks.useAuthoredMutation,
}));

// The metadata edge, faked at the one seam this hook composes: `@angee/metadata`
// owns folding model labels into refine invalidation params, and `invalidation.ts`
// there owns testing that fold. What this file owns is that the mapped targets are
// *appended* to the caller's own `invalidates` rather than replacing them.
vi.mock("@angee/metadata", async (importOriginal) => {
  const metadata = await importOriginal<typeof import("@angee/metadata")>();
  const { testDataResource } = await import("@angee/metadata/testing");
  const schema = metadata.schemaFieldMetadataFromDataResources([
    testDataResource("catalog.Product"), testDataResource("catalog.Review"),
  ]);
  return {
    ...metadata,
    useResourceMutationModels: (labels: readonly string[] | undefined) => {
      const models = [...new Set((labels ?? []).map((label) => metadata.canonicalModelLabel(schema.resources, label, { optional: true }) ?? label))];
      return { models, resourceModels: models.filter((label) => schema.resources.some((resource) => resource.modelLabel === label)) };
    },
    useResourceInvalidates: (modelLabels: readonly string[] | undefined) =>
      (modelLabels ?? []).map((modelLabel) => ({
        dataProviderName: "console",
        invalidates: ["list", "many", "detail"],
        resource: modelLabel,
      })),
  };
});

const RateDocument = { rate: true } as never;

describe("useAuthoredResourceMutation", () => {
  beforeEach(() => {
    dataMocks.useAuthoredMutation.mockReset();
    dataMocks.useAuthoredMutation.mockReturnValue([
      dataMocks.mutate,
      { fetching: false, error: null },
    ]);
  });

  test("maps invalidateModels into refine resource invalidations", () => {
    renderHook(() =>
      useAuthoredResourceMutation(RateDocument, {
        invalidateModels: ["catalog.Product"],
      }),
    );

    expect(dataMocks.useAuthoredMutation).toHaveBeenCalledWith(
      RateDocument,
      expect.objectContaining({
        invalidateModels: ["catalog.Product"],
        invalidates: [
          {
            dataProviderName: "console",
            invalidates: ["list", "many", "detail"],
            resource: "catalog.Product",
          },
        ],
      }),
    );
  });

  test("canonicalizes aliases before forwarding authored and resource invalidation", () => {
    renderHook(() =>
      useAuthoredResourceMutation(RateDocument, {
        invalidateModels: ["Product"],
      }),
    );

    expect(dataMocks.useAuthoredMutation).toHaveBeenCalledWith(
      RateDocument,
      expect.objectContaining({
        invalidateModels: ["catalog.Product"],
        invalidates: [
          {
            dataProviderName: "console",
            invalidates: ["list", "many", "detail"],
            resource: "catalog.Product",
          },
        ],
      }),
    );
  });

  test("preserves authored-only models and records while mapping native resource labels separately", () => {
    const records = [{ model: "catalog.RecordBinding", id: "binding-1", relatedRecords: [{ model: "catalog.Target", id: "target-1" }] }];
    renderHook(() => useAuthoredResourceMutation(RateDocument, {
      invalidateModels: ["Product", "catalog.ProductBody"], records,
    }));
    const options = dataMocks.useAuthoredMutation.mock.calls[0]?.[1];
    expect(options.invalidateModels).toEqual(["catalog.Product", "catalog.ProductBody"]);
    expect(options.records).toBe(records);
    expect(options.invalidates).toEqual([
      { dataProviderName: "console", invalidates: ["list", "many", "detail"], resource: "catalog.Product" },
    ]);
  });

  test("keeps caller-supplied invalidates and appends the mapped resource targets", () => {
    const preset = {
      resource: "catalog.Review",
      dataProviderName: "console",
      invalidates: ["list"] as ("list" | "many" | "detail")[],
    };
    renderHook(() =>
      useAuthoredResourceMutation(RateDocument, {
        invalidateModels: ["catalog.Product"],
        invalidates: [preset],
      }),
    );

    const options = dataMocks.useAuthoredMutation.mock.calls[0]?.[1];
    expect(options.invalidates).toEqual([
      preset,
      {
        dataProviderName: "console",
        invalidates: ["list", "many", "detail"],
        resource: "catalog.Product",
      },
    ]);
  });
});
