// @vitest-environment happy-dom
import { act, renderHook, waitFor } from "@testing-library/react";
import { useMutation as useUrqlMutation } from "urql";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { useDocumentMutation } from "./document-mutation";

vi.mock("urql", () => ({
  useMutation: vi.fn(),
}));

const useMutationMock = vi.mocked(useUrqlMutation);

describe("useDocumentMutation", () => {
  beforeEach(() => {
    useMutationMock.mockReset();
  });

  test("tracks the submitted promise instead of stale urql fetching", async () => {
    let release!: () => void;
    const gate = new Promise<{ data: { ok: boolean } }>((resolve) => {
      release = () => resolve({ data: { ok: true } });
    });
    const run = vi.fn(() => gate);
    useMutationMock.mockReturnValue([
      { fetching: true, error: null },
      run,
    ] as never);

    const { result } = renderHook(() =>
      useDocumentMutation<{ ok: boolean }, { id: string }>("mutation Test { ok }"),
    );

    expect(result.current.fetching).toBe(false);

    let pending!: Promise<{ ok: boolean } | undefined>;
    act(() => {
      pending = result.current.execute({ id: "obj_1" });
    });
    await waitFor(() => expect(result.current.fetching).toBe(true));

    await act(async () => {
      release();
      await expect(pending).resolves.toEqual({ ok: true });
    });

    expect(result.current.fetching).toBe(false);
    expect(run).toHaveBeenCalledWith({ id: "obj_1" });
  });
});
