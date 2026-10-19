// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const sdk = vi.hoisted(() => ({ query: vi.fn(), upload: vi.fn(), navigate: vi.fn() }));

vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()), useAuthoredQuery: sdk.query,
}));
vi.mock("@angee/ui", async (original) => ({
  ...(await original<typeof import("@angee/ui")>()),
  useRuntimeViewAs: () => ({ viewAs: null, pending: false }),
  useResourceRecordHrefLookup: () => (model: string, id: string) => model === "storage.File" ? `/storage/${id}` : undefined,
}));
vi.mock("@tanstack/react-router", async (original) => ({
  ...(await original<typeof import("@tanstack/react-router")>()), useNavigate: () => sdk.navigate,
}));
vi.mock("./data/use-upload", () => ({
  useStorageUpload: () => ({ tasks: [], upload: sdk.upload, retry: vi.fn(), clearFinished: vi.fn() }),
}));

import { RecordFilesPane, recordFilesGroup, recordFilesTarget, useRecordFilesCount } from "./RecordFilesPane";
import { StorageDrives, StorageRecordFiles } from "./data/documents";
import type { ChatterViewContext } from "@angee/ui";

const context: ChatterViewContext = {
  pathname: "/records/rec_1", params: { id: "rec_1" },
  route: { name: "record", path: "/records/$id", viewType: "example/record", modelLabel: "example.Record", recordEdges: ["storage.FileAttachment"] },
  view: { kind: "record", type: "example/record", sqid: "rec_1" },
};

function Count() {
  return <span>{useRecordFilesCount(context)}</span>;
}

beforeEach(() => {
  sdk.query.mockReset().mockImplementation((document) => document === StorageDrives
    ? { data: { drives: [{ id: "drv_1", name: "Files" }] }, isPending: false }
    : { data: { record_files: { can_upload: true, attachments: [
      { id: "fat_1", label: "", file: { id: "fil_1", filename: "guide.pdf", title: "Guide", size_bytes: 2048, created_by_label: "River" } },
    ] } }, isPending: false });
  sdk.upload.mockReset();
  sdk.navigate.mockReset();
});
afterEach(cleanup);

test("the group shares the actor-scoped attachment count and each row links to its file page", () => {
  expect(recordFilesGroup.content.when?.(context)).toBe(true);
  expect(recordFilesGroup.content.when?.({ ...context, route: { ...context.route!, recordEdges: [] } })).toBe(false);
  render(<><Count /><RecordFilesPane target={recordFilesTarget(context)} /></>);
  expect(screen.getByText("1")).toBeTruthy();
  expect(sdk.query).toHaveBeenCalledWith(StorageRecordFiles, {
    modelLabel: "example.Record", recordId: "rec_1",
  }, { enabled: true, models: ["storage.FileAttachment"] });
  const row = screen.getByRole("link", { name: /Guide/ });
  expect(row.getAttribute("href")).toBe("/storage/fil_1");
  expect(row.textContent).toContain("River");
  fireEvent.click(row);
  expect(sdk.navigate).toHaveBeenCalledWith({ href: "/storage/fil_1" });
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
