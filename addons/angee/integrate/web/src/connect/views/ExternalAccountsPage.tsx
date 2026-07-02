import * as React from "react";
import { Column, ResourceList, Field, Form, Group, List } from "@angee/ui";

import { useIntegrateT } from "../../i18n";

const MODEL = "ExternalAccount";

const accountList = (
  <List resource={MODEL}>
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
    <Form resource={MODEL}>
      <Field name="display_name" title />
      <Field name="status" widget="statusbar" />
      <Group label={t("externalAccounts.group.identity")} columns={2}>
        <Field name="provider_label" label={t("externalAccounts.provider")} readOnly />
        <Field name="external_id" readOnly />
        <Field name="email" />
        <Field name="avatar_url" />
      </Group>
    </Form>
  );
  return (
    <ResourceList resource={MODEL} placement="inline" routed hideCreate>
      {accountList}
      {accountForm}
    </ResourceList>
  );
}
