import type { BaseAddonRoute } from "@angee/app";
import { defineBaseAddon } from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { ArchiveRestore, Download, HardDrive, Image, Pencil } from "lucide-react";

import { enStorageMessages } from "./i18n";
import { storagePreviews } from "./previews";
import { StoragePage } from "./views/StoragePage";
import { StorageSettingsPage } from "./views/StorageSettingsPage";

const STORAGE_ID = "storage";

const storageRoutes: readonly BaseAddonRoute[] = [
  {
    name: "storage.files",
    path: "/storage",
    layout: "console",
    menu: STORAGE_ID,
    component: StoragePage,
  },
  {
    // The file record nests under the list; `StoragePage` reads the `$id` param
    // and swaps its content to the detail form.
    name: "storage.file",
    path: "/storage/$id",
    layout: "console",
    parent: "storage.files",
  },
  {
    // The drives/backends admin. A static `/storage/settings` outranks the
    // `/storage/$id` file route, so it is a sibling, not a file id. Its chrome
    // resolves from the menu child that references it.
    name: "storage.settings",
    path: "/storage/settings",
    layout: "console",
    component: StorageSettingsPage,
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

export default storage;
