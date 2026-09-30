import * as React from "react";
import { Action, Field, Form, Group, registerForm, useAuthoredResourceMutation, useRecordActionMutation, useEnumOptions, useImplPrefill, type FormSubmit, type RegisteredFormProps } from "@angee/ui";
import type { DocumentVariables } from "@angee/refine";
import type { ActionFieldName } from "@angee/gql/console/actions";

import {
  CreateInferenceProvider,
  INFERENCE_PROVIDER_UPDATE_INVALIDATES,
  UpdateInferenceProvider,
} from "../documents";
import { useAgentsT } from "../i18n";

const PROVIDER_MODEL = "agents.InferenceProvider";
const MODEL_MODEL = "agents.InferenceModel";

function InferenceProviderForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useAgentsT();
  const [refreshModels] = useRecordActionMutation<ActionFieldName>(
    "refresh_provider_models",
    { invalidateModels: [MODEL_MODEL] },
  );
  const [updateProvider] = useAuthoredResourceMutation(UpdateInferenceProvider, {
    invalidateModels: INFERENCE_PROVIDER_UPDATE_INVALIDATES,
  });
  const [createProvider] = useAuthoredResourceMutation(CreateInferenceProvider, {
    invalidateModels: INFERENCE_PROVIDER_UPDATE_INVALIDATES,
  });
  const backendClassOptions = useEnumOptions(PROVIDER_MODEL, "backend_class");
  const privateConfigReset = React.useMemo(() => ({ config: {} }), []);
  const backendClassPrefill = useImplPrefill(PROVIDER_MODEL, "backend_class", privateConfigReset);
  const submitProvider = React.useCallback<FormSubmit>(
    async (data, context) => {
      if (context.isCreate) {
        const variables: DocumentVariables<typeof CreateInferenceProvider> = {
          data: data as DocumentVariables<typeof CreateInferenceProvider>["data"],
        };
        return (await createProvider(variables))?.create_inference_provider ?? null;
      }
      if (!context.id) throw new Error("Inference provider update requires a saved record.");
      // `data` is FormView's already-normalized payload: relation fields arrive
      // as flat public ids (FormView owns the {id} -> id flattening), so it maps
      // straight onto the patch input. The cast only bridges FormSubmit's untyped
      // `Record<string, unknown>` contract to the typed document variables.
      const variables: DocumentVariables<typeof UpdateInferenceProvider> = {
        data: { ...data, id: context.id } as DocumentVariables<
          typeof UpdateInferenceProvider
        >["data"],
      };
      const result = await updateProvider(variables);
      return result?.update_inference_provider ?? null;
    },
    [createProvider, updateProvider],
  );

  return (
      <Form {...props} resource={PROVIDER_MODEL} submit={submitProvider}>
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

