// @vitest-environment happy-dom
import {
  act,
  renderHook } from "@testing-library/react";
import type { ReactElement,
  ReactNode } from "react";
import { beforeEach,
  describe,
  expect,
  test,
  vi } from "vitest";
import {
  ModelMetadataProvider,
} from "@angee/metadata";
import { withTestResourceInventory, testResourceQuery, testQueryField } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import type {
  SchemaFieldMetadata,
} from "@angee/metadata";

const sdk = vi.hoisted(() => {
  type RefineMutation = {
    kind: string;
    calls: unknown[];
    options: Record<string, unknown>;
  };
  const invalidated: unknown[] = [];
  return {
    refineMutations: [] as RefineMutation[],
    invalidations: [] as unknown[],
    createPage: vi.fn(),
    trash: vi.fn(),
    restore: vi.fn(),
    invalidatedModels: invalidated,
    invalidateModels: (models: unknown) => {
      invalidated.push(models);
    },
  };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: () => [sdk.createPage, { fetching: false, error: null }],
  useInvalidateAuthoredModels: () => sdk.invalidateModels,
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  rowPublicId: (record: { id?: string } | null | undefined) => record?.id ?? null,
  useTrashRecord: () => ({ available: true, busy: false, trash: sdk.trash, restore: sdk.restore }),
  useBusyRun: vi.fn((onChanged?: () => void) => ({
    busy: false,
    run: async <T,>(task: () => Promise<T>) => {
      const result = await task();
      onChanged?.();
      return result;
    },
  })),
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  const mutation = (kind: string, response: (input: unknown) => unknown) =>
    (options: Record<string, unknown> = {}) => {
      const calls: unknown[] = [];
      sdk.refineMutations.push({ kind, calls, options });
      return {
        mutateAsync: vi.fn(async (input: unknown) => {
          calls.push(input);
          return { data: response(input) };
        }),
        mutation: { error: null, isPending: false },
      };
    };
  return {
    ...actual,
    useUpdate: mutation("update", (input) => ({
      id: (input as { id?: string }).id,
      ...(input as { values?: Record<string, unknown> }).values,
    })),
    useInvalidate: () => vi.fn(async (input: unknown) => {
      sdk.invalidations.push(input);
    }),
  };
});

import { usePageActions } from "./use-page-actions";

describe("knowledge page actions", () => {
  beforeEach(() => {
    sdk.refineMutations.length = 0;
    sdk.invalidations.length = 0;
    sdk.createPage.mockReset();
    sdk.createPage.mockResolvedValue({ create_page: { id: "pag_new" } });
    sdk.trash.mockReset();
    sdk.trash.mockResolvedValue(true);
    sdk.restore.mockReset();
    sdk.restore.mockResolvedValue(true);
    sdk.invalidatedModels.length = 0;
  });

  test("uses refine mutations, the shared trash verbs, and preserves returned page id", async () => {
    const { result } = renderHook(() => usePageActions(), {
      wrapper: MetadataWrapper,
    });
    const [updatePage] = sdk.refineMutations;
    expect(sdk.refineMutations).toHaveLength(1);
    expect(updatePage).toMatchObject({
      kind: "update",
      options: { resource: "pages", dataProviderName: "console" },
    });

    let createdId: string | null = null;
    let trashed = false;
    let restored = false;
    await act(async () => {
      createdId = await result.current.createPage({
        vault: "vlt_1",
        title: "New page",
        kind: "note",
        parent: null,
      });
      await result.current.movePage("pag_1", "pag_parent");
      trashed = await result.current.trashPage("pag_1", "Plan");
      restored = await result.current.restorePage("pag_1");
    });

    expect(createdId).toBe("pag_new");
    expect(sdk.createPage).toHaveBeenCalledWith({
      vault: "vlt_1", title: "New page", kind: "note", parent: null,
    });
    expect(updatePage?.calls).toEqual([
      { id: "pag_1", values: { parent: "pag_parent" } },
    ]);
    expect([trashed, restored]).toEqual([true, true]);
    expect(sdk.trash).toHaveBeenCalledWith("pag_1", "Plan");
    expect(sdk.restore).toHaveBeenCalledWith("pag_1");
    expect(sdk.invalidatedModels).toEqual([
      ["knowledge.Page"],
      ["knowledge.Page"],
      ["knowledge.Page"],
      ["knowledge.Page"],
    ]);
  });

  test("keeps navigator verbs stable across rerenders", () => {
    const { result, rerender } = renderHook(
      () => usePageActions(),
      { wrapper: MetadataWrapper },
    );
    const first = result.current;

    rerender();

    expect(result.current).toBe(first);
    expect(result.current.createPage).toBe(first.createPage);
    expect(result.current.trashPage).toBe(first.trashPage);
    expect(result.current.restorePage).toBe(first.restorePage);
    expect(result.current.movePage).toBe(first.movePage);
  });
});

const PAGE_METADATA: SchemaFieldMetadata = withTestResourceInventory({
  types: {
    PageType: {
      fields: {
        title: { name: "title", kind: "scalar", scalar: "String" },
      },
      resource: {
        query: testResourceQuery({ identity: { field: "id" }, fields: { "title": testQueryField("title", { scalar: "String", kind: "scalar", filter: null, sort: { field: "title" } }),
                "id": testQueryField("id", { scalar: "ID", filter: null }) }, axes: {}, sort: { default: [] } }),

        schemaName: "console",
        modelLabel: "knowledge.Page",
        appLabel: "knowledge",
        modelName: "Page",

        roots: {
          list: "pages",
          update: "updatePage",
          deletePreview: "deletePagePreview",
        },
        typeNames: {
          node: "PageType",
          filter: "PageFilter",
          order: "PageOrder",
          deletePayload: "PageDeletePreview",
        },
        capabilities: ["list", "create", "update", "delete"],


        aggregateFields: [],


      },
    },
  },
});

function MetadataWrapper({ children }: { children: ReactNode }): ReactElement {
  return (
    <OperationDocumentsProvider documents={PAGE_OPERATION_DOCUMENTS}>
      <ModelMetadataProvider metadata={PAGE_METADATA}>
        {children}
      </ModelMetadataProvider>
    </OperationDocumentsProvider>
  );
}

const PAGE_OPERATION_DOCUMENTS = {
  console: {
    deletePreviews: {
      "knowledge.Page": { kind: "Document", definitions: [] },
    },
  },
};
