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
    resourceMutations: [] as ResourceMutation[],
  };
});

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
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
    useResourceMutation: vi.fn(
      (modelLabel: string, action: string, options: Record<string, unknown> = {}) => {
        const calls: unknown[] = [];
        sdk.resourceMutations.push({ action, calls, modelLabel, options });
        return [
          vi.fn(async (variables: unknown) => {
            calls.push(variables);
            return { id: "pag_new", title: "New page" };
          }),
          { error: null, fetching: false },
        ];
      },
    ),
  };
});

import { usePageActions } from "./use-page-actions";

describe("knowledge page actions", () => {
  beforeEach(() => {
    sdk.resourceMutations.length = 0;
  });

  test("uses SDK CRUD mutations and preserves returned page id", async () => {
    const onChanged = vi.fn();
    const { result } = renderHook(() => usePageActions({ onChanged }));
    const [createPage, deletePage, updatePage] = sdk.resourceMutations;

    expect(createPage).toMatchObject({
      action: "create",
      modelLabel: "knowledge.Page",
      options: { fields: ["title"] },
    });
    expect(deletePage).toMatchObject({
      action: "delete",
      modelLabel: "knowledge.Page",
    });
    expect(updatePage).toMatchObject({
      action: "update",
      modelLabel: "knowledge.Page",
      options: { fields: ["title"] },
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
        data: {
          vault: "vlt_1",
          title: "New page",
          kind: "page",
          parent: null,
        },
      },
    ]);
    expect(updatePage?.calls).toEqual([
      { data: { id: "pag_1", parent: "pag_parent" } },
    ]);
    expect(deletePage?.calls).toEqual([{ id: "pag_1", confirm: true }]);
    expect(onChanged).toHaveBeenCalledTimes(3);
  });
});
