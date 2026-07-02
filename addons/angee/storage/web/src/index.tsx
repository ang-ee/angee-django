/// <reference path="./previews/assets.d.ts" />

import type { BaseAddonRoute } from "@angee/app";
import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { ArchiveRestore, Download, HardDrive, Image, Pencil } from "lucide-react";

import { enStorageMessages } from "./i18n";
import { storagePreviews } from "./previews";

const STORAGE_ID = "storage";

const storageRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes("storage.files", "/storage", lazyRouteComponent(() => import("./views/StoragePage"), "StoragePage"), undefined, { detailName: "storage.file", menu: STORAGE_ID }),
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
export type { StorageUpload, UploadedFile, UploadTarget, UploadTask } from "./data/use-upload";

export default storage;
