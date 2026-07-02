import * as React from "react";
import { Column, ResourceList, Field, Form, Group, List } from "@angee/ui";
import { usePartiesT } from "./i18n";

const MODEL = "parties.Organization";

const organizationsList = (
  <List resource={MODEL}>
    <Column field="display_name" />
    <Column field="domain" />
    <Column field="created_at" />
  </List>
);

/** Organizations (the organisation-kind contacts): full create/edit/list/detail. */
export function OrganizationsPage(): React.ReactElement {
  const t = usePartiesT();
  return (
    <ResourceList resource={MODEL} placement="inline" routed>
      {organizationsList}
      <Form resource={MODEL}>
        <Field name="display_name" title />
        <Group label={t("organization.group.details")} columns={2}>
          <Field name="legal_name" label={t("organization.field.legalName")} />
          <Field name="domain" label={t("organization.field.domain")} />
        </Group>
        <Field name="notes" />
      </Form>
    </ResourceList>
  );
}
