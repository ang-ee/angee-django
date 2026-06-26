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
    authoredCalls: [] as unknown[],
    refineMutations: [] as RefineMutation[],
    invalidations: [] as unknown[],
  };
});

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useAuthoredMutation: vi.fn(() => [
      vi.fn(async (variables: unknown) => {
        sdk.authoredCalls.push(variables);
        return {};
      }),
      { error: null, fetching: false },
    ]),
    useBusyRun: vi.fn((onChanged?: () => void) => ({
      busy: false,
      run: async <T,>(task: () => Promise<T>) => {
        const result = await task();
        onChanged?.();
        return result;
      },
    })),
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
    totalDeletedCount: 1,
    deleted: [],
    updated: [],
    blocked: [],
    hasBlockers: false,
    root: { label: "record", objectLabel: "Record", objectId: "row_1", children: [] },
  };
  return {
    ...actual,
    useCreate: mutation("create", () => ({ id: "row_1" })),
    useUpdate: mutation("update", (input) => ({
      id: (input as { id?: string }).id,
      ...(input as { values?: Record<string, unknown> }).values,
    })),
    useCustomMutation: mutation("deletePreview", () => ({
      deleteFilePreview: deletePreview,
      deleteFolderPreview: deletePreview,
    })),
    useInvalidate: () => vi.fn(async (input: unknown) => {
      sdk.invalidations.push(input);
    }),
  };
});

import { useFileActions } from "./use-file-actions";
import { useFolderActions } from "./use-folder-actions";

describe("storage file/folder actions", () => {
  beforeEach(() => {
    sdk.authoredCalls.length = 0;
    sdk.refineMutations.length = 0;
    sdk.invalidations.length = 0;
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
      await result.current.move("fil_1", "fld_1");
      await result.current.trashMany(["fil_2", "fil_3"]);
      await result.current.restore("fil_1");
    });

    expect(deleteFile?.calls).toEqual([
      expect.objectContaining({ values: { id: "fil_1", confirm: true } }),
      expect.objectContaining({ values: { id: "fil_2", confirm: true } }),
      expect.objectContaining({ values: { id: "fil_3", confirm: true } }),
    ]);
    expect(updateFile?.calls).toEqual([
      { id: "fil_1", values: { folder: "fld_1" } },
    ]);
    expect(sdk.authoredCalls).toEqual([{ id: "fil_1" }]);
    expect(onChanged).toHaveBeenCalledTimes(4);
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
  });
});

const STORAGE_METADATA: SchemaFieldMetadata = {
  types: {
    FileType: {
      typeName: "FileType",
      fields: {},
      rootFields: {
        detail: "file",
        list: "files",
        update: "updateFile",
        delete: "deleteFile",
      },
      resource: {
        schemaName: "console",
        modelLabel: "storage.File",
        appLabel: "storage",
        modelName: "File",
        publicIdField: "id",
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
        filterFields: [],
        orderFields: [],
        aggregateFields: [],
        groupByFields: [],
        relationAxes: [],
      },
    },
    FolderType: {
      typeName: "FolderType",
      fields: {
        name: { name: "name", kind: "scalar", scalar: "String" },
      },
      rootFields: {
        detail: "folder",
        list: "folders",
        create: "createFolder",
        update: "updateFolder",
        delete: "deleteFolder",
      },
      resource: {
        schemaName: "console",
        modelLabel: "storage.Folder",
        appLabel: "storage",
        modelName: "Folder",
        publicIdField: "id",
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
        filterFields: [],
        orderFields: ["name"],
        aggregateFields: [],
        groupByFields: [],
        relationAxes: [],
      },
    },
  },
};

function MetadataWrapper({ children }: { children: ReactNode }): ReactElement {
  return (
    <OperationDocumentsProvider documents={STORAGE_OPERATION_DOCUMENTS}>
      <ModelMetadataProvider metadata={STORAGE_METADATA}>
        {children}
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
