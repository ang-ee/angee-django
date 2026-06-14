import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Field,
  Form,
  Group,
  List,
  type ActionContext,
} from "@angee/base";
import { runActionResult, useAuthoredMutation } from "@angee/sdk";

import {
  ROTATE_WEBHOOK_SECRET_MUTATION,
  TEST_WEBHOOK_DELIVERY_MUTATION,
  type IdVariables,
  type RotateWebhookSecretData,
  type TestWebhookDeliveryData,
} from "../documents";

const MODEL = "integrate.WebhookSubscription";

const webhookList = (
  <List model={MODEL} pageSize={50}>
    <Column field="targetUrl" />
    <Column field="enabled" />
    <Column field="lastDeliveryStatus" />
  </List>
);

/** Outbound webhook subscriptions and their delivery operations. */
export function WebhooksPage(): React.ReactElement {
  const [testDelivery] = useAuthoredMutation<TestWebhookDeliveryData, IdVariables>(
    TEST_WEBHOOK_DELIVERY_MUTATION,
  );
  const [rotateSecret] = useAuthoredMutation<RotateWebhookSecretData, IdVariables>(
    ROTATE_WEBHOOK_SECRET_MUTATION,
  );

  const sendTest = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await testDelivery({ id: ctx.record.id });
      ctx.refresh();
      return runActionResult(result?.testWebhookDelivery);
    },
    [testDelivery],
  );
  const rotate = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await rotateSecret({ id: ctx.record.id });
      const outcome = result?.rotateWebhookSecret;
      if (outcome && !outcome.ok) throw new Error("Could not rotate the signing secret.");
      const secret = outcome?.secret;
      if (secret) {
        await ctx.prompt({
          title: "New signing secret",
          body: "Copy this now — it is shown only once.",
          fields: [
            { name: "secret", label: "Signing secret", defaultValue: secret, readOnly: true },
          ],
        });
      }
      return "Signing secret rotated.";
    },
    [rotateSecret],
  );

  return (
    <DataPage model={MODEL} placement="inline" routed>
      {webhookList}
      <Form model={MODEL}>
        <Field name="targetUrl" title />
        <Field name="enabled" />
        <Group label="Filters" columns={2}>
          <Field name="eventKinds" widget="tagInput" />
          <Field name="implAppFilter" widget="tagInput" />
          <Field name="integrationFilter" />
        </Group>
        {/* Write-only signing key — set on create, never read back from the server. */}
        <Field name="secret" widget="text" kind="string" createOnly />
        <Action id="send-test" label="Send test event" run={sendTest} />
        <Action id="rotate-secret" label="Rotate secret" run={rotate} />
        <Action
          id="disable"
          label="Disable"
          danger
          set={{ enabled: false }}
          visibleWhen={(record) => record.enabled === true}
        />
        <Action
          id="enable"
          label="Enable"
          set={{ enabled: true }}
          visibleWhen={(record) => record.enabled === false}
        />
      </Form>
    </DataPage>
  );
}
