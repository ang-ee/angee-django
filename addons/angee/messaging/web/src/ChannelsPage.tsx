import * as React from "react";
import { Action, Column, ResourceList, Field, Form, Group, List, SlotOutlet, registerForm, useRecordActionMutation, useSlot, type RegisteredFormProps } from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";

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
    <ResourceList resource={CHANNEL_MODEL} form={channelForm} placement="inline" routed hideCreate toolbarActions={<SlotOutlet entries={toolbarEntries} />}>
      <List resource={CHANNEL_MODEL}>
        <Column field="display_name" header={t("channel.name")} />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        <Column field="backend_class" />
        <Column field="sync_stage" />
        <Column field="last_sync_status" />
        <Column field="last_sync_items" />
        <Column field="last_sync_completed_at" />
      </List>
    </ResourceList>
  );
}

function ChannelForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useMessagingT();
  const [sync] = useRecordActionMutation<ActionFieldName>("sync_integration");
  return (
      <Form {...props} resource={CHANNEL_MODEL}>
        {/* The one channel fact a human owns; the rest of this form is runtime truth. */}
        <Field name="display_name" title />
        <Field name="lifecycle" readOnly />
        {/* Selected so the shared Resume verb can see a disconnected row still holds its login. */}
        <Field name="credential_status" readOnly />
        <Field name="runtime_status" readOnly />
        <Field name="backend_class" readOnly />
        <Field name="config" readOnly />
        <Group label={t("channel.group.webform")} columns={2}>
          <Field name="slug" widget="slug" showWhen={isWebformChannel} />
          <Field name="is_published" showWhen={isWebformChannel} />
          <Field name="form_schema_version" showWhen={isWebformChannel} />
          <Field name="max_body_bytes" showWhen={isWebformChannel} />
          <Field name="max_field_bytes" showWhen={isWebformChannel} />
          <Field name="form_schema" widget="json" showWhen={isWebformChannel} />
        </Group>
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
  );
}

export const channelForm = registerForm(CHANNEL_MODEL, ChannelForm);

function isWebformChannel(values: Record<string, unknown>): boolean {
  return String(values.backend_class ?? "").toLowerCase() === "webform";
}
