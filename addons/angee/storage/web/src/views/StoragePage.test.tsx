// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  ChatterTabsTestHost,
  PrimaryPaneTestHost,
  ShellPageTestProviders,
} from "@angee/app/testing";

const routerMocks = vi.hoisted(() => ({
  navigate: vi.fn(), params: {} as Record<string, string>, }));

const sdkMocks = vi.hoisted(() => ({
  useAuthoredQuery: vi.fn(), useBreadcrumbLeafLabel: vi.fn(), refetch: {
    backends: vi.fn(async () => undefined), drives: vi.fn(async () => undefined), files: vi.fn(async () => undefined), folders: vi.fn(async () => undefined), }, }));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => routerMocks.navigate, useParams: () => routerMocks.params, }));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: sdkMocks.useAuthoredQuery,
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  const { useCallback } = await import("react");
  // A stable confirm, like the real (memoized) `useConfirm` — an unstable one
  // would churn the published navigator's identity and loop the publish effect.
  const confirmAlways = async () => true;
  return {
    ...actual,
    useRouteRecordId: () => routerMocks.params.id,
    // Mirror the real (memoized) translator so a published node keeps a stable
    // identity across renders — an unstable `t` would republish every commit.
    useNamespaceT: (_namespace: string, messages: Record<string, string>) =>
      useCallback(
        (key: string, vars?: Record<string, string>) => {
          let message = messages[key] ?? key;
          for (const [name, value] of Object.entries(vars ?? {})) {
            message = message.replace(`{${name}}`, value);
          }
          return message;
        }, [messages], ), EmptyState: ({ title }: { title: string }) => (
      <section data-testid="empty-state">{title}</section>
    ), Glyph: () => <span />, LoadingPanel: ({ message }: { message: string }) => (
      <section data-testid="loading">{message}</section>
    ), PreviewPane: ({ file }: { file: { name: string } }) => (
      <section data-testid="preview-pane">{file.name}</section>
    ), SelectionBarAction: ({ children }: { children: React.ReactNode }) => (
      <button type="button">{children}</button>
    ), TreeView: ({
      rows, rowKey, label, selectedId, onSelect, }: {
      rows: readonly Record<string, string>[];
      rowKey: string;
      label: string;
      selectedId?: string;
      onSelect?: (row: Record<string, string>) => void;
    }) => (
      <div
        data-testid="tree"
        data-row-ids={rows.map((row) => row[rowKey]).join(", ")}
        data-selected={selectedId ?? ""}
      >
        {rows.map((row) => (
          <button
            key={row[rowKey]}
            type="button"
            data-testid={`tree-row-${row[rowKey]}`}
            onClick={() => onSelect?.(row)}
          >
            {row[label]}
          </button>
        ))}
      </div>
    ), useBreadcrumbLeafLabel: sdkMocks.useBreadcrumbLeafLabel, useConfirm: () => confirmAlways, };
});

