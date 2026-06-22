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
  type DataToolbarGroupOption,
} from "@angee/base";
import {
  canConnectRecord,
  ConnectOAuthButton,
  connectCallbackPathForRecord,
} from "@angee/integrate";
import { rowPublicId, useAuthoredMutation, type Row } from "@angee/sdk";
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
        canConnectRecord(row) ? <ProviderConnectButton row={row} refresh={context.refresh} /> : null
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
  const id = rowPublicId(row) ?? "";
  if (!id) return null;

  return (
    <ConnectOAuthButton
      label={t("agents.inference.connect.action")}
      connectedTitle={t("agents.inference.connect.connected")}
      startErrorTitle={t("agents.inference.connect.startError")}
      callbackPath={connectCallbackPathForRecord(row)}
      next="/agents/providers"
      onConnected={refresh}
      start={async ({ redirectUri, next }) => {
        const result = await connectProvider({ id, redirectUri, next });
        return result?.connectInferenceProvider;
      }}
    />
  );
}

export function InferenceModelsPage(): React.ReactElement {
  const t = useAgentsT();
  const modelUseOptions = useEnumOptions(MODEL_MODEL, "modelUse");
  const groupOptions = React.useMemo<readonly DataToolbarGroupOption[]>(
    () => [
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
  const defaultGroups = React.useMemo(
    () => ({
      list: { field: "modelUse" },
      board: { field: "provider.name" },
    }),
    [],
  );

  return (
    <DataPage model={MODEL_MODEL} placement="inline" routed>
      <List
        model={MODEL_MODEL}
        list={GroupListView}
        groupOptions={groupOptions}
        defaultGroups={defaultGroups}
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
