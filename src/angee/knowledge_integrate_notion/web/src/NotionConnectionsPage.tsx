import type { ReactElement } from "react";
import { Column, List, ResourceList } from "@angee/ui";
import { IntegrationSyncColumns } from "@angee/integrate";

import { NOTION_CONNECTION_MODEL, notionConnectionForm } from "./NotionConnectionForm";

/** Use the native resource collection and record form for connection setup. */
export function NotionConnectionsPage(): ReactElement {
  return (
    <ResourceList resource={NOTION_CONNECTION_MODEL} form={notionConnectionForm} placement="inline" routed>
      <List resource={NOTION_CONNECTION_MODEL}>
        <Column field="display_name" />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="statusBadge" />
        {IntegrationSyncColumns()}
      </List>
    </ResourceList>
  );
}
