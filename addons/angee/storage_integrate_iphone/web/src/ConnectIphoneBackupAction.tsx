import { ConnectMountAction, MOUNT_MODEL } from "@angee/storage-integrate";
import * as React from "react";

import { ConnectIphoneBackup } from "./documents";

/** Mount-toolbar action for provisioning an iPhone backup. */
export function ConnectIphoneBackupAction(): React.ReactElement {
  return (
    <ConnectMountAction
      mutationDocument={ConnectIphoneBackup}
      backendClass="iphone_backup"
      i18nPrefix="iphone.mount.connect"
      idPrefix="mount-iphone-backup"
      invalidateModel={MOUNT_MODEL}
    />
  );
}
