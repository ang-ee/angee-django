import * as React from "react";
import { Action, Column, DataPage, Field, Form, Group, List } from "@angee/base";

import { useIamT } from "../i18n";

const MODEL = "ExternalAccount";

const accountList = (
  <List model={MODEL}>
    <Column field="providerLabel" />
    <Column field="externalId" />
    <Column field="email" />
    <Column field="status" widget="statusBadge" />
    <Column field="credentialStatus" />
    <Column field="lastUsedAt" />
  </List>
);

/** Linked external identities (list / edit / delete; created via OAuth login). */
export function ExternalAccountsPage(): React.ReactElement {
  const t = useIamT();
  // Identity (provider + externalId) is fixed at link time; the console edits the
  // scalar profile/status. Creation happens through the OAuth login flow, so the
  // Create button is hidden here.
  const accountForm = (
    <Form model={MODEL}>
      <Field name="displayName" title />
      <Field name="status" widget="statusbar" />
      <Group label={t("iam.externalAccounts.group.identity")} columns={2}>
        <Field name="providerLabel" label={t("iam.externalAccounts.provider")} readOnly />
        <Field name="externalId" readOnly />
        <Field name="email" />
        <Field name="avatarUrl" />
      </Group>
      <Action
        id="revoke"
        label={t("iam.revoke")}
        danger
        set={{ status: "revoked" }}
        confirm={{
          title: t("iam.externalAccounts.revoke.title"),
          body: t("iam.externalAccounts.revoke.body"),
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
