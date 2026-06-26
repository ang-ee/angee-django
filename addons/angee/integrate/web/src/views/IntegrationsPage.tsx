import * as React from "react";
import {
  rowPublicId,
  type Row,
} from "@angee/resources";
import {
  Column,
  ResourceList,
  Field,
  Form,
  Group,
  List,
  useEnumOptions,
  useImplPrefill,
  useAuthoredMutation,
} from "@angee/ui";

import { canConnectRecord, ConnectOAuthButton } from "../connect/ConnectOAuthButton";
import { ConnectIntegration } from "../documents";
import { useIntegrateT } from "../i18n";

const MODEL = "integrate.Integration";
const CONNECT_NEXT = "/integrate";

export function IntegrationsPage(): React.ReactElement {
  const t = useIntegrateT();
  const implClassOptions = useEnumOptions(MODEL, "impl_class");
  const implClassPrefill = useImplPrefill(MODEL, "impl_class");

  const cardActions = React.useCallback(
    (row: Row, context: { refresh: () => void }) =>
      canConnectRecord(row) ? (
        <IntegrationConnectButton row={row} refresh={context.refresh} />
      ) : null,
    [],
  );

  return (
    <ResourceList
      resource={MODEL}
      placement="inline"
      routed
      cardActions={cardActions}
    >
      <List
        resource={MODEL}
        defaultGroups={{
          list: { field: "impl_class" },
          board: { field: "impl_class" },
        }}
      >
        <Column field="display_name" />
        <Column field="impl_label" header={t("integrate.col.implementation")} />
        <Column field="vendor.display_name" header={t("integrate.col.vendor")} />
        <Column field="status" widget="statusBadge" />
        <Column
          field="credential.display_name"
          header={t("integrate.col.credential")}
        />
        <Column field="last_error" header={t("integrate.col.lastError")} />
      </List>
      <Form resource={MODEL} layout="tabs">
        <Field name="display_name" title readOnly />
        <Group label={t("integrate.integrations.identity")} columns={2}>
          <Field name="owner" createOnly />
          <Field name="vendor" createOnly />
          <Field
            name="impl_class"
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
          <Field name="last_used_at" readOnly />
          <Field name="last_used_status" readOnly />
          <Field name="use_count_24h" readOnly />
          <Field name="error_count_24h" readOnly />
          <Field name="last_error" readOnly />
        </Group>
      </Form>
    </ResourceList>
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
      next={CONNECT_NEXT}
      onConnected={refresh}
      start={async ({ redirectUri, next }) => {
        const result = await connectIntegration({
          integrationId: id,
          redirectUri,
          next,
        });
        return result?.connect_integration;
      }}
    />
  );
}
