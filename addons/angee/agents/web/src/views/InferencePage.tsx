import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Field,
  Form,
  Group,
  GroupListView,
  List,
  useRecordActionMutation,
  useEnumOptions,
  useImplPrefill,
  type DataToolbarFilterOption,
  type DataToolbarGroupOption,
} from "@angee/base";
import {
  canConnectRecord,
  ConnectOAuthButton,
  connectCallbackPathForRecord,
} from "@angee/integrate";
import { useAuthoredMutation, useResourceList, type Row } from "@angee/sdk";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { ConnectInferenceProvider } from "../documents";
import { useAgentsT } from "../i18n";

const PROVIDER_MODEL = "agents.InferenceProvider";
const MODEL_MODEL = "agents.InferenceModel";

export function InferenceProvidersPage(): React.ReactElement {
  const t = useAgentsT();
  const [refreshModels] = useRecordActionMutation<ActionFieldName>("refreshProviderModels");
  const backendClassOptions = useEnumOptions(PROVIDER_MODEL, "backendClass");
  const backendClassPrefill = useImplPrefill(PROVIDER_MODEL, "backendClass");

  return (
    <DataPage
      model={PROVIDER_MODEL}
      placement="inline"
      routed
      cardActions={(row, context) =>
        canConnectProvider(row) ? <ProviderConnectButton row={row} refresh={context.refresh} /> : null
      }
    >
      <List model={PROVIDER_MODEL}>
        <Column field="name" />
        <Column field="backendClass" />
        <Column field="status" widget="statusBadge" />
        <Column field="credential.displayName" header={t("agents.inference.credential")} />
      </List>
      <Form model={PROVIDER_MODEL}>
        <Field name="name" title />
        <Group label={t("agents.inference.backend")} columns={2}>
          <Field name="owner" />
          <Field
            name="backendClass"
            widget="select"
            options={backendClassOptions}
            prefill={backendClassPrefill}
          />
          <Field name="vendor" />
          <Field name="credential" />
          <Field name="account" />
          <Field name="status" widget="statusbar" />
        </Group>
        <Group label={t("agents.inference.provider")} columns={2}>
          <Field name="baseUrl" />
          <Field name="credentialEnv" />
        </Group>
        <Field name="config" widget="json" />
        <Action id="refresh-models" label={t("agents.inference.refreshModels")} icon="refresh" run={refreshModels} />
      </Form>
    </DataPage>
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
  const id = typeof row.id === "string" ? row.id : "";
  if (!id) return null;

  return (
    <ConnectOAuthButton
      label={t("agents.inference.connect.action")}
      connectedTitle={t("agents.inference.connect.connected")}
      startErrorTitle={t("agents.inference.connect.startError")}
      callbackPath={inferenceConnectCallbackPath(row)}
      next="/agents/providers"
      onConnected={refresh}
      start={async ({ redirectUri, next }) => {
        const result = await connectProvider({ id, redirectUri, next });
        return result?.connectInferenceProvider;
      }}
    />
  );
}

function canConnectProvider(row: Row): boolean {
  return canConnectRecord(row);
}

export function inferenceConnectCallbackPath(
  row: Record<string, unknown>,
): string | undefined {
  return connectCallbackPathForRecord(row);
}

export function InferenceModelsPage(): React.ReactElement {
  const t = useAgentsT();
  const modelUseOptions = useEnumOptions(MODEL_MODEL, "modelUse");
  const providers = useResourceList(PROVIDER_MODEL, {
    fields: ["id", "name"],
    pageSize: 100,
  });
  const providerFilters = React.useMemo<readonly DataToolbarFilterOption[]>(
    () =>
      providers.rows
        .flatMap((provider) => {
          const id = typeof provider.id === "string" ? provider.id : "";
          const name = typeof provider.name === "string" ? provider.name : "";
          if (!id || !name) return [];
          return [{
            id: `provider:${id}`,
            label: name,
            chipLabel: name,
            filter: { providerId: { exact: id } },
          }];
        })
        .sort((left, right) => String(left.label).localeCompare(String(right.label))),
    [providers.rows],
  );
  const groupOptions = React.useMemo<readonly DataToolbarGroupOption[]>(
    () => [
      {
        id: "provider.name",
        label: t("agents.inference.provider"),
        group: {
          field: "provider.name",
          aggregateField: "provider",
          aggregateKey: "providerId",
        },
      },
      {
        id: "modelUse",
        label: t("agents.inference.capability"),
        group: { field: "modelUse" },
      },
      {
        id: "status",
        label: t("agents.inference.status"),
        group: { field: "status" },
      },
    ],
    [t],
  );

  return (
    <DataPage model={MODEL_MODEL} placement="inline" routed>
      <List
        model={MODEL_MODEL}
        list={GroupListView}
        filters={providerFilters}
        groupOptions={groupOptions}
        defaultGroups={{
          list: { field: "modelUse" },
          board: {
            field: "provider.name",
            aggregateField: "provider",
            aggregateKey: "providerId",
          },
        }}
      >
        <Column field="name" />
        <Column field="provider.name" header={t("agents.inference.provider")} />
        <Column field="displayName" />
        <Column field="modelUse" />
        <Column field="status" widget="statusBadge" />
      </List>
      <Form model={MODEL_MODEL}>
        <Field name="name" title />
        <Field name="displayName" />
        <Group label={t("agents.inference.catalogue")} columns={2}>
          <Field name="provider" createOnly />
          <Field name="publisher" />
          <Field name="modelUse" widget="select" options={modelUseOptions} createOnly />
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
