// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ActionFormProvider, AppRuntimeProvider, Button, DescriptorFieldList, ToastProvider, defaultWidgets, useActionForm } from "@angee/ui";
import { BackupSelectionField } from "./BackupSelection";
import { defaultBackupSelection, type BackupSelection } from "./selection";

afterEach(cleanup);

test.each([["All discovered", "all"], ["None", "none"]])("changing Selected to %s saves no inactive names", async (label, mode) => {
  const saved = vi.fn();
  const initial = defaultBackupSelection();
  initial.databases = { mode: "selected", names: ["primary"], exclude: ["analytics"] };
  function Harness() {
    const action = useActionForm<{ selection: BackupSelection }>({ defaultValues: { selection: initial },
      submit: (values) => { saved(values); return { status: "ok", data: values }; } });
    return <ActionFormProvider {...action.form}>
      <DescriptorFieldList fields={[{ name: "selection", label: "Selection", widget: "backupSelection" }]} />
      <Button onClick={() => { void action.run(); }}>Save</Button>
    </ActionFormProvider>;
  }
  render(<AppRuntimeProvider runtime={{ widgets: { ...defaultWidgets, backupSelection: { read: BackupSelectionField, edit: BackupSelectionField } } }}>
    <ToastProvider><Harness /></ToastProvider>
  </AppRuntimeProvider>);
  const databases = await screen.findByRole("group", { name: "Databases" });
  fireEvent.click(await within(databases).findByRole("combobox", { name: "Include" }));
  const option = await screen.findByRole("option", { name: label });
  fireEvent.pointerDown(option, { pointerType: "mouse" });
  fireEvent.click(option);
  // Switching native category tabs must retain the same parent field draft.
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Databases", "Services", "Workspaces", "Source repositories", "Storage"]);
  fireEvent.click(screen.getByRole("tab", { name: "Services" }));
  expect(await screen.findByRole("group", { name: "Services" })).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Storage" }));
  expect(await screen.findByRole("group", { name: "Storage" })).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Databases" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(saved.mock.calls[0]?.[0].selection.databases).toEqual({ mode, names: [], exclude: ["analytics"] });
  expect(initial.databases.names).toEqual(["primary"]);
});
