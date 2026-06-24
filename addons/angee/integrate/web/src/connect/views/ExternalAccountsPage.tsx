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
} from "@angee/base";

import { useIntegrateT } from "../../i18n";

const MODEL = "ExternalAccount";

const accountList = (
  <List model={MODEL} list={GroupListView}>
    <Column field="provider_label" />
    <Column field="external_id" />
    <Column field="email" />
    <Column field="status" widget="statusBadge" />
    <Column field="credential_status" />
    <Column field="last_used_at" />
  </List>
);

/** Linked external identities (list / edit / delete; created via the connect flow). */
export function ExternalAccountsPage(): React.ReactElement {
  const t = useIntegrateT();
  // Identity (provider + external id) is fixed at link time; the console edits the
  // scalar profile/status. Creation happens through the connect flow, so the
  // Create button is hidden here.
  const accountForm = (
    <Form model={MODEL}>
      <Field name="display_name" title />
      <Field name="status" widget="statusbar" />
      <Group label={t("integrate.externalAccounts.group.identity")} columns={2}>
        <Field name="provider_label" label={t("integrate.externalAccounts.provider")} readOnly />
        <Field name="external_id" readOnly />
        <Field name="email" />
        <Field name="avatar_url" />
      </Group>
      <Action
        id="revoke"
        label={t("integrate.revoke")}
        danger
        set={{ status: "revoked" }}
        confirm={{
          title: t("integrate.externalAccounts.revoke.title"),
          body: t("integrate.externalAccounts.revoke.body"),
          danger: true,
        }}
        visibleWhen={(record) =>
          String(record.status ?? "").toUpperCase() !== "REVOKED"
        }
      />
    </Form>
  );
  return (
    <DataPage model={MODEL} placement="inline" routed hideCreate>
      {accountList}
      {accountForm}
    </DataPage>
  );
}
