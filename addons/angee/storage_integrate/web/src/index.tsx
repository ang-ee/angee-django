import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";

import { MOUNT_MODEL } from "./documents";
import { enStorageIntegrateMessages } from "./i18n";

const storageIntegrate = defineBaseAddon({
  id: "storage-integrate",
  routes: resourcePageRoutes(
    "storage-integrate.mounts",
    "/storage/mounts",
    lazyRouteComponent(() => import("./views/MountsPage"), "MountsPage"),
    MOUNT_MODEL,
  ),
  menus: [
    {
      id: "storage-integrate.mounts",
      label: "Mounts",
      route: "storage-integrate.mounts",
      parentId: "storage",
      icon: "drive",
      description: "Connect and synchronize external storage folders",
    },
  ],
  i18n: { storage: enStorageIntegrateMessages },
});

export { ConnectLocalFolderAction } from "./ConnectLocalFolderAction";
export { MountSourceBrowser } from "./MountSourceBrowser";
export type { MountSourceBrowserProps } from "./MountSourceBrowser";
export { MOUNT_MODEL } from "./documents";
export default storageIntegrate;
