// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useCallback } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AddonCard, AddonCardActions, type AddonResourceRow } from "./AddonCard";

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(async () => ({
    install: { ok: true, message: "Installed" },
    disable: { ok: true, message: "Disabled" },
  })),
  resourceMutation: vi.fn(),
  preview: {
    can_apply: true,
    refusal: null as string | null,
    enableLabel: "Tags",
    disableLabel: "Legacy",
  },
  refetchPreview: vi.fn(),
  toast: { success: vi.fn(), danger: vi.fn() },
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    Glyph: () => <span />,
    useAuthoredResourceMutation: mocks.resourceMutation,
    useRelationOptions: () => ({ options: [] }),
    useToast: () => mocks.toast,
    // Mirror the real (memoized) translator so the namespace bundle resolves to English.
    useNamespaceT: (_namespace: string, messages: Record<string, string>) =>
      useCallback(
        (key: string, values: Record<string, string | number> = {}) =>
          (messages[key] ?? key).replace(/\{(\w+)\}/g, (_match, name: string) => String(values[name] ?? `{${name}}`)),
        [messages],
      ),
  };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: () => [mocks.mutate, { fetching: false, error: null }],
  useAuthoredQuery: (_document: unknown, variables: { action?: "INSTALL" | "DISABLE" }) => ({
    data: {
      addon_change_preview: {
        action: variables.action ?? "INSTALL",
        addon: "angee.notes",
        revision: "revision-1",
        can_apply: mocks.preview.can_apply,
        refusal: mocks.preview.refusal,
        roots_before: ["angee.notes"],
        roots_after: ["angee.notes", "angee.tags"],
        addons_to_enable: [{ name: "angee.tags", label: mocks.preview.enableLabel, root: false, depends_on: [] }],
        addons_to_disable: [{ name: "angee.legacy", label: mocks.preview.disableLabel, root: true, depends_on: ["angee.notes"] }],
        data_inventory: [{
          addon: "angee.legacy",
          models: [{ label: "legacy.Entry", verbose_name: "entry", row_count: 3 }],
          contributed_fields: [{ model_label: "notes.note", field_name: "legacy_code", verbose_name: "Legacy code" }],
        }],
        migration_warning: "Migration effects require planning.",
      },
    },
    isFetching: false,
    error: null,
    refetch: mocks.refetchPreview,
  }),
}));

function row(overrides: Partial<AddonResourceRow> = {}): AddonResourceRow {
  return {
    id: "angee.notes",
    name: "angee.notes",
    label: "notes",
    namespace: "angee",
    category: "Example",
    description: "Product logic for the example.",
    keywords: ["console", "demo"],
    kind: "consumer",
    source: "local",
    state: "enabled",
    forced: false,
    pending: false,
    model_count: 2,
    field_count: 9,
    resource_count: 0,
    depends_on: [],
    depended_by: [],
    ...overrides,
  };
}

const CONTEXT = { refresh: vi.fn() };

beforeEach(() => {
  mocks.resourceMutation.mockReset();
  mocks.resourceMutation.mockReturnValue([
    mocks.mutate,
    { fetching: false, error: null },
  ]);
  mocks.mutate.mockClear();
  mocks.preview.can_apply = true;
  mocks.preview.refusal = null;
  mocks.preview.enableLabel = "Tags";
  mocks.preview.disableLabel = "Legacy";
  mocks.refetchPreview.mockClear();
  mocks.toast.success.mockClear();
  mocks.toast.danger.mockClear();
  CONTEXT.refresh.mockClear();
});
afterEach(cleanup);

describe("AddonCard", () => {
  test("renders the manifest metadata and lifecycle badges", () => {
    render(<AddonCard row={row()} />);
    expect(screen.getByText("notes")).toBeTruthy();
    expect(screen.getByText("angee.notes")).toBeTruthy();
    expect(screen.getByText("Product logic for the example.")).toBeTruthy();
    expect(screen.getByText("demo")).toBeTruthy(); // keyword chip
    expect(screen.getByText("Installed")).toBeTruthy(); // state badge (enabled → Installed)
  });

  test("marks a forced addon as required", () => {
    render(<AddonCard row={row({ forced: true })} />);
    expect(screen.getByText("Required")).toBeTruthy();
  });

  test("shows the server's canonical-name label for an unresolved remote addon", () => {
    render(<AddonCard row={row({
      id: "example.remote",
      name: "example.remote",
      label: "example.remote",
      source: "remote",
      state: "disabled",
    })} />);
    expect(screen.getAllByText("example.remote")).toHaveLength(2);
  });

  test("shows the pending-restart badge", () => {
    render(<AddonCard row={row({ state: "disabled", pending: true })} />);
    expect(screen.getByText("Pending restart")).toBeTruthy();
  });
});

