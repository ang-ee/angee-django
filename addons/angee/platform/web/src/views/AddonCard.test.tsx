// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useCallback } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AddonCard, AddonCardActions, type AddonResourceRow } from "./AddonCard";

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(async () => ({
    install: { ok: true, message: "Installed" },
    uninstall: { ok: true, message: "Uninstalled" },
  })),
  toast: { success: vi.fn(), danger: vi.fn() },
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    Glyph: () => <span />,
    useToast: () => mocks.toast,
    useAuthoredMutation: () => [mocks.mutate, { fetching: false, error: null }],
    // Mirror the real (memoized) translator so the namespace bundle resolves to English.
    useNamespaceT: (_namespace: string, messages: Record<string, string>) =>
      useCallback((key: string) => messages[key] ?? key, [messages]),
  };
});

function row(overrides: Partial<AddonResourceRow> = {}): AddonResourceRow {
  return {
    id: "angee.notes",
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
    vcs_path: "",
    ...overrides,
  };
}

const CONTEXT = { refresh: vi.fn() };

beforeEach(() => {
  mocks.mutate.mockClear();
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

  test("shows the pending-restart badge", () => {
    render(<AddonCard row={row({ state: "disabled", pending: true })} />);
    expect(screen.getByText("Pending restart")).toBeTruthy();
  });
});

describe("AddonCardActions", () => {
  test("uninstalls an enabled addon", async () => {
    render(<AddonCardActions row={row()} context={CONTEXT} />);
    const button = screen.getByRole("button", { name: "Uninstall" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(mocks.mutate).toHaveBeenCalledWith({ addon: "angee.notes" }));
    expect(mocks.toast.success).toHaveBeenCalled();
    expect(CONTEXT.refresh).toHaveBeenCalled();
  });

  test("locks Uninstall for a forced addon", () => {
    render(<AddonCardActions row={row({ forced: true })} context={CONTEXT} />);
    const button = screen.getByRole("button", { name: "Uninstall" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  test("installs an available addon", async () => {
    render(<AddonCardActions row={row({ state: "disabled" })} context={CONTEXT} />);
    fireEvent.click(screen.getByRole("button", { name: "Install" }));
    await waitFor(() => expect(mocks.mutate).toHaveBeenCalledWith({ addon: "angee.notes" }));
  });

  test("shows a disabled pending button once queued", () => {
    render(<AddonCardActions row={row({ state: "disabled", pending: true })} context={CONTEXT} />);
    const button = screen.getByRole("button", { name: "Pending restart" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  test("shows pending restart for a queued uninstall (composed but removed)", () => {
    // An enabled addon dropped from settings.yaml is still composed but pending uninstall:
    // the footer must show the restart state, never a live Uninstall it could re-fire.
    render(<AddonCardActions row={row({ state: "enabled", pending: true })} context={CONTEXT} />);
    expect(screen.getByRole("button", { name: "Pending restart" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Uninstall" })).toBeNull();
  });

  test("locks Install for a non-materialised marketplace addon", () => {
    render(<AddonCardActions row={row({ state: "disabled", source: "remote" })} context={CONTEXT} />);
    const button = screen.getByRole("button", { name: "Install" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(mocks.mutate).not.toHaveBeenCalled();
  });
});