// The explorer pane composes RelationPicker through its own module import, so
// the picker double mocks the subpath module (same resolved id), not the barrel.
vi.mock("@angee/ui/views/RelationPicker", () => ({
  RelationPicker: ({
    value, options, onChange, onCreated, "aria-label": ariaLabel, }: {
    value?: string | null;
    options: readonly { value: string; label: string }[];
    onChange?: (value: string) => void;
    onCreated?: (value: string) => void;
    "aria-label"?: string;
  }) => (
    <div>
      <select
        aria-label={ariaLabel}
        data-testid="root-picker"
        value={value ?? ""}
        onChange={(event) => onChange?.(event.currentTarget.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        data-testid="create-root"
        onClick={() => {
          onChange?.("drive-created");
          onCreated?.("drive-created");
        }}
      >
        Create root
      </button>
    </div>
  ),
}));

vi.mock("../data/use-file-actions", () => ({
  useFileActions: () => ({
    busy: false, move: vi.fn(), restore: vi.fn(async () => undefined), restoreMany: vi.fn(async () => undefined), trash: vi.fn(async () => undefined), trashMany: vi.fn(async () => undefined), }), }));

vi.mock("../data/use-folder-actions", () => ({
  useFolderActions: () => ({
    busy: false, create: vi.fn(), remove: vi.fn(async () => undefined), rename: vi.fn(), }), }));

vi.mock("../data/use-upload", () => ({
  useStorageUpload: () => ({
    clearFinished: vi.fn(), tasks: [], upload: vi.fn(), }), }));

vi.mock("./FileBrowserContent", () => ({
  FileBrowserContent: ({
    rows, uploadTarget, canUpload, }: {
    rows: readonly { id: string }[];
    uploadTarget: { driveId: string; folderId: string | null };
    canUpload: boolean;
  }) => (
    <section
      data-testid="file-list"
      data-row-ids={rows.map((row) => row.id).join(", ")}
      data-upload-drive={uploadTarget.driveId}
      data-upload-folder={uploadTarget.folderId ?? ""}
      data-can-upload={String(canUpload)}
    />
  ), }));

vi.mock("./FileDetail", () => ({
  // The detail is now the file's metadata form only — published into the
  // chatter's `details` tab. The pager + lifecycle verbs moved to the control band.
  FileDetail: ({ file }: { file: { id: string } }) => (
    <section data-testid="file-detail" data-file-id={file.id} />
  ), }));

vi.mock("./NewFolderControl", () => ({
  NewFolderControl: () => <button type="button">New folder</button>, }));

vi.mock("./SelectedFolderControl", () => ({
  SelectedFolderControl: ({ name }: { name: string }) => (
    <section data-testid="selected-folder">{name}</section>
  ), }));

import {
  StorageBackends,
  StorageDrives,
  StorageFiles,
  StorageFolders,
} from "../data/documents";
import { StoragePage } from "./StoragePage";

function pageTree() {
  return (
    <ShellPageTestProviders>
      <StoragePage />
      <PrimaryPaneTestHost />
      <ChatterTabsTestHost />
    </ShellPageTestProviders>
  );
}

let storageData = makeStorageData();

beforeEach(() => {
  storageData = makeStorageData();
  routerMocks.params = {};
  routerMocks.navigate.mockClear();
  sdkMocks.useBreadcrumbLeafLabel.mockClear();
  for (const refetch of Object.values(sdkMocks.refetch)) {
    refetch.mockClear();
  }
  sdkMocks.useAuthoredQuery.mockImplementation((document) => {
    if (document === StorageDrives) {
      return queryResult("drives", { drives: storageData.drives });
    }
    if (document === StorageFolders) {
      return queryResult("folders", { folders: storageData.folders });
    }
    if (document === StorageFiles) {
      return queryResult("files", { files: storageData.files });
    }
    if (document === StorageBackends) {
      return queryResult("backends", { backends: storageData.backends });
    }
    throw new Error("Unexpected storage query document");
  });
});

afterEach(() => {
  cleanup();
});

describe("StoragePage explorer wiring", () => {
  test("uses the open file drive for a direct link", () => {
    routerMocks.params = { id: "file-b" };

    render(pageTree());

    expect(rootPickerValue()).toBe("drive-b");
    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, __trash__, folder-b, file-b",
    );
    expect(treeAttribute("data-selected")).toBe("file-b");
    expect(screen.getByTestId("file-detail").getAttribute("data-file-id")).toBe(
      "file-b",
    );
    expect(screen.getByTestId("preview-pane").textContent).toBe("beta.txt");
    expect(sdkMocks.useBreadcrumbLeafLabel).toHaveBeenLastCalledWith("beta.txt");
  });

  test("detail navigation follows the active file scope", () => {
    routerMocks.params = { id: "file-a" };

    render(pageTree());

    expect(pagerText()).toBe("1 / 2");
    expect(pagerPrev().disabled).toBe(true);
    expect(pagerNext().disabled).toBe(false);

    fireEvent.click(pagerNext());

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({
      to: "/storage/file-a-folder",
    });
  });

  test("detail navigation steps back and stops at the last file", () => {
    routerMocks.params = { id: "file-a-folder" };

    render(pageTree());

    expect(pagerText()).toBe("2 / 2");
    // The last file in the scope has no next step.
    expect(pagerNext().disabled).toBe(true);

    fireEvent.click(pagerPrev());

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({
      to: "/storage/file-a",
    });
  });

  test("detail navigation degrades when the open file is out of scope", () => {
    // A trashed file isn't in the default (All files) scope, so it has no
    // position in the pager — only the scope total, with paging disabled.
    storageData = {
      ...storageData,
      files: [
        ...storageData.files,
        {
          ...file("file-b-trashed", "old.txt", "drive-b", null, "2025-01-04T00:00:00Z"),
          is_trashed: true,
        },
      ],
    };
    routerMocks.params = { id: "file-b-trashed" };

    render(pageTree());

    expect(pagerText()).toBe("/ 1");
    expect(pagerPrev().disabled).toBe(true);
    expect(pagerNext().disabled).toBe(true);
  });

  test("switching drives resets the folder scope and closes the detail route", () => {
    render(pageTree());

    expect(screen.queryByTestId("preview-pane")).toBeNull();

    fireEvent.click(screen.getByTestId("tree-row-folder-a"));

    expect(treeAttribute("data-selected")).toBe("folder-a");
    expect(fileListAttribute("data-row-ids")).toBe("file-a-folder");

    fireEvent.change(screen.getByLabelText("Drive"), {
      target: { value: "drive-b" },
    });

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({ to: "/storage" });
    expect(rootPickerValue()).toBe("drive-b");
    expect(treeAttribute("data-selected")).toBe("__all__");
    expect(fileListAttribute("data-row-ids")).toBe("file-b");
  });

  test("selects an inline-created drive after the refetched options include it", () => {
    const view = render(pageTree());

    fireEvent.click(screen.getByTestId("create-root"));

    expect(sdkMocks.refetch.drives).toHaveBeenCalledOnce();
    expect(rootPickerValue()).toBe("drive-a");

    storageData = {
      ...storageData,
      drives: [
        ...storageData.drives,
        { id: "drive-created", slug: "created", name: "Created Drive" },
      ],
      folders: [
        ...storageData.folders,
        folder("folder-created", "Created Folder", "drive-created"),
      ],
    };
    view.rerender(pageTree());

    expect(rootPickerValue()).toBe("drive-created");
    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, __trash__, folder-created",
    );
    expect(treeAttribute("data-selected")).toBe("__all__");
  });
});

