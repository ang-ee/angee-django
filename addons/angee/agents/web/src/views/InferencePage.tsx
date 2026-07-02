import * as React from "react";
import { rowPublicId, type Row, } from "@angee/metadata";
import {
  Action, Column, ResourceList, Facet, Field, Form, Group, List, useRecordActionMutation, useEnumOptions, useImplPrefill, type FormSubmit } from "@angee/ui";
import { canConnectRecord, ConnectOAuthButton, } from "@angee/integrate";
import { useAuthoredMutation, type DocumentVariables } from "@angee/refine";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { ConnectInferenceProvider, UpdateInferenceProvider } from "../documents";
import { useAgentsT } from "../i18n";

const PROVIDER_MODEL = "agents.InferenceProvider";
const MODEL_MODEL = "agents.InferenceModel";

export function InferenceProvidersPage(): React.ReactElement {
  const t = useAgentsT();
  const [refreshModels] = useRecordActionMutation<ActionFieldName>(
    "refresh_provider_models",
    { invalidateModels: [MODEL_MODEL] },
  );
  const [updateProvider] = useAuthoredMutation(UpdateInferenceProvider, {
    invalidateModels: [PROVIDER_MODEL, MODEL_MODEL],
  });
  const backendClassOptions = useEnumOptions(PROVIDER_MODEL, "backend_class");
  const backendClassPrefill = useImplPrefill(PROVIDER_MODEL, "backend_class");
  const submitProvider = React.useCallback<FormSubmit>(
    async (data, context) => {
      if (!context.id) {
        throw new Error("Inference provider updates require a saved record.");
      }
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
    [updateProvider],
  );

  return (
    <ResourceList
      resource={PROVIDER_MODEL}
      placement="inline"
      routed
      cardActions={(row, context) =>
        canConnectRecord(row) ? <ProviderConnectButton row={row} refresh={context.refresh} /> : null
      }
    >
      <List resource={PROVIDER_MODEL}>
        <Facet field="vendor" label="Vendor" labelField="display_name" />
        <Column field="name" />
        <Column field="backend_class" />
        <Column field="status" widget="statusBadge" />
        <Column field="credential.display_name" header={t("inference.credential")} />
      </List>
      <Form resource={PROVIDER_MODEL} submit={submitProvider}>
        <Field name="name" title />
        <Group label={t("inference.backend")} columns={2}>
          <Field name="owner" />
          <Field
            name="backend_class"
            widget="select"
            options={backendClassOptions}
            prefill={backendClassPrefill}
          />
          <Field name="vendor" />
          <Field name="credential" />
          <Field name="account" />
          <Field name="status" widget="statusbar" />
        </Group>
        <Group label={t("inference.provider")} columns={2}>
          <Field name="base_url" />
        </Group>
        <Field name="config" widget="json" />
        <Action id="refresh-models" label={t("inference.refreshModels")} icon="refresh" run={refreshModels} />
      </Form>
    </ResourceList>
  );
}

function ProviderConnectButton({
  row,
  refresh,
}: {
  row: Row;
  refresh: () => void;
}): React.ReactElement | null {
  const t = useAgentsT();
  const [connectProvider] = useAuthoredMutation(ConnectInferenceProvider);
  const id = rowPublicId(row) ?? "";
  if (!id) return null;

  return (
    <ConnectOAuthButton
      label={t("inference.connect.action")}
      connectedTitle={t("inference.connect.connected")}
      startErrorTitle={t("inference.connect.startError")}
      next="/agents/providers"
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
