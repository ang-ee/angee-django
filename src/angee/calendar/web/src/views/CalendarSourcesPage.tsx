import * as React from "react";
import { ActionMenu, Column, ContainerOutlet, List, ResourceList, containerHasContent, useContainer } from "@angee/ui";
import { IntegrationSyncColumns } from "@angee/integrate";

import { useCalendarT } from "../i18n";
import { SOURCE_MODEL, SOURCES_TOOLBAR } from "../resources";
import { calendarSourceForm } from "./CalendarSourceForm";

/**
 * Connected calendar sources. Subscribe and connect verbs come from the
 * `calendar.sources#toolbar` container (source addons contribute theirs, such as
 * the ICS feed subscription) and render as one menu; a source is created through
 * its verb, so the form is read-only and carries the "Sync now" action; pausing and
 * disconnecting are integrate's operator verbs.
 */
export function CalendarSourcesPage(): React.ReactElement {
  const t = useCalendarT();
  const toolbarEntries = useContainer(SOURCES_TOOLBAR);
  return (
    <ResourceList resource={SOURCE_MODEL} form={calendarSourceForm} placement="inline" routed hideCreate toolbarActions={
      containerHasContent(toolbarEntries) ? (
        <ActionMenu label={t("source.connect.menu")} glyph="plus" variant="primary" size="sm">
          <ContainerOutlet entries={toolbarEntries} />
        </ActionMenu>
      ) : null
    }>
      <List resource={SOURCE_MODEL}>
        <Column field="display_name" header={t("source.name")} />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="statusBadge" />
        <Column field="backend_class" />
        {IntegrationSyncColumns()}
      </List>
    </ResourceList>
  );
}
