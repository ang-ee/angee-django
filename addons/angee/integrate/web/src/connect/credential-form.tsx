import type { ActionFieldName } from "@angee/gql/console/actions";
import { useAuthoredMutation, type DocumentVariables } from "@angee/refine";
import {
  Action,
  Field,
  Form,
  Group,
  recordActionId,
  registerForm,
  useAuthoredResourceMutation,
  useRecordActionMutation,
  type ActionContext,
  type FormSubmit,
  type RegisteredFormProps,
} from "@angee/ui";
import * as React from "react";

import {
  INTEGRATE_CREATE_CREDENTIAL_INVALIDATES,
  IntegrateCreateCredential,
  IntegrateRevealCredential,
} from "./documents";
import { useIntegrateT } from "../i18n";

const MODEL = "integrate.Credential";

/**
 * The complete credential form used by the routed Credentials page and relation
 * picker inline creation. Create owns the kind-dispatched, write-only material;
 * edit owns lifecycle health and explicit refresh/reveal actions.
 */
export function CredentialForm({
  resource: _resource,
  ...props
}: RegisteredFormProps): React.ReactElement {
  const t = useIntegrateT();
  const isCreate = props.id == null;
  const [revealCredential] = useAuthoredMutation(IntegrateRevealCredential);
  const [refresh] = useRecordActionMutation<ActionFieldName>("refresh_credential", {
    defaultMessage: t("credentials.refresh.done"),
  });
  const [createCredential] = useAuthoredResourceMutation(IntegrateCreateCredential, {
    invalidateModels: INTEGRATE_CREATE_CREDENTIAL_INVALIDATES,
  });

  const reveal = React.useCallback(
    async (context: ActionContext) => {
      const id = recordActionId(context);
      if (!id) return;
      const result = await revealCredential({ id });
      const secret = result?.reveal_credential.secret ?? "";
      if (!secret) throw new Error(t("credentials.reveal.noSecret"));
      await context.prompt({
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

  const submitCredential = React.useCallback<FormSubmit>(
    async (data) => {
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

  return (
    <Form {...props} resource={MODEL} createSubmit={submitCredential}>
      {isCreate ? (
        <>
          <Field name="name" title placeholder={t("credentials.create.namePlaceholder")} />
          <Field
            name="kind"
            label={t("credentials.create.kind")}
            widget="select"
            options={[
              { value: "static_token", label: t("credentials.create.kind.staticToken") },
              { value: "ssh_key", label: t("credentials.create.kind.sshKey") },
            ]}
          />
          <Field
            name="apiKey"
            label={t("credentials.create.apiToken")}
            widget="text"
            kind="string"
            placeholder={t("credentials.create.apiTokenPlaceholder")}
            showWhen={(values) => values.kind === "static_token"}
          />
          <Field
            name="privateKey"
            label={t("credentials.create.privateKey")}
            widget="textarea"
            kind="string"
            placeholder={t("credentials.create.privateKeyPlaceholder")}
            showWhen={(values) => values.kind === "ssh_key"}
          />
        </>
      ) : (
        <>
          <Field name="display_name" title readOnly />
          <Field name="status" widget="statusbar" />
          <Group label={t("credentials.group.health")} columns={2}>
            <Field name="kind" readOnly />
            <Field name="expires_at" readOnly />
            <Field name="last_refresh_at" readOnly />
            <Field name="last_refresh_status" readOnly />
          </Group>
          <Action
            id="refresh"
            label={t("credentials.action.refresh")}
            run={refresh}
            visibleWhen={(record) => String(record.kind ?? "").toLowerCase() === "oauth"}
          />
          <Action
            id="reveal"
            label={t("credentials.action.reveal")}
            icon="eye"
            run={reveal}
          />
        </>
      )}
    </Form>
  );
}

export const credentialCreateForm = registerForm(MODEL, CredentialForm);