function rootPickerValue(): string {
  return (screen.getByLabelText("Drive") as HTMLSelectElement).value;
}

function treeAttribute(name: string): string | null {
  return screen.getByTestId("tree").getAttribute(name);
}

function fileListAttribute(name: string): string | null {
  return screen.getByTestId("file-list").getAttribute(name);
}

// The record pager rides the shell control band beside the open file's preview.
function pagerText(): string {
  return (
    screen
      .getByRole("navigation", { name: "Record navigation" })
      .textContent?.replace(/\s+/g, " ")
      .trim() ?? ""
  );
}

function pagerPrev(): HTMLButtonElement {
  return screen.getByRole("button", {
    name: "Previous record",
  }) as HTMLButtonElement;
}

function pagerNext(): HTMLButtonElement {
  return screen.getByRole("button", {
    name: "Next record",
  }) as HTMLButtonElement;
}

function queryResult(
  name: keyof typeof sdkMocks.refetch,
  data: Record<string, unknown>,
) {
  return {
    data,
    fetching: false,
    error: null,
    refetch: sdkMocks.refetch[name],
  };
}

function makeStorageData() {
  return {
    drives: [
      { id: "drive-a", slug: "alpha", name: "Drive A" },
      { id: "drive-b", slug: "beta", name: "Drive B" },
    ],
    folders: [
      folder("folder-a", "Folder A", "drive-a"),
      folder("folder-b", "Folder B", "drive-b"),
    ],
    files: [
      file("file-a", "alpha.txt", "drive-a", null, "2025-01-03T00:00:00Z"),
      file(
        "file-a-folder",
        "folder-alpha.txt",
        "drive-a",
        "folder-a",
        "2025-01-02T00:00:00Z",
      ),
      file("file-b", "beta.txt", "drive-b", null, "2025-01-01T00:00:00Z"),
    ],
    backends: [{ id: "backend", slug: "local", label: "Local" }],
  };
}

function folder(id: string, name: string, drive: string) {
  return {
    id,
    name,
    description: "",
    is_virtual: false,
    smart_kind: null,
    drive,
    parent: null,
  };
}

function file(
  id: string,
  filename: string,
  drive: string,
  folderId: string | null,
  updatedAt: string,
) {
  return {
    id,
    filename,
    title: "",
    size_bytes: 128,
    content_hash: "hash",
    upload_state: "ready",
    is_trashed: false,
    updated_at: updatedAt,
    created_by_label: "Alex",
    url: `/files/${id}`,
    drive,
    folder: folderId,
    mime_type: {
      mime_type: "text/plain",
      category: "text",
      label: "Text",
      icon_key: "file",
    },
  };
}
