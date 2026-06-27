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
  icon: string;
  uploadState: string;
  sizeBytes: number;
  owner: string;
  updatedAt: string;
  url: string;
  drive: string;
  folder: string | null;
}

/** A navigator node — folders, synthetic scopes, and the open file. */
export interface StorageTreeRow extends Record<string, unknown> {
  id: string;
  name: string;
  parent: string;
  icon: string;
  kind: "scope" | "folder" | "file";
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
        file.drive === driveId && inScope(file, scope),
    )
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
    .map((file) => ({
      id: file.id,
      name: file.title || file.filename,
      filename: file.filename,
      mime: file.mime_type?.mime_type ?? "",
      mimeLabel: file.mime_type?.label || file.mime_type?.mime_type || "—",
      icon: file.mime_type?.icon_key || "file",
      uploadState: file.upload_state,
      sizeBytes: file.size_bytes,
      owner: file.created_by_label ?? "—",
      updatedAt: file.updated_at,
      url: file.url,
      drive: file.drive,
      folder: file.folder,
    }));
}

function inScope(file: StorageFile, scope: string): boolean {
  if (scope === TRASH_SCOPE) return file.is_trashed;
  if (file.is_trashed) return false;
  if (scope === ALL_SCOPE) return true;
  return file.folder === scope;
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
