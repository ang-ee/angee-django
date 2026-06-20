import * as React from "react";
import {
  Column,
  DataPage,
  Field,
  Form,
  Group,
  GroupListView,
  List,
  useEnumOptions,
  useImplPrefill,
  type DataToolbarGroupOption,
} from "@angee/base";
import { rowPublicId, useAuthoredMutation, type Row } from "@angee/sdk";

import { canConnectRecord, ConnectOAuthButton } from "../connect/ConnectOAuthButton";
import { connectCallbackPathForRecord } from "../connect/redirects";
import { ConnectIntegration } from "../documents";
import { useIntegrateT } from "../i18n";

const MODEL = "integrate.Integration";
const CONNECT_NEXT = "/integrate";

export function IntegrationsPage(): React.ReactElement {
  const t = useIntegrateT();
  const implClassOptions = useEnumOptions(MODEL, "implClass");
  const implClassPrefill = useImplPrefill(MODEL, "implClass");

  // Aggregate on the real groupable axes (impl_class / vendor / status); the
  // implementation lane displays the impl's category. Tone is resolved by the shared
  // STATUS_TONES vocabulary — never a private map (docs/frontend/guidelines.md).
  const groupOptions = React.useMemo<readonly DataToolbarGroupOption[]>(
    () => [
      {
        id: "impl-category",
        label: t("integrate.col.implementation"),
        group: {
          field: "implCategory",
          aggregateField: "implClass",
          aggregateKey: "implClass",
        },
      },
      {
        id: "vendor",
        label: t("integrate.col.vendor"),
        group: {
          field: "vendorLabel",
          aggregateField: "vendor",
          aggregateKey: "vendorId",
        },
      },
      { id: "status", label: t("integrate.col.status"), group: { field: "status" } },
    ],
    [t],
  );

  const cardActions = React.useCallback(
    (row: Row, context: { refresh: () => void }) =>
      canConnectRecord(row) ? (
        <IntegrationConnectButton row={row} refresh={context.refresh} />
      ) : null,
    [],
  );

  return (
    <DataPage
      model={MODEL}
      placement="inline"
      routed
      cardActions={cardActions}
    >
      <List
        model={MODEL}
        list={GroupListView}
        groupOptions={groupOptions}
        defaultGroups={{
          list: {
            field: "implCategory",
            aggregateField: "implClass",
            aggregateKey: "implClass",
          },
          board: {
            field: "implCategory",
            aggregateField: "implClass",
            aggregateKey: "implClass",
          },
        }}
      >
        <Column field="displayName" />
        <Column field="implLabel" header={t("integrate.col.implementation")} />
        <Column field="vendorLabel" header={t("integrate.col.vendor")} />
        <Column field="status" widget="statusBadge" />
        <Column
          field="credential.displayName"
          header={t("integrate.col.credential")}
        />
        <Column field="lastError" header={t("integrate.col.lastError")} />
      </List>
      <Form model={MODEL} layout="tabs">
        <Field name="displayName" title readOnly />
        <Group label={t("integrate.integrations.identity")} columns={2}>
          <Field name="owner" createOnly />
          <Field name="vendor" createOnly />
          <Field
            name="implClass"
            label={t("integrate.integrations.implClass")}
            widget="select"
            options={implClassOptions}
            prefill={implClassPrefill}
            createOnly
          />
          <Field name="status" widget="statusbar" editOnly />
        </Group>
        <Group label={t("integrate.integrations.authentication")} columns={2}>
          <Field name="credential" editOnly />
          <Field name="account" editOnly />
        </Group>
        <Group label={t("integrate.integrations.runtime")} columns={2}>
          <Field name="lastUsedAt" readOnly />
          <Field name="lastUsedStatus" readOnly />
          <Field name="useCount24h" readOnly />
          <Field name="errorCount24h" readOnly />
          <Field name="lastError" readOnly />
        </Group>
      </Form>
    </DataPage>
  );
}

function IntegrationConnectButton({
  row,
  refresh,
}: {
  row: Row;
  refresh: () => void;
}): React.ReactElement | null {
  const t = useIntegrateT();
  const [connectIntegration] = useAuthoredMutation(ConnectIntegration);
  const id = rowPublicId(row) ?? "";
  if (!id) return null;

  return (
    <ConnectOAuthButton
      label={t("integrate.integrations.action.connect")}
      connectedTitle={t("integrate.integrations.connect.connected")}
      startErrorTitle={t("integrate.integrations.connect.startError")}
      callbackPath={connectCallbackPathForRecord(row)}
      next={CONNECT_NEXT}
      onConnected={refresh}
      start={async ({ redirectUri, next }) => {
        const result = await connectIntegration({
          integrationId: id,
          redirectUri,
          next,
        });
        return result?.connectIntegration;
      }}
    />
  );
}
