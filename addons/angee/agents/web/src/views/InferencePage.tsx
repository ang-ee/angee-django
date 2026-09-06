import * as React from "react";
import { rowPublicId, type Row, } from "@angee/metadata";
import {
  Action, Column, ResourceList, Facet, Field, Form, Group, List, registerForm, useAuthoredResourceMutation, useRecordActionMutation, useEnumOptions, useImplPrefill, useRouteHref, type FormSubmit, type RegisteredFormProps } from "@angee/ui";
import { canConnectRecord, ConnectOAuthButton, } from "@angee/integrate";
import { useAuthoredMutation, type DocumentVariables } from "@angee/refine";
import type { ActionFieldName } from "@angee/gql/console/actions";

import {
  ConnectInferenceProvider,
  CreateInferenceProvider,
  INFERENCE_PROVIDER_UPDATE_INVALIDATES,
  UpdateInferenceProvider,
} from "../documents";
import { useAgentsT } from "../i18n";

const PROVIDER_MODEL = "agents.InferenceProvider";
const MODEL_MODEL = "agents.InferenceModel";

export function InferenceProvidersPage(): React.ReactElement {
  const t = useAgentsT();
  return (
    <ResourceList
      resource={PROVIDER_MODEL}
      form={inferenceProviderForm}
      placement="inline"
      routed
      cardActions={(row, context) =>
        canConnectRecord(row) ? <ProviderConnectButton row={row} refresh={context.refresh} /> : null
      }
    >
      <List resource={PROVIDER_MODEL}>
        <Facet field="vendor" label={t("facet.vendor")} labelField="display_name" />
        <Column field="name" />
        <Column field="backend_class" />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        <Column field="credential.display_name" header={t("inference.credential")} />
      </List>
    </ResourceList>
  );
}

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
          <Field name="lifecycle" widget="statusbar" />
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

function ProviderConnectButton({
  row,
  refresh,
}: {
  row: Row;
  refresh: () => void;
}): React.ReactElement | null {
  const t = useAgentsT();
  const routeHref = useRouteHref();
  const [connectProvider] = useAuthoredMutation(ConnectInferenceProvider);
  const id = rowPublicId(row) ?? "";
  if (!id) return null;

  return (
    <ConnectOAuthButton
      label={t("inference.connect.action")}
      connectedTitle={t("inference.connect.connected")}
      startErrorTitle={t("inference.connect.startError")}
      next={routeHref("agents.providers")}
      onConnected={refresh}
      start={async ({ redirectUri, next }) => {
        const result = await connectProvider({ id, redirectUri, next });
        return result?.connect_inference_provider;
      }}
    />
  );
}

export function InferenceModelsPage(): React.ReactElement {
  const t = useAgentsT();
  const modelUseOptions = useEnumOptions(MODEL_MODEL, "model_use");
  const defaultGroups = React.useMemo(
    () => ({
      list: { field: "model_use" },
      board: { field: "provider.name" },
    }),
    [],
  );

  return (
    <ResourceList resource={MODEL_MODEL} placement="inline" routed>
      <List
        resource={MODEL_MODEL}
        defaultGroups={defaultGroups}
      >
        <Facet field="provider" label={t("inference.provider")} labelField="name" />
        <Column field="name" />
        <Column field="provider.name" header={t("inference.provider")} />
        <Column field="display_name" />
        <Column field="model_use" />
        <Column field="status" widget="statusBadge" />
      </List>
      <Form resource={MODEL_MODEL}>
        <Field name="name" title />
        <Field name="display_name" />
        <Group label={t("inference.catalogue")} columns={2}>
          <Field name="provider" createOnly />
          <Field name="publisher" />
          <Field name="model_use" widget="select" options={modelUseOptions} createOnly />
          <Field name="status" widget="statusbar" />
          <Field name="is_default" />
          <Field name="context_window" />
          <Field name="max_output_tokens" />
        </Group>
        <Field name="description" />
        <Field name="capabilities" widget="json" />
        <Field name="config" widget="json" />
      </Form>
    </ResourceList>
  );
}
