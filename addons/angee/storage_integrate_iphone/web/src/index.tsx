import { defineBaseAddon } from "@angee/app";

import { ConnectIphoneBackupAction } from "./ConnectIphoneBackupAction";
import { enStorageIntegrateIphoneMessages } from "./i18n";

const storageIntegrateIphone = defineBaseAddon({
  id: "storage-integrate-iphone",
  i18n: { storage: enStorageIntegrateIphoneMessages },
  containers: {
    "storage-integrate.mounts#toolbar": {
      "storage-integrate-iphone.connect": { sequence: 20, content: <ConnectIphoneBackupAction /> },
    },
  },
});

export { ConnectIphoneBackupAction } from "./ConnectIphoneBackupAction";
export default storageIntegrateIphone;
