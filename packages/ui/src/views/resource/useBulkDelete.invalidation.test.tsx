// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { ToastProvider } from "../../feedback";

const harness = vi.hoisted(() => {
  const dataResource = {
    schemaName: "console",
    modelLabel: "test.Row",
    appLabel: "test",
    modelName: "row",
    roots: {
      list: "test_rows",
      aggregate: "test_rows_aggregate",
      deletePreview: "delete_test_row",
    },
    typeNames: { node: "TestRowType" },
    recordRepresentation: "name",
    capabilities: ["list", "aggregate"],
    fields: [],
    aggregateFields: [],
  };
  return {
    dataResource,
    invalidateDataResource: vi.fn(async () => undefined),
    previewMutate: vi.fn(async () => ({
      totalDeletedCount: 1,
      deleted: [{ label: "Row", count: 1 }],
      updated: [],
      blocked: [],
      hasBlockers: false,
      root: { label: "Row", objectLabel: "Alpha", objectId: "row-a", children: [] },
    })),
  };
});

vi.mock("@refinedev/core", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@refinedev/core")>()),
  useCan: () => ({ data: { can: true }, isLoading: false, error: null }),
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAngeeDeletePreview: () => ({
    mutate: harness.previewMutate,
    fetching: false,
    error: null,
  }),
}));

// Keep refineResourceIdentifier/refineResourceName real: the point of the test is
// which of the two the delete path hands to the invalidation.
vi.mock("@angee/metadata", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/metadata")>()),
  useModelMetadata: () => ({ resource: harness.dataResource }),
  useModelRootFields: () => harness.dataResource.roots,
}));

vi.mock("./resource-operations", () => ({
  useDeletePreviewOperation: () => ({
    target: {
      dataProviderName: "console",
      root: "delete_test_row",
      modelLabel: "test.Row",
    },
    document: {},
  }),
  useInvalidateDataResource: () => harness.invalidateDataResource,
}));

const { useBulkDelete } = await import("./useBulkDelete");

afterEach(() => {
  cleanup();
  harness.invalidateDataResource.mockClear();
  harness.previewMutate.mockClear();
});

function wrapper({ children }: { children: ReactNode }) {
  return <ToastProvider>{children}</ToastProvider>;
}

test("a confirmed delete hands the deleted row to the invalidation owner", async () => {
  const { result } = renderHook(
    () => useBulkDelete("test.Row", new Set(["row-a"]), () => undefined),
    { wrapper },
  );

  act(() => { result.current.deleteInitiate(); });
  await waitFor(() => expect(result.current.isPreviewOpen).toBe(true));
  act(() => { result.current.onConfirm(); });
  await waitFor(() => expect(harness.invalidateDataResource).toHaveBeenCalledTimes(1));

  // What the owner then invalidates is its own contract, tested in
  // useInvalidateDataResource.test.tsx against a real QueryClient.
  expect(harness.invalidateDataResource).toHaveBeenCalledWith(
    harness.dataResource,
    "row-a",
  );
});
