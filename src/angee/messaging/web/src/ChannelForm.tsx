import * as React from "react";
import { Field, Form, registerForm, type RegisteredFormProps } from "@angee/ui";
import { IntegrationSyncFields, useIntegrationSyncAction } from "@angee/integrate";

import { CHANNEL_MODEL } from "./documents";
import { useMessagingT } from "./i18n";

function ChannelForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useMessagingT();
  const syncAction = useIntegrationSyncAction("sync_integration", t("channel.action.sync"));
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
      {IntegrationSyncFields({ label: t("channel.group.lastSync") })}
      {syncAction}
    </Form>
  );
}

export const channelForm = registerForm(CHANNEL_MODEL, ChannelForm);
