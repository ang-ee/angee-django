import * as React from "react";
import { Column, List, ResourceList } from "@angee/ui";

import { FOLDER_MODEL, folderForm } from "./FolderForm";
import { usePartiesT } from "./i18n";

/**
 * Contact folders: the address books connected directories mirror. A directory's
 * sync creates each folder, so the page only browses them (`hideCreate`); the
 * folder's REBAC decides which rows a viewer lists.
 */
export function FoldersPage(): React.ReactElement {
  const t = usePartiesT();
  return (
    <ResourceList resource={FOLDER_MODEL} form={folderForm} placement="inline" routed hideCreate>
      <List resource={FOLDER_MODEL} emptyContent={t("folder.empty")}>
        <Column field="name" />
        <Column field="directory" />
        <Column field="updated_at" />
      </List>
    </ResourceList>
  );
}
