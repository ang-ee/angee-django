import * as React from "react";
import {
  Action,
  Column,
  ResourceList,
  Field,
  Form,
  Group,
  List,
  recordActionId,
  useRecordActionMutation,
  useAuthoredMutation,
  type ActionContext,
} from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useIntegrateT } from "../i18n";
import { RotateWebhookSecret } from "../documents";

const MODEL = "integrate.WebhookSubscription";

const webhookList = (
  <List resource={MODEL}>
    <Column field="target_url" />
    <Column field="enabled" />
    <Column field="last_delivery_status" />
  </List>
);

/** Outbound webhook subscriptions and their delivery operations. */
export function WebhooksPage(): React.ReactElement {
  const t = useIntegrateT();
  const [sendTest] = useRecordActionMutation<ActionFieldName>("test_webhook_delivery");
  const [rotateSecret] = useAuthoredMutation(RotateWebhookSecret);
  const rotate = React.useCallback(
    async (ctx: ActionContext) => {
      const id = recordActionId(ctx);
      if (!id) return;
      const result = await rotateSecret({ id });
      const outcome = result?.rotate_webhook_secret;
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
    <ResourceList resource={MODEL} placement="inline" routed>
      {webhookList}
      <Form resource={MODEL}>
        <Field name="target_url" title />
        <Field name="enabled" />
        <Group label={t("integrate.webhooks.filters")} columns={2}>
          <Field name="event_kinds" widget="tagInput" />
          <Field name="impl_app_filter" widget="tagInput" />
          <Field name="integration_filter" />
        </Group>
        {/* Write-only signing key — set on create, never read back from the server. */}
        <Field name="secret" widget="text" kind="string" createOnly />
        <Action id="send-test" label={t("integrate.webhooks.sendTest")} run={sendTest} />
        <Action id="rotate-secret" label={t("integrate.webhooks.rotateSecret")} run={rotate} />
      </Form>
    </ResourceList>
  );
}
