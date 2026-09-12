import * as React from "react";
import { Column, ResourceList, Field, Form, Group, List, SlotOutlet, registerForm, useSlot, type RegisteredFormProps } from "@angee/ui";
import { IntegrationSyncColumns, IntegrationSyncFields, useIntegrationSyncAction } from "@angee/integrate";

import { CHANNEL_MODEL } from "./documents";
import { useMessagingT } from "./i18n";
import { MESSAGING_CHANNEL_FORM_FIELDS_SLOT, MESSAGING_CHANNEL_TOOLBAR_SLOT } from "./slots";

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
      <SlotOutlet entries={toolbarEntries} />
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

function ChannelForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useMessagingT();
  const extensionFields = useSlot(MESSAGING_CHANNEL_FORM_FIELDS_SLOT);
  const syncAction = useIntegrationSyncAction("sync_integration", t("channel.action.sync"));
  return (
      <Form {...props} resource={CHANNEL_MODEL}>
        {/* The one channel fact a human owns; the rest of this form is runtime truth. */}
        <Field name="display_name" title />
        <Field name="lifecycle" readOnly />
        {/* Selected so the shared Resume verb can see a disconnected row still holds its login. */}
        <Field name="credential_status" readOnly />
        <Field name="runtime_status" widget="colorDot" readOnly />
        <Field name="backend_class" readOnly />
        <Field name="config" readOnly />
        <SlotOutlet entries={extensionFields} />
        <Group label={t("channel.group.webform")} columns={2}>
          <Field name="slug" widget="slug" showWhen={isWebformChannel} />
          <Field name="is_published" showWhen={isWebformChannel} />
          <Field name="form_schema_version" showWhen={isWebformChannel} />
          <Field name="max_body_bytes" showWhen={isWebformChannel} />
          <Field name="max_field_bytes" showWhen={isWebformChannel} />
          <Field name="form_schema" widget="json" showWhen={isWebformChannel} />
        </Group>
        {IntegrationSyncFields({ label: t("channel.group.lastSync") })}
        {syncAction}
      </Form>
  );
}

export const channelForm = registerForm(CHANNEL_MODEL, ChannelForm);

function isWebformChannel(values: Record<string, unknown>): boolean {
  return String(values.backend_class ?? "").toLowerCase() === "webform";
}
