import { ConnectMountAction } from "@angee/storage-integrate";
import * as React from "react";

/** Mount-toolbar action for provisioning an iPhone backup. */
export function ConnectIphoneBackupAction(): React.ReactElement {
  return <ConnectMountAction backendClass="iphone_backup" i18nPrefix="iphone.mount.connect" />;
}
