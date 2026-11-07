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
  return {
    authoredCalls: [] as unknown[],
    refineMutations: [] as RefineMutation[],
    invalidations: [] as unknown[],
    refuseRestoreId: null as string | null,
    blockedDeleteId: null as string | null,
    failedUpdateId: null as string | null,
  };
});

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useBusyRun: vi.fn((onChanged?: () => void) => ({
      busy: false,
      run: async <T,>(task: () => Promise<T>) => {
        const result = await task();
        onChanged?.();
        return result;
      },
    })),
    // Restore rides the shared trash owner; the test records the ids it is asked for.
    useTrashRecord: vi.fn(() => ({
      available: true,
      busy: false,
      trash: vi.fn(async () => true),
      restore: vi.fn(async (id: string) => {
        sdk.authoredCalls.push({ id });
        return id !== sdk.refuseRestoreId;
      }),
    })),
  };
});

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAuthoredMutation: vi.fn(() => [
      vi.fn(async (variables: unknown) => {
        sdk.authoredCalls.push(variables);
        return {};
      }),
      { error: null, fetching: false },
    ]),
  };
});

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
  const deletePreview = {
    total_deleted_count: 1,
    deleted: [],
    updated: [],
    blocked: [],
    has_blockers: false,
    root: { label: "record", object_label: "Record", object_id: "row_1", children: [] },
  };
  return {
    ...actual,
    useCreate: mutation("create", () => ({ id: "row_1" })),
    useUpdate: mutation("update", (input) => {
      if ((input as { id?: string }).id === sdk.failedUpdateId) throw new Error("Move refused");
      return { id: (input as { id?: string }).id, ...(input as { values?: Record<string, unknown> }).values };
    }),
    useCustomMutation: mutation("deletePreview", (input) => {
      const preview = (input as { values?: { id?: string } }).values?.id === sdk.blockedDeleteId
        ? { ...deletePreview, has_blockers: true, total_deleted_count: 0 }
        : deletePreview;
      return { deleteFilePreview: preview, deleteFolderPreview: preview };
    }),
    useInvalidate: () => vi.fn(async (input: unknown) => {
      sdk.invalidations.push(input);
    }),
  };
});

import { ToastProvider } from "@angee/ui";
import { useFileActions } from "./use-file-actions";
import { useFolderActions } from "./use-folder-actions";

const FILE_INVALIDATION = {
  resource: "console:storage.File",
  dataProviderName: "console",
  invalidates: ["resourceAll"],
};

