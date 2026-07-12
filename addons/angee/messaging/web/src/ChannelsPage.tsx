import * as React from "react";
import { Action, Column, ResourceList, Field, Form, Group, List, SlotOutlet, useRecordActionMutation, useSlot } from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useMessagingT } from "./i18n";
import { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "./slots";

const MODEL = "messaging.Channel";

/**
 * Connected message channels. Channels are created through bespoke connect flows
 * because a channel row and its credential must be authored together; once present,
 * the list/detail stay model-driven and sync rides the generic integration action.
 */
export function ChannelsPage(): React.ReactElement {
  const t = useMessagingT();
  const [sync] = useRecordActionMutation<ActionFieldName>("sync_integration");
  const toolbarEntries = useSlot(MESSAGING_CHANNEL_TOOLBAR_SLOT);
  return (
    <ResourceList resource={MODEL} placement="inline" routed hideCreate toolbarActions={<SlotOutlet entries={toolbarEntries} />}>
      <List resource={MODEL}>
        <Column field="display_name" header={t("channel.name")} />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        <Column field="backend_class" />
        <Column field="sync_stage" />
        <Column field="last_sync_status" />
        <Column field="last_sync_items" />
        <Column field="last_sync_completed_at" />
      </List>
      <Form resource={MODEL}>
        <Field name="display_name" title readOnly />
        <Field name="lifecycle" readOnly />
        <Field name="runtime_status" readOnly />
        <Field name="backend_class" readOnly />
        <Field name="config" readOnly />
        <Group label={t("channel.group.lastSync")} columns={2}>
          <Field name="is_syncing" readOnly />
          <Field name="sync_stage" readOnly />
          <Field name="sync_error" readOnly />
          <Field name="sync_progress" widget="json" readOnly />
          <Field name="last_sync_summary" widget="json" readOnly />
          <Field name="last_sync_status" readOnly />
          <Field name="last_sync_items" readOnly />
          <Field name="last_sync_completed_at" readOnly />
        </Group>
        <Action id="sync" label={t("channel.action.sync")} icon="refresh" run={sync} />
      </Form>
    </ResourceList>
  );
}
