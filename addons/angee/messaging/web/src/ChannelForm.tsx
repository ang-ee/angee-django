import * as React from "react";
import { Field, Form, Group, registerForm, SlotOutlet, useSlot, type RegisteredFormProps } from "@angee/ui";
import { IntegrationSyncFields, useIntegrationSyncAction } from "@angee/integrate";

import { CHANNEL_MODEL } from "./documents";
import { useMessagingT } from "./i18n";
import { MESSAGING_CHANNEL_FORM_FIELDS_SLOT } from "./slots";

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
