import type { BaseAddon, BaseAddonRoute, BaseMenuItem } from "@angee/base";
import { Folder, HardDrive, Image } from "lucide-react";

import { StoragePage } from "./views/StoragePage";

const STORAGE_ID = "storage";

const storageRoutes: readonly BaseAddonRoute[] = [
  {
    name: "storage.files",
    path: "/storage",
    shell: "console",
    menu: STORAGE_ID,
    component: StoragePage,
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
  icons: { drive: HardDrive, folder: Folder, image: Image },
};

export default storage;
