import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Field,
  Form,
  Group,
  List,
  recordActionId,
  type ActionContext,
} from "@angee/base";
import { useAuthoredMutation } from "@angee/sdk";

import { useIntegrateT } from "../../i18n";
import { IntegrateRevealCredential } from "../documents.console";

const MODEL = "Credential";

const credentialList = (
  <List model={MODEL}>
    <Column field="displayName" />
    <Column field="kind" />
    <Column field="status" widget="statusBadge" />
    <Column field="expiresAt" />
    <Column field="lastRefreshAt" />
  </List>
);

/** Per-user credential health (list / status / revoke / reveal); create via the form override. */
export function CredentialsPage(): React.ReactElement {
  const t = useIntegrateT();
  const [revealCredential] = useAuthoredMutation(IntegrateRevealCredential);

  // The secret is never in the credential's read projection; this fetches and
  // decrypts it server-side on explicit admin request, then shows it once for copy.
  const reveal = React.useCallback(
    async (ctx: ActionContext) => {
      const id = recordActionId(ctx);
      if (!id) return;
      const result = await revealCredential({ id });
      const secret = result?.revealCredential.secret ?? "";
      if (!secret) {
        throw new Error(t("integrate.credentials.reveal.noSecret"));
      }
      await ctx.prompt({
        title: t("integrate.credentials.reveal.title"),
        body: t("integrate.credentials.reveal.body"),
        fields: [
          {
            name: "secret",
            label: t("integrate.credentials.reveal.secretLabel"),
            defaultValue: secret,
            readOnly: true,
          },
        ],
      });
    },
    [revealCredential, t],
  );

  // Create uses the addon-registered `Credential` form override (a kind dropdown
  // that swaps the material field); this declared form is the lifecycle editor
  // (status / revoke / reveal / health) the detail shows on edit.
  const credentialForm = (
    <Form model={MODEL}>
      <Field name="displayName" title readOnly />
      <Field name="status" widget="statusbar" />
      <Group label={t("integrate.credentials.group.health")} columns={2}>
        <Field name="kind" readOnly />
        <Field name="expiresAt" readOnly />
        <Field name="lastRefreshAt" readOnly />
        <Field name="lastRefreshStatus" readOnly />
      </Group>
      <Action id="reveal" label={t("integrate.credentials.action.reveal")} icon="eye" run={reveal} />
      <Action
        id="revoke"
        label={t("integrate.revoke")}
        danger
        set={{ status: "revoked" }}
        confirm={{
          title: t("integrate.credentials.revoke.title"),
          body: t("integrate.credentials.revoke.body"),
          danger: true,
        }}
        visibleWhen={(record) =>
          String(record.status ?? "").toUpperCase() !== "REVOKED"
        }
      />
    </Form>
  );

  return (
    <DataPage model={MODEL} placement="inline" routed>
      {credentialList}
      {credentialForm}
    </DataPage>
  );
}
