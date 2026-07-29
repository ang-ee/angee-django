import { useAuthoredMutation } from "@angee/refine";
import * as React from "react";
import { Action, Column, ResourceList, Field, Form, Group, List, recordActionId, useAuthoredResourceMutation, useRecordActionMutation, type ActionContext, type FormSubmit } from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";
import type { DocumentVariables } from "@angee/refine";

import { useIntegrateT } from "../../i18n";
import {
  INTEGRATE_CREATE_CREDENTIAL_INVALIDATES,
  IntegrateCreateCredential,
  IntegrateRevealCredential,
} from "../documents";

const MODEL = "Credential";

const credentialList = (
  <List resource={MODEL}>
    <Column field="display_name" />
    <Column field="kind" />
    <Column field="status" widget="statusBadge" />
    <Column field="expires_at" />
    <Column field="last_refresh_at" />
  </List>
);

/** Per-user credential health (list / status / reveal); create via the form override. */
export function CredentialsPage(): React.ReactElement {
  const t = useIntegrateT();
  const [revealCredential] = useAuthoredMutation(IntegrateRevealCredential);

  // Force an OAuth credential to renew its access token now (the lazy on-use refresh
  // only fires when a consumer touches it). Single-id `{ ok, message }` action: the
  // helper toasts the outcome and re-pulls the row so the health fields update.
  const [refresh] = useRecordActionMutation<ActionFieldName>("refresh_credential", {
    defaultMessage: t("credentials.refresh.done"),
  });

  // The secret is never in the credential's read projection; this fetches and
  // decrypts it server-side on explicit admin request, then shows it once for copy.
  const reveal = React.useCallback(
    async (ctx: ActionContext) => {
      const id = recordActionId(ctx);
      if (!id) return;
      const result = await revealCredential({ id });
      const secret = result?.reveal_credential.secret ?? "";
      if (!secret) {
        throw new Error(t("credentials.reveal.noSecret"));
      }
      await ctx.prompt({
        title: t("credentials.reveal.title"),
        body: t("credentials.reveal.body"),
        fields: [
          {
            name: "secret",
            label: t("credentials.reveal.secretLabel"),
            defaultValue: secret,
            readOnly: true,
          },
        ],
      });
    },
    [revealCredential, t],
  );

  // Credentials have no auto-CRUD insert root, so create saves through the
  // `create_credential` mutation that owns kind-dispatched material. `createSubmit`
  // is create-only: editing keeps the stock update path for `status`.
  const [createCredential] = useAuthoredResourceMutation(IntegrateCreateCredential, {
    invalidateModels: INTEGRATE_CREATE_CREDENTIAL_INVALIDATES,
  });
  const submitCredential = React.useCallback<FormSubmit>(
    async (data) => {
      // `data` carries the override form's field names; map the write-only
      // material fields onto the discriminated `CredentialInput` keys. The cast
      // bridges FormSubmit's untyped payload to the typed document variables.
      const variables = {
        data: {
          name: String(data.name ?? ""),
          kind: String(data.kind ?? ""),
          api_key: String(data.apiKey ?? ""),
          private_key: String(data.privateKey ?? ""),
        },
      } as DocumentVariables<typeof IntegrateCreateCredential>;
      const result = await createCredential(variables);
      return result?.create_credential ?? null;
    },
    [createCredential],
  );

  // Create uses the addon-registered `Credential` form override (a kind dropdown
  // that swaps the material field); this declared form is the lifecycle editor
  // (status / reveal / health) the detail shows on edit.
  const credentialForm = (
    <Form resource={MODEL} createSubmit={submitCredential}>
      <Field name="display_name" title readOnly />
      <Field name="status" widget="statusbar" />
      <Group label={t("credentials.group.health")} columns={2}>
        <Field name="kind" readOnly />
        <Field name="expires_at" readOnly />
        <Field name="last_refresh_at" readOnly />
        <Field name="last_refresh_status" readOnly />
      </Group>
      {/* Only OAuth credentials carry a refresh token; static/ssh/basic kinds cannot renew. */}
      <Action
        id="refresh"
        label={t("credentials.action.refresh")}
        run={refresh}
        visibleWhen={(record) => String(record.kind ?? "").toLowerCase() === "oauth"}
      />
      <Action id="reveal" label={t("credentials.action.reveal")} icon="eye" run={reveal} />
    </Form>
  );

  return (
    <ResourceList resource={MODEL} placement="inline" routed>
      {credentialList}
      {credentialForm}
    </ResourceList>
  );
}
