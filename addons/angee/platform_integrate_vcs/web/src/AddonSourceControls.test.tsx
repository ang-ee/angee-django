// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useCallback } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AddonSourceControls, parseAddonSourceValues } from "./AddonSourceControls";


const mocks = vi.hoisted(() => ({
  resourceMutation: vi.fn(),
  query: { data: undefined, fetching: false, error: null as Error | null, refetch: vi.fn() },
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    Glyph: () => null,
    useAuthoredResourceMutation: mocks.resourceMutation,
    useRelationOptions: () => ({ options: [] }),
    useToast: () => ({ success: vi.fn(), danger: vi.fn() }),
    useNamespaceT: (_namespace: string, messages: Record<string, string>) =>
      useCallback((key: string) => messages[key] ?? key, [messages]),
  };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => mocks.query,
}));

beforeEach(() => {
  mocks.resourceMutation.mockReset();
  mocks.resourceMutation.mockReturnValue([vi.fn(), { fetching: false, error: null }]);
  mocks.query.data = undefined;
  mocks.query.error = null;
  mocks.query.refetch.mockReset();
});
afterEach(cleanup);

describe("AddonSourceControls values", () => {
  test("omits blank optional source coordinates", () => {
    const parsed = parseAddonSourceValues({
      vcsBridgeId: " bridge-1 ",
      name: " angee/framework ",
      ref: "   ",
      path: undefined,
    });

    expect(parsed).toEqual({
      data: {
        vcs_bridge_id: "bridge-1",
        name: "angee/framework",
      },
    });
    expect(parsed.data).not.toHaveProperty("ref");
    expect(parsed.data).not.toHaveProperty("path");
  });

  test("preserves trimmed non-empty source coordinates", () => {
    expect(
      parseAddonSourceValues({
        vcsBridgeId: "bridge-1",
        name: "angee/framework",
        ref: " main ",
        path: " addons/demo ",
      }),
    ).toEqual({
      data: {
        vcs_bridge_id: "bridge-1",
        name: "angee/framework",
        ref: "main",
        path: "addons/demo",
      },
    });
  });
});


describe("AddonSourceControls contribution", () => {
  test("invalidates the platform board for add and scan", () => {
    render(<AddonSourceControls />);
    expect(mocks.resourceMutation.mock.calls.map((call) => call[1])).toEqual([
      { invalidateModels: ["platform.Addon"], shouldInvalidate: expect.any(Function) },
      { invalidateModels: ["platform.Addon"], shouldInvalidate: expect.any(Function) },
    ]);
  });

  test("renders a bounded load failure with retry instead of the empty state", () => {
    mocks.query.error = new Error("Request failed.");
    render(<AddonSourceControls />);
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    expect(screen.getByRole("alert").textContent).toContain("Request failed.");
    expect(screen.queryByText("No addon sources yet. Add one to get started.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(mocks.query.refetch).toHaveBeenCalledOnce();
  });
});
