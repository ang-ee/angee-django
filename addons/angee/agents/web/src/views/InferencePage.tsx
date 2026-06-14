import * as React from "react";
import { Action, type ActionContext, Column, DataPage, Field, Form, Group, List } from "@angee/base";
import { useAuthoredMutation } from "@angee/sdk";

import {
  REFRESH_PROVIDER_MODELS_MUTATION,
  type IdVariables,
  type RefreshProviderModelsData,
} from "../documents";

const PROVIDER_MODEL = "agents.InferenceProvider";
const MODEL_MODEL = "agents.InferenceModel";

export function InferenceProvidersPage(): React.ReactElement {
  const [refreshProviderModels] = useAuthoredMutation<RefreshProviderModelsData, IdVariables>(
    REFRESH_PROVIDER_MODELS_MUTATION,
  );

  const refreshModels = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await refreshProviderModels({ id: ctx.record.id });
      ctx.refresh();
      return result?.refreshProviderModels.message;
    },
    [refreshProviderModels],
  );

  return (
    <DataPage model={PROVIDER_MODEL} placement="inline" routed>
      <List model={PROVIDER_MODEL} pageSize={50}>
        <Column field="name" />
        <Column field="backendClass" />
        <Column field="status" widget="statusBadge" />
      </List>
      <Form model={PROVIDER_MODEL}>
        <Field name="name" title />
        <Field name="integration" />
        <Group label="Backend" columns={2}>
          <Field name="backendClass" />
          <Field name="baseUrl" />
        </Group>
        <Field name="status" widget="statusbar" />
        <Field name="config" widget="json" />
        <Action id="refresh-models" label="Refresh models" icon="refresh" run={refreshModels} />
      </Form>
    </DataPage>
  );
}

export function InferenceModelsPage(): React.ReactElement {
  return (
    <DataPage model={MODEL_MODEL} placement="inline" routed>
      <List model={MODEL_MODEL} pageSize={50}>
        <Column field="name" />
        <Column field="displayName" />
        <Column field="modelUse" />
        <Column field="status" widget="statusBadge" />
      </List>
      <Form model={MODEL_MODEL}>
        <Field name="name" title />
        <Field name="displayName" />
        <Group label="Catalogue" columns={2}>
          <Field name="provider" />
          <Field name="publisher" />
          <Field name="modelUse" />
          <Field name="status" widget="statusbar" />
          <Field name="isDefault" />
          <Field name="contextWindow" />
          <Field name="maxOutputTokens" />
        </Group>
        <Field name="description" />
        <Field name="capabilities" widget="json" />
        <Field name="config" widget="json" />
      </Form>
    </DataPage>
  );
}
