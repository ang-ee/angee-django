import { defineBaseAddon } from "@angee/app";

import { ConnectIphoneBackupAction } from "./ConnectIphoneBackupAction";
import { enStorageIntegrateIphoneMessages } from "./i18n";

const storageIntegrateIphone = defineBaseAddon({
  id: "storage-integrate-iphone",
  // The connect copy is read by the shared Mount action, in storage-integrate's namespace.
  i18n: { "storage-integrate": enStorageIntegrateIphoneMessages },
  containers: {
    "storage-integrate.mounts#toolbar": {
      "storage-integrate-iphone.connect": { sequence: 20, content: <ConnectIphoneBackupAction /> },
    },
  },
});

export { ConnectIphoneBackupAction } from "./ConnectIphoneBackupAction";
export default storageIntegrateIphone;
