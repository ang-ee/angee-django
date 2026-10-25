// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AppRuntimeProvider, ToastProvider, useActionForm, type RecordToolbarContext } from "@angee/ui";
import { BackupWizard } from "./BackupWizard";
import { defaultBackupSelection, type BackupSelection } from "./selection";
const mocks = vi.hoisted(() => ({ queries: vi.fn(), save: vi.fn(), run: vi.fn(), pending: false, stale: false, error: null as Error | null, refetch: vi.fn() }));
vi.mock("@angee/refine", async (original) => ({ ...await original<typeof import("@angee/refine")>(),
  useAuthoredQuery: (_doc: unknown, variables: { id: string | null; selection: BackupSelection }, options: { enabled: boolean }) => {
    mocks.queries(variables, options.enabled);
    return { data: options.enabled ? { preview_backup_plan: { version: 1, discovered_at: "2026-10-24T12:00:00Z", selection: mocks.stale ? { ...variables.selection, storage: { mode: "all", names: [], exclude: [] } } : variables.selection,
      estimated_bytes: "1", has_unknown_sizes: false, resources: [{ group: "DATABASES", name: "primary", status: "INCLUDED", reason: "", estimated_bytes: "1" }] } } : undefined, isFetching: mocks.pending, error: mocks.error, refetch: mocks.refetch };
  },
}));
let parentDraft: () => BackupSelection;
function Harness({ readonly = false, create = false }: { readonly?: boolean; create?: boolean }) {
  const draft = defaultBackupSelection(); draft.storage.mode = "none";
  const parent = useActionForm<{ selection: BackupSelection }>({ defaultValues: { selection: draft }, submit: () => ({ status: "ok", data: null }) });
  parentDraft = () => parent.form.getValues("selection");
  const context = { patchRecord: vi.fn(), reload: vi.fn(), recordId: create ? null : "bpl_one", record: { id: "bpl_one", permissions: ["run"] },
    form: { form: parent.form, formReadOnly: readonly, pending: false, submitForm: mocks.save } } as unknown as RecordToolbarContext;
  return <BackupWizard context={context} capture={{ pending: false, run: mocks.run }} />;
}
function mount(props = {}) {
  return render(<AppRuntimeProvider runtime={{}}><ToastProvider><Harness {...props} /></ToastProvider></AppRuntimeProvider>);
}
async function open() {
  fireEvent.click(screen.getByRole("button", { name: "Discover & estimate" }));
  return screen.findByRole("dialog");
}
beforeEach(() => { mocks.pending = false; mocks.stale = false; mocks.error = null; mocks.save.mockResolvedValue({ id: "bpl_saved" }); mocks.run.mockResolvedValue(true); });
afterEach(() => { cleanup(); vi.clearAllMocks(); });
test("opening discovers an unsaved current draft and Save plan never starts a capture", async () => {
  mount({ create: true });
  const dialog = await open();
  expect(mocks.queries).toHaveBeenLastCalledWith(expect.objectContaining({ id: null, selection: expect.objectContaining({ storage: expect.objectContaining({ mode: "none" }) }) }), true);
  fireEvent.click(within(dialog).getByRole("button", { name: "Save plan" }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledOnce());
  expect(mocks.run).not.toHaveBeenCalled();
  expect(parentDraft().storage.mode).toBe("none");
});
test("Save & back up waits for accepted persistence and uses the accepted selection", async () => {
  let resolveSave!: (row: { id: string }) => void;
  mocks.save.mockReturnValue(new Promise((resolve) => { resolveSave = resolve; }));
  mount(); const dialog = await open();
  fireEvent.click(within(dialog).getByRole("button", { name: "Save & back up" }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledOnce());
  expect(mocks.run).not.toHaveBeenCalled();
  resolveSave({ id: "bpl_saved" });
  await waitFor(() => expect(mocks.run).toHaveBeenCalledWith("bpl_saved", parentDraft()));
});
test("failed native save keeps the review open and never captures", async () => {
  mocks.save.mockResolvedValue(null);
  mount(); const dialog = await open();
  fireEvent.click(within(dialog).getByRole("button", { name: "Save & back up" }));
  expect(await within(dialog).findByText("Review the plan fields before continuing.")).toBeTruthy();
  expect(mocks.run).not.toHaveBeenCalled();
});
test("read-only runners can capture but cannot save; cancel performs neither", async () => {
  mount({ readonly: true }); const dialog = await open();
  expect(within(dialog).getByRole("button", { name: "Save plan" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(within(dialog).getByRole("button", { name: "Back up now" }));
  await waitFor(() => expect(mocks.run).toHaveBeenCalledWith("bpl_one", parentDraft()));
  expect(mocks.save).not.toHaveBeenCalled();
});
test("pending discovery disables both acceptance choices and cancel makes no writes", async () => {
  mocks.pending = true; mount(); const dialog = await open();
  for (const name of ["Save plan", "Save & back up"]) expect(within(dialog).getByRole("button", { name }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(mocks.save).not.toHaveBeenCalled(); expect(mocks.run).not.toHaveBeenCalled();
});

test("inventory review edits are isolated until acceptance and cancel preserves the parent draft", async () => {
  mount(); const dialog = await open();
  const row = (await within(dialog).findByText("primary")).closest("tr")!;
  fireEvent.click(within(row).getByRole("checkbox"));
  fireEvent.click(await within(dialog).findByRole("button", { name: "Exclude checked" }));
  await waitFor(() => expect(mocks.queries).toHaveBeenLastCalledWith(expect.objectContaining({ selection: expect.objectContaining({ databases: expect.objectContaining({ exclude: ["primary"] }) }) }), true));
  expect(parentDraft().databases.exclude).toEqual([]);
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(parentDraft().databases.exclude).toEqual([]);
  expect(mocks.save).not.toHaveBeenCalled(); expect(mocks.run).not.toHaveBeenCalled();
});

test("an earlier selection snapshot cannot be accepted even through form submission", async () => {
  mocks.stale = true; mount(); const dialog = await open();
  const save = within(dialog).getByRole("button", { name: "Save plan" });
  expect(save.hasAttribute("disabled")).toBe(true);
  fireEvent.submit(save.closest("form")!);
  await waitFor(() => expect(within(dialog).getAllByText("Updating discovery for the current selection…").length).toBeGreaterThan(0));
  expect(mocks.save).not.toHaveBeenCalled(); expect(mocks.run).not.toHaveBeenCalled();
});

test("failed discovery cannot accept a cached preview and retry only rediscovers", async () => {
  mocks.error = new Error("Discovery could not reach the operator.");
  mount(); const dialog = await open();
  for (const name of ["Save plan", "Save & back up"]) expect(within(dialog).getByRole("button", { name }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(within(dialog).getByRole("button", { name: "Retry discovery" }));
  expect(mocks.refetch).toHaveBeenCalledOnce();
  expect(mocks.save).not.toHaveBeenCalled(); expect(mocks.run).not.toHaveBeenCalled();
});
