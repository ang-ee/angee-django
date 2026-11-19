import { actionDocuments, type ActionFieldName } from "@angee/gql/console/actions";
import type { Row } from "@angee/metadata";
import { extractActionOutcome, useAuthoredMutation, type DocumentVariables } from "@angee/refine";
import {
  savedFormSubmitResult, useUiT, Action,
  Field,
  Form,
  Group,
  registerForm,
  useSecretReveal,
  useAuthoredResourceMutation,
  useRecordAction,
  useRecordActionMutation,
  type FormSubmit,
  type FieldDescriptor,
  type ActionDescriptor,
  type RecordActionRunner,
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

function replacementFields(record: Row | null): string[] {
  return Array.isArray(record?.replacement_fields)
    ? record.replacement_fields.filter((field): field is string => typeof field === "string") : [];
}

/** Resolve field editability from the credential owner's projection, never from an unsaved kind. */
function credentialMetadataField(name: string): NonNullable<FieldDescriptor["resolve"]> {
  return (record) => ({
    name, readOnly: !Array.isArray(record.editable_fields) || !record.editable_fields.includes(name),
  });
}

/**
 * The complete credential form used by the routed Credentials page and relation
 * picker inline creation. Create owns the kind-dispatched, write-only material;
 * edit owns manual metadata, lifecycle health and refresh/reveal/replacement actions.
 */
export function CredentialForm({
  resource: _resource,
  ...props
}: RegisteredFormProps): React.ReactElement {
  const uiT = useUiT();
  const t = useIntegrateT();
  const isCreate = props.id == null;
  const [revealCredential] = useAuthoredMutation(IntegrateRevealCredential, { transient: true });
  const [refresh] = useRecordActionMutation<ActionFieldName>("refresh_credential", {
    defaultMessage: t("credentials.refresh.done"),
  });
  const [createCredential] = useAuthoredResourceMutation(IntegrateCreateCredential, {
    invalidateModels: INTEGRATE_CREATE_CREDENTIAL_INVALIDATES,
    transient: true, errorNotification: false,
  });
  const [replaceSecret] = useAuthoredResourceMutation(actionDocuments.replace_credential_secret, {
    dataProviderName: "console", transient: true, errorNotification: false, invalidateModels: [MODEL],
    shouldInvalidate: (data) => data?.replace_credential_secret.ok === true,
  });
  const replace: ActionDescriptor = {
    id: "replace-secret", placement: "menu", label: t("credentials.action.replaceSecret"), danger: true,
    confirm: { title: t("credentials.replace.confirmTitle"), body: t("credentials.replace.confirmBody"), danger: true },
    visibleWhen: (record) => replacementFields(record).length > 0,
    args: ({ record }) => replacementFields(record).map((name) => ({
      name, label: t(`credentials.material.${name}`), widget: "password", optional: name === "api_key",
    })),
    submit: async (values, { record }) => extractActionOutcome(await replaceSecret({
      id: String(record?.id ?? ""), confirmed: true,
      data: Object.fromEntries(replacementFields(record).map((name) => [name, String(values[name] ?? "")])),
    }), "replace_credential_secret"),
  };
  const revealSecret = useSecretReveal();

  const revealById = React.useCallback<RecordActionRunner>(
    async (id) => {
      const result = await revealCredential({ id });
      const secret = result?.reveal_credential.secret ?? "";
      if (!secret) throw new Error(t("credentials.reveal.noSecret"));
      await revealSecret({
        title: t("credentials.reveal.title"),
        body: t("credentials.reveal.body"),
        secrets: [{ name: "secret", label: t("credentials.reveal.secretLabel"), value: secret }],
      });
    },
    [revealCredential, revealSecret, t],
  );
  const reveal = useRecordAction(revealById, { refresh: false });

  const submitCredential = React.useCallback<FormSubmit>(
    async (data) => {
      const variables = {
        data: {
          name: String(data.name ?? ""),
          kind: String(data.kind ?? ""),
          api_key: String(data.api_key ?? ""),
          private_key: String(data.private_key ?? ""),
          key_file: String(data.key_file ?? ""),
        },
      } as DocumentVariables<typeof IntegrateCreateCredential>;
      const result = await createCredential(variables);
      return savedFormSubmitResult(result?.create_credential, uiT("form.genericSaveError"));
    },
    [createCredential, uiT],
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
              { value: "jwt_bearer", label: t("credentials.create.kind.jwtBearer") },
            ]}
          />
          <Field
            name="api_key"
            label={t("credentials.create.apiToken")}
            widget="password"
            kind="string"
            placeholder={t("credentials.create.apiTokenPlaceholder")}
            showWhen={(values) => values.kind === "static_token"}
          />
          <Field
            name="private_key"
            label={t("credentials.create.privateKey")}
            widget="textarea"
            kind="string"
            placeholder={t("credentials.create.privateKeyPlaceholder")}
            showWhen={(values) => values.kind === "ssh_key"}
          />
          <Field
            name="key_file"
            label={t("credentials.create.keyFile")}
            widget="textarea"
            kind="string"
            placeholder={t("credentials.create.keyFilePlaceholder")}
            showWhen={(values) => values.kind === "jwt_bearer"}
          />
        </>
      ) : (
        <>
          <Field name="display_name" title readOnly />
          <Field name="editable_fields" hidden readOnly />
          <Field name="replacement_fields" hidden readOnly />
          <Field name="status" widget="statusbar" status />
          <Group label={t("credentials.group.health")} columns={2}>
            <Field name="name" resolve={credentialMetadataField("name")} />
            <Field name="kind" readOnly />
            <Field name="expires_at" resolve={credentialMetadataField("expires_at")} />
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
            placement="menu"
            label={t("credentials.action.reveal")}
            icon="eye"
            run={reveal}
          />
          <Action {...replace} />
        </>
      )}
    </Form>
  );
}

export const credentialCreateForm = registerForm(MODEL, CredentialForm);