describe("storage file/folder actions", () => {
  beforeEach(() => {
    sdk.authoredCalls.length = 0;
    sdk.refineMutations.length = 0;
    sdk.invalidations.length = 0;
    sdk.refuseRestoreId = null;
    sdk.blockedDeleteId = null;
    sdk.failedUpdateId = null;
  });

  test("file actions use refine mutations and confirm soft deletes", async () => {
    const onChanged = vi.fn();
    const { result } = renderHook(() => useFileActions({ onChanged }), {
      wrapper: MetadataWrapper,
    });
    const [deleteFile, updateFile] = sdk.refineMutations;

    expect(deleteFile).toMatchObject({
      kind: "deletePreview",
    });
    expect(updateFile).toMatchObject({
      kind: "update",
      options: { resource: "files", dataProviderName: "console" },
    });

    await act(async () => {
      await result.current.trash("fil_1");
      await expect(result.current.move("fil_1", "fld_1")).resolves.toBe(true);
      await result.current.move("fil_1", null);
      await result.current.trashMany(["fil_2", "fil_3"]);
      await result.current.restore("fil_1");
    });

    expect(deleteFile?.calls).toEqual([
      expect.objectContaining({ values: { id: "fil_1", confirm: true } }),
      expect.objectContaining({ values: { id: "fil_2", confirm: true } }),
      expect.objectContaining({ values: { id: "fil_3", confirm: true } }),
    ]);
    // The verbs own the error surface: Refine's notification is off on every call.
    expect(updateFile?.calls).toEqual([
      { id: "fil_1", values: { folder: "fld_1" }, errorNotification: false },
      { id: "fil_1", values: { folder: null }, errorNotification: false },
    ]);
    expect(sdk.authoredCalls).toEqual([{ id: "fil_1" }]);
    expect(onChanged).toHaveBeenCalledTimes(5);
  });

  test("bulk restore stops on the shared owner's reported refusal", async () => {
    sdk.refuseRestoreId = "fil_2";
    const onChanged = vi.fn();
    const { result } = renderHook(() => useFileActions({ onChanged }), { wrapper: MetadataWrapper });
    await act(async () => {
      await expect(result.current.restoreMany(["fil_1", "fil_2", "fil_3"])).resolves.toBe(false);
    });
    expect(sdk.authoredCalls).toEqual([{ id: "fil_1" }, { id: "fil_2" }]);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  test("bulk trash does not treat a blocked confirmation as deletion", async () => {
    sdk.blockedDeleteId = "fil_2";
    const { result } = renderHook(() => useFileActions(), { wrapper: MetadataWrapper });
    await act(async () => {
      await expect(result.current.trashMany(["fil_1", "fil_2", "fil_3"])).resolves.toBe(false);
    });
    expect(sdk.refineMutations[0]?.calls).toHaveLength(2);
    expect(sdk.refineMutations[0]?.calls).toEqual([
      expect.objectContaining({ values: { id: "fil_1", confirm: true }, errorNotification: false }),
      expect.objectContaining({ values: { id: "fil_2", confirm: true }, errorNotification: false }),
    ]);
    expect(sdk.invalidations).toEqual([FILE_INVALIDATION]);
  });

  test("a failed move settles once in the verb and resolves false", async () => {
    sdk.failedUpdateId = "fil_9";
    const onChanged = vi.fn();
    const { result } = renderHook(() => useFileActions({ onChanged }), { wrapper: MetadataWrapper });
    await act(async () => {
      await expect(result.current.move("fil_9", "fld_1")).resolves.toBe(false);
    });
    expect(onChanged).not.toHaveBeenCalled();
  });

  test("a blocked folder deletion preserves the folder and does not refresh its files", async () => {
    sdk.blockedDeleteId = "fld_2";
    const { result } = renderHook(() => useFolderActions(), { wrapper: MetadataWrapper });
    await act(async () => {
      await expect(result.current.remove("fld_2")).resolves.toBe(false);
    });
    expect(sdk.invalidations).toEqual([]);
  });

  test("folder actions use refine mutations and confirm removes", async () => {
    const { result } = renderHook(() => useFolderActions(), {
      wrapper: MetadataWrapper,
    });
    const [createFolder, updateFolder, deleteFolder] = sdk.refineMutations;

    expect(createFolder).toMatchObject({
      kind: "create",
      options: { resource: "folders", dataProviderName: "console" },
    });
    expect(updateFolder).toMatchObject({
      kind: "update",
      options: { resource: "folders", dataProviderName: "console" },
    });
    expect(deleteFolder).toMatchObject({
      kind: "deletePreview",
    });

    await act(async () => {
      await result.current.create({
        drive: "drv_1",
        name: "Design",
        parent: null,
      });
      await result.current.rename("fld_1", "Docs");
      await result.current.remove("fld_1");
    });

    expect(createFolder?.calls).toEqual([
      { values: { drive: "drv_1", name: "Design", parent: null } },
    ]);
    expect(updateFolder?.calls).toEqual([
      { id: "fld_1", values: { name: "Docs" } },
    ]);
    expect(deleteFolder?.calls).toEqual([
      expect.objectContaining({ values: { id: "fld_1", confirm: true } }),
    ]);
    expect(sdk.invalidations).toEqual([
      FILE_INVALIDATION,
      {
        resource: "console:storage.Folder",
        dataProviderName: "console",
        invalidates: ["resourceAll"],
      },
      FILE_INVALIDATION,
    ]);
  });
});

const STORAGE_METADATA: SchemaFieldMetadata = withTestResourceInventory({
  types: {
    FileType: {
      fields: {},
      resource: {
        query: testResourceQuery({ identity: { field: "id" }, fields: { "id": testQueryField("id", { scalar: "ID", filter: null }) }, axes: {}, sort: { default: [] } }),

        schemaName: "console",
        modelLabel: "storage.File",
        appLabel: "storage",
        modelName: "File",

        roots: {
          detail: "file",
          list: "files",
          update: "updateFile",
          deletePreview: "deleteFilePreview",
        },
        typeNames: {
          node: "FileType",
          filter: "FileFilter",
          order: "FileOrder",
          deletePayload: "FileDeletePreview",
        },
        capabilities: ["detail", "list", "update", "delete"],


        aggregateFields: [],


      },
    },
    FolderType: {
      fields: {
        name: { name: "name", kind: "scalar", scalar: "String" },
      },
      resource: {
        query: testResourceQuery({ identity: { field: "id" }, fields: { "name": testQueryField("name", { scalar: "String", kind: "scalar", filter: null, sort: { field: "name" } }),
                "id": testQueryField("id", { scalar: "ID", filter: null }) }, axes: {}, sort: { default: [] } }),

        schemaName: "console",
        modelLabel: "storage.Folder",
        appLabel: "storage",
        modelName: "Folder",

        roots: {
          detail: "folder",
          list: "folders",
          create: "createFolder",
          update: "updateFolder",
          deletePreview: "deleteFolderPreview",
        },
        typeNames: {
          node: "FolderType",
          filter: "FolderFilter",
          order: "FolderOrder",
          deletePayload: "FolderDeletePreview",
        },
        capabilities: ["detail", "list", "create", "update", "delete"],


        aggregateFields: [],


      },
    },
  },
});

function MetadataWrapper({ children }: { children: ReactNode }): ReactElement {
  return (
    <OperationDocumentsProvider documents={STORAGE_OPERATION_DOCUMENTS}>
      <ModelMetadataProvider metadata={STORAGE_METADATA}>
        <ToastProvider>{children}</ToastProvider>
      </ModelMetadataProvider>
    </OperationDocumentsProvider>
  );
}

const STORAGE_OPERATION_DOCUMENTS = {
  console: {
    deletePreviews: {
      "storage.File": { kind: "Document", definitions: [] },
      "storage.Folder": { kind: "Document", definitions: [] },
    },
  },
};
