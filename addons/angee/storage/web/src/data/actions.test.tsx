// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const sdk = vi.hoisted(() => {
  type ResourceMutation = {
    action: string;
    calls: unknown[];
    modelLabel: string;
    options: Record<string, unknown>;
  };
  return {
    authoredCalls: [] as unknown[],
    resourceMutations: [] as ResourceMutation[],
  };
});

vi.mock("@angee/sdk", () => ({
  useAuthoredMutation: vi.fn(() => [
    vi.fn(async (variables: unknown) => {
      sdk.authoredCalls.push(variables);
      return {};
    }),
    { error: null, fetching: false },
  ]),
}));

vi.mock("@angee/base", () => ({
  useBusyRun: vi.fn((onChanged?: () => void) => ({
    busy: false,
    run: async <T,>(task: () => Promise<T>) => {
      const result = await task();
      onChanged?.();
      return result;
    },
  })),
}));

vi.mock("@angee/data", () => ({
  useResourceMutation: vi.fn(
    (modelLabel: string, action: string, options: Record<string, unknown> = {}) => {
      const calls: unknown[] = [];
      sdk.resourceMutations.push({ action, calls, modelLabel, options });
      return [
        vi.fn(async (variables: unknown) => {
          calls.push(variables);
          return { id: "row_1" };
        }),
        { error: null, fetching: false },
      ];
    },
  ),
}));

import { useFileActions } from "./use-file-actions";
import { useFolderActions } from "./use-folder-actions";

describe("storage file/folder actions", () => {
  beforeEach(() => {
    sdk.authoredCalls.length = 0;
    sdk.resourceMutations.length = 0;
  });

  test("file actions use resource mutations and confirm soft deletes", async () => {
    const onChanged = vi.fn();
    const { result } = renderHook(() => useFileActions({ onChanged }));
    const [deleteFile, updateFile] = sdk.resourceMutations;

    expect(deleteFile).toMatchObject({
      action: "delete",
      modelLabel: "storage.File",
    });
    expect(updateFile).toMatchObject({
      action: "update",
      modelLabel: "storage.File",
    });

    await act(async () => {
      await result.current.trash("fil_1");
      await result.current.move("fil_1", "fld_1");
      await result.current.trashMany(["fil_2", "fil_3"]);
      await result.current.restore("fil_1");
    });

    expect(deleteFile?.calls).toEqual([
      { id: "fil_1", confirm: true },
      { id: "fil_2", confirm: true },
      { id: "fil_3", confirm: true },
    ]);
    expect(updateFile?.calls).toEqual([
      { data: { id: "fil_1", folder: "fld_1" } },
    ]);
    expect(sdk.authoredCalls).toEqual([{ id: "fil_1" }]);
    expect(onChanged).toHaveBeenCalledTimes(4);
  });

  test("folder actions use resource mutations and confirm removes", async () => {
    const { result } = renderHook(() => useFolderActions());
    const [createFolder, updateFolder, deleteFolder] = sdk.resourceMutations;

    expect(createFolder).toMatchObject({
      action: "create",
      modelLabel: "storage.Folder",
      options: { fields: ["name"] },
    });
    expect(updateFolder).toMatchObject({
      action: "update",
      modelLabel: "storage.Folder",
      options: { fields: ["name"] },
    });
    expect(deleteFolder).toMatchObject({
      action: "delete",
      modelLabel: "storage.Folder",
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
      { data: { drive: "drv_1", name: "Design", parent: null } },
    ]);
    expect(updateFolder?.calls).toEqual([
      { data: { id: "fld_1", name: "Docs" } },
    ]);
    expect(deleteFolder?.calls).toEqual([{ id: "fld_1", confirm: true }]);
  });
});
