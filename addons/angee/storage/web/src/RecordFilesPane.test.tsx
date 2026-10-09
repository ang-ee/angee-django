// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const sdk = vi.hoisted(() => ({ query: vi.fn(), upload: vi.fn() }));

vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()), useAuthoredQuery: sdk.query,
}));
vi.mock("@angee/ui", async (original) => ({
  ...(await original<typeof import("@angee/ui")>()),
  useRuntimeViewAs: () => ({ viewAs: null, pending: false }),
}));
vi.mock("./data/use-upload", () => ({
  useStorageUpload: () => ({ tasks: [], upload: sdk.upload, retry: vi.fn(), clearFinished: vi.fn() }),
}));
vi.mock("./views/FilePreview", () => ({ FileRecordPreview: ({ id }: { id: string }) => <p>Preview {id}</p> }));

import { RecordFilesPane, recordFilesTarget, useRecordFilesCount } from "./RecordFilesPane";
import { StorageDrives, StorageRecordFiles } from "./data/documents";
import type { ChatterViewContext } from "@angee/ui";

const context: ChatterViewContext = {
  pathname: "/records/rec_1", params: { id: "rec_1" },
  route: { name: "record", path: "/records/$id", viewType: "example/record", modelLabel: "example.Record" },
  view: { kind: "record", type: "example/record", sqid: "rec_1" },
};

function Count() {
  return <span>{useRecordFilesCount(context)}</span>;
}

beforeEach(() => {
  sdk.query.mockReset().mockImplementation((document) => document === StorageDrives
    ? { data: { drives: [{ id: "drv_1", name: "Files" }] }, isPending: false }
    : { data: { record_files: { can_upload: true, attachments: [
      { id: "fat_1", label: "", file: { id: "fil_1", filename: "guide.pdf", title: "Guide", is_trashed: false } },
    ] } }, isPending: false });
  sdk.upload.mockReset();
});
afterEach(cleanup);

test("shares the actor-scoped attachment count and opens the existing file preview", () => {
  render(<><Count /><RecordFilesPane target={recordFilesTarget(context)} /></>);
  expect(screen.getByText("1")).toBeTruthy();
  expect(sdk.query).toHaveBeenCalledWith(StorageRecordFiles, {
    modelLabel: "example.Record", recordId: "rec_1",
  }, { enabled: true, models: ["storage.FileAttachment"] });
  fireEvent.click(screen.getByRole("button", { name: "Guide" }));
  expect(screen.getByText("Preview fil_1")).toBeTruthy();
});

test("uploads to the record through the existing record target and renders a skeleton while loading", () => {
  sdk.query.mockImplementation((document) => document === StorageDrives
    ? { data: { drives: [{ id: "drv_1", name: "Files" }] }, isPending: false }
    : { data: undefined, isPending: true });
  const view = render(<RecordFilesPane target={recordFilesTarget(context)} />);
  expect(screen.getByRole("status").textContent).toContain("Loading attached files");
  sdk.query.mockImplementation((document) => document === StorageDrives
    ? { data: { drives: [{ id: "drv_1", name: "Files" }] }, isPending: false }
    : { data: { record_files: { can_upload: true, attachments: [] } }, isPending: false });
  view.rerender(<RecordFilesPane target={recordFilesTarget(context)} />);
  fireEvent.change(view.container.querySelector('input[type="file"]')!, {
    target: { files: [new File(["content"], "note.txt", { type: "text/plain" })] },
  });
  expect(sdk.upload).toHaveBeenCalledWith(expect.any(Array), {
    driveId: "drv_1", visibility: "RECORD",
    record: { model_label: "example.Record", record_id: "rec_1" },
  });
});
