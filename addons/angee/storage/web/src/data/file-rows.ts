import type { DndPayload } from "@angee/ui";

import type { StorageFile, StorageFolder } from "./documents";

/** Dnd payload kind for a dragged file; tree nodes accept it as a drop target. */
export const STORAGE_FILE_DND = "storage.file";

/** The body of a dragged-file payload: the file's public id. */
export interface FileDragData {
  id: string;
}

/** Make a file row draggable: its move payload, keyed by the file's node id. */
export function fileDragPayload(row: StorageFileRow): DndPayload<FileDragData> {
  return { type: STORAGE_FILE_DND, data: { id: row.id } };
}

// Public ids are uniform (the node id is the sqid, so `file.folder` matches a
// `folder.id`), which is what lets the tree join and file-move payloads work.

/** The two non-folder scopes the navigator offers, plus the default. */
export const ALL_SCOPE = "__all__";
export const TRASH_SCOPE = "__trash__";

/** The page search key that carries the navigator's folder scope, so it is
 * deep-linkable and rides the address bar beside the `group` view param. */
export const FOLDER_SCOPE_PARAM = "folder";

// The `?folder=` value standing in for the Trash scope. All files is the absent
// default and a real folder rides as its own sqid, so the whole scope is one
// param — one URL value for the single navigator selection.
const TRASH_SCOPE_PARAM = "trash";

/** Read the navigator scope (All files / Trash / a folder sqid) from the page
 * search params — the inverse of {@link folderScopeToParam}. */
export function folderScopeFromSearch(
  search: Readonly<Record<string, unknown>>,
): string {
  const raw = search[FOLDER_SCOPE_PARAM];
  if (raw === TRASH_SCOPE_PARAM) return TRASH_SCOPE;
  if (typeof raw === "string" && raw !== "") return raw;
  return ALL_SCOPE;
}

/** Encode a navigator scope as its `?folder=` value; All files is the absent
 * default (`undefined` drops the param), so the address bar stays clean. */
export function folderScopeToParam(scope: string): string | undefined {
  if (scope === TRASH_SCOPE) return TRASH_SCOPE_PARAM;
  if (scope === ALL_SCOPE) return undefined;
  return scope;
}

/** The server-backed storage.File row shape used by List renderers. */
export interface StorageFileRow extends Record<string, unknown> {
  id: string;
  filename: string;
  title: string;
  size_bytes: number;
  upload_state: string;
  is_trashed: boolean;
  updated_at: string;
  created_by_label: string | null;
  url: string;
  drive: string;
  folder: string | null;
  mime_type: {
    mime_type: string;
    category: string;
    label: string;
    icon_key: string;
  } | null;
}

/** A navigator node — folders, synthetic scopes, and the open file. */
export interface StorageTreeRow extends Record<string, unknown> {
  id: string;
  name: string;
  parent: string;
  icon: string;
  kind: "scope" | "folder" | "file";
}

/** Build the navigator rows: All files, Trash, then the drive's real folders. */
export function folderTreeRows(
  folders: readonly StorageFolder[],
  driveId: string,
  openFile?: StorageFile | null,
): StorageTreeRow[] {
  const rows: StorageTreeRow[] = [
    {
      id: ALL_SCOPE,
      name: "All files",
      parent: "",
      icon: "files",
      kind: "scope",
    },
    {
      id: TRASH_SCOPE,
      name: "Trash",
      parent: "",
      icon: "trash",
      kind: "scope",
    },
  ];
  const folderIds = new Set<string>();
  for (const folder of folders) {
    if (folder.is_virtual) continue;
    if ((folder.drive ?? "") !== driveId) continue;
    folderIds.add(folder.id);
    rows.push({
      id: folder.id,
      name: folder.name,
      parent: folder.parent ?? "",
      icon: "folder",
      kind: "folder",
    });
  }
  if (openFile && openFile.drive === driveId) {
    rows.push({
      id: openFile.id,
      name: openFile.title || openFile.filename,
      // Anchor under the file's folder only when that folder is a rendered
      // node; otherwise fall back to All files so the open file never orphans
      // out of the tree (folders may resolve after files, or be filtered out).
      parent:
        openFile.folder && folderIds.has(openFile.folder)
          ? openFile.folder
          : ALL_SCOPE,
      icon: openFile.mime_type?.icon_key || "file",
      kind: "file",
    });
  }
  return rows;
}
