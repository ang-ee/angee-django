import { defineBaseAddon } from "@angee/app";

import { ConnectGoogleDriveAction } from "./ConnectGoogleDriveAction";
import { enStorageIntegrateGoogleDriveMessages } from "./i18n";

const storageIntegrateGdrive = defineBaseAddon({
  id: "storage-integrate-gdrive",
  // The connect copy is read by the shared Mount action, in storage-integrate's namespace.
  i18n: { "storage-integrate": enStorageIntegrateGoogleDriveMessages },
  containers: {
    "storage-integrate.mounts#toolbar": {
      "storage-integrate-gdrive.connect": { sequence: 30, content: <ConnectGoogleDriveAction /> },
    },
  },
});

export { ConnectGoogleDriveAction } from "./ConnectGoogleDriveAction";
export default storageIntegrateGdrive;
