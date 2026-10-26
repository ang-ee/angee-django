import * as React from "react";
import { Action, Field, Form, Group, registerForm, useRecordActionMutation, useEnumOptions, useImplPrefill, type RegisteredFormProps } from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useAgentsT } from "../i18n";

const PROVIDER_MODEL = "agents.InferenceProvider";
const MODEL_MODEL = "agents.InferenceModel";

function InferenceProviderForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useAgentsT();
  const [refreshModels] = useRecordActionMutation<ActionFieldName>(
    "refresh_provider_models",
    { invalidateModels: [MODEL_MODEL] },
  );
  const backendClassOptions = useEnumOptions(PROVIDER_MODEL, "backend_class");
  const privateConfigReset = React.useMemo(() => ({ config: {} }), []);
  const backendClassPrefill = useImplPrefill(PROVIDER_MODEL, "backend_class", privateConfigReset);

  return (
      <Form {...props} resource={PROVIDER_MODEL}>
        <Field name="name" title />
        <Group label={t("inference.backend")} columns={2}>
          <Field name="owner" />
          <Field
            name="backend_class"
            widget="select"
            options={backendClassOptions}
            prefill={backendClassPrefill}
            prefillPreserveDirty
            prefillReplace={["config"]}
            createOnly
          />
          <Field name="vendor" />
          <Field name="credential" />
          <Field name="account" />
          <Field name="lifecycle" widget="statusbar" status />
          <Field name="runtime_status" readOnly />
        </Group>
        <Group label={t("inference.provider")} columns={2}>
          <Field name="base_url" />
        </Group>
        <Field name="config" widget="json" />
        <Action id="refresh-models" label={t("inference.refreshModels")} icon="refresh" run={refreshModels} />
      </Form>
  );
}

export const inferenceProviderForm = registerForm(PROVIDER_MODEL, InferenceProviderForm);
