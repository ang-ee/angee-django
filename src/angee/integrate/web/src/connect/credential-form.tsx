import type { ActionFieldName } from "@angee/gql/console/actions";
import type { Row } from "@angee/metadata";
import { useAuthoredMutation } from "@angee/refine";
import {
  savedFormSubmitResult, useUiT, Action,
  Field,
  Form,
  Group,
  registerForm,
  useSecretReveal,
  useAuthoredResourceMutation,
  useActionOutcomeMutation,
  useRecordAction,
  useRecordActionMutation,
  type FormSubmit,
  type FieldDescriptor,
  type ActionDescriptor,
  type ActionScalarArg,
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

interface MaterialPresentation {
  widget: "text" | "password" | "textarea";
  secret: boolean;
  label: string;
  placeholder?: string;
  createKind?: string;
}

// Presentation only: replacement eligibility and required fields come from the server.
const MATERIAL_FIELDS: Readonly<Record<string, MaterialPresentation>> = {
  api_key: { widget: "password", secret: true, label: "credentials.create.apiToken", placeholder: "credentials.create.apiTokenPlaceholder", createKind: "static_token" },
  private_key: { widget: "textarea", secret: true, label: "credentials.create.privateKey", placeholder: "credentials.create.privateKeyPlaceholder", createKind: "ssh_key" },
  key_file: { widget: "textarea", secret: true, label: "credentials.create.keyFile", placeholder: "credentials.create.keyFilePlaceholder", createKind: "jwt_bearer" },
  username: { widget: "text", secret: false, label: "credentials.create.username" },
  password: { widget: "password", secret: true, label: "credentials.create.password" },
  app_id: { widget: "text", secret: false, label: "credentials.create.appId" },
  app_secret: { widget: "password", secret: true, label: "credentials.create.appSecret" },
};

function materialField(name: string, t: ReturnType<typeof useIntegrateT>): ActionScalarArg {
  const field = MATERIAL_FIELDS[name];
  if (!field) throw new Error("Unknown credential material field.");
  return {
    name, kind: "string", widget: field.widget, label: t(field.label),
    ...(field.placeholder ? { placeholder: t(field.placeholder) } : {}),
    ...(field.secret ? { autoComplete: "new-password" } : {}),
  };
}

function replacementFields(record: Row | null): string[] {
  return Array.isArray(record?.replacement_fields)
    ? record.replacement_fields.filter((field): field is string => typeof field === "string") : [];
}

/** Resolve field editability from the credential owner's projection, never from an unsaved kind. */
const credentialNameField: NonNullable<FieldDescriptor["resolve"]> = (record) => ({
  name: "name", readOnly: !Array.isArray(record.editable_fields) || !record.editable_fields.includes("name"),
});

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
  // The entered secret leaves the mutation cache once the create settles.
  const [createCredential] = useAuthoredResourceMutation(IntegrateCreateCredential, {
    invalidateModels: INTEGRATE_CREATE_CREDENTIAL_INVALIDATES,
    transient: true, errorNotification: false,
  });
  const [replaceSecret] = useActionOutcomeMutation<ActionFieldName>("replace_credential_secret", {
    transient: true, errorNotification: false, invalidateModels: [MODEL],
  });
  const replace: ActionDescriptor = {
    id: "replace-secret", placement: "menu", label: t("credentials.action.replaceSecret"), danger: true,
    confirm: { title: t("credentials.replace.confirmTitle"), body: t("credentials.replace.confirmBody"), danger: true },
    visibleWhen: (record) => replacementFields(record).length > 0,
    args: ({ record }) => replacementFields(record).map((name) => materialField(name, t)),
    submit: async (values, { record }) => replaceSecret(String(record?.id ?? ""), {
      confirmed: true,
      data: Object.fromEntries(replacementFields(record).map((name) => [name, String(values[name] ?? "")])),
    }),
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
      const result = await createCredential({
        data: {
          name: String(data.name ?? ""),
          kind: String(data.kind ?? ""),
          api_key: String(data.api_key ?? ""),
          private_key: String(data.private_key ?? ""),
          key_file: String(data.key_file ?? ""),
        },
      });
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
          {Object.entries(MATERIAL_FIELDS).filter(([, field]) => field.createKind).map(([name, field]) => (
            <Field key={name} {...materialField(name, t)} showWhen={(values) => values.kind === field.createKind} />
          ))}
        </>
      ) : (
        <>
          <Field name="display_name" title readOnly />
          <Field name="editable_fields" hidden readOnly />
          <Field name="replacement_fields" hidden readOnly />
          <Field name="status" widget="statusbar" status />
          <Group label={t("credentials.group.health")} columns={2}>
            <Field name="name" resolve={credentialNameField} />
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
