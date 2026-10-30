import * as React from "react";
import { ActionMenu, Column, ContainerOutlet, ResourceList, List, containerHasContent, useContainer } from "@angee/ui";
import { IntegrationSyncColumns } from "@angee/integrate";

import { directoryForm } from "./DirectoryForm";
import { usePartiesT } from "./i18n";

const MODEL = "parties.Directory";

/**
 * Connected contacts directories. Connect verbs come from the
 * `parties.directories#toolbar` container (this addon contributes CardDAV; vendor
 * addons add theirs) and render as one Connect menu; rows are model-driven via
 * ResourceList, each detail carrying a declarative "Sync now" record action.
 * Directories are created through the connect flow and have no delete root, so the
 * form is read-only (`hideCreate`) and no delete affordance renders — a directory
 * is removed by deleting the integration, and its synced contacts by the source.
 */
export function DirectoriesPage(): React.ReactElement {
  const t = usePartiesT();
  const toolbarEntries = useContainer("parties.directories#toolbar");
  return (
    <ResourceList resource={MODEL} form={directoryForm} placement="inline" routed hideCreate toolbarActions={
      containerHasContent(toolbarEntries) ? (
        <ActionMenu label={t("directory.connect.menu")} glyph="plus" variant="primary" size="sm">
          <ContainerOutlet entries={toolbarEntries} />
        </ActionMenu>
      ) : null
    }>
      <List resource={MODEL}>
        <Column field="display_name" header={t("directory.name")} />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="statusBadge" />
        <Column field="backend_class" />
        {IntegrationSyncColumns()}
      </List>
    </ResourceList>
  );
}
