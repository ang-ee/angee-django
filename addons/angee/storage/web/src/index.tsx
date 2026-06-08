import type { BaseAddon, BaseAddonRoute, BaseMenuItem } from "@angee/base";
import { ArchiveRestore, Download, Folder, HardDrive, Image } from "lucide-react";

import { StoragePage } from "./views/StoragePage";
import { FileCrumb } from "./views/FileDetail";

const STORAGE_ID = "storage";

const storageRoutes: readonly BaseAddonRoute[] = [
  {
    name: "storage.files",
    path: "/storage",
    shell: "console",
    menu: STORAGE_ID,
    component: StoragePage,
  },
  {
    // The file record nests under the list; `StoragePage` (the parent) reads the
    // `$id` param and swaps its content to the detail form, so this route carries
    // only the crumb.
    name: "storage.file",
    path: "/storage/$id",
    shell: "console",
    parent: "storage.files",
    crumb: (match) => (
      <FileCrumb id={String((match.params as { id?: string }).id ?? "")} />
    ),
  },
];

const storageMenu: readonly BaseMenuItem[] = [
  {
    id: STORAGE_ID,
    label: "Files",
    icon: "files",
    group: "platform",
    route: "storage.files",
  },
];

// Glyphs the browser reaches for that the base registry doesn't carry; `file`,
// `files`, and `trash` already live there.
const storage: BaseAddon = {
  id: STORAGE_ID,
  routes: storageRoutes,
  menus: storageMenu,
  icons: {
    drive: HardDrive,
    folder: Folder,
    image: Image,
    download: Download,
    restore: ArchiveRestore,
  },
};

export default storage;
