import type { DndPayload } from "@angee/base";

import {
  DRIVE_TYPE,
  FOLDER_TYPE,
  relationGlobalId,
  toGlobalId,
} from "../lib/global-id";
import type { StorageFile, StorageFolder } from "./documents";

/** Dnd payload kind for a dragged file; tree nodes accept it as a drop target. */
export const STORAGE_FILE_DND = "storage.file";

/** The body of a dragged-file payload — the file's relay GlobalID. */
export interface FileDragData {
  id: string;
}

/** Make a file row draggable: its move payload, keyed by the file's node id. */
export function fileDragPayload(row: StorageFileRow): DndPayload<FileDragData> {
  return { type: STORAGE_FILE_DND, data: { id: row.id } };
}

// The browser fetches every drive/folder/file once and scopes client-side, so
// these transforms own the projection: files → list rows, folders → tree rows.
// Public ids are uniform (the node id is the sqid, so `file.folder` matches a
// `folder.id`), which is what lets the tree join and the scope filter work here.

/** The two non-folder scopes the navigator offers, plus the default. */
export const ALL_SCOPE = "__all__";
export const TRASH_SCOPE = "__trash__";

/** A file projected for the list — `id` keyed for `RowsListView`. */
export interface StorageFileRow extends Record<string, unknown> {
  id: string;
  name: string;
  filename: string;
  mime: string;
  mimeLabel: string;
  uploadState: string;
  sizeBytes: number;
  owner: string;
  updatedAt: string;
  url: string;
  drive: string;
  folder: string | null;
}

/** A navigator node — folders plus the synthetic All/Trash scopes. */
export interface StorageTreeRow extends Record<string, unknown> {
  id: string;
  name: string;
  parent: string;
  icon: string;
}

/** Project + scope a drive's files for the list, newest first. */
export function fileRows(
  files: readonly StorageFile[],
  options: { driveId: string; scope: string },
): StorageFileRow[] {
  const { driveId, scope } = options;
  return files
    .filter(
      (file) =>
        toGlobalId(DRIVE_TYPE, file.drive) === driveId && inScope(file, scope),
    )
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
    .map((file) => ({
      id: file.id,
      name: file.title || file.filename,
      filename: file.filename,
      mime: file.mimeType?.mimeType ?? "",
      mimeLabel: file.mimeType?.label || file.mimeType?.mimeType || "—",
      uploadState: file.uploadState,
      sizeBytes: file.sizeBytes,
      owner: file.createdByLabel ?? "—",
      updatedAt: file.updatedAt,
      url: file.url,
      drive: toGlobalId(DRIVE_TYPE, file.drive),
      folder: relationGlobalId(FOLDER_TYPE, file.folder),
    }));
}

function inScope(file: StorageFile, scope: string): boolean {
  if (scope === TRASH_SCOPE) return file.isTrashed;
  if (file.isTrashed) return false;
  if (scope === ALL_SCOPE) return true;
  return relationGlobalId(FOLDER_TYPE, file.folder) === scope;
}

/** The selected file's full record, for the preview pane. */
export function fileById(
  files: readonly StorageFile[],
  id: string | null,
): StorageFile | null {
  if (!id) return null;
  return files.find((file) => file.id === id) ?? null;
}

/** Build the navigator rows: All files, Trash, then the drive's real folders. */
export function folderTreeRows(
  folders: readonly StorageFolder[],
  driveId: string,
): StorageTreeRow[] {
  const rows: StorageTreeRow[] = [
    { id: ALL_SCOPE, name: "All files", parent: "", icon: "files" },
    { id: TRASH_SCOPE, name: "Trash", parent: "", icon: "trash" },
  ];
  for (const folder of folders) {
    if (folder.isVirtual) continue;
    if (toGlobalId(DRIVE_TYPE, folder.drive ?? "") !== driveId) continue;
    rows.push({
      id: folder.id,
      name: folder.name,
      parent: relationGlobalId(FOLDER_TYPE, folder.parent) ?? "",
      icon: "folder",
    });
  }
  return rows;
}
