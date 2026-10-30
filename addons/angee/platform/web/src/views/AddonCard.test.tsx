// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useCallback } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { DisableAddon, EnableAddon, InstallAddon, UninstallAddon } from "../documents";
import { AddonCard, AddonCardActions, type AddonResourceRow } from "./AddonCard";

const mocks = vi.hoisted(() => ({
  enable: vi.fn(async () => ({ enable: { ok: true, message: "Enabled" } })),
  disable: vi.fn(async () => ({ disable: { ok: true, message: "Disabled" } })),
  install: vi.fn<() => Promise<{ install: { id: string } | null }>>(),
  uninstall: vi.fn(async () => ({ uninstall: { id: "operation-remove" } })),
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
    // The shared settle: fire, then toast the outcome the way the owner does.
    useActionResultRun: () => async (fire: () => Promise<{ ok?: boolean; message?: string } | null | undefined>) => {
      const outcome = await fire();
      if (outcome?.ok) mocks.toast.success({ title: outcome.message });
      else mocks.toast.danger({ title: outcome?.message });
      return outcome ?? undefined;
    },
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
  useAuthoredQuery: (_document: unknown, variables: { action?: "ENABLE" | "DISABLE" }) => ({
    data: {
      addon_change_preview: {
        action: variables.action ?? "ENABLE",
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
    kind: "CONSUMER",
    source: "LOCAL",
    state: "ENABLED",
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
  mocks.resourceMutation.mockImplementation((document: unknown) => {
    const mutate = document === EnableAddon ? mocks.enable
      : document === DisableAddon ? mocks.disable
      : document === InstallAddon ? mocks.install
      : document === UninstallAddon ? mocks.uninstall
      : undefined;
    if (!mutate) throw new Error("Unexpected addon lifecycle document.");
    return [mutate, { fetching: false, error: null }];
  });
  mocks.enable.mockClear();
  mocks.disable.mockClear();
  mocks.install.mockReset();
  mocks.install.mockResolvedValue({ install: { id: "operation-add" } });
  mocks.uninstall.mockClear();
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
  test("renders the package metadata and lifecycle badges", () => {
    render(<AddonCard row={row()} />);
    expect(screen.getByText("notes")).toBeTruthy();
    expect(screen.getByText("angee.notes")).toBeTruthy();
    expect(screen.getByText("Product logic for the example.")).toBeTruthy();
    expect(screen.getByText("demo")).toBeTruthy(); // keyword chip
    expect(screen.getByText("Enabled")).toBeTruthy();
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
      source: "REMOTE",
      state: "DISABLED",
    })} />);
    expect(screen.getAllByText("example.remote")).toHaveLength(2);
  });

  test("shows the pending-restart badge", () => {
    render(<AddonCard row={row({ state: "DISABLED", pending: true })} />);
    expect(screen.getByText("Pending restart")).toBeTruthy();
  });
});

describe("AddonCardActions", () => {
  test("uses the server's labels in the confirmation title and unresolved impacts", () => {
    mocks.preview.enableLabel = "angee.tags";
    mocks.preview.disableLabel = "angee.legacy";
    render(<AddonCardActions row={row({ label: "angee.notes", state: "DISABLED" })} context={CONTEXT} />);

    fireEvent.click(screen.getByRole("button", { name: "Enable" }));

    expect(screen.getByRole("heading", { name: "Enable angee.notes?" })).toBeTruthy();
    expect(screen.getAllByText("angee.tags")).toHaveLength(2);
    expect(screen.getAllByText("angee.legacy")).toHaveLength(3);
  });

  test("routes root and dependency mutations through the resource owner", () => {
    render(<AddonCardActions row={row()} context={CONTEXT} />);

    expect(mocks.resourceMutation).toHaveBeenCalledTimes(4);
    expect(mocks.resourceMutation.mock.calls.map((call) => call[0])).toEqual([
      EnableAddon, DisableAddon, InstallAddon, UninstallAddon,
    ]);
    expect(mocks.resourceMutation.mock.calls.map((call) => call[1])).toEqual([
      {
        invalidateModels: ["platform.Addon"],
        shouldInvalidate: expect.any(Function),
      },
      {
        invalidateModels: ["platform.Addon"],
        shouldInvalidate: expect.any(Function),
      },
      { invalidateModels: ["platform.Addon"] },
      { invalidateModels: ["platform.Addon"] },
    ]);
    const enableOptions = mocks.resourceMutation.mock.calls[0]![1];
    const disableOptions = mocks.resourceMutation.mock.calls[1]![1];
    expect(enableOptions.shouldInvalidate({ enable: { ok: true } })).toBe(true);
    expect(enableOptions.shouldInvalidate({ enable: { ok: false } })).toBe(false);
    expect(disableOptions.shouldInvalidate({ disable: { ok: true } })).toBe(true);
    expect(disableOptions.shouldInvalidate({ disable: { ok: false } })).toBe(false);
  });

  test("previews both deltas before revision-bound disable", async () => {
    render(<AddonCardActions row={row()} context={CONTEXT} />);
    const button = screen.getByRole("button", { name: "Disable" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    expect(mocks.disable).not.toHaveBeenCalled();
    expect(screen.getByText("Tags")).toBeTruthy();
    expect(screen.getByText("Legacy")).toBeTruthy();
    expect(screen.getByText("3 rows")).toBeTruthy();
    expect(screen.getByText("Contributed fields")).toBeTruthy();
    expect(screen.getByText("notes.note.legacy_code")).toBeTruthy();
    const confirmation = screen.getAllByRole("button", { name: "Disable" }).at(-1);
    if (!confirmation) throw new Error("Disable confirmation is missing.");
    fireEvent.click(confirmation);
    await waitFor(() => expect(mocks.disable).toHaveBeenCalledWith({ addon: "angee.notes", revision: "revision-1" }));
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
    expect(mocks.disable).not.toHaveBeenCalled();
  });

  test("previews before revision-bound enable of an installed addon", async () => {
    render(<AddonCardActions row={row({ state: "DISABLED" })} context={CONTEXT} />);
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));
    expect(mocks.enable).not.toHaveBeenCalled();
    const confirmation = screen.getAllByRole("button", { name: "Enable" }).at(-1);
    if (!confirmation) throw new Error("Enable confirmation is missing.");
    fireEvent.click(confirmation);
    await waitFor(() => expect(mocks.enable).toHaveBeenCalledExactlyOnceWith({ addon: "angee.notes", revision: "revision-1" }));
    expect(mocks.install).not.toHaveBeenCalled();
    expect(mocks.toast.success).toHaveBeenCalledWith({ title: "Enabled" });
    expect(CONTEXT.refresh).toHaveBeenCalledOnce();
  });

  test("leaves queued state to the card badge", () => {
    const { container } = render(
      <AddonCardActions row={row({ state: "DISABLED", pending: true })} context={CONTEXT} />,
    );
    expect(container.textContent).toBe("");
  });

  test("shows pending restart for a queued disable", () => {
    // An enabled addon dropped from settings.yaml is still composed but pending disable:
    // the footer must show the restart state, never a live Disable it could re-fire.
    render(<AddonCardActions row={row({ state: "ENABLED", pending: true })} context={CONTEXT} />);
    expect(screen.queryByRole("button", { name: "Disable" })).toBeNull();
  });

  test.each([
    { state: "DISABLED", source: "REMOTE" },
    { state: "REMOVED", source: "LOCAL" },
  ])("queues a dependency install for $source/$state without a root preview", async ({ state, source }) => {
    render(<AddonCardActions row={row({ state, source })} context={CONTEXT} />);
    const button = screen.getByRole("button", { name: "Install" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(mocks.toast.success).toHaveBeenCalledWith({ title: "Dependency change queued." }));
    expect(mocks.install).toHaveBeenCalledExactlyOnceWith({ addon: "angee.notes" });
    expect(mocks.enable).not.toHaveBeenCalled();
    expect(mocks.disable).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(CONTEXT.refresh).not.toHaveBeenCalled();
  });

  test("queues dependency removal of an installed disabled addon", async () => {
    render(<AddonCardActions row={row({ state: "DISABLED" })} context={CONTEXT} />);
    const button = screen.getByRole("button", { name: "Uninstall" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(mocks.toast.success).toHaveBeenCalledWith({ title: "Dependency change queued." }));
    expect(mocks.uninstall).toHaveBeenCalledExactlyOnceWith({ addon: "angee.notes" });
    expect(mocks.disable).not.toHaveBeenCalled();
    expect(mocks.install).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(CONTEXT.refresh).not.toHaveBeenCalled();
  });

  test("does not report a queued dependency job when the server returns no operation", async () => {
    mocks.install.mockResolvedValueOnce({ install: null });
    render(<AddonCardActions row={row({ state: "REMOVED" })} context={CONTEXT} />);
    fireEvent.click(screen.getByRole("button", { name: "Install" }));
    await waitFor(() => expect(mocks.toast.danger).toHaveBeenCalledOnce());
    expect(mocks.toast.success).not.toHaveBeenCalled();
    expect(mocks.enable).not.toHaveBeenCalled();
    expect(CONTEXT.refresh).not.toHaveBeenCalled();
  });
});
