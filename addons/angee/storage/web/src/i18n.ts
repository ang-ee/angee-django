// English fallback strings for the storage addon. The host runtime owns the
// active translations; these are the defaults used when a key is missing.
// Components resolve them through `useStorageT()` (below).

import { createNamespaceT } from "@angee/ui";

export const enStorageMessages: Record<string, string> = {
  // Browser-level loading and empty states.
  "loading": "Loading storage",
  "loadingFile": "Loading file",
  "drives.unavailableTitle": "Storage unavailable",
  "drives.emptyTitle": "No drives",
  "drives.emptyDescription": "No storage drives are available to you.",
  "file.notFoundTitle": "File not found",
  "file.notFoundDescription": "This file is no longer available.",
  "preview.unsupported": "No inline preview for this file type.",
  // Rich renderer (PDF / media / HEIC) loading and error surfaces.
  "preview.loading": "Loading preview",
  "preview.loadError": "Could not load this preview.",
  "preview.decoding": "Decoding photo",
  "preview.pdfPage": "Page {page} of {total}",
  "preview.pdfPrev": "Previous page",
  "preview.pdfNext": "Next page",

  // Primary-pane folder navigator landmark.
  "nav.label": "Files",

  // Drive switcher.
  "drive.label": "Drive",
  "drive.placeholder": "Select a drive",
  "drive.searchPlaceholder": "Search drives…",

  // Folder delete confirm + inline folder controls.
  "folder.deleteTitle": 'Delete "{name}"?',
  "folder.deleteBody": "Files inside this folder move to the drive root.",
  "folder.deleteConfirm": "Delete",
  "folder.rename": "Rename folder",
  "folder.delete": "Delete folder",
  "folder.nameLabel": "Folder name",
  "folder.save": "Save",
  "newFolder.button": "New folder",
  "newFolder.placeholder": "Folder name",
  "newFolder.nameLabel": "New folder name",
  "newFolder.create": "Create",

  // Selection-bar bulk verbs.
  "bulk.restore": "Restore",
  "bulk.trash": "Trash",

  // File list + upload surface.
  "list.emptyUpload": "Drop files here or use Upload.",
  "list.empty": "No files here yet",
  "upload.button": "Upload",
  "upload.dropOverlay": "Drop to upload",
  "upload.heading": "Uploads",
  "upload.clearFinished": "Clear finished",

  // Upload task statuses (threaded into the status map, not resolved in a hook).
  "upload.status.hashing": "Preparing…",
  "upload.status.uploading": "Uploading…",
  "upload.status.finalizing": "Finalizing…",
  "upload.status.done": "Uploaded",
  "upload.status.deduped": "Already stored",
  "upload.status.failed": "Failed",

  // Upload error messages (surfaced as the task's failure tooltip).
  "upload.error.cannotStart": "Upload could not start.",
  "upload.error.transfer": "Upload failed ({status}).",
  "upload.error.cannotFinalize": "Could not finalize upload.",
  "upload.error.generic": "Upload failed.",

  // File detail toolbar.
  "file.rename": "Rename",
  "file.download": "Download",
  "file.restore": "Restore",
  "file.trash": "Trash",
  "file.detailsTab": "Details",
  "file.unknownType": "Unknown type",
  // File preview pane header subtitle: mime label · formatted size (formatSize
  // already carries the unit, e.g. "1.4 MB").
  "file.subtitle": "{type} · {size}",

  // File detail record form — section + field labels.
  "file.details": "Details",
  "file.filename": "Filename",
  "file.owner": "Owner",
  "file.stage": "Stage",

  // File-row stage badge.
  "stage.ready": "Ready",
  "stage.uploading": "Uploading",
  "stage.failed": "Failed",
  "stage.unknown": "Unknown",

  // File-list column headers.
  "column.name": "Name",
  "column.type": "Type",
  "column.stage": "Stage",
  "column.size": "Size",
  "column.owner": "Owner",
  "column.modified": "Modified",

  // Settings admin console sections.
  "settings.drives.title": "Drives",
  "settings.drives.description":
    "The unit of access control and key namespace files live in.",
  "settings.backends.title": "Backends",
  "settings.backends.description":
    "Storage infrastructure a drive is created against.",

  // Settings admin console — drive/backend form field labels.
  "settings.archived": "Archived",
  "settings.backendClass": "Backend class",
  "settings.config": "Config",
};

// A translator bound to the `storage` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `createNamespaceT` owner, so the copy still renders provider-less.
export const useStorageT = createNamespaceT("storage", enStorageMessages);
