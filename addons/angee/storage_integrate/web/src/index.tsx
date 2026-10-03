import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";

import { ConnectLocalFolderAction } from "./ConnectLocalFolderAction";
import { MOUNT_MODEL } from "./documents";
import { enStorageIntegrateMessages } from "./i18n";
import { mountForm } from "./views/MountForm";

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
  forms: { [MOUNT_MODEL]: mountForm },
  containers: {
    // Connect verbs for each storage backend, on the Mount list's toolbar.
    "storage-integrate.mounts#toolbar": {
      "storage-integrate.connect-local-folder": { sequence: 10, content: <ConnectLocalFolderAction /> },
    },
  },
});

export { ConnectLocalFolderAction } from "./ConnectLocalFolderAction";
export { ConnectMountAction } from "./ConnectMountAction";
export type { ConnectMountActionProps } from "./ConnectMountAction";
export { MountSourceBrowser } from "./MountSourceBrowser";
export type { MountSourceBrowserProps } from "./MountSourceBrowser";
export { MOUNT_MODEL } from "./documents";
export default storageIntegrate;
