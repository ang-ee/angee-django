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

vi.mock("@angee/metadata", () => ({
  refineInvalidationParams: (target: { modelLabel: string }) => ({
    dataProviderName: "console",
    invalidates: ["list", "many", "detail"],
    resource: target.modelLabel,
  }),
  resourceInvalidationTargets: (_metadata: unknown, modelLabels: readonly string[]) =>
    modelLabels.map((modelLabel) => ({ modelLabel })),
  useSchemaFieldMetadata: () => ({ schemas: {} }),
}));

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
