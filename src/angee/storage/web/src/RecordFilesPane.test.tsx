// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const sdk = vi.hoisted(() => ({ query: vi.fn(), upload: vi.fn(), navigate: vi.fn(), mounted: vi.fn(), preview: { viewAs: null as string | null, pending: false } }));

vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()), useAuthoredQuery: sdk.query,
}));
vi.mock("@angee/ui", async (original) => ({
  ...(await original<typeof import("@angee/ui")>()),
  useRuntimeViewAs: () => sdk.preview,
  useResourceRecordHrefLookup: () => (model: string, id: string) => model === "storage.File" ? `/storage/${id}` : undefined,
}));
vi.mock("./data/use-upload", async () => {
  const { useEffect, useState } = await import("react");
  return { useStorageUpload() {
    const [tasks, setTasks] = useState<{ id: string; name: string; status: "uploading" }[]>([]);
    useEffect(() => { sdk.mounted(); }, []);
    return { tasks, retry: vi.fn(), clearFinished: () => setTasks([]), upload: (files: File[], target: unknown) => {
      sdk.upload(files, target);
      setTasks((previous) => [...previous, ...files.map((file) => ({ id: file.name, name: file.name, status: "uploading" as const }))]);
    } };
  } };
});

vi.mock("./data/use-drive-picker", () => ({
  useDrivePicker: (_value: unknown, enabled: boolean) => ({
    selectedId: enabled ? "drv_1" : "",
    initial: { fetching: false },
    picker: { options: [{ value: "drv_1", label: "Files" }], onOpenChange: vi.fn(), onSearchChange: vi.fn(), searchState: {} },
  }),
}));

import { recordFilesGroup } from "./RecordFilesPane";
import { StorageRecordFiles } from "./data/documents";
import { AppRuntimeProvider, CHATTER_CONTAINERS, InAppLinkProvider, RelatedPane, containersFromChildren, type ChatterViewContext } from "@angee/ui";

const context: ChatterViewContext = {
  pathname: "/records/rec_1", params: { id: "rec_1" },
  route: { name: "record", path: "/records/$id", viewType: "example/record", modelLabel: "example.Record", recordEdges: ["storage.FileAttachment"] },
  view: { kind: "record", type: "example/record", sqid: "rec_1" },
};

function FilesGroup() {
  return <AppRuntimeProvider runtime={{ containers: containersFromChildren(CHATTER_CONTAINERS, { "related#groups": { "storage.files": recordFilesGroup } }) }}>
    <InAppLinkProvider navigate={sdk.navigate}><RelatedPane context={context} /></InAppLinkProvider>
  </AppRuntimeProvider>;
}

beforeEach(() => {
  sdk.query.mockReset().mockImplementation(() => ({ data: { record_files: { can_upload: true, attachments: [
      { id: "fat_1", label: "", file: { id: "fil_1", filename: "guide.pdf", title: "Guide", size_bytes: 2048, created_by_label: "River" } },
    ] } }, isPending: false }));
  sdk.preview = { viewAs: null, pending: false };
  sdk.upload.mockReset();
  sdk.navigate.mockReset();
  sdk.mounted.mockReset();
});
afterEach(cleanup);

test("the group shares the actor-scoped attachment count and each row links to its file page", () => {
  render(<FilesGroup />);
  expect(screen.getByText("Files").closest("li")?.textContent).toContain("Files· 1");
  expect(sdk.query).toHaveBeenCalledWith(StorageRecordFiles, {
    modelLabel: "example.Record", recordId: "rec_1",
  }, { enabled: true, models: ["storage.FileAttachment"] });
  const row = screen.getByRole("link", { name: /Guide/ });
  expect(row.getAttribute("href")).toBe("/storage/fil_1");
  expect(row.textContent).toContain("River");
  fireEvent.click(row);
  expect(sdk.navigate).toHaveBeenCalledWith("/storage/fil_1");
});

test("the label picker and row drop target share one upload owner, with progress after the popover closes", async () => {
  sdk.query.mockImplementation(() => ({ data: undefined, isPending: true }));
  const view = render(<FilesGroup />);
  expect(screen.getByRole("status").textContent).toContain("Loading attached files");
  sdk.query.mockImplementation(() => ({ data: { record_files: { can_upload: true, attachments: [] } }, isPending: false }));
  view.rerender(<FilesGroup />);
  const trigger = screen.getByRole("button", { name: "Upload" });
  expect(trigger.closest("li")?.textContent).toContain("Files");
  fireEvent.click(trigger);
  expect(screen.getByRole("combobox", { name: /Drive/ })).toBeTruthy();
  fireEvent.change(await screen.findByLabelText("Choose files", { selector: 'input[type="file"]' }), {
    target: { files: [new File(["content"], "note.txt", { type: "text/plain" })] },
  });
  expect(sdk.upload).toHaveBeenCalledWith(expect.any(Array), {
    driveId: "drv_1", visibility: "RECORD",
    record: { model_label: "example.Record", record_id: "rec_1" },
  });
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByText("note.txt")).toBeTruthy();
  const dropTarget = screen.getByText("No files attached").closest("div.relative")!;
  fireEvent.drop(dropTarget, { dataTransfer: { files: [new File(["more"], "second.txt")], types: ["Files"] } });
  expect(sdk.upload).toHaveBeenCalledTimes(2);
  expect(screen.getByText("second.txt")).toBeTruthy();
  expect(sdk.mounted).toHaveBeenCalledTimes(1);
});

test.each([{ viewAs: "actor", pending: false }, { viewAs: null, pending: true }])("view-as suppresses Upload while keeping readable files (%j)", (preview) => {
  sdk.preview = preview;
  render(<FilesGroup />);
  expect(screen.getByRole("link", { name: /Guide/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Upload" })).toBeNull();
});
