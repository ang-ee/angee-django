import * as React from "react";
import { ActionMenu, Column, ResourceList, List, SlotOutlet, useSlot } from "@angee/ui";
import { IntegrationSyncColumns } from "@angee/integrate";

import { channelForm } from "./ChannelForm";
import { CHANNEL_MODEL } from "./documents";
import { useMessagingT } from "./i18n";
import { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "./slots";

/**
 * Connected message channels. Channels are created through bespoke connect flows
 * because a channel row and its credential must be authored together; once present,
 * the list/detail stay model-driven and sync rides the generic integration action.
 */
export function ChannelsPage(): React.ReactElement {
  const t = useMessagingT();
  const toolbarEntries = useSlot(MESSAGING_CHANNEL_TOOLBAR_SLOT);
  return (
    <ResourceList resource={CHANNEL_MODEL} form={channelForm} placement="inline" routed hideCreate toolbarActions={
      toolbarEntries.length > 0 ? (
        <ActionMenu label={t("channel.connect.menu")} glyph="plus" variant="primary" size="sm">
          <SlotOutlet entries={toolbarEntries} />
        </ActionMenu>
      ) : null
    }>
      <List resource={CHANNEL_MODEL} defaultGroups={{ list: { field: "backend_class" } }}>
        <Column field="display_name" header={t("channel.name")} />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        <Column field="backend_class" />
        {IntegrationSyncColumns()}
      </List>
    </ResourceList>
  );
}
