// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const sdkMocks = vi.hoisted(() => ({
  updatePage: vi.fn(),
  updateBody: vi.fn(),
  useAuthoredMutation: vi.fn(),
  useResourceMutation: vi.fn(),
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useAuthoredMutation: sdkMocks.useAuthoredMutation,
    useResourceMutation: sdkMocks.useResourceMutation,
  };
});

import { usePageEditor } from "./use-page-editor";

describe("usePageEditor", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sdkMocks.updatePage.mockReset();
    sdkMocks.updateBody.mockReset();
    sdkMocks.useAuthoredMutation.mockReset();
    sdkMocks.useResourceMutation.mockReset();
    sdkMocks.updatePage.mockResolvedValue({ id: "pag_1", title: "Updated" });
    sdkMocks.updateBody.mockResolvedValue({
      updatePageBody: {
        ok: true,
        markdown: { bodyHash: "hash-next" },
      },
    });
    sdkMocks.useResourceMutation.mockImplementation(() => [
      sdkMocks.updatePage,
      { fetching: false, error: null },
    ]);
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

  test("debounces body saves to the latest draft", async () => {
    const onSaved = vi.fn();
    const { result } = renderHook(() =>
      usePageEditor(
        "pag_1",
        { title: "Page", body: "Old body", bodyHash: "hash-old" },
        onSaved,
      ),
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
      expectedHash: "hash-old",
    });
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("saved");
  });

  test("commits title changes through the SDK page update mutation", async () => {
    const onSaved = vi.fn();
    const { result } = renderHook(() =>
      usePageEditor(
        "pag_1",
        { title: "Page", body: "Old body", bodyHash: "hash-old" },
        onSaved,
      ),
    );

    act(() => {
      result.current.setTitle("Renamed page");
    });

    await act(async () => {
      result.current.commitTitle();
      await Promise.resolve();
    });

    expect(sdkMocks.useResourceMutation).toHaveBeenCalledWith(
      "knowledge.Page",
      "update",
      { fields: ["title"] },
    );
    expect(sdkMocks.updatePage).toHaveBeenCalledWith({
      data: { id: "pag_1", title: "Renamed page" },
    });
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("saved");
  });

  test("flushes the pending body save on unmount", async () => {
    const onSaved = vi.fn();
    const { result, unmount } = renderHook(() =>
      usePageEditor(
        "pag_2",
        { title: "Page", body: "Old body", bodyHash: "hash-old" },
        onSaved,
      ),
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
      expectedHash: "hash-old",
    });
    expect(onSaved).toHaveBeenCalledTimes(1);
  });
});

function graphqlOperationName(document: unknown): string {
  return (
    (document as { definitions?: Array<{ name?: { value?: string } }> })
      .definitions?.[0]?.name?.value ?? ""
  );
}
