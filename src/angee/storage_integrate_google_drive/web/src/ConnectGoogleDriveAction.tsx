import { ConnectMountAction } from "@angee/storage-integrate";
import * as React from "react";

/** Mount-toolbar action for provisioning a Google Drive folder, read with one of the user's Google accounts. */
export function ConnectGoogleDriveAction(): React.ReactElement {
  return <ConnectMountAction backendClass="google_drive" i18nPrefix="googleDrive.mount.connect" />;
}
