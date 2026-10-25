// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AppRuntimeProvider, ToastProvider, defaultWidgets } from "@angee/ui";
import { BackupDiscoveryView } from "./BackupDiscovery";
import type { BackupPreview } from "./documents";
import { defaultBackupSelection } from "./selection";

afterEach(cleanup);
const preview: BackupPreview = {
  version: 1, discovered_at: "2026-10-24T12:00:00Z", estimated_bytes: "4096", has_unknown_sizes: true,
  selection: defaultBackupSelection(), resources: [
    { group: "DATABASES", name: "primary", status: "INCLUDED", reason: "", estimated_bytes: "4096" },
    { group: "SOURCES", name: "local-only", status: "UNSUPPORTED", reason: "Revision is not published", estimated_bytes: null },
  ],
};
function mount(props: Partial<Parameters<typeof BackupDiscoveryView>[0]> = {}) {
  const onChange = vi.fn();
  const element = <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><ToastProvider>
    <BackupDiscoveryView preview={preview} selection={defaultBackupSelection()} pending={false} onChange={onChange} onRetry={vi.fn()} {...props} />
  </ToastProvider></AppRuntimeProvider>;
  return { ...render(element), onChange };
}

test("shows unknown source sizes and unavailable reasons without inventing archive estimates", async () => {
  mount();
  expect(await screen.findByText("Revision is not published")).toBeTruthy();
  expect(screen.getByText("Databases")).toBeTruthy();
  expect(screen.getByText("Source repositories")).toBeTruthy();
  expect(screen.getByText("Included")).toBeTruthy();
  expect(screen.getByText("Unsupported")).toBeTruthy();
  expect(screen.getByText("Unknown")).toBeTruthy();
  expect(screen.getByText("Known source bytes")).toBeTruthy();
  expect(screen.getByText(/Some source sizes are unknown/)).toBeTruthy();
  expect(screen.getByText(/ZIP compression and final archive size are not estimated/)).toBeTruthy();
});

test("native checked inventory rows edit exclusions in the existing form draft", async () => {
  const { onChange } = mount();
  const row = (await screen.findByText("primary")).closest("tr")!;
  fireEvent.click(within(row).getByRole("checkbox"));
  fireEvent.click(await screen.findByRole("button", { name: "Exclude checked" }));
  expect(onChange).toHaveBeenCalledOnce();
  expect(onChange.mock.calls[0]?.[0].databases.exclude).toEqual(["primary"]);
});

test("pending inventory cannot apply checked selections", async () => {
  const { onChange } = mount({ pending: true });
  const row = (await screen.findByText("primary")).closest("tr")!;
  fireEvent.click(within(row).getByRole("checkbox"));
  const button = await screen.findByRole("button", { name: "Exclude checked" });
  expect(button.hasAttribute("disabled")).toBe(true);
  fireEvent.click(button);
  expect(onChange).not.toHaveBeenCalled();
});

test("changed selections mark the old snapshot stale", () => {
  const selection = defaultBackupSelection();
  selection.storage.mode = "none";
  mount({ selection });
  expect(screen.getByRole("status").textContent).toMatch(/Updating discovery/);
});

test.each([undefined, { ...preview, resources: [] }])("failed discovery offers retry without the contradictory empty-state instruction", (cachedPreview) => {
  const onRetry = vi.fn();
  mount({ preview: cachedPreview, error: new Error("Discovery could not reach the operator."), onRetry });
  expect(screen.getByRole("alert").textContent).toContain("Discovery could not reach the operator.");
  expect(screen.queryByText("Discover the stack to review included, excluded, unsupported, and unavailable resources.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry discovery" }));
  expect(onRetry).toHaveBeenCalledOnce();
});
