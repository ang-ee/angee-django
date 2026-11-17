import { ConnectMountAction, MOUNT_MODEL } from "@angee/storage-integrate";
import * as React from "react";

import { ConnectGoogleDrive } from "./documents";

/** Mount-toolbar action for provisioning a Google Drive folder, read with one of the user's Google accounts. */
export function ConnectGoogleDriveAction(): React.ReactElement {
  return (
    <ConnectMountAction
      mutationDocument={ConnectGoogleDrive}
      backendClass="google_drive"
      i18nPrefix="gdrive.mount.connect"
      invalidateModel={MOUNT_MODEL}
      requiresAccount
      choosesCopyBackend
    />
  );
}
