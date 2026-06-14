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
import { useAuthoredMutation } from "@angee/sdk";

import {
  REVEAL_CREDENTIAL_MUTATION,
  type RevealCredentialData,
  type RevealCredentialVariables,
} from "../documents";

const MODEL = "Credential";

const credentialList = (
  <List model={MODEL} pageSize={50}>
    <Column field="displayName" />
    <Column field="kind" />
    <Column field="status" widget="statusBadge" />
    <Column field="expiresAt" />
    <Column field="lastRefreshAt" />
  </List>
);

/** Per-user credential health (list / status / revoke / reveal); create via the form override. */
export function CredentialsPage(): React.ReactElement {
  const [revealCredential] = useAuthoredMutation<
    RevealCredentialData,
    RevealCredentialVariables
  >(REVEAL_CREDENTIAL_MUTATION);

  // The secret is never in the credential's read projection; this fetches and
  // decrypts it server-side on explicit admin request, then shows it once for copy.
  const reveal = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await revealCredential({ id: ctx.record.id });
      const secret = result?.revealCredential.secret ?? "";
      if (!secret) {
        throw new Error("This credential has no stored secret to reveal.");
      }
      await ctx.prompt({
        title: "Credential secret",
        body: "Copy it now — it is shown on request only and never kept in the form.",
        fields: [{ name: "secret", label: "Secret", defaultValue: secret, readOnly: true }],
        confirm: "Done",
      });
    },
    [revealCredential],
  );

  // Create uses the addon-registered `Credential` form override (a kind dropdown
  // that swaps the material field); this declared form is the lifecycle editor
  // (status / revoke / reveal / health) the detail shows on edit.
  const credentialForm = (
    <Form model={MODEL}>
      <Field name="displayName" title readOnly />
      <Field name="status" widget="statusbar" />
      <Group label="Health" columns={2}>
        <Field name="kind" readOnly />
        <Field name="expiresAt" readOnly />
        <Field name="lastRefreshAt" readOnly />
        <Field name="lastRefreshStatus" readOnly />
      </Group>
      <Action id="reveal" label="Reveal secret" icon="eye" run={reveal} />
      <Action
        id="revoke"
        label="Revoke"
        danger
        set={{ status: "revoked" }}
        confirm={{
          title: "Revoke this credential?",
          body: "Anything using it to authenticate will stop working.",
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
