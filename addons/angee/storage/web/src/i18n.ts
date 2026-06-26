// English fallback strings for the storage addon. The host runtime owns the
// active translations; these are the defaults used when a key is missing.
// Components resolve them through `useStorageT()` (below).

import { useNamespaceT } from "@angee/ui";
import type { MessageVars } from "@angee/refine";

export const enStorageMessages: Record<string, string> = {
  // Browser-level loading and empty states.
  "storage.loading": "Loading storage",
  "storage.loadingFile": "Loading file",
  "storage.drives.unavailableTitle": "Storage unavailable",
  "storage.drives.emptyTitle": "No drives",
  "storage.drives.emptyDescription": "No storage drives are available to you.",
  "storage.file.notFoundTitle": "File not found",
  "storage.file.notFoundDescription": "This file is no longer available.",
  "storage.preview.emptyTitle": "Select a file",
  "storage.preview.emptyDescription": "Choose a file from the list to preview it.",
  "storage.preview.unsupported": "No inline preview for this file type.",
  // Rich renderer (PDF / media / HEIC) loading and error surfaces.
  "storage.preview.loading": "Loading preview",
  "storage.preview.loadError": "Could not load this preview.",
  "storage.preview.decoding": "Decoding photo",
  "storage.preview.pdfPage": "Page {page} of {total}",
  "storage.preview.pdfPrev": "Previous page",
  "storage.preview.pdfNext": "Next page",

  // Drive switcher.
  "storage.drive.label": "Drive",
  "storage.drive.placeholder": "Select a drive",
  "storage.drive.searchPlaceholder": "Search drives…",

  // Folder delete confirm + inline folder controls.
  "storage.folder.deleteTitle": 'Delete "{name}"?',
  "storage.folder.deleteBody": "Files inside this folder move to the drive root.",
  "storage.folder.deleteConfirm": "Delete",
  "storage.folder.rename": "Rename folder",
  "storage.folder.delete": "Delete folder",
  "storage.folder.nameLabel": "Folder name",
  "storage.folder.save": "Save",
  "storage.newFolder.button": "New folder",
  "storage.newFolder.placeholder": "Folder name",
  "storage.newFolder.nameLabel": "New folder name",
  "storage.newFolder.create": "Create",

  // Selection-bar bulk verbs.
  "storage.bulk.restore": "Restore",
  "storage.bulk.trash": "Trash",

  // File list + upload surface.
  "storage.list.emptyUpload": "Drop files here or use Upload.",
  "storage.list.empty": "No files here yet",
  "storage.upload.button": "Upload",
  "storage.upload.dropOverlay": "Drop to upload",
  "storage.upload.heading": "Uploads",
  "storage.upload.clearFinished": "Clear finished",

  // Upload task statuses (threaded into the status map, not resolved in a hook).
  "storage.upload.status.hashing": "Preparing…",
  "storage.upload.status.uploading": "Uploading…",
  "storage.upload.status.finalizing": "Finalizing…",
  "storage.upload.status.done": "Uploaded",
  "storage.upload.status.deduped": "Already stored",
  "storage.upload.status.failed": "Failed",

  // Upload error messages (surfaced as the task's failure tooltip).
  "storage.upload.error.cannotStart": "Upload could not start.",
  "storage.upload.error.transfer": "Upload failed ({status}).",
  "storage.upload.error.cannotFinalize": "Could not finalize upload.",
  "storage.upload.error.generic": "Upload failed.",

  // File detail toolbar.
  "storage.file.rename": "Rename",
  "storage.file.download": "Download",
  "storage.file.restore": "Restore",
  "storage.file.trash": "Trash",
  "storage.file.crumbFallback": "File",

  // File detail record form — section + field labels.
  "storage.file.details": "Details",
  "storage.file.filename": "Filename",
  "storage.file.owner": "Owner",
  "storage.file.stage": "Stage",

  // File-row stage badge.
  "storage.stage.ready": "Ready",
  "storage.stage.uploading": "Uploading",
  "storage.stage.failed": "Failed",
  "storage.stage.unknown": "Unknown",

  // File-list column headers.
  "storage.column.name": "Name",
  "storage.column.type": "Type",
  "storage.column.stage": "Stage",
  "storage.column.size": "Size",
  "storage.column.owner": "Owner",
  "storage.column.modified": "Modified",

  // Settings admin console sections.
  "storage.settings.drives.title": "Drives",
  "storage.settings.drives.description":
    "The unit of access control and key namespace files live in.",
  "storage.settings.backends.title": "Backends",
  "storage.settings.backends.description":
    "Storage infrastructure a drive is created against.",

  // Settings admin console — drive/backend form field labels.
  "storage.settings.archived": "Archived",
  "storage.settings.backendClass": "Backend class",
  "storage.settings.config": "Config",
  "storage.settings.default": "Default",
};

// A translator bound to the `storage` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `useNamespaceT` owner, so the copy still renders provider-less.
export function useStorageT(): (key: string, vars?: MessageVars) => string {
  return useNamespaceT("storage", enStorageMessages);
}
