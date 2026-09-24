// @vitest-environment happy-dom

import type { DocumentType } from "@angee/gql/console";
import type { ActionMutate } from "@angee/refine";
import type { ActionResultRun, MutationDialogProps } from "@angee/ui";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useCallback } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AddonSourceControls, parseAddonSourceValues } from "./AddonSourceControls";
import { AddonSources } from "./documents";

const mocks = vi.hoisted(() => ({
  outcomeMutation: vi.fn(),
  addSource: vi.fn<ActionMutate>(),
  scan: vi.fn<ActionMutate>(),
  settle: vi.fn<ActionResultRun>(),
  dialog: vi.fn<(props: MutationDialogProps<ReturnType<typeof parseAddonSourceValues>>) => null>(() => null),
  toast: { success: vi.fn(), danger: vi.fn() },
  query: {
    data: undefined as DocumentType<typeof AddonSources> | undefined,
    isFetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    Glyph: () => null,
    MutationDialog: mocks.dialog,
    useActionOutcomeMutation: mocks.outcomeMutation,
    useActionResultRun: () => mocks.settle,
    useRelationOptions: () => ({ options: [] }),
    useToast: () => mocks.toast,
    useNamespaceT: (_namespace: string, messages: Record<string, string>) =>
      useCallback((key: string) => messages[key] ?? key, [messages]),
  };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => mocks.query,
}));

beforeEach(() => {
  mocks.outcomeMutation.mockReset();
  mocks.outcomeMutation.mockImplementation((field: string) => [
    field === "add_source" ? mocks.addSource : mocks.scan,
    { fetching: false, error: null },
  ]);
  mocks.addSource.mockReset();
  mocks.scan.mockReset();
  mocks.settle.mockReset();
  mocks.settle.mockImplementation(async (fire) => (await fire()) ?? undefined);
  mocks.dialog.mockClear();
  mocks.toast.success.mockReset();
  mocks.toast.danger.mockReset();
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
  test("uses generated actions with platform board invalidation and their declared argument names", () => {
    render(<AddonSourceControls />);
    expect(mocks.outcomeMutation.mock.calls).toEqual([
      ["add_source", { idArgument: null, invalidateModels: ["platform.Addon"] }],
      ["scan", { idArgument: "source_id", invalidateModels: ["platform.Addon"] }],
    ]);
  });

  test("submits parsed source data without an id argument and toasts success once", async () => {
    mocks.addSource.mockResolvedValue({ ok: true, message: "Source added." });
    render(<AddonSourceControls />);
    const dialog = mocks.dialog.mock.calls[0]![0];
    const values = dialog.parseValues({ vcsBridgeId: "bridge-1", name: "angee/framework" });

    await dialog.onSubmit(values);

    expect(mocks.addSource).toHaveBeenCalledWith("", {
      data: { vcs_bridge_id: "bridge-1", name: "angee/framework" },
    });
    expect(mocks.toast.success).toHaveBeenCalledExactlyOnceWith({ title: "Source added." });
    expect(mocks.settle).not.toHaveBeenCalled();
  });

  test("keeps domain failures available to MutationDialog's inline error owner", async () => {
    mocks.addSource.mockResolvedValue({ ok: false, message: "Source refused." });
    render(<AddonSourceControls />);
    const dialog = mocks.dialog.mock.calls[0]![0];
    const values = dialog.parseValues({ vcsBridgeId: "bridge-1", name: "angee/framework" });

    await expect(dialog.onSubmit(values)).rejects.toThrow("Source refused.");

    expect(mocks.toast.success).not.toHaveBeenCalled();
    expect(mocks.settle).not.toHaveBeenCalled();
  });

  test.each([true, false])("settles scan feedback and refreshes sources only on success (ok=%s)", async (ok) => {
    mocks.scan.mockResolvedValue({ ok, message: "Scan outcome." });
    mocks.query.data = {
      sources: [{ id: "source-1", display_name: "Angee", kind: "addon", ref: "main", path: "" }],
    };
    render(<AddonSourceControls />);
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));
    const scan = within(screen.getByRole("list")).getByRole("button", { name: "Scan" });
    fireEvent.click(scan);

    await waitFor(() => expect(scan.hasAttribute("disabled")).toBe(false));

    expect(mocks.scan).toHaveBeenCalledExactlyOnceWith("source-1");
    expect(mocks.settle).toHaveBeenCalledOnce();
    expect(mocks.query.refetch).toHaveBeenCalledTimes(ok ? 1 : 0);
    expect(mocks.toast.success).not.toHaveBeenCalled();
    expect(mocks.toast.danger).not.toHaveBeenCalled();
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
