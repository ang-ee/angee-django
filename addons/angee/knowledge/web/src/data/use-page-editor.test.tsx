// @vitest-environment happy-dom
import {
  act,
  renderHook } from "@testing-library/react";
import type { ReactElement,
  ReactNode } from "react";
import { afterEach,
  beforeEach,
  describe,
  expect,
  test,
  vi } from "vitest";
import {
  ModelMetadataProvider,
} from "@angee/metadata";
import type {
  SchemaFieldMetadata,
} from "@angee/metadata";

const sdkMocks = vi.hoisted(() => ({
  updatePage: vi.fn(),
  updateBody: vi.fn(),
  useAuthoredMutation: vi.fn(),
  useUpdate: vi.fn(),
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return { ...actual };
});

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAuthoredMutation: sdkMocks.useAuthoredMutation,
  };
});

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useUpdate: (options: Record<string, unknown>) => {
      sdkMocks.useUpdate(options);
      return {
        mutateAsync: sdkMocks.updatePage,
        mutation: { isPending: false, error: null },
      };
    },
  };
});

import { usePageEditor } from "./use-page-editor";

describe("usePageEditor", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sdkMocks.updatePage.mockReset();
    sdkMocks.updateBody.mockReset();
    sdkMocks.useAuthoredMutation.mockReset();
    sdkMocks.useUpdate.mockReset();
    sdkMocks.updatePage.mockResolvedValue({ id: "pag_1", title: "Updated" });
    sdkMocks.updateBody.mockResolvedValue({
      update_page_body: {
        ok: true,
        markdown: { body_hash: "hash-next" },
      },
    });
    sdkMocks.useAuthoredMutation.mockImplementation((document: unknown) => {
      const operationName = graphqlOperationName(document);
      if (operationName === "KnowledgeUpdatePageBody") {
        return [sdkMocks.updateBody, { fetching: false, error: null }];
      }
      throw new Error(`Unexpected mutation: ${operationName}`);
    });
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  test("debounces body saves to the latest draft without refreshing the tree", async () => {
    const onTitleSaved = vi.fn();
    const { result } = renderHook(() =>
      usePageEditor(
        "pag_1",
        { title: "Page", body: "Old body", bodyHash: "hash-old" },
        onTitleSaved,
      ),
      { wrapper: MetadataWrapper },
    );

    act(() => {
      result.current.setBody("First draft");
      result.current.setBody("Latest draft");
    });

    expect(result.current.body).toBe("Latest draft");
    expect(result.current.status).toBe("saving");
    expect(sdkMocks.updateBody).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(700);
    });

    expect(sdkMocks.updateBody).toHaveBeenCalledTimes(1);
    expect(sdkMocks.updateBody).toHaveBeenCalledWith({
      page: "pag_1",
      body: "Latest draft",
      expected_hash: "hash-old",
    });
    expect(onTitleSaved).not.toHaveBeenCalled();
    expect(result.current.status).toBe("saved");
  });

  test("cancels a pending body save when the draft returns to the saved body", async () => {
    const onTitleSaved = vi.fn();
    const { result } = renderHook(() =>
      usePageEditor(
        "pag_1",
        { title: "Page", body: "Old body", bodyHash: "hash-old" },
        onTitleSaved,
      ),
      { wrapper: MetadataWrapper },
    );

    act(() => {
      result.current.setBody("Changed body");
      result.current.setBody("Old body");
    });

    expect(result.current.body).toBe("Old body");
    expect(result.current.status).toBe("idle");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(700);
    });

    expect(sdkMocks.updateBody).not.toHaveBeenCalled();
    expect(onTitleSaved).not.toHaveBeenCalled();
  });

  test("commits title changes through the SDK page update mutation", async () => {
    const onTitleSaved = vi.fn();
    const { result } = renderHook(() =>
      usePageEditor(
        "pag_1",
        { title: "Page", body: "Old body", bodyHash: "hash-old" },
        onTitleSaved,
      ),
      { wrapper: MetadataWrapper },
    );

    act(() => {
      result.current.setTitle("Renamed page");
    });

    await act(async () => {
      result.current.commitTitle();
      await Promise.resolve();
    });

    expect(sdkMocks.useUpdate).toHaveBeenCalledWith(
      expect.objectContaining({
        resource: "pages",
        dataProviderName: "console",
        invalidates: ["list", "many", "detail"],
      }),
    );
    expect(sdkMocks.updatePage).toHaveBeenCalledWith({
      id: "pag_1",
      values: { title: "Renamed page" },
    });
    expect(onTitleSaved).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("saved");
  });

  test("flushes the pending body save on unmount without refreshing the tree", async () => {
    const onTitleSaved = vi.fn();
    const { result, unmount } = renderHook(() =>
      usePageEditor(
        "pag_2",
        { title: "Page", body: "Old body", bodyHash: "hash-old" },
        onTitleSaved,
      ),
      { wrapper: MetadataWrapper },
    );

    act(() => {
      result.current.setBody("Leaving now");
    });
    expect(sdkMocks.updateBody).not.toHaveBeenCalled();

    await act(async () => {
      unmount();
      await Promise.resolve();
    });

    expect(sdkMocks.updateBody).toHaveBeenCalledTimes(1);
    expect(sdkMocks.updateBody).toHaveBeenCalledWith({
      page: "pag_2",
      body: "Leaving now",
      expected_hash: "hash-old",
    });
    expect(onTitleSaved).not.toHaveBeenCalled();
  });
});

function graphqlOperationName(document: unknown): string {
  return (
    (document as { definitions?: Array<{ name?: { value?: string } }> })
      .definitions?.[0]?.name?.value ?? ""
  );
}

const PAGE_METADATA: SchemaFieldMetadata = {
  types: {
    PageType: {
      typeName: "PageType",
      fields: {
        title: { name: "title", kind: "scalar", scalar: "String" },
      },
      rootFields: {
        detail: "page",
        list: "pages",
        update: "updatePage",
      },
      resource: {
        schemaName: "console",
        modelLabel: "knowledge.Page",
        appLabel: "knowledge",
        modelName: "Page",
        publicIdField: "id",
        roots: {
          detail: "page",
          list: "pages",
          update: "updatePage",
        },
        typeNames: {
          node: "PageType",
          filter: "PageFilter",
          order: "PageOrder",
        },
        capabilities: ["detail", "list", "update"],
        filterFields: [],
        orderFields: ["title"],
        aggregateFields: [],
        groupByFields: [],
        relationAxes: [],
      },
    },
  },
};

function MetadataWrapper({ children }: { children: ReactNode }): ReactElement {
  return (
    <ModelMetadataProvider metadata={PAGE_METADATA}>
      {children}
    </ModelMetadataProvider>
  );
}
