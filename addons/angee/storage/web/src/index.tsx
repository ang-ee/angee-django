/// <reference path="./previews/assets.d.ts" />

import type { BaseAddonRoute } from "@angee/app";
import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { Tab, useRecordChromeContext, useRecordPeekContext } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { ArchiveRestore, Download, HardDrive, Image, Pencil } from "lucide-react";

import { enStorageMessages } from "./i18n";
import { RecordFilesPane, recordFilesTarget, useRecordFilesCount } from "./RecordFilesPane";
import { FileRecordPreview } from "./views/FilePreview";
import { storagePreviews } from "./previews";
import { folderForm } from "./views/folder-form";

const STORAGE_ID = "storage";

const storageRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes("storage.files", "/storage", lazyRouteComponent(() => import("./views/StoragePage"), "StoragePage"), "storage.File", { detailName: "storage.file", menu: STORAGE_ID }),
  {
    // The drives/backends admin. A static `/storage/settings` outranks the
    // `/storage/$id` file route, so it is a sibling, not a file id. Its chrome
    // resolves from the menu child that references it.
    name: "storage.settings",
    path: "/storage/settings",
    component: lazyRouteComponent(() => import("./views/StorageSettingsPage"), "StorageSettingsPage"),
  },
];

const storageMenu: readonly BaseMenuItem[] = [
  {
    id: STORAGE_ID,
    label: "Files",
    icon: "files",
    route: "storage.files",
    children: [
      { id: "storage.files", label: "Files", icon: "files", route: "storage.files" },
      {
        id: "storage.settings",
        label: "Settings",
        icon: "drive",
        route: "storage.settings",
      },
    ],
  },
];

// Glyphs the browser reaches for that the base registry doesn't carry; `file`,
// `files`, and `trash` already live there.
const storage = defineBaseAddon({
  id: STORAGE_ID,
  routes: storageRoutes,
  menus: storageMenu,
  forms: { "storage.Folder": folderForm },
  containers: {
    "storage.File#sections": {
      "storage.file-preview": {
        sequence: 10,
        content: <Tab id="preview" label={{ namespace: "storage", key: "preview.label", fallback: enStorageMessages["preview.label"] }}><FilePreviewSection /></Tab>,
      },
    },
    "record#aside": {
      "storage.files": {
        sequence: 35,
        content: {
          label: "Files",
          icon: "file",
          aliases: ["files"],
          when: (context) => context.view.kind === "record" && Boolean(context.route?.modelLabel && context.view.sqid),
          useCount: useRecordFilesCount,
          render: (context) => <RecordFilesPane target={recordFilesTarget(context)} />,
        },
      },
    },
  },
  i18n: { storage: enStorageMessages },
  icons: {
    drive: HardDrive,
    image: Image,
    download: Download,
    restore: ArchiveRestore,
    edit: Pencil,
  },
  // Rich file renderers (PDF, media, HEIC) contributed to `PreviewPane`.
  previews: storagePreviews,
});

export { useStorageUpload } from "./data/use-upload";
export { RecordFilesPane, recordFilesTarget, type RecordFilesTarget } from "./RecordFilesPane";
export { FileRecordPreview, filePreviewReference } from "./views/FilePreview";
export type { StorageUpload, UploadedFile, UploadTarget, UploadTask } from "./data/use-upload";
export { StorageUploadTasks } from "./views/StorageUploadTasks";
export { useStorageT } from "./i18n";

export default storage;


function FilePreviewSection() {
  const record = useRecordChromeContext();
  const peek = useRecordPeekContext();
  return <div className="h-[65vh] min-h-80"><FileRecordPreview id={record.recordId} page={peek?.reference.page} /></div>;
}
