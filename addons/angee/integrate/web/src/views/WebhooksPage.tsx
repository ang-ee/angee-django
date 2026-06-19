import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Field,
  Form,
  Group,
  List,
  useRecordActionMutation,
  type ActionContext,
} from "@angee/base";
import { useAuthoredMutation } from "@angee/sdk";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useIntegrateT } from "../i18n";
import { RotateWebhookSecret } from "../documents";

const MODEL = "integrate.WebhookSubscription";

const webhookList = (
  <List model={MODEL}>
    <Column field="targetUrl" />
    <Column field="enabled" />
    <Column field="lastDeliveryStatus" />
  </List>
);

/** Outbound webhook subscriptions and their delivery operations. */
export function WebhooksPage(): React.ReactElement {
  const t = useIntegrateT();
  const [sendTest] = useRecordActionMutation<ActionFieldName>("testWebhookDelivery");
  const [rotateSecret] = useAuthoredMutation(RotateWebhookSecret);
  const rotate = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await rotateSecret({ id: ctx.record.id });
      const outcome = result?.rotateWebhookSecret;
      if (outcome && !outcome.ok)
        throw new Error(t("integrate.webhooks.rotateFailed"));
      const secret = outcome?.secret;
      if (secret) {
        await ctx.prompt({
          title: t("integrate.webhooks.newSecretTitle"),
          body: t("integrate.webhooks.newSecretBody"),
          fields: [
            {
              name: "secret",
              label: t("integrate.webhooks.signingSecret"),
              defaultValue: secret,
              readOnly: true,
            },
          ],
        });
      }
      return t("integrate.webhooks.rotated");
    },
    [rotateSecret, t],
  );

  return (
    <DataPage model={MODEL} placement="inline" routed>
      {webhookList}
      <Form model={MODEL}>
        <Field name="targetUrl" title />
        <Field name="enabled" />
        <Group label={t("integrate.webhooks.filters")} columns={2}>
          <Field name="eventKinds" widget="tagInput" />
          <Field name="implAppFilter" widget="tagInput" />
          <Field name="integrationFilter" />
        </Group>
        {/* Write-only signing key — set on create, never read back from the server. */}
        <Field name="secret" widget="text" kind="string" createOnly />
        <Action id="send-test" label={t("integrate.webhooks.sendTest")} run={sendTest} />
        <Action id="rotate-secret" label={t("integrate.webhooks.rotateSecret")} run={rotate} />
        <Action
          id="disable"
          label={t("integrate.action.disable")}
          danger
          set={{ enabled: false }}
          visibleWhen={(record) => record.enabled === true}
        />
        <Action
          id="enable"
          label={t("integrate.webhooks.enable")}
          set={{ enabled: true }}
          visibleWhen={(record) => record.enabled === false}
        />
      </Form>
    </DataPage>
  );
}
