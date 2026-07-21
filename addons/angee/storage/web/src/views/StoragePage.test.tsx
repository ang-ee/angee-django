// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  ChatterTabsTestHost,
  PrimaryPaneTestHost,
  ShellPageTestProviders,
} from "@angee/app/testing";

// A reactive stand-in for the router's search store: `useSearch` reads it and a
// functional `navigate({ search })` writes it, so the URL-owned folder scope
// round-trips exactly as it does in the app.
const routerState = vi.hoisted(() => {
  let search: Record<string, unknown> = {};
  const listeners = new Set<() => void>();
  return {
    getSearch: (): Record<string, unknown> => search,
    setSearch: (next: Record<string, unknown>): void => {
      search = next;
      for (const listener of listeners) listener();
    },
    subscribe: (listener: () => void): (() => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    reset: (): void => {
      search = {};
    },
  };
});

const routerMocks = vi.hoisted(() => ({
  navigate: vi.fn(
    (options?: {
      search?: (current: Record<string, unknown>) => Record<string, unknown>;
    }) => {
      if (options && typeof options.search === "function") {
        routerState.setSearch(options.search(routerState.getSearch()));
      }
    },
  ),
  params: {} as Record<string, string>,
}));

const sdkMocks = vi.hoisted(() => ({
  folderDrives: [] as string[],
  useAuthoredQuery: vi.fn(), useBreadcrumbLeafLabel: vi.fn(), refetch: {
    backends: vi.fn(async () => undefined), drives: vi.fn(async () => undefined), file: vi.fn(async () => undefined), folders: vi.fn(async () => undefined), }, }));

vi.mock("@tanstack/react-router", async () => {
  const { useSyncExternalStore } = await import("react");
  const useSearchStore = (): Record<string, unknown> =>
    useSyncExternalStore(
      routerState.subscribe,
      routerState.getSearch,
      routerState.getSearch,
    );
  return {
    useNavigate: () => routerMocks.navigate,
    useParams: () => routerMocks.params,
    useSearch: () => useSearchStore(),
    useRouterState: ({
      select,
    }: {
      select: (state: { location: { searchStr: string } }) => unknown;
    }) => {
      const current = useSearchStore();
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(current)) {
        if (value == null || value === "") continue;
        params.set(key, String(value));
      }
      const query = params.toString();
      return select({ location: { searchStr: query ? `?${query}` : "" } });
    },
  };
});

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
      rows, rowKey, label, selectedId, onSelect, onExpand, hasChildren, }: {
      rows: readonly Record<string, string>[];
      rowKey: string;
      label: string;
      selectedId?: string;
      onSelect?: (row: Record<string, string>) => void;
      onExpand?: (nodeId: string) => void;
      hasChildren?: string;
    }) => (
      <div
        data-testid="tree"
        data-row-ids={rows.map((row) => row[rowKey]).join(", ")}
        data-selected={selectedId ?? ""}
      >
        {rows.map((row) => (
          <span key={row[rowKey]}>
            <button
              type="button"
              data-testid={`tree-row-${row[rowKey]}`}
              onClick={() => onSelect?.(row)}
            >
              {row[label]}
            </button>
            {hasChildren && row[hasChildren] ? (
              <button
                type="button"
                data-testid={`tree-expand-${row[rowKey]}`}
                onClick={() => onExpand?.(String(row[rowKey]))}
              >
                expand
              </button>
            ) : null}
          </span>
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

vi.mock("./FileBrowserContent", async () => {
  const React = await import("react");
  return {
  FileBrowserContent: ({
    baseFilter, defaultGroup, hidden, onListStateChange, uploadTarget, canUpload, }: {
    baseFilter: Record<string, { exact: unknown }>;
    defaultGroup: { field: string } | null;
    hidden?: boolean;
    onListStateChange: (state: Record<string, unknown>) => void;
    uploadTarget: { driveId: string; folderId: string | null };
    canUpload: boolean;
  }) => {
    const rows = storageData.files
      .filter((row) => row.drive === baseFilter.drive?.exact)
      .filter((row) => row.is_trashed === baseFilter.is_trashed?.exact)
      .filter((row) =>
        baseFilter.folder ? row.folder === baseFilter.folder.exact : true,
      )
      .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
    const rowsKey = rows.map((row) => row.id).join(",");
    const filterKey = JSON.stringify(baseFilter);
    React.useEffect(() => {
      onListStateChange({
        rows,
        total: rows.length,
        page: 1,
        pageSize: 50,
        pageCount: 1,
        hasNext: false,
        hasPrev: false,
        fetching: false,
        navigationScope: {
          filter: baseFilter,
          order: { updated_at: "DESC" },
          page: 1,
          pageSize: 50,
        },
      });
    }, [filterKey, onListStateChange, rowsKey]);
    return (
    <section
      data-testid="file-list"
      data-hidden={String(Boolean(hidden))}
      data-row-ids={rows.map((row) => row.id).join(", ")}
      data-group={defaultGroup?.field ?? ""}
      data-filter={JSON.stringify(baseFilter)}
      data-upload-drive={uploadTarget.driveId}
      data-upload-folder={uploadTarget.folderId ?? ""}
      data-can-upload={String(canUpload)}
    />
    );
  },
  };
});

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
  StorageFileById,
  StorageFolderChildren,
  StorageFolderRoots,
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
  routerState.reset();
  routerMocks.navigate.mockClear();
  sdkMocks.folderDrives.length = 0;
  sdkMocks.useBreadcrumbLeafLabel.mockClear();
  for (const refetch of Object.values(sdkMocks.refetch)) {
    refetch.mockClear();
  }
  sdkMocks.useAuthoredQuery.mockImplementation((document, variables) => {
    if (document === StorageDrives) {
      return queryResult("drives", { drives: storageData.drives });
    }
    if (document === StorageFolderRoots) {
      const drive = String((variables as { drive?: string })?.drive ?? "");
      sdkMocks.folderDrives.push(drive);
      return queryResult("folders", {
        folders: storageData.folders.filter(
          (row) => row.drive === drive && row.parent == null,
        ),
      });
    }
    if (document === StorageFolderChildren) {
      const drive = String((variables as { drive?: string })?.drive ?? "");
      const parent = String((variables as { parent?: string })?.parent ?? "");
      return queryResult("folders", {
        folders: storageData.folders.filter(
          (row) => row.drive === drive && row.parent === parent,
        ),
      });
    }
    if (document === StorageFileById) {
      const id = String((variables as { id?: string })?.id ?? "");
      return queryResult("file", {
        files_by_pk: storageData.files.find((row) => row.id === id) ?? null,
      });
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
    expect(sdkMocks.folderDrives.at(-1)).toBe("drive-b");
  });

  test("detail navigation follows the List snapshot", () => {
    const view = render(pageTree());
    routerMocks.params = { id: "file-a" };
    view.rerender(pageTree());

    expect(pagerText()).toBe("1 / 2");
    expect(pagerPrev().disabled).toBe(true);
    expect(pagerNext().disabled).toBe(false);

    fireEvent.click(pagerNext());

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({
      to: "/storage/file-a-folder",
      search: expect.any(Function),
    });
  });

  test("detail navigation steps back and stops at the snapshot edge", () => {
    const view = render(pageTree());
    routerMocks.params = { id: "file-a-folder" };
    view.rerender(pageTree());

    expect(pagerText()).toBe("2 / 2");
    // The last file in the scope has no next step.
    expect(pagerNext().disabled).toBe(true);

    fireEvent.click(pagerPrev());

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({
      to: "/storage/file-a",
      search: expect.any(Function),
    });
  });

  test("switching drives resets the folder scope and closes the detail route", () => {
    render(pageTree());

    expect(screen.queryByTestId("preview-pane")).toBeNull();
    expect(fileListAttribute("data-group")).toBe("folder");
    expect(fileListFilter()).toEqual({
      drive: { exact: "drive-a" },
      is_trashed: { exact: false },
    });

    fireEvent.click(screen.getByTestId("tree-row-folder-a"));

    expect(treeAttribute("data-selected")).toBe("folder-a");
    expect(fileListAttribute("data-row-ids")).toBe("file-a-folder");
    expect(fileListAttribute("data-group")).toBe("");
    expect(fileListFilter()).toEqual({
      drive: { exact: "drive-a" },
      is_trashed: { exact: false },
      folder: { exact: "folder-a" },
    });

    fireEvent.change(screen.getByLabelText("Drive"), {
      target: { value: "drive-b" },
    });

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({
      to: "/storage",
      search: expect.any(Function),
    });
    const driveNavigation = routerMocks.navigate.mock.calls.at(-1)?.[0] as {
      search: (current: Record<string, unknown>) => Record<string, unknown>;
    };
    expect(
      driveNavigation.search({ group: "folder~folder~folder_id" }),
    ).toEqual({ group: "folder~folder~folder_id" });
    expect(rootPickerValue()).toBe("drive-b");
    expect(treeAttribute("data-selected")).toBe("__all__");
    expect(fileListAttribute("data-row-ids")).toBe("file-b");
    expect(fileListAttribute("data-group")).toBe("folder");
    expect(fileListFilter()).toEqual({
      drive: { exact: "drive-b" },
      is_trashed: { exact: false },
    });
    expect(sdkMocks.folderDrives.at(-1)).toBe("drive-b");

    fireEvent.click(screen.getByTestId("tree-row-__trash__"));

    expect(fileListAttribute("data-group")).toBe("");
    expect(fileListAttribute("data-can-upload")).toBe("false");
    expect(fileListFilter()).toEqual({
      drive: { exact: "drive-b" },
      is_trashed: { exact: true },
    });
  });

  test("selects the folder scope from the `?folder=` URL param on load", () => {
    routerState.setSearch({ folder: "folder-a" });

    render(pageTree());

    expect(treeAttribute("data-selected")).toBe("folder-a");
    expect(fileListAttribute("data-row-ids")).toBe("file-a-folder");
    expect(fileListAttribute("data-group")).toBe("");
    expect(fileListFilter()).toEqual({
      drive: { exact: "drive-a" },
      is_trashed: { exact: false },
      folder: { exact: "folder-a" },
    });
  });

  test("selects the Trash scope from the `?folder=trash` sentinel, keeping group", () => {
    routerState.setSearch({ folder: "trash", group: "folder~folder~folder_id" });

    render(pageTree());

    expect(treeAttribute("data-selected")).toBe("__trash__");
    expect(fileListFilter()).toEqual({
      drive: { exact: "drive-a" },
      is_trashed: { exact: true },
    });

    // Selecting All files drops the `folder` param while leaving `group` intact.
    fireEvent.click(screen.getByTestId("tree-row-__all__"));

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({
      to: "/storage",
      search: expect.any(Function),
    });
    const allNavigation = routerMocks.navigate.mock.calls.at(-1)?.[0] as {
      search: (current: Record<string, unknown>) => Record<string, unknown>;
    };
    expect(
      allNavigation.search({ folder: "trash", group: "folder~folder~folder_id" }),
    ).toEqual({ group: "folder~folder~folder_id" });
    expect(treeAttribute("data-selected")).toBe("__all__");
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

  test("loads a folder's children only when it is expanded", () => {
    render(pageTree());

    // Only the drive's top-level folders load up front; the nested child is not
    // fetched with the roots.
    expect(treeAttribute("data-row-ids")).toBe("__all__, __trash__, folder-a");

    // Expanding folder-a fires its per-parent children query and appends the row.
    fireEvent.click(screen.getByTestId("tree-expand-folder-a"));

    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, __trash__, folder-a, folder-a-child",
    );
  });

  test("resets the lazy folder accumulator when the drive switches", () => {
    render(pageTree());

    fireEvent.click(screen.getByTestId("tree-expand-folder-a"));
    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, __trash__, folder-a, folder-a-child",
    );

    // Switching drives starts the accumulator over at the new drive's roots.
    fireEvent.change(screen.getByLabelText("Drive"), {
      target: { value: "drive-b" },
    });
    expect(treeAttribute("data-row-ids")).toBe("__all__, __trash__, folder-b");

    // Returning to drive-a shows its roots again with the child re-collapsed.
    fireEvent.change(screen.getByLabelText("Drive"), {
      target: { value: "drive-a" },
    });
    expect(treeAttribute("data-row-ids")).toBe("__all__, __trash__, folder-a");
  });

  test("a failed children fetch drops the head and stays retryable, not wedged", () => {
    // A stable error like react-query returns; the children query fails until the
    // flag flips (a permission error / dropped socket that later recovers).
    const denied = new Error("children fetch denied");
    let childrenShouldError = true;
    sdkMocks.useAuthoredQuery.mockImplementation((document, variables) => {
      if (document === StorageDrives) {
        return queryResult("drives", { drives: storageData.drives });
      }
      if (document === StorageFolderRoots) {
        const drive = String((variables as { drive?: string })?.drive ?? "");
        return queryResult("folders", {
          folders: storageData.folders.filter(
            (row) => row.drive === drive && row.parent == null,
          ),
        });
      }
      if (document === StorageFolderChildren) {
        if (childrenShouldError) {
          return {
            data: undefined,
            fetching: false,
            error: denied,
            refetch: sdkMocks.refetch.folders,
          };
        }
        const drive = String((variables as { drive?: string })?.drive ?? "");
        const parent = String((variables as { parent?: string })?.parent ?? "");
        return queryResult("folders", {
          folders: storageData.folders.filter(
            (row) => row.drive === drive && row.parent === parent,
          ),
        });
      }
      if (document === StorageFileById) {
        return queryResult("file", { files_by_pk: null });
      }
      if (document === StorageBackends) {
        return queryResult("backends", { backends: storageData.backends });
      }
      throw new Error("Unexpected storage query document");
    });

    render(pageTree());
    expect(treeAttribute("data-row-ids")).toBe("__all__, __trash__, folder-a");

    // Expanding folder-a fails: the head must drain (no wedge), no child appears,
    // and folder-a keeps its optimistic caret so the fetch is retryable.
    fireEvent.click(screen.getByTestId("tree-expand-folder-a"));
    expect(treeAttribute("data-row-ids")).toBe("__all__, __trash__, folder-a");
    expect(screen.queryByTestId("tree-expand-folder-a")).not.toBeNull();

    // The error clears; re-expanding now loads the child — proof the queue drained
    // and re-enqueuing fires a fresh fetch rather than sitting on a dead queue.
    childrenShouldError = false;
    fireEvent.click(screen.getByTestId("tree-expand-folder-a"));
    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, __trash__, folder-a, folder-a-child",
    );
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

function fileListFilter(): Record<string, unknown> {
  return JSON.parse(fileListAttribute("data-filter") ?? "{}") as Record<
    string,
    unknown
  >;
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
      // A nested folder to exercise lazy expansion: it loads only when
      // `folder-a` is expanded, never with the drive's top-level roots.
      folder("folder-a-child", "Folder A Child", "drive-a", "folder-a"),
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

function folder(
  id: string,
  name: string,
  drive: string,
  parent: string | null = null,
) {
  return {
    id,
    name,
    description: "",
    is_virtual: false,
    drive,
    parent,
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
