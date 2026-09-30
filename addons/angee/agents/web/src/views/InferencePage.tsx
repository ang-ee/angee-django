import * as React from "react";
import { rowPublicId, type Row } from "@angee/metadata";
import { Column, ResourceList, Facet, Field, Form, Group, List, useEnumOptions, useRouteHref } from "@angee/ui";
import { canConnectRecord, ConnectOAuthButton } from "@angee/integrate";
import { useAuthoredMutation } from "@angee/refine";

import { ConnectInferenceProvider } from "../documents";
import { useAgentsT } from "../i18n";
import { inferenceProviderForm } from "./InferenceProviderForm";

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
        <Facet field="vendor" label={t("facet.vendor")} />
        <Column field="name" />
        <Column field="backend_class" />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        <Column field="credential.display_name" header={t("inference.credential")} />
      </List>
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
      board: { field: "provider" },
    }),
    [],
  );

  return (
    <ResourceList resource={MODEL_MODEL} placement="inline" routed>
      <List
        resource={MODEL_MODEL}
        defaultGroups={defaultGroups}
      >
        <Facet field="provider" label={t("inference.provider")} />
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
          <Field name="status" widget="statusbar" status />
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
