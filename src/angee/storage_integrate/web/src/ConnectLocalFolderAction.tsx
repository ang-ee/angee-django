import * as React from "react";

import { ConnectMountAction } from "./ConnectMountAction";

/** Toolbar action for provisioning a local-folder Mount. */
export function ConnectLocalFolderAction(): React.ReactElement {
  return <ConnectMountAction backendClass="local_folder" i18nPrefix="mount.localFolder" />;
}