describe("AddonCardActions", () => {
  test("uses the server's labels in the confirmation title and unresolved impacts", () => {
    mocks.preview.enableLabel = "angee.tags";
    mocks.preview.disableLabel = "angee.legacy";
    render(<AddonCardActions row={row({ label: "angee.notes", state: "disabled" })} context={CONTEXT} />);

    fireEvent.click(screen.getByRole("button", { name: "Install" }));

    expect(screen.getByRole("heading", { name: "Install angee.notes?" })).toBeTruthy();
    expect(screen.getAllByText("angee.tags")).toHaveLength(2);
    expect(screen.getAllByText("angee.legacy")).toHaveLength(3);
  });

  test("routes install and disable invalidation through the resource owner", () => {
    render(<AddonCardActions row={row()} context={CONTEXT} />);

    expect(mocks.resourceMutation).toHaveBeenCalledTimes(2);
    expect(mocks.resourceMutation.mock.calls.map((call) => call[1])).toEqual([
      {
        invalidateModels: ["platform.Addon"],
        shouldInvalidate: expect.any(Function),
      },
      {
        invalidateModels: ["platform.Addon"],
        shouldInvalidate: expect.any(Function),
      },
    ]);
  });

  test("previews both deltas before revision-bound disable", async () => {
    render(<AddonCardActions row={row()} context={CONTEXT} />);
    const button = screen.getByRole("button", { name: "Disable" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    expect(mocks.mutate).not.toHaveBeenCalled();
    expect(screen.getByText("Tags")).toBeTruthy();
    expect(screen.getByText("Legacy")).toBeTruthy();
    expect(screen.getByText("3 rows")).toBeTruthy();
    expect(screen.getByText("Contributed fields")).toBeTruthy();
    expect(screen.getByText("notes.note.legacy_code")).toBeTruthy();
    const confirmation = screen.getAllByRole("button", { name: "Disable" }).at(-1);
    if (!confirmation) throw new Error("Disable confirmation is missing.");
    fireEvent.click(confirmation);
    await waitFor(() => expect(mocks.mutate).toHaveBeenCalledWith({ addon: "angee.notes", revision: "revision-1" }));
    expect(mocks.toast.success).toHaveBeenCalled();
    expect(CONTEXT.refresh).toHaveBeenCalled();
  });

  test("opens a read-only refusal preview for a forced addon", () => {
    mocks.preview.can_apply = false;
    mocks.preview.refusal = "Required by angee.messaging.";
    render(<AddonCardActions row={row({ forced: true })} context={CONTEXT} />);
    const button = screen.getByRole("button", { name: "Disable" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    expect(screen.getByText("Required by angee.messaging.")).toBeTruthy();
    const confirmation = screen.getAllByRole("button", { name: "Disable" }).at(-1) as HTMLButtonElement;
    expect(confirmation.disabled).toBe(true);
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  test.each([
    ["disabled", "Install"],
    ["removed", "Reinstall"],
  ])("previews before revision-bound install from %s", async (state, label) => {
    render(<AddonCardActions row={row({ state })} context={CONTEXT} />);
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(mocks.mutate).not.toHaveBeenCalled();
    const confirmation = screen.getAllByRole("button", { name: "Install" }).at(-1);
    if (!confirmation) throw new Error("Install confirmation is missing.");
    fireEvent.click(confirmation);
    await waitFor(() => expect(mocks.mutate).toHaveBeenCalledWith({ addon: "angee.notes", revision: "revision-1" }));
  });

  test("leaves queued state to the card badge", () => {
    const { container } = render(
      <AddonCardActions row={row({ state: "disabled", pending: true })} context={CONTEXT} />,
    );
    expect(container.textContent).toBe("");
  });

  test("shows pending restart for a queued disable", () => {
    // An enabled addon dropped from settings.yaml is still composed but pending disable:
    // the footer must show the restart state, never a live Disable it could re-fire.
    render(<AddonCardActions row={row({ state: "enabled", pending: true })} context={CONTEXT} />);
    expect(screen.queryByRole("button", { name: "Disable" })).toBeNull();
  });

  test("locks Install for a non-materialised marketplace addon", () => {
    render(<AddonCardActions row={row({ state: "disabled", source: "remote" })} context={CONTEXT} />);
    const button = screen.getByRole("button", { name: "Install" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  test("keeps reinstall blocked when the server cannot find the addon source", () => {
    mocks.preview.can_apply = false;
    mocks.preview.refusal = "angee.notes is not available to install.";
    render(<AddonCardActions row={row({ state: "removed" })} context={CONTEXT} />);
    fireEvent.click(screen.getByRole("button", { name: "Reinstall" }));
    expect(screen.getByText(mocks.preview.refusal)).toBeTruthy();
    const confirmation = screen.getByRole("button", { name: "Install" });
    expect((confirmation as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(confirmation);
    expect(mocks.mutate).not.toHaveBeenCalled();
  });
});
