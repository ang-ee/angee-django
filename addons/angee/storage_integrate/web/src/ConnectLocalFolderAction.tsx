import * as React from "react";

import { ConnectMountAction } from "./ConnectMountAction";
import { ConnectLocalFolder, MOUNT_MODEL } from "./documents";

/** Toolbar action for provisioning a local-folder Mount. */
export function ConnectLocalFolderAction(): React.ReactElement {
  return (
    <ConnectMountAction
      mutationDocument={ConnectLocalFolder}
      backendClass="local_folder"
      i18nPrefix="mount.connect"
      idPrefix="mount-local-folder"
      invalidateModel={MOUNT_MODEL}
    />
  );
}
