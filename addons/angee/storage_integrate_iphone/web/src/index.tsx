import { defineBaseAddon } from "@angee/app";
import { STORAGE_MOUNT_TOOLBAR_SLOT } from "@angee/storage-integrate";

import { ConnectIphoneBackupAction } from "./ConnectIphoneBackupAction";
import { enStorageIntegrateIphoneMessages } from "./i18n";

const storageIntegrateIphone = defineBaseAddon({
  id: "storage-integrate-iphone",
  i18n: { storage: enStorageIntegrateIphoneMessages },
  slots: [
    {
      slot: STORAGE_MOUNT_TOOLBAR_SLOT,
      id: "storage-integrate-iphone.connect",
      sequence: 20,
      content: <ConnectIphoneBackupAction />,
    },
  ],
});

export { ConnectIphoneBackupAction } from "./ConnectIphoneBackupAction";
export default storageIntegrateIphone;
