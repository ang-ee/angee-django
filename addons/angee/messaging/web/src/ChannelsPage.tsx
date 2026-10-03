import * as React from "react";
import { Column, ResourceList, List, ContainerOutlet, useContainer } from "@angee/ui";
import { IntegrationSyncColumns } from "@angee/integrate";

import { channelForm } from "./ChannelForm";
import { CHANNEL_MODEL } from "./documents";
import { useMessagingT } from "./i18n";

/**
 * Connected message channels. Channels are created through bespoke connect flows
 * because a channel row and its credential must be authored together; once present,
 * the list/detail stay model-driven and sync rides the generic integration action.
 */
export function ChannelsPage(): React.ReactElement {
  const t = useMessagingT();
  const toolbarEntries = useContainer("messaging.channels#toolbar");
  return (
    <ResourceList resource={CHANNEL_MODEL} form={channelForm} placement="inline" routed hideCreate toolbarActions={
      <ContainerOutlet entries={toolbarEntries} />
    }>
      <List resource={CHANNEL_MODEL}>
        <Column field="display_name" header={t("channel.name")} />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        <Column field="backend_class" />
        {IntegrationSyncColumns()}
      </List>
    </ResourceList>
  );
}
