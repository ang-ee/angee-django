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
} from "@angee/resources";
import { OperationDocumentsProvider } from "@angee/refine";
import type {
  SchemaFieldMetadata,
} from "@angee/resources";

const sdk = vi.hoisted(() => {
  type RefineMutation = {
    kind: string;
    calls: unknown[];
    options: Record<string, unknown>;
  };
  return {
    refineMutations: [] as RefineMutation[],
    invalidations: [] as unknown[],
  };
});

vi.mock("@angee/ui", () => ({
  rowPublicId: (record: { id?: string } | null | undefined) => record?.id ?? null,
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
    useCreate: mutation("create", () => ({ id: "pag_new", title: "New page" })),
    useUpdate: mutation("update", (input) => ({
      id: (input as { id?: string }).id,
      ...(input as { values?: Record<string, unknown> }).values,
    })),
    useCustomMutation: mutation("deletePreview", () => ({
      deletePagePreview: {
        totalDeletedCount: 1,
        deleted: [],
        updated: [],
        blocked: [],
        hasBlockers: false,
        root: { label: "page", objectLabel: "Page", objectId: "pag_1", children: [] },
      },
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
  });

  test("uses refine mutations and preserves returned page id", async () => {
    const onChanged = vi.fn();
    const { result } = renderHook(() => usePageActions({ onChanged }), {
      wrapper: MetadataWrapper,
    });
    const [createPage, updatePage, deletePage] = sdk.refineMutations;

    expect(createPage).toMatchObject({
      kind: "create",
      options: {
        resource: "pages",
        dataProviderName: "console",
        meta: { fields: ["id", "title"] },
      },
    });
    expect(deletePage).toMatchObject({
      kind: "deletePreview",
    });
    expect(updatePage).toMatchObject({
      kind: "update",
      options: { resource: "pages", dataProviderName: "console" },
    });

    let createdId: string | null = null;
    await act(async () => {
      createdId = await result.current.createPage({
        vault: "vlt_1",
        title: "New page",
        kind: "page",
        parent: null,
      });
      await result.current.movePage("pag_1", "pag_parent");
      await result.current.deletePage("pag_1");
    });

    expect(createdId).toBe("pag_new");
    expect(createPage?.calls).toEqual([
      {
        values: {
          vault: "vlt_1",
          title: "New page",
          kind: "page",
          parent: null,
        },
      },
    ]);
    expect(updatePage?.calls).toEqual([
      { id: "pag_1", values: { parent: "pag_parent" } },
    ]);
    expect(deletePage?.calls).toEqual([
      expect.objectContaining({
        values: { id: "pag_1", confirm: true },
        dataProviderName: "console",
      }),
    ]);
    expect(sdk.invalidations).toEqual([
      expect.objectContaining({
        resource: "pages",
        dataProviderName: "console",
        id: "pag_1",
        invalidates: ["list", "many", "detail"],
      }),
    ]);
    expect(onChanged).toHaveBeenCalledTimes(3);
  });
});

const PAGE_METADATA: SchemaFieldMetadata = {
  types: {
    PageType: {
      typeName: "PageType",
      fields: {
        title: { name: "title", kind: "scalar", scalar: "String" },
      },
      rootFields: {
        list: "pages",
        create: "createPage",
        update: "updatePage",
        delete: "deletePage",
      },
      resource: {
        schemaName: "console",
        modelLabel: "knowledge.Page",
        appLabel: "knowledge",
        modelName: "Page",
        publicIdField: "id",
        roots: {
          list: "pages",
          create: "createPage",
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
